"""Schema-bound output that survives real models: lenient in, strict out.

Retrospective lessons encoded here:
- 3.8.1: bind the schema the way the provider accepts it. Muse has no forced tool choice, so the
  schema goes in ``text.format`` / ``response_format``; ``to_json_schema`` emits the subset Meta
  accepts (no ``$ref`` recursion, inlined defs, ``additionalProperties: false``, every property
  required with nullable types for optional fields). Free-form objects become JSON-encoded strings
  so constrained decoding can never force them to ``{}``; coercion parses them back.
- 3.8.1 quirks: nested objects returned as JSON *strings*, stray XML tags, bare arrays for
  list-typed schemas and double-wrapped lists are all repaired before validation.
- 3.8.3: lenient in (shape coercion, null -> empty/default, label normalisation: exact, alias, first
  allowed word, safe default - a verdict defaults to "inconclusive", never "confirmed"), strict out
  (pydantic; out-of-range confidence still fails).
- 3.8.4: JSON repair ladder (fences, strict parse, injected argument keys, balanced-brace scan,
  truncation recovery) and retry with the *exact* validation error appended at the end of the user
  message, then ``None`` so the caller falls back deterministically.
- 3.8.5: a reply cut off at ``max_output_tokens`` is logged loudly and retried once with a larger
  budget instead of being "repaired" into a silently incomplete object.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

log = logging.getLogger("omniops.llm")

ARG_PARSE_ERROR_KEY = "__argument_parse_error__"
SAFE_DEFAULTS = ("inconclusive", "unknown", "other", "none")
LABEL_ALIASES: dict[str, str] = {
    "refute": "refuted", "rejected": "refuted", "disproved": "refuted", "disproven": "refuted",
    "falsified": "refuted", "confirm": "confirmed", "verified": "confirmed",
    "unclear": "inconclusive", "uncertain": "inconclusive", "undetermined": "inconclusive",
    "insufficient_data": "inconclusive", "insufficient_evidence": "inconclusive", "mixed": "inconclusive",
    "na": "not_applicable", "n/a": "not_applicable", "not_relevant": "not_applicable",
    "none_found": "not_found", "nothing_found": "not_found", "no_findings": "not_found",
    "found_issue": "found",
}
_NEGATION = re.compile(r"(?:\bnot|\bno|\bnon|\bnever|\bun)[\s_-]*$", re.IGNORECASE)
_FENCE = re.compile(r"^\s*```[\w-]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)
_XML_WRAP = re.compile(r"^\s*<([A-Za-z_][\w:.-]*)(?:\s[^>]*)?>(.*)</\1>\s*$", re.DOTALL)
_XML_EDGE = re.compile(r"^\s*</?[A-Za-z_][\w:.-]*(?:\s[^>]*)?>\s*|\s*</?[A-Za-z_][\w:.-]*(?:\s[^>]*)?>\s*$")
_SCHEMA_NAME = re.compile(r"[^A-Za-z0-9_-]")


# ---------------------------------------------------------------------------------------------
# JSON repair ladder
# ---------------------------------------------------------------------------------------------


def strip_wrappers(text: str) -> str:
    """Remove code fences and stray XML tags around a JSON payload."""
    s = text.strip()
    for _ in range(4):
        before = s
        if m := _FENCE.match(s):
            s = m.group(1).strip()
        if m := _XML_WRAP.match(s):
            s = m.group(2).strip()
        s = _XML_EDGE.sub("", s).strip()
        if s == before:
            break
    return s


def _scan(s: str, start: int) -> int | None:
    """Index just past the balanced JSON value starting at ``s[start]`` (string-aware), else None."""
    stack: list[str] = []
    in_str = esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if not stack or stack.pop() != c:
                return None
            if not stack:
                return i + 1
    return None


def _drop_trailing_commas(s: str) -> str:
    out: list[str] = []
    in_str = esc = False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "," and re.match(r"\s*[}\]]", s[i + 1:]):
            continue
        out.append(c)
    return "".join(out)


def _close_truncated(s: str) -> str | None:
    """Cut a truncated JSON document back to its last complete value and close the brackets."""
    stack: list[str] = []
    in_str = esc = False
    str_is_value = False
    prev = ""                                  # last significant char outside strings
    cut: tuple[int, list[str]] | None = None
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
                if str_is_value:
                    cut = (i + 1, list(stack))
                prev = '"'
            continue
        if c.isspace():
            continue
        if c == '"':
            in_str = True
            str_is_value = prev == ":" or (bool(stack) and stack[-1] == "]" and prev in "[,")
        elif c in "{[":
            in_array = bool(stack) and stack[-1] == "]"
            stack.append("}" if c == "{" else "]")
            if not in_array:                   # a cut-off array element is dropped, never left as {}
                cut = (i + 1, list(stack))
        elif c in "}]":
            if not stack or stack.pop() != c:
                return None
            if not stack:
                return s[: i + 1]
            cut = (i + 1, list(stack))
        elif c == ",":
            cut = (i, list(stack))
        prev = c
    if cut is None:
        return None
    end, open_stack = cut
    return s[:end].rstrip().rstrip(",:") + "".join(reversed(open_stack))


def _loads(s: str) -> Any:
    return json.loads(s, strict=False)


def parse_json_lenient(text: str) -> Any:
    """Parse model text into JSON, climbing the repair ladder. Raises ValueError with the reason."""
    s = strip_wrappers(text or "")
    if not s:
        raise ValueError("empty reply: no JSON found")
    for candidate in (s, _drop_trailing_commas(s)):
        try:
            return _loads(candidate)
        except json.JSONDecodeError:
            pass
    starts = [i for i, c in enumerate(s) if c in "{["]
    skip_until = 0
    for i in starts:                           # balanced-brace scan: the first value that parses
        if i < skip_until:
            continue
        end = _scan(s, i)
        if end is None:                        # unbalanced from here on: truncated, not prose
            break
        chunk = s[i:end]
        for candidate in (chunk, _drop_trailing_commas(chunk)):
            try:
                return _loads(candidate)
            except json.JSONDecodeError:
                pass
        skip_until = end
    if starts:                                 # truncation recovery from the first opening bracket
        closed = _close_truncated(s[starts[0]:])
        if closed:
            try:
                return _loads(_drop_trailing_commas(closed))
            except json.JSONDecodeError:
                pass
    raise ValueError(f"no parseable JSON object in the reply (starts with {s[:80]!r})")


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Lenient function-call arguments: dicts pass, JSON strings (even double-encoded) are repaired.

    Unparseable arguments come back as ``{ARG_PARSE_ERROR_KEY: reason}`` so the loop can answer
    with an error tool result instead of calling the handler with garbage.
    """
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    try:
        val = parse_json_lenient(str(raw))
        if isinstance(val, str):
            val = parse_json_lenient(val)
    except ValueError as e:
        return {ARG_PARSE_ERROR_KEY: str(e)}
    if isinstance(val, dict):
        return val
    return {ARG_PARSE_ERROR_KEY: f"arguments must be a JSON object, got {type(val).__name__}"}


