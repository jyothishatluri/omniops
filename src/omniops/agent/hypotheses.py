"""Hypothesis tracking: competing, falsifiable hypotheses with causal levels, wired to the evidence board.

Lessons encoded (OpsIntelligence retrospective):
- 3.6.1: 2-4 competing hypotheses spanning code/deploy, resource, dependency and traffic; a cause the
  alert text suggests starts low and needs two live alternatives (anti-anchoring); confidence >= 0.6
  needs >= 2 independent corroborating measurements (different tables or agents; the alert, memory
  and human leads never corroborate, and a specialist verdict shares the provenance of the query it
  cites); prior incidents and specialist findings must become hypotheses (Content API: the answer sat
  in a postmortem and a shadow agent, untested).
- 3.6.6 / 3.8.2: tracking must actually be wired. Field names come from ``types.Hypothesis``; a step
  that records 0 hypotheses is a loud warning, never silence.
- 3.8.3: lenient in, strict out. Labels are normalised exact -> alias -> first allowed word -> safe
  default; "70%" / "0.7" / 70 are coerced, key aliases and wrappers are accepted, a partial update
  keeps the fields it omits, but out-of-range confidence still fails.
- 3.15: evidence ids are validated against the board; dangling ids (and shadow-agent S# ids, D8) are
  dropped with a warning, and the card restores each hypothesis's ids from here (``card_rows``).
- 3.6.4: verdicts are applied by code: a confident refutation of a parent's premise caps the parent;
  nothing is confirmed by default. Appendix thresholds: tie within 0.08, refuted cap 0.1,
  inconclusive cap 0.5.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel, ValidationError

from omniops.agent.context import keep_ends
from omniops.agent.types import (
    Category,
    CausalLevel,
    CheckVerdict,
    EvidenceRecord,
    Hypothesis,
    HypothesisSet,
    HypStatus,
    SubCheck,
)

if TYPE_CHECKING:
    from omniops.agent.context import RunContext

log = logging.getLogger("omniops.hypotheses")

LEVELS: tuple[str, ...] = get_args(CausalLevel)
CATEGORIES: tuple[str, ...] = get_args(Category)
STATUSES: tuple[str, ...] = get_args(HypStatus)
LEVEL_RANK = {"symptom": 0, "mechanism": 1, "trigger": 2}

# Appendix: confidence thresholds.
CORROBORATION_MIN = 0.6        # at or above this, >= 2 independent supporting items are required
UNCORROBORATED_CAP = 0.55
REFUTED_CAP = 0.1
INCONCLUSIVE_CAP = 0.5
DEMOTED_CAP = 0.3              # a parent whose premise a sub-check confidently refuted
ANCHOR_CAP = 0.4               # the cause the alert text suggests, until independently corroborated
TIE_GAP = 0.08
DEFAULT_CONFIDENCE = 0.3       # a hypothesis recorded without a confidence
CHILD_START = 0.25             # untested sub-hypotheses stay below the "possible" wording band
CONFIRM_STEP = 0.1
PARENT_STEP = 0.05
MAX_CONFIDENCE = 0.9           # one confirmed check never yields certainty
MEASURED_MIN = 0.3             # evidence floor: an item counts at measurement confidence >= 0.3

LIVE = frozenset({"open", "confirmed", "inconclusive"})
# Measurements corroborate; the alert, memory and human leads are things to verify (3.6.1).
CORROBORATING_KINDS = frozenset({"seed", "query", "specialist", "deepening"})
# Diversity requirement: early hypothesis sets must span these cause families.
REQUIRED_FAMILIES: dict[str, frozenset[str]] = {
    "code/deploy": frozenset({"code_deploy", "config"}),
    "resource": frozenset({"resource"}),
    "dependency": frozenset({"dependency", "external"}),
    "traffic": frozenset({"traffic"}),
}

_LEVEL_ALIASES = {
    "root cause": "trigger", "root": "trigger", "cause": "trigger", "triggering": "trigger", "fault": "trigger",
    "change": "trigger", "why": "trigger", "proximate": "mechanism", "component": "mechanism", "how": "mechanism",
    "propagation": "mechanism", "effect": "symptom", "impact": "symptom", "observed": "symptom",
    "observation": "symptom", "what": "symptom", "alert": "symptom",
}
_CATEGORY_ALIASES = {
    "code": "code_deploy", "deploy": "code_deploy", "deployed": "code_deploy", "deployment": "code_deploy",
    "release": "code_deploy", "rollout": "code_deploy", "rollback": "code_deploy", "regression": "code_deploy",
    "bug": "code_deploy", "version": "code_deploy", "commit": "code_deploy",
    "configuration": "config", "flag": "config", "feature flag": "config", "configmap": "config",
    "setting": "config", "settings": "config",
    "memory": "resource", "cpu": "resource", "oom": "resource", "oomkilled": "resource", "exhaustion": "resource",
    "saturation": "resource", "capacity": "resource", "pool": "resource", "thread": "resource", "disk": "resource",
    "heap": "resource", "leak": "resource", "resources": "resource", "gc": "resource",
    "upstream": "dependency", "downstream": "dependency", "database": "dependency", "db": "dependency",
    "cache": "dependency", "queue": "dependency", "dependencies": "dependency", "redis": "dependency",
    "postgres": "dependency", "mysql": "dependency", "kafka": "dependency",
    "third party": "external", "vendor": "external", "provider": "external", "saas": "external",
    "load": "traffic", "surge": "traffic", "spike": "traffic", "volume": "traffic", "throughput": "traffic",
    "infra": "infrastructure", "node": "infrastructure", "network": "infrastructure", "dns": "infrastructure",
    "kubernetes": "infrastructure", "k8s": "infrastructure", "platform": "infrastructure",
    "cluster": "infrastructure",
    "schema": "data", "index": "data", "migration": "data", "bad data": "data", "corruption": "data",
}
_STATUS_ALIASES = {
    "validated": "confirmed", "supported": "confirmed", "proven": "confirmed", "verified": "confirmed",
    "true": "confirmed", "disproved": "refuted", "disproven": "refuted", "rejected": "refuted",
    "ruled out": "refuted", "invalidated": "refuted", "false": "refuted", "unclear": "inconclusive",
    "unknown": "inconclusive", "undetermined": "inconclusive", "partial": "inconclusive", "active": "open",
    "pending": "open", "testing": "open", "untested": "open", "new": "open", "proposed": "open",
    "candidate": "open", "done": "exhausted",
}
_WORD_CONFIDENCE = {"low": 0.25, "medium": 0.45, "moderate": 0.45, "high": 0.65}
_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "statement": ("hypothesis", "claim", "description", "text", "title", "summary"),
    "causal_level": ("level", "causal", "causallevel", "depth", "layer"),
    "category": ("family", "cause_family", "cause_category", "type", "kind"),
    "confidence": ("conf", "score", "probability", "likelihood", "certainty"),
    "supporting": ("supporting_evidence", "evidence", "evidence_ids", "support", "supports", "citations"),
    "contradicting": ("contradicting_evidence", "contradicts", "against", "refuting", "counter_evidence"),
    "missing": ("missing_evidence", "gaps", "needed", "needs", "unknowns"),
    "next_query": ("next_check", "next_step", "next_sql", "query", "sql", "discriminating_query"),
    "status": ("state",),
    "parent": ("parent_id",),
    "verdict_notes": ("notes", "verdicts"),
}
_EID = re.compile(r"\b([A-Za-z]{1,2})-?(\d+)\b")
_HID = re.compile(r"(\d+(?:\.\d+)*)")
_WORDS = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"a", "an", "the", "on", "in", "of", "for", "to", "and", "or", "is", "are", "was", "be", "by",
                   "at", "from", "with", "high", "low", "alert", "firing", "service"})
# Words that name a cause family in an alert, minus ones that appear in alert bodies as plain context
# ("version: 1.4.2", "node: ip-10-...") and would flag every hypothesis of that family as anchored.
_ALERT_CAUSE_ALIASES = {k: v for k, v in _CATEGORY_ALIASES.items()
                        if k not in {"version", "commit", "node", "cluster", "kubernetes", "k8s", "platform", "volume",
                                     "throughput", "index", "schema", "setting", "settings", "type"}}
_ONSET = re.compile(r"onset (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)")
_RATIO = re.compile(r"\(x([0-9.]+) vs baseline\)")


# ---------------------------------------------------------------------------------------------
# Evidence-board access. The board's own module is built separately; these adapters code against
# its documented contract (records with ids) without depending on one method name.
# ---------------------------------------------------------------------------------------------


def _is_record(x: Any) -> bool:
    return hasattr(x, "id") and hasattr(x, "summary") and hasattr(x, "kind")


def board_records(board: Any) -> list[EvidenceRecord]:
    """Every citable evidence record on the board, in board order (shadow-agent records excluded)."""
    for attr in ("visible", "records", "all", "evidence"):
        v = getattr(board, attr, None)
        if callable(v):
            v = v()
        if isinstance(v, dict):
            v = list(v.values())
        if isinstance(v, (list, tuple)):
            return [r for r in v if _is_record(r)]
    try:
        return [r for r in board if _is_record(r)]
    except TypeError:
        return []


def board_lookup(board: Any, eid: str) -> EvidenceRecord | None:
    """The citable record with this id, or None. Shadow-agent records (S#) never steer the primary flow (D8)."""
    get = getattr(board, "get", None)
    if callable(get):
        try:
            rec = get(eid)
        except (KeyError, IndexError, TypeError):
            rec = None
    else:
        rec = next((r for r in board_records(board) if r.id == eid), None)
    if not _is_record(rec) or rec.id.startswith("S"):
        return None
    return rec


def board_add(board: Any, rec: EvidenceRecord) -> str:
    """Publish a record; the board assigns the id when ``rec.id`` is empty. Returns the id."""
    for name in ("add", "add_record", "append", "publish"):
        fn = getattr(board, name, None)
        if callable(fn):
            out = fn(rec)
            if isinstance(out, str):
                return out
            return out.id if _is_record(out) else rec.id
    raise TypeError(f"evidence board {type(board).__name__} has no add()")


def measured_changes(records: Iterable[EvidenceRecord], top: int = 8) -> list[str]:
    """The largest measured step changes (digest lines with 'xN vs baseline'), biggest first (3.2)."""
    found: list[tuple[float, str]] = []
    for r in records:
        if r.error or not r.meaningful:
            continue
        for line in f"{r.summary}\n{r.rendered}".splitlines():
            m = _RATIO.search(line)
            if m:
                try:
                    found.append((float(m.group(1)), f"{r.id}: {' '.join(line.split())[:220]}"))
                except ValueError:
                    continue
    found.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out = []
    for _, line in found:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out[:top]


def earliest_onset(records: Iterable[EvidenceRecord]) -> tuple[datetime, str] | None:
    """The earliest computed onset across evidence digests, with the evidence id that measured it."""
    best: tuple[datetime, str] | None = None
    for r in records:
        if r.error or not r.meaningful:
            continue
        for m in _ONSET.finditer(f"{r.summary}\n{r.rendered}"):
            try:
                ts = datetime.fromisoformat(m.group(1))
            except ValueError:
                continue
            if best is None or ts < best[0]:
                best = (ts, r.id)
    return best


# ---------------------------------------------------------------------------------------------
# Lenient-in coercion
# ---------------------------------------------------------------------------------------------


def normalize_label(value: Any, allowed: Iterable[str], aliases: dict[str, str], default: str) -> str:
    """Exact match -> known alias -> first allowed word in the text -> alias found in the text -> default."""
    if value is None:
        return default
    text = " ".join(_WORDS.findall(str(value).lower()))
    if not text:
        return default
    allowed = tuple(allowed)
    key = text.replace(" ", "_")
    if key in allowed:
        return key
    if text in aliases:
        return aliases[text]
    padded = f" {text} "
    hits = [(padded.find(f" {a.replace('_', ' ')} "), a) for a in allowed]
    hits = [h for h in hits if h[0] >= 0]
    if hits:
        return min(hits)[1]
    hits = [(padded.find(f" {k} "), v) for k, v in aliases.items()]
    hits = [h for h in hits if h[0] >= 0]
    return min(hits)[1] if hits else default


def parse_confidence(value: Any) -> float:
    """'70%', '0.7', 70 and 0.7 all mean 0.7. Out-of-range values still fail (3.8.3)."""
    if value is None or value == "":
        return DEFAULT_CONFIDENCE
    if isinstance(value, bool):
        raise TypeError(f"confidence {value!r} is not a number")
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _WORD_CONFIDENCE:
            return _WORD_CONFIDENCE[s]
        pct = s.endswith("%")
        try:
            num = float(s.rstrip("%").strip())
        except ValueError:
            raise ValueError(f"confidence {value!r} is not a number between 0 and 1") from None
        if pct or (1 < num <= 100 and num.is_integer()):
            num /= 100
    elif isinstance(value, (int, float)):
        num = float(value)
        if 1 < num <= 100 and num.is_integer():
            num /= 100
    else:
        raise TypeError(f"confidence {value!r} is not a number")
    if not 0.0 <= num <= 1.0:
        raise ValueError(f"confidence {value!r} is out of range (0-1)")
    return round(num, 3)


def parse_evidence_ids(value: Any) -> list[str]:
    """'E1, e2', ['[E3]'], 4 -> ['E1', 'E2', 'E3', 'E4']; S-prefixed shadow ids are kept."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            ids = [f"E{item}"]
        elif isinstance(item, dict):
            ids = parse_evidence_ids(item.get("id") or item.get("evidence_id"))
        else:
            s = str(item).strip()
            ids = [f"{p.upper()}{n}" for p, n in _EID.findall(s)] or ([f"E{s}"] if s.isdigit() else [])
        out += [i for i in ids if i not in out]
    return out


def normalize_hypothesis_id(value: Any) -> str | None:
    """'h2', 'hyp-2', 'Hypothesis 2', 2 -> 'H2'; 'H1.2' stays."""
    if value is None or isinstance(value, bool):
        return None
    m = _HID.search(str(value))
    return f"H{m.group(1)}" if m else None


def _as_list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    return [s] if s else []


def statement_key(text: str) -> str:
    """Normalised statement text: re-recorded hypotheses without an id merge on it."""
    return " ".join(_WORDS.findall(text.lower()))


def _content_words(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if w not in _STOP and len(w) > 2 and not w.isdigit()}


def _union(*lists: Iterable[str]) -> list[str]:
    out: list[str] = []
    for lst in lists:
        out += [x for x in lst if x not in out]
    return out


def _unwrap(items: Any) -> list[Any]:
    """Bare arrays, {"hypotheses": [...]} wrappers, HypothesisSet, JSON strings and single objects."""
    if isinstance(items, HypothesisSet):
        return list(items.hypotheses)
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            return [items]
    if isinstance(items, dict):
        inner = items.get("hypotheses")
        if isinstance(inner, (list, str, dict)):
            return _unwrap(inner)
        return [items]
    if isinstance(items, (list, tuple)):
        return list(items)
    return [items] if items is not None else []


def _normalize(raw: Any) -> dict[str, Any]:
    if isinstance(raw, BaseModel):
        # Only the fields the model actually sent (3.8.3): pydantic defaults (category "other", status
        # "open") must not overwrite a stored hypothesis on a partial update.
        d = raw.model_dump(exclude_unset=True)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"statement": raw}
        d = parsed if isinstance(parsed, dict) else {"statement": raw}
    elif isinstance(raw, dict):
        d = dict(raw)
    else:
        raise TypeError(f"expected an object, got {type(raw).__name__}")
    d = {re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): v for k, v in d.items()}
    for canon, aliases in _KEY_ALIASES.items():
        if d.get(canon) in (None, "", []):
            for a in aliases:
                if d.get(a) not in (None, "", []):
                    d[canon] = d[a]
                    break
    # Only the fields the input carries: a partial update ({"id": "H1", "confidence": 0.6}) keeps the rest.
    out: dict[str, Any] = {"id": normalize_hypothesis_id(d.get("id"))}

    def given(key: str) -> bool:
        return d.get(key) not in (None, "", [])

    statement = " ".join(str(d.get("statement") or "").split())
    if statement:
        out["statement"] = statement
    if given("causal_level"):
        out["causal_level"] = normalize_label(d["causal_level"], LEVELS, _LEVEL_ALIASES, "symptom")
    if given("category"):
        out["category"] = normalize_label(d["category"], CATEGORIES, _CATEGORY_ALIASES, "other")
    if given("confidence"):
        out["confidence"] = parse_confidence(d["confidence"])
    if given("status"):
        out["status"] = normalize_label(d["status"], STATUSES, _STATUS_ALIASES, "open")
    if given("parent"):
        out["parent"] = normalize_hypothesis_id(d["parent"])
    for key in ("supporting", "contradicting"):
        out[key] = parse_evidence_ids(d.get(key))
    for key in ("missing", "verdict_notes"):
        out[key] = _as_list_of_str(d.get(key))
    if given("next_query"):
        out["next_query"] = "; ".join(_as_list_of_str(d["next_query"])) or None
    return out


# ---------------------------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------------------------


@dataclass
class UpdateResult:
    accepted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def text(self) -> str:
        """Feedback for the model (the record_hypotheses tool result)."""
        lines = [f"recorded {len(self.accepted)} hypothesis(es): {', '.join(self.accepted) or 'none'}"]
        lines += [f"rejected {r}" for r in self.rejected]
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)


@dataclass
class VerdictEffect:
    hypothesis_id: str
    verdict: str
    demoted: str | None = None            # parent whose premise was refuted
    fallen_top_level: str | None = None   # top-level hypothesis that fell (refuted or demoted)
    reached_trigger: bool = False         # confirmed, terminal and at trigger level
    notes: list[str] = field(default_factory=list)


class HypothesisTracker:
    """The run's hypothesis tree. All mutations go through here so caps and id checks always apply."""

    def __init__(self, board: Any, *, alert_text: str = "", warn: Callable[[str], None] | None = None) -> None:
        self.board = board
        self.hypotheses: dict[str, Hypothesis] = {}
        self.checks: dict[str, SubCheck] = {}
        self.reformed = False
        self._alert_text = alert_text
        self._warn = warn or log.warning
        self._judged: set[str] = set()      # status set by the verdict judge or by code
        self._confirmed: set[str] = set()   # confirmed by a verdict (the only way to confirm)
        self._demoted: set[str] = set()

    def bind(self, ctx: RunContext) -> HypothesisTracker:
        """Route loud-zero warnings to the run and read the alert text for anti-anchoring."""
        self._warn = ctx.warn
        parts: list[str] = []
        if ctx.alert is not None:
            parts += [ctx.alert.title, ctx.alert.rule_name or "", ctx.alert.text]
        if ctx.question:
            parts.append(ctx.question)
        self._alert_text = " ".join(p for p in parts if p)
        return self

    # -- reads ------------------------------------------------------------------------------

    def get(self, hid: str) -> Hypothesis | None:
        return self.hypotheses.get(hid)

    def as_list(self) -> list[Hypothesis]:
        return [h.model_copy(deep=True) for h in self.hypotheses.values()]

    def roots(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses.values() if not h.parent or h.parent not in self.hypotheses]

    def children(self, hid: str) -> list[Hypothesis]:
        return [h for h in self.hypotheses.values() if h.parent == hid]

    def ancestors(self, hid: str) -> list[Hypothesis]:
        """Parent first, root last."""
        out: list[Hypothesis] = []
        h = self.hypotheses.get(hid)
        while h is not None and h.parent and h.parent in self.hypotheses and len(out) < 20:
            h = self.hypotheses[h.parent]
            out.append(h)
        return out

    def root_of(self, hid: str) -> str:
        anc = self.ancestors(hid)
        return anc[-1].id if anc else hid

    def is_judged(self, hid: str) -> bool:
        return hid in self._judged

    @staticmethod
    def _rank(h: Hypothesis) -> tuple[float, bool, int]:
        return (round(h.confidence, 3), h.status == "confirmed", LEVEL_RANK[h.causal_level])

    def leader(self) -> Hypothesis | None:
        """The best non-refuted hypothesis (what the report leads with); deeper levels win ties."""
        cands = [h for h in self.hypotheses.values() if h.status != "refuted"]
        return max(cands, key=self._rank) if cands else None

    def focus(self, *, min_confidence: float = 0.0, exclude_ids: Iterable[str] = (),
              exclude_subtrees: Iterable[str] = ()) -> Hypothesis | None:
        """The best live hypothesis to deepen next (exhausted, refuted and demoted ones are skipped)."""
        ids, subtrees = set(exclude_ids), set(exclude_subtrees)
        cands = [h for h in self.hypotheses.values()
                 if h.status in LIVE and h.confidence >= min_confidence and h.id not in ids
                 and h.id not in self._demoted and h.id not in subtrees
                 and not any(a.id in subtrees for a in self.ancestors(h.id))]
        return max(cands, key=self._rank) if cands else None

    def _family_scores(self) -> list[tuple[float, Hypothesis]]:
        """Competing families: each live root with the best confidence in its subtree."""
        out = []
        for root in self.roots():
            if root.status == "refuted":
                continue
            members = [root] + [h for h in self.hypotheses.values()
                                if h.status != "refuted" and any(a.id == root.id for a in self.ancestors(h.id))]
            best = max(members, key=self._rank)
            out.append((best.confidence, best))
        out.sort(key=lambda x: (-x[0], -LEVEL_RANK[x[1].causal_level]))
        return out

    def top_two(self) -> tuple[Hypothesis, Hypothesis] | None:
        fams = self._family_scores()
        return (fams[0][1], fams[1][1]) if len(fams) >= 2 else None

    def top_two_gap(self) -> float | None:
        fams = self._family_scores()
        return round(fams[0][0] - fams[1][0], 3) if len(fams) >= 2 else None

    def is_tie(self) -> bool:
        """Closer than 0.08 is a deferred decision, not an answer (3.6.3)."""
        gap = self.top_two_gap()
        return gap is not None and gap < TIE_GAP

    def refuted(self) -> list[Hypothesis]:
        """Negative facts: what this run ruled out."""
        return [h for h in self.hypotheses.values() if h.status == "refuted"]

    def negative_facts(self) -> list[str]:
        out = []
        for h in self.refuted():
            why = h.verdict_notes[-1] if h.verdict_notes else "refuted"
            ev = f" ({', '.join(h.contradicting)})" if h.contradicting else ""
            out.append(f"{h.id}: {h.statement} - {why[:240]}{ev}")
        for hid in sorted(self._demoted):
            h = self.hypotheses[hid]
            if h.status != "refuted":
                out.append(f"{h.id}: premise refuted by a sub-check; confidence capped at {DEMOTED_CAP}")
        return out

    @staticmethod
    def _measured(r: EvidenceRecord | None) -> bool:
        return (r is not None and not r.error and r.meaningful and r.confidence >= MEASURED_MIN
                and r.kind in CORROBORATING_KINDS)

    def _provenances(self, r: EvidenceRecord, _seen: frozenset[str] = frozenset()
                     ) -> set[tuple[str, tuple[str, ...]]]:
        """(agent, tables) of a measurement. A specialist verdict is not a source of its own: it stands
        for the measurements it cites (a verdict citing seed E3 adds E3's provenance, never a second
        one), so a verdict and the query behind it never count as two independent items, and a verdict
        citing nothing measured adds nothing."""
        if r.kind != "specialist":
            return {(r.agent, tuple(sorted(r.tables)) or (r.kind,))}
        out: set[tuple[str, tuple[str, ...]]] = set()
        for eid in r.data.get("evidence_ids") or []:
            cited = board_lookup(self.board, eid)
            if cited is None or cited.id in _seen or cited is r or not self._measured(cited):
                continue
            out |= self._provenances(cited, _seen | {r.id})
        return out

    def independent_support(self, h: Hypothesis) -> int:
        """Distinct (agent, tables) provenances among measured, meaningful supporting items (3.6.1)."""
        keys: set[tuple[str, tuple[str, ...]]] = set()
        for eid in h.supporting:
            r = board_lookup(self.board, eid)
            if r is None or not self._measured(r):
                continue
            keys |= self._provenances(r)
        return len(keys)

    def card_rows(self) -> list[dict[str, Any]]:
        """Hypotheses for the conclusion card with evidence ids restored from here (3.15)."""
        rows = []
        for h in self.hypotheses.values():
            if h.status == "refuted" or h.confidence < MEASURED_MIN:
                continue
            ids = [e for e in h.supporting if board_lookup(self.board, e) is not None]
            rows.append({"id": h.id, "statement": h.statement, "confidence": h.confidence, "status": h.status,
                         "causal_level": h.causal_level, "evidence_ids": ids})
        rows.sort(key=lambda r: -r["confidence"])
        return rows

    # -- notes ------------------------------------------------------------------------------

    def alert_suggested(self, h: Hypothesis) -> bool:
        """True when the hypothesis is the cause the alert text itself points at."""
        if not self._alert_text:
            return False
        text = f" {' '.join(_WORDS.findall(self._alert_text.lower()))} "
        suggested = {v for k, v in _ALERT_CAUSE_ALIASES.items() if f" {k} " in text}
        suggested |= {c for c in CATEGORIES if f" {c.replace('_', ' ')} " in text and c != "other"}
        if h.category in suggested:
            return True
        alert_words = _content_words(self._alert_text)
        return len(alert_words) >= 2 and len(alert_words & _content_words(h.statement)) / len(alert_words) >= 0.6

    def diversity_notes(self) -> list[str]:
        """Early sets (nothing judged yet) must span code/deploy, resource, dependency and traffic."""
        if self._judged:
            return []
        live = [h for h in self.roots() if h.status != "refuted"]
        if not live:
            return []
        covered = {h.category for h in self.roots()}
        missing = [name for name, cats in REQUIRED_FAMILIES.items() if not cats & covered]
        if not missing:
            return []
        return [(f"diversity: no hypothesis covers {', '.join(missing)}. Early hypotheses must span code/deploy, "
                 "resource, dependency and traffic: add one per missing family, or name in another hypothesis's "
                 "contradicting/missing which evidence rules that family out.")]

    def anchoring_notes(self) -> list[str]:
        lead = self.leader()
        if lead is None or not self.alert_suggested(lead):
            return []
        alternatives = [r for r in self.roots() if r.status != "refuted" and r.id != self.root_of(lead.id)]
        note = (f"anti-anchoring: leader {lead.id} is the cause the alert text itself suggests. It stays at "
                f"<= {ANCHOR_CAP} until two independent measurements corroborate it")
        if len(alternatives) < 2:
            note += f", and it needs at least two live alternatives (currently {len(alternatives)})"
        return [note + "."]

    def untested_lead_notes(self) -> list[str]:
        """Prior incidents and specialist findings that no hypothesis tests (Content API miss)."""
        cited = {e for h in self.hypotheses.values() for e in h.supporting + h.contradicting}
        out = []
        for r in board_records(self.board):
            if r.id in cited or r.error or not r.meaningful:
                continue
            prior = r.kind == "memory" or (r.kind == "specialist" and r.agent == "prior_incidents"
                                           and r.data.get("verdict") == "found")
            if prior:
                out.append(f"prior incident {r.id} is not tested by any hypothesis: make it its own hypothesis "
                           "and check whether the prior trigger class fired before this onset.")
            elif r.kind == "specialist" and r.data.get("verdict") == "found":
                out.append(f"specialist finding {r.id} ({r.agent}) is not covered by any hypothesis: test it.")
        return out

    def notes(self) -> list[str]:
        return self.diversity_notes() + self.anchoring_notes() + self.untested_lead_notes()

    # -- writes -----------------------------------------------------------------------------

    def update(self, items: Any, *, origin: str = "model") -> UpdateResult:
        """Merge model-proposed hypotheses (lenient in), validate ids, apply caps (strict out)."""
        result = UpdateResult()
        raw_items = _unwrap(items)
        for i, raw in enumerate(raw_items, 1):
            try:
                h = self._merge(_normalize(raw), result.notes)
            except (ValueError, TypeError, ValidationError) as exc:
                result.rejected.append(f"item {i}: {' '.join(str(exc).split())[:300]}")
                continue
            if h.id not in result.accepted:
                result.accepted.append(h.id)
        self._apply_caps(result.notes)
        result.notes += self.notes()
        if not result.accepted:
            self._warn(f"hypotheses ({origin}): 0 hypotheses recorded from {len(raw_items)} item(s)"
                       + (f"; rejected: {'; '.join(result.rejected)[:400]}" if result.rejected else ""))
        return result

    def _valid_ids(self, ids: Iterable[str], hid: str, notes: list[str]) -> list[str]:
        ok, dangling = [], []
        for e in ids:
            (ok if board_lookup(self.board, e) is not None else dangling).append(e)
        if dangling:
            msg = f"{hid}: dropped evidence id(s) not on the board: {', '.join(dangling)}"
            self._warn(msg)
            notes.append(msg + " (cite only ids that exist)")
        return ok

    def _next_root_id(self) -> str:
        nums = [int(m.group(1)) for k in self.hypotheses if (m := re.fullmatch(r"H(\d+)", k))]
        return f"H{max(nums, default=0) + 1}"

    def _next_child_id(self, parent: str) -> str:
        n = 1
        while f"{parent}.{n}" in self.hypotheses:
            n += 1
        return f"{parent}.{n}"

    def _merge(self, d: dict[str, Any], notes: list[str]) -> Hypothesis:
        hid = d["id"]
        if hid is None and "statement" in d:
            key = statement_key(d["statement"])
            hid = next((h.id for h in self.hypotheses.values() if statement_key(h.statement) == key), None)
        old = self.hypotheses.get(hid) if hid else None
        parent = d.get("parent")
        if parent is None and old is None and hid and "." in hid and hid.rsplit(".", 1)[0] in self.hypotheses:
            parent = hid.rsplit(".", 1)[0]
        if parent is not None and (parent not in self.hypotheses or parent == hid):
            notes.append(f"{hid or 'new hypothesis'}: unknown parent {parent} ignored")
            parent = None
        if old is None:
            if "statement" not in d:
                raise ValueError("missing 'statement' (a specific, falsifiable claim)")
            hid = hid or (self._next_child_id(parent) if parent else self._next_root_id())
            base: dict[str, Any] = {"causal_level": "symptom", "category": "other",
                                    "confidence": DEFAULT_CONFIDENCE, "status": "open"}
        else:
            base = old.model_dump()
        supporting = self._valid_ids(d.pop("supporting"), hid, notes)
        contradicting = self._valid_ids(d.pop("contradicting"), hid, notes)
        merged = base | {k: v for k, v in d.items() if k not in ("id", "parent")}
        merged.update(id=hid, parent=parent or (old.parent if old else None))
        status = merged["status"]
        if status == "confirmed" and hid not in self._confirmed:
            # Never confirmed by default: only the verdict judge confirms (3.6.4).
            status = old.status if old is not None else "open"
            notes.append(f"{hid}: only the verdict judge confirms; status stays {status}")
        if old is not None:
            if hid in self._judged and old.status in {"refuted", "confirmed", "exhausted"} and status != old.status:
                notes.append(f"{hid}: status stays {old.status} (set by the verdict judge); "
                             "record a new hypothesis to pursue a different claim")
                status = old.status
            supporting = _union(old.supporting, supporting)
            contradicting = _union(old.contradicting, contradicting)
            merged["verdict_notes"] = _union(old.verdict_notes, d["verdict_notes"])
            merged["missing"] = d["missing"] or old.missing
        merged.update(status=status, supporting=supporting, contradicting=contradicting)
        h = Hypothesis.model_validate(merged)
        self.hypotheses[hid] = h
        return h

    def _cap(self, h: Hypothesis) -> str | None:
        before = h.confidence
        reasons = []
        indep = self.independent_support(h)
        caps = []
        if h.confidence >= CORROBORATION_MIN and indep < 2:
            caps.append((UNCORROBORATED_CAP, (f">= {CORROBORATION_MIN} needs >= 2 independent supporting items "
                                              f"(different tables or agents; has {indep})")))
        if indep < 2 and self.alert_suggested(h):
            caps.append((ANCHOR_CAP, "the alert text suggests this cause; it starts low until corroborated"))
        if h.status == "refuted":
            caps.append((REFUTED_CAP, "refuted"))
        if h.status == "inconclusive":
            caps.append((INCONCLUSIVE_CAP, "inconclusive"))
        if h.id in self._demoted:
            caps.append((DEMOTED_CAP, "a sub-check refuted its premise"))
        for cap, why in caps:
            if h.confidence > cap:
                h.confidence = cap
                reasons.append(why)
        if h.confidence < before or reasons:
            return f"{h.id}: confidence {before:.2f} -> {h.confidence:.2f} ({'; '.join(reasons)})"
        return None

    def _apply_caps(self, notes: list[str]) -> None:
        for h in self.hypotheses.values():
            note = self._cap(h)
            if note:
                notes.append(note)

    def add_subchecks(self, parent_id: str, checks: Iterable[SubCheck]) -> list[Hypothesis]:
        """Register sub-hypotheses one level below ``parent_id``; ids are re-numbered when taken."""
        parent = self.hypotheses[parent_id]
        out = []
        for chk in checks:
            hid = normalize_hypothesis_id(chk.id)
            if not hid or hid in self.hypotheses or hid.rsplit(".", 1)[0] != parent_id:
                hid = self._next_child_id(parent_id)
            level = chk.causal_level
            if LEVEL_RANK[level] < LEVEL_RANK[parent.causal_level]:
                level = parent.causal_level       # one level deeper, never back up the chain
            h = Hypothesis(id=hid, statement=chk.statement, causal_level=level, category=parent.category,
                           confidence=min(parent.confidence, CHILD_START), parent=parent_id, next_query=chk.sql,
                           missing=[f"confirms if: {chk.confirms_if}", f"refutes if: {chk.refutes_if}"])
            self.hypotheses[hid] = h
            self.checks[hid] = chk.model_copy(update={"id": hid, "causal_level": level})
            out.append(h)
        return out

    def apply_verdict(self, sub_id: str, verdict: CheckVerdict, evidence_ids: Iterable[str] = ()) -> VerdictEffect:
        """Apply a judged check. Code sets the numbers; the judge only says confirmed/refuted/inconclusive."""
        h = self.hypotheses.get(sub_id)
        if h is None:
            raise ValueError(f"unknown hypothesis {sub_id!r}")
        effect = VerdictEffect(sub_id, verdict.verdict)
        eids = self._valid_ids(evidence_ids, sub_id, effect.notes)
        parent = self.hypotheses.get(h.parent) if h.parent else None
        self._judged.add(sub_id)
        h.verdict_notes.append(f"{verdict.verdict}: {verdict.rationale}" + (f" [{', '.join(eids)}]" if eids else ""))
        if verdict.verdict == "confirmed":
            h.status = "confirmed"
            self._confirmed.add(sub_id)
            base = parent.confidence if parent else h.confidence
            h.supporting = _union(parent.supporting if parent else [], h.supporting, eids)
            h.confidence = min(MAX_CONFIDENCE, max(base, 0.5) + CONFIRM_STEP)
            if parent:
                parent.supporting = _union(parent.supporting, eids)
                parent.confidence = min(MAX_CONFIDENCE, parent.confidence + PARENT_STEP)
            effect.reached_trigger = verdict.terminal and h.causal_level == "trigger"
        elif verdict.verdict == "refuted":
            h.status = "refuted"
            h.contradicting = _union(h.contradicting, eids)
            if h.parent is None:
                effect.fallen_top_level = h.id
            if verdict.refutes_parent_premise and parent is not None:
                parent.contradicting = _union(parent.contradicting, eids)
                parent.verdict_notes.append(f"premise refuted by {sub_id}: {verdict.rationale[:240]}")
                if parent.status == "confirmed":      # a confirmation whose premise fell no longer holds
                    parent.status = "inconclusive"
                    self._confirmed.discard(parent.id)
                self._demoted.add(parent.id)
                effect.demoted = parent.id
                if parent.parent is None:
                    effect.fallen_top_level = parent.id
        else:
            h.status = "inconclusive"
        self._apply_caps(effect.notes)
        return effect

    def mark_exhausted(self, hid: str, reason: str) -> bool:
        """A hypothesis that used up its attempts without confirmation. Confirmed ones keep their status."""
        h = self.hypotheses.get(hid)
        if h is None:
            return False
        h.verdict_notes.append(f"exhausted: {reason}")
        if h.status in {"open", "inconclusive"}:
            h.status = "exhausted"
            self._judged.add(hid)
            return True
        return False

    # -- prompt views -----------------------------------------------------------------------

    def render_tree(self, *, max_chars: int = 8000) -> str:
        if not self.hypotheses:
            return "HYPOTHESES: none recorded yet."
        lead = self.leader()
        gap = self.top_two_gap()
        head = f"HYPOTHESES (leader {lead.id if lead else 'none'}"
        if gap is not None:
            head += f"; top-two gap {gap:.2f}{' = TIE, run discriminating queries' if gap < TIE_GAP else ''}"
        lines = [head + "):"]

        def walk(h: Hypothesis, depth: int) -> None:
            pad = "  " * depth
            lines.append(f"{pad}{h.id} [{h.causal_level} | {h.category} | {h.confidence:.2f} | {h.status}] "
                         f"{h.statement}")
            detail = []
            if h.supporting:
                detail.append("supporting " + ", ".join(h.supporting))
            if h.contradicting:
                detail.append("contradicting " + ", ".join(h.contradicting))
            if h.missing:
                detail.append("missing: " + "; ".join(h.missing))
            if h.next_query:
                detail.append("next: " + h.next_query)
            if detail:
                lines.append(f"{pad}    " + " | ".join(detail))
            lines.extend(f"{pad}    verdict {n[:300]}" for n in h.verdict_notes[-2:])
            for c in sorted(self.children(h.id), key=lambda x: x.id):
                walk(c, depth + 1)

        for root in self.roots():
            walk(root, 0)
        neg = self.negative_facts()
        if neg:
            lines += ["RULED OUT (negative facts):"] + [f"- {x}" for x in neg]
        notes = self.notes()
        if notes:
            lines += ["NOTES:"] + [f"- {n}" for n in notes]
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 40] + "\n[truncated hypothesis tree]"


