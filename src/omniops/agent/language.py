"""Calibrated wording tied to hypothesis confidence, and a checker that downgrades overclaims.

Retrospective lessons encoded here:
- 3.7: four wording tiers (>=0.7 "The root cause is", 0.5-0.7 "The most likely contributing factor",
  0.3-0.5 "A possible factor (unconfirmed)", below 0.3 not shown); a low-confidence banner below 0.4;
  certainty words are not allowed where the evidence does not carry them.
- 7.1: a mechanism-level leader is labelled "mechanism (trigger not found)" instead of passing for a
  root cause.
- 3.15: the report and the card use the same wording function, so they cannot disagree on certainty.

Code enforces the wording after generation (code measures, the LLM judges): the prompt asks for the
right phrase, and :func:`calibrate` rewrites any phrase that claims more than the tracker's confidence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

ROOT_CAUSE_MIN = 0.7
LIKELY_MIN = 0.5
POSSIBLE_MIN = 0.3
LOW_CONFIDENCE_BANNER_BELOW = 0.4

Tier = Literal["root_cause", "likely", "possible", "omit"]

TIER_PHRASES: dict[Tier, str] = {
    "root_cause": "The root cause",
    "likely": "The most likely contributing factor",
    "possible": "A possible factor (unconfirmed)",
    "omit": "The cause is not established; an unconfirmed lead",
}

# The same tiers as a predicate ("X is <tail>")
TIER_TAILS: dict[Tier, str] = {
    "root_cause": "the root cause",
    "likely": "the most likely contributing factor",
    "possible": "a possible factor (unconfirmed)",
    "omit": "an unconfirmed lead (the cause is not established)",
}

LEVEL_LABELS = {
    "trigger": "trigger (the concrete change or fault)",
    "mechanism": "mechanism (trigger not found)",
    "symptom": "symptom only (cause not found)",
}

# Spans that must never be rewritten: code fences, inline code, Slack links/mentions.
_PROTECTED = re.compile(r"```.*?```|`[^`\n]*`|<[^<>\n]+>", re.DOTALL)

_QUALIFIER = r"(?:(?:most\s+)?(?:likely|probable|possible|potential|confirmed|definitive|actual|true|real)\s+)?"
# "is not established", "is unknown", "remains unclear": a sentence that denies a cause is not a claim.
_NEGATED = (r"(?!\s+(?:not\b|never\b|unknown|unclear|undetermined|uncertain|unconfirmed|unidentified|unresolved"
            r"|inconclusive|undecided|yet\s+to\s+be|still\s+(?:not|unknown|unclear|undetermined|uncertain|under)"
            r"|under\s+investigation))")
# "The (likely) root cause (of the outage) is ..." - the whole opener is replaced by the tier phrase
_ROOT_CAUSE_CLAIM = re.compile(
    r"\b(?:(?:the|a|one)\s+)?" + _QUALIFIER + r"root\s+cause(?:\s+of\s+(?:the|this)\s+[\w-]+)?\s+"
    r"(?:is|was|has\s+been\s+identified\s+as)\b" + _NEGATED,
    re.IGNORECASE,
)
# "... is the root cause" - subject-first form
_ROOT_CAUSE_TAIL = re.compile(r"\b(is|was)\s+(?:the|a|one)\s+" + _QUALIFIER + r"root\s+cause\b", re.IGNORECASE)
_CERTAINTY = re.compile(
    r"\b(?:definitively|conclusively|undoubtedly|unquestionably|certainly|definitely|"
    r"without\s+(?:a|any)\s+doubt|beyond\s+(?:any\s+)?doubt)\b",
    re.IGNORECASE,
)
# "cannot definitely say", "not certainly": a negated certainty word already hedges.
_NEGATION_BEFORE = re.compile(r"(?:\b(?:not|never|no|cannot)|n[\'\u2019]t)\s*$", re.IGNORECASE)
_PROOF = re.compile(r"\b(this|which|it|that)\s+(?:proves|confirms)\s+(?:that\s+)?", re.IGNORECASE)


def tier(confidence: float) -> Tier:
    """Wording tier for a hypothesis confidence (3.7)."""
    if confidence >= ROOT_CAUSE_MIN:
        return "root_cause"
    if confidence >= LIKELY_MIN:
        return "likely"
    if confidence >= POSSIBLE_MIN:
        return "possible"
    return "omit"


def lead_phrase(confidence: float) -> str | None:
    """The sentence opener allowed at this confidence, e.g. "The root cause is"; None below 0.3."""
    t = tier(confidence)
    return None if t == "omit" else f"{TIER_PHRASES[t]} is"


def shown(confidence: float, status: str = "open") -> bool:
    """Whether a hypothesis may appear in user-facing text: refuted and sub-0.3 ones are omitted."""
    return status != "refuted" and confidence >= POSSIBLE_MIN


def needs_banner(top_confidence: float | None) -> bool:
    return top_confidence is not None and top_confidence < LOW_CONFIDENCE_BANNER_BELOW


def confidence_word(confidence: float) -> str:
    if confidence >= ROOT_CAUSE_MIN:
        return "high"
    if confidence >= LIKELY_MIN:
        return "medium"
    if confidence >= POSSIBLE_MIN:
        return "low"
    return "very low"


def level_label(level: str) -> str:
    return LEVEL_LABELS.get(level, level)


def calibrated_sentence(statement: str, confidence: float) -> str:
    """"<tier phrase> <statement>" with the statement's first letter lowered when it starts a word."""
    s = statement.strip().rstrip(".")
    if s[:1].isupper() and not s[1:2].isupper():
        s = s[0].lower() + s[1:]
    return f"{lead_phrase(confidence) or TIER_PHRASES['omit'] + ' is'} {s}."