def fit_arguments(args: dict[str, Any], parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Strip keys the model injected around/into tool arguments (3.8.4 step 3).

    Unwraps ``{"arguments": {...}}`` / ``{"name": ..., "parameters": {...}}`` style envelopes when the
    tool has no such parameter, and drops unknown keys when the schema forbids extras.
    """
    props = parameters.get("properties") or {}
    if not props:
        return args
    for _ in range(2):
        if set(args) & set(props):
            break
        inner = [v for k, v in args.items() if k in ("arguments", "parameters", "input", "args")
                 and isinstance(v, dict)]
        if len(inner) != 1:
            break
        args = inner[0]
    if parameters.get("additionalProperties") is False:
        args = {k: v for k, v in args.items() if k in props}
    return args


# ---------------------------------------------------------------------------------------------
# Label normalisation (3.8.3)
# ---------------------------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"[\s-]+", "_", s.strip().lower())


def normalize_label(value: Any, allowed: Sequence[str], *, aliases: Mapping[str, str] | None = None,
                    default: str | None = None) -> str | None:
    """exact -> alias -> first allowed word in the text (ignoring negated ones) -> safe default.

    Returns ``default`` (which may be None) when nothing matches.
    """
    if not isinstance(value, str):
        return default
    by_norm = {_norm(a): a for a in allowed}
    key = _norm(value)
    if key in by_norm:
        return by_norm[key]
    for table in (aliases or {}, LABEL_ALIASES):
        target = table.get(key) or table.get(value.strip().lower())
        if target in allowed:
            return target
    best: tuple[int, str] | None = None
    text = value.lower()
    for a in allowed:
        pattern = r"(?<![a-z0-9])" + r"[\s_-]".join(map(re.escape, re.split(r"[\s_-]+", a.lower()))) \
            + r"(?![a-z0-9])"
        for m in re.finditer(pattern, text):
            if _NEGATION.search(text[: m.start()]):
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), a)
            break
    if best:
        return best[1]
    return default


def safe_default(allowed: Sequence[str], field_default: Any = None) -> str | None:
    """The field's own default if it is allowed, else the first conservative label that is allowed."""
    if isinstance(field_default, str) and field_default in allowed:
        return field_default
    return next((d for d in SAFE_DEFAULTS if d in allowed), None)


# ---------------------------------------------------------------------------------------------
# Shape coercion against a pydantic model (lenient in)
# ---------------------------------------------------------------------------------------------


def _unwrap_annotation(ann: Any) -> tuple[Any, bool]:
    """(inner type, accepts None) for Optional / Annotated / X | None."""
    nullable = False
    while True:
        origin = get_origin(ann)
        if origin is Annotated:
            ann = get_args(ann)[0]
            continue
        if origin in (Union, types.UnionType):
            args = [a for a in get_args(ann) if a is not type(None)]
            nullable = nullable or len(args) < len(get_args(ann))
            if len(args) == 1:
                ann = args[0]
                continue
        return ann, nullable


def _is_model(ann: Any) -> bool:
    return isinstance(ann, type) and issubclass(ann, BaseModel)


def _expects_container(ann: Any) -> bool:
    inner, _ = _unwrap_annotation(ann)
    return _is_model(inner) or get_origin(inner) in (list, dict, set, tuple) or inner in (list, dict)


def _maybe_json(v: Any) -> Any:
    """Re-parse a nested JSON string (possibly XML-wrapped) when a container was expected."""
    if not isinstance(v, str):
        return v
    s = strip_wrappers(v)
    if s[:1] in ("{", "["):
        try:
            return parse_json_lenient(s)
        except ValueError:
            return v
    return v


def _list_fields(model: type[BaseModel]) -> list[str]:
    return [n for n, f in model.model_fields.items()
            if get_origin(_unwrap_annotation(f.annotation)[0]) in (list, set, tuple)]


def _empty_for(ann: Any) -> Any:
    inner, _ = _unwrap_annotation(ann)
    origin = get_origin(inner) or inner
    if origin in (list, set, tuple):
        return []
    if origin is dict:
        return {}
    if inner is str:
        return ""
    return None


def _unwrap_envelope(data: dict[str, Any], model: type[BaseModel]) -> Any:
    """{"Output": {...}} / {"result": [...]} -> the payload when no model field is present."""
    names = set(model.model_fields) | {f.alias for f in model.model_fields.values() if f.alias}
    for _ in range(2):
        if not isinstance(data, dict) or set(data) & names or len(data) != 1:
            break
        inner = _maybe_json(next(iter(data.values())))
        if isinstance(inner, dict):
            data = inner
        elif isinstance(inner, list) and len(lf := _list_fields(model)) == 1:
            data = {lf[0]: inner}
        else:
            break
    return data


def _coerce_model(data: Any, model: type[BaseModel]) -> Any:
    data = _maybe_json(data)
    if isinstance(data, list):
        lf = _list_fields(model)
        if len(lf) == 1:                                  # bare array for a list-typed schema
            data = {lf[0]: data}
        elif len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
    if isinstance(data, str):                             # "string into object" for one-string models
        req = [n for n, f in model.model_fields.items()
               if f.is_required() and _unwrap_annotation(f.annotation)[0] is str]
        if len(req) == 1:
            data = {req[0]: data}
    if not isinstance(data, dict):
        return data
    data = _unwrap_envelope(data, model)
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, f in model.model_fields.items():
        key = name if name in out else (f.alias if f.alias and f.alias in out else None)
        if key is None:
            continue
        v = out[key]
        _, nullable = _unwrap_annotation(f.annotation)
        if v is None and not nullable:
            if not f.is_required():
                del out[key]                              # null -> the field's default
                continue
            v = _empty_for(f.annotation)
        out[key] = _coerce_value(v, f.annotation, f)
    return out


def _coerce_value(v: Any, ann: Any, f: FieldInfo | None = None) -> Any:
    if v is None:
        return None
    inner, _ = _unwrap_annotation(ann)
    origin = get_origin(inner)
    if origin is Literal:
        allowed = [a for a in get_args(inner) if isinstance(a, str)]
        if allowed and isinstance(v, str):
            fallback = safe_default(allowed, f.default if f is not None else None)
            label = normalize_label(v, allowed, default=fallback)
            return label if label is not None else v
        return v
    if _is_model(inner):
        return _coerce_model(v, inner)
    if origin in (list, set, tuple):
        v = _maybe_json(v)
        item_ann = get_args(inner)[0] if get_args(inner) else Any
        if isinstance(v, dict):
            only = list(v.values())
            if len(v) == 1 and isinstance(only[0], list):          # double-wrapped list
                v = only[0]
            else:
                v = [v]
        elif not isinstance(v, list):
            v = [v]
        return [_coerce_value(x, item_ann) for x in v]
    if origin is dict or inner is dict:
        return _maybe_json(v)
    if inner is str and isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if inner is str and isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def format_validation_error(err: ValidationError, limit: int = 12) -> str:
    lines = []
    for e in err.errors()[:limit]:
        loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
        got = repr(e.get("input"))
        lines.append(f"- {loc}: {e.get('msg')} (got {got[:100]})")
    extra = len(err.errors()) - limit
    if extra > 0:
        lines.append(f"- ... and {extra} more errors")
    return "\n".join(lines)


def coerce_and_validate[T: BaseModel](raw_text: str, model: type[T]) -> tuple[T | None, str | None]:
    """Repair, coerce and validate a model reply. Returns (object, None) or (None, exact error)."""
    try:
        data = parse_json_lenient(raw_text)
    except ValueError as e:
        return None, f"invalid JSON: {e}"
    try:
        return model.model_validate(_coerce_model(data, model)), None
    except ValidationError as e:
        return None, f"schema validation failed for {model.__name__}:\n{format_validation_error(e)}"


# ---------------------------------------------------------------------------------------------
# pydantic -> JSON schema acceptable to Meta's structured output
# ---------------------------------------------------------------------------------------------

_FOLD_KEYS = ("format", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
              "maxLength", "pattern", "minItems", "maxItems")
_DROP_KEYS = ("title", "default", "examples", "discriminator")


def schema_name(name: str) -> str:
    return _SCHEMA_NAME.sub("_", name)[:64] or "output"


def _inline(node: Any, defs: dict[str, Any], stack: tuple[str, ...]) -> Any:
    if isinstance(node, list):
        return [_inline(x, defs, stack) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name in stack:
            raise ValueError(f"recursive schema through {name!r}: flatten it to a fixed depth")
        merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
        return _inline(merged, defs, (*stack, name))
    if isinstance(node.get("allOf"), list) and len(node["allOf"]) == 1:
        rest = {k: v for k, v in node.items() if k != "allOf"}
        return _inline({**node["allOf"][0], **rest}, defs, stack)
    return {k: _inline(v, defs, stack) for k, v in node.items() if k != "$defs"}


def _describe(node: dict[str, Any], note: str) -> None:
    node["description"] = f"{node['description']} ({note})" if node.get("description") else note


def _strict(node: dict[str, Any]) -> dict[str, Any]:
    node = {k: v for k, v in node.items() if k not in _DROP_KEYS}
    folded = [f"{k}={node.pop(k)}" for k in _FOLD_KEYS if k in node]
    if folded:
        _describe(node, ", ".join(folded))
    if "anyOf" in node:
        node["anyOf"] = [_strict(x) for x in node["anyOf"]]
        return node
    if "properties" in node:
        required = set(node.get("required", []))
        props = {}
        for name, sub in node["properties"].items():
            s = _strict(sub)
            props[name] = s if name in required else _nullable(s)
        node.update(type="object", properties=props, required=list(props), additionalProperties=False)
        return node
    if node.get("type") == "object" or not ({"type", "enum", "const"} & node.keys()):
        # Free-form object / Any: constrained decoding of "{}" would lose data, so ask for JSON text.
        out: dict[str, Any] = {"type": "string", "description": node.get("description", "")}
        _describe(out, "a JSON value encoded as a string")
        return out
    if node.get("type") == "array" and isinstance(node.get("items"), dict):
        node["items"] = _strict(node["items"])
    return node


def _nullable(node: dict[str, Any]) -> dict[str, Any]:
    if "anyOf" in node:
        if not any(x.get("type") == "null" for x in node["anyOf"]):
            node["anyOf"].append({"type": "null"})
        return node
    if node.get("type") == "null":
        return node
    desc = node.pop("description", None)
    out: dict[str, Any] = {"anyOf": [node, {"type": "null"}]}
    if desc:
        out["description"] = desc
    return out


def to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A JSON schema for ``text.format``/``response_format`` in the strict subset Meta accepts."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})
    return _strict(_inline(raw, defs, ()))


# ---------------------------------------------------------------------------------------------
# The retry driver shared by real clients and the scripted test double
# ---------------------------------------------------------------------------------------------


@dataclass
class StructuredTurn:
    text: str
    stop_reason: str | None
    model: str


def with_feedback(user: str, error: str) -> str:
    """The exact validation error goes at the END of the user message (stable prefix stays cached)."""
    return (f"{user}\n\n---\nYour previous reply was rejected by the validator:\n{error}\n"
            "Reply again with exactly one JSON object that satisfies the schema, fixing these problems.")


async def run_structured[T: BaseModel](
    turn: Callable[[str, int], Awaitable[StructuredTurn]],
    *,
    user: str,
    schema: type[T],
    attempts: int,
    max_output_tokens: int,
    ceiling: int,
    on_reject: Callable[[str, str], bool] | None = None,
    on_valid: Callable[[str], None] | None = None,
    label: str = "",
) -> T | None:
    """Ask, validate, re-ask with the exact error; ``None`` after ``attempts`` (3.8.4).

    - A reply that stopped on ``max_output_tokens`` is retried once with double the budget (up to
      ``ceiling``); that retry does not consume an attempt. A cut-off reply after that is a rejected
      attempt ("reply shorter"), never truncation-repaired into a smaller object that still validates
      (3.8.5); it is not a model-quality failure, so ``on_reject`` is not told.
    - ``on_reject(model, error)`` records the failure (escalation ladder); when it returns True on the
      last attempt, the escalated model gets one more attempt.
    """
    budget = max_output_tokens
    bumped = escalation_bonus = False
    limit = max(1, attempts)
    error: str | None = None
    tries = 0
    while tries < limit:
        tries += 1
        out = await turn(with_feedback(user, error) if error else user, budget)
        if out.stop_reason == "max_output_tokens":
            log.warning("structured %s on %s stopped at max_output_tokens=%d (reasoning counts against it)",
                        label or schema.__name__, out.model, budget)
            if not bumped and budget < ceiling:
                bumped = True
                budget = min(ceiling, budget * 2)
                limit += 1
                continue
            error = (f"your reply was cut off at the output limit ({budget} tokens, reasoning included), so it is "
                     "incomplete. Reply with a shorter JSON object: keep every required field, write less text.")
            log.warning("structured %s attempt %d/%d on %s rejected: cut off at the output limit",
                        label or schema.__name__, tries, limit, out.model)
            continue
        obj, error = coerce_and_validate(out.text, schema)
        if obj is not None:
            if on_valid:
                on_valid(out.model)
            return obj
        log.warning("structured %s attempt %d/%d on %s rejected: %s", label or schema.__name__, tries, limit,
                    out.model, (error or "")[:300])
        escalated = on_reject(out.model, error or "") if on_reject else False
        if escalated and tries == limit and not escalation_bonus:
            escalation_bonus = True
            limit += 1
    log.warning("structured %s gave up after %d attempts: falling back deterministically",
                label or schema.__name__, tries)
    return None