# ---------------------------------------------------------------------------------------------
# Hypothesis formation (role "hypotheses")
# ---------------------------------------------------------------------------------------------


@cache
def load_prompt(name: str) -> str:
    """Static sectioned prompt text from agent/prompts/<name>.md."""
    return (Path(__file__).with_name("prompts") / f"{name}.md").read_text(encoding="utf-8")


def evidence_lines(ctx: RunContext, limit: int = 60, width: int = 500) -> list[str]:
    """Pinned records first, then the most recent ones; specialists and memory included (profile-lookup outage case)."""
    recs = board_records(ctx.board)
    pinned = [r for r in recs if r.pinned]
    unpinned = [r for r in recs if not r.pinned]
    n = max(0, limit - len(pinned))
    keep = {r.id for r in pinned + (unpinned[len(unpinned) - n:] if n else [])}
    return [r.line(width) for r in recs if r.id in keep]


def alert_brief(ctx: RunContext) -> str:
    a = ctx.alert
    parts = []
    if a is not None:
        parts.append(f"ALERT ({a.platform}, {a.status}): {a.title}")
        for label, v in (("service", a.service), ("env", a.env), ("host", a.host), ("namespace", a.namespace),
                         ("group", a.group), ("query", a.query), ("threshold", a.threshold), ("value", a.value)):
            if v:
                parts.append(f"  {label}: {v}")
        if a.text:
            parts.append("  text: " + " ".join(a.text.split())[:800])
    if ctx.question:
        parts.append(f"QUESTION: {ctx.question}")
    an = ctx.anchor
    if an.window_start and an.window_end:
        parts.append(f"ANALYSIS WINDOW: {an.window_start.isoformat()} -> {an.window_end.isoformat()} "
                     f"(now() is pinned to {an.now.isoformat()})")
    return "\n".join(parts) or "ALERT: none"