def map_prose(text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to the text outside code fences, inline code and Slack ``<...>`` links."""
    out: list[str] = []
    pos = 0
    for m in _PROTECTED.finditer(text):
        out.append(fn(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(text[pos:]))
    return "".join(out)


@dataclass
class Calibration:
    text: str
    changes: list[str] = field(default_factory=list)


def _match_case(original: str, replacement: str) -> str:
    return replacement[:1].upper() + replacement[1:] if original[:1].isupper() else replacement[:1].lower() + \
        replacement[1:]


def calibrate(text: str, confidence: float | None) -> Calibration:
    """Downgrade phrases that claim more certainty than ``confidence`` supports.

    ``confidence`` is the leading hypothesis' tracker confidence; None (no hypotheses, e.g. a plain
    data question) leaves the text untouched. At >= 0.7 every phrase is allowed.
    """
    if confidence is None or confidence >= ROOT_CAUSE_MIN:
        return Calibration(text)
    changes: list[str] = []
    t = tier(confidence)

    def claim(m: re.Match[str]) -> str:
        new = _match_case(m.group(0), f"{TIER_PHRASES[t]} is")
        changes.append(f"'{m.group(0)}' -> '{new}' (confidence {confidence:.2f})")
        return new

    def tail(m: re.Match[str]) -> str:
        new = f"{m.group(1)} {TIER_TAILS[t]}"
        changes.append(f"'{m.group(0)}' -> '{new}' (confidence {confidence:.2f})")
        return new

    def certainty(m: re.Match[str]) -> str:
        if _NEGATION_BEFORE.search(m.string[:m.start()]):
            return m.group(0)
        new = _match_case(m.group(0), "likely")
        changes.append(f"'{m.group(0)}' -> '{new}' (confidence {confidence:.2f})")
        return new

    def proof(m: re.Match[str]) -> str:
        new = f"{m.group(1)} suggests that "
        changes.append(f"'{m.group(0).strip()}' -> '{new.strip()}' (confidence {confidence:.2f})")
        return new

    def fix(segment: str) -> str:
        segment = _ROOT_CAUSE_CLAIM.sub(claim, segment)
        segment = _ROOT_CAUSE_TAIL.sub(tail, segment)
        segment = _CERTAINTY.sub(certainty, segment)
        return _PROOF.sub(proof, segment)

    return Calibration(map_prose(text, fix), changes)