async def form_hypotheses(ctx: RunContext, *, instructions: str = "") -> UpdateResult:
    """Ask the hypotheses role for 2-4 competing hypotheses from all evidence; merge them into the tracker."""
    tracker = ctx.tracker.bind(ctx)
    records = board_records(ctx.board)
    blocks = [alert_brief(ctx), "EVIDENCE:", *evidence_lines(ctx)]
    changes = measured_changes(records)
    if changes:
        blocks += ["MEASURED CHANGES (largest first; a hypothesis that ignores the biggest one must say why):",
                   *changes]
    if ctx.thread_context:
        blocks += ["THREAD REPLIES (leads to verify, never confirmation):", keep_ends(ctx.thread_context, 3000)]
    blocks += ["CURRENT " + tracker.render_tree()]
    if instructions:
        blocks += ["INSTRUCTIONS FOR THIS CALL:", instructions]
    res = await ctx.deps.llm.structured(role="hypotheses", system=load_prompt("hypotheses"),
                                        user="\n".join(blocks), schema=HypothesisSet,
                                        timeout_s=max(20.0, min(240.0, ctx.remaining())))
    if res is None:
        ctx.warn("hypotheses: the model returned no valid hypothesis set; 0 hypotheses formed")
        return UpdateResult()
    return tracker.update(res, origin="formation")
