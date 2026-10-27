"""Shared types for the investigation layer. Every agent module codes against these.

Field names here are the single source of truth for prompts, schemas and consumers: the
OpsIntelligence retrospective lost hypotheses for 11 weeks to schema/prompt/consumer drift
(3.8.2). Change a field here, then change its prompt text and its readers in the same commit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CausalLevel = Literal["symptom", "mechanism", "trigger"]
HypStatus = Literal["open", "confirmed", "refuted", "inconclusive", "exhausted"]
Category = Literal["code_deploy", "config", "resource", "dependency", "traffic", "infrastructure", "data",
                   "external", "other"]
Verdict = Literal["confirmed", "refuted", "inconclusive"]


# ---------------------------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------------------------


class Alert(BaseModel):
    """A normalised alert, whatever platform sent it."""

    model_config = ConfigDict(extra="allow")

    platform: str = "unknown"            # datadog | grafana | prometheus | dynatrace | newrelic | pagerduty | ...
    rule_id: str | None = None           # monitor / rule / policy id on that platform
    rule_name: str | None = None
    title: str = ""
    status: Literal["firing", "resolved", "warning", "nodata", "unknown"] = "firing"
    severity: str | None = None
    service: str | None = None
    env: str | None = None
    host: str | None = None
    namespace: str | None = None
    group: str | None = None             # alert group / instance (e.g. host:web-1, pod=checkout-x)
    query: str | None = None             # the alert's query/expression if shown
    threshold: str | None = None
    value: str | None = None
    triggered_at: datetime | None = None
    window_start: datetime | None = None  # from the notification link, when present
    window_end: datetime | None = None
    url: str | None = None
    links: list[str] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)
    text: str = ""                       # the human-readable body
    raw: dict[str, Any] = Field(default_factory=dict)
    source_channel: str | None = None
    source_ts: str | None = None         # Slack message ts
    parsed_by: str = "deterministic"     # deterministic | llm | manual
    customer_report: bool = False        # a human report ("checkout is broken") rather than a monitor
    # What the parsers extract beyond the core fields; declared so every reader uses one name (3.8.2).
    raw_status: str | None = None        # the platform's own status words (Triggered, Re-Triggered, [FIRING:2])
    retriggered: bool = False
    incident_id: str | None = None       # per-occurrence id: event / issue / incident / problem
    pod: str | None = None
    alert_count: int | None = None       # alerts grouped into this one notification
    summary: str | None = None           # the notification's summary / description annotation
    time_notes: list[str] = Field(default_factory=list)   # why a time in the message was used or ignored
    sender: str | None = None            # the posting integration's identity
    onset: datetime | None = None        # an explicit onset earlier than the trigger, when one is known
    reported_onset: datetime | None = None   # customer reports: the onset the reporter stated
    reported_minutes_ago: int | None = None
    reporter: str | None = None          # customer reports: the Slack user who reported it

    def key(self) -> str:
        """Flap / de-dup key: same rule + group."""
        return f"{self.platform}:{self.rule_id or self.rule_name or self.title}:{self.group or ''}"


# ---------------------------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------------------------


@dataclass
class EvidenceRecord:
    id: str                               # E1, E2 ... (S1.. for shadow agents)
    kind: Literal["alert", "seed", "query", "specialist", "memory", "deepening", "lead"]
    agent: str                            # primary | code_change | ... | seed
    title: str                            # what this evidence is (the purpose of the query)
    summary: str                          # deterministic digest + key rows; what it shows
    sql: str | None = None
    tables: list[str] = field(default_factory=list)
    rendered: str = ""                    # full rendered result (clipped when shown to models)
    row_count: int = 0
    empty_verdict: str | None = None      # absent | inconclusive | None
    error: str | None = None
    links: list[dict[str, str]] = field(default_factory=list)
    window: tuple[str, str] | None = None
    confidence: float = 1.0               # measurement confidence: 1.0 direct, 0.7 inferred, 0.4 weak, 0 error
    meaningful: bool = True               # False for errors, empties without control, discovery-only lookups
    pinned: bool = False
    created: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)   # structured payload (specialist verdicts etc.)

    def line(self, max_chars: int = 600) -> str:
        """One compact line for evidence lists in prompts."""
        flag = "" if self.meaningful else " [not meaningful]"
        if self.error:
            flag = f" [ERROR: {self.error[:120]}]"
        elif self.empty_verdict:
            flag = f" [EMPTY: {self.empty_verdict}]"
        s = " ".join(self.summary.split())
        return f"{self.id} ({self.agent}; {self.title}){flag}: {s[:max_chars]}"


# ---------------------------------------------------------------------------------------------
# Hypotheses (schema-bound; field descriptions double as instructions)
# ---------------------------------------------------------------------------------------------


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Stable id like H1, H2, H1.1 for sub-hypotheses")
    statement: str = Field(description="Specific, falsifiable claim with numbers/entities (service, host, change)")
    causal_level: CausalLevel = Field(description="symptom (what users/monitors see), mechanism (the misbehaving "
                                      "component), trigger (the concrete change, statement, code path, limit or fault)")
    category: Category = Field(default="other", description="Cause family")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1; >=0.6 needs >=2 independent corroborating items")
    supporting: list[str] = Field(default_factory=list, description="Evidence ids (E#) that support it")
    contradicting: list[str] = Field(default_factory=list, description="Evidence ids that contradict it")
    missing: list[str] = Field(default_factory=list, description="Evidence that would settle it, not yet gathered")
    next_query: str | None = Field(default=None, description="The single most discriminating next SQL/check")
    status: HypStatus = "open"
    parent: str | None = None
    verdict_notes: list[str] = Field(default_factory=list)


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis] = Field(description="2-4 competing hypotheses spanning different cause families")


class SubCheck(BaseModel):
    id: str = Field(description="Sub-hypothesis id, e.g. H1.1")
    statement: str = Field(description="One level deeper than the parent, never sideways")
    causal_level: CausalLevel
    sql: str = Field(description="One validating SQL query using only verified query shapes / described tables")
    confirms_if: str = Field(description="Concrete observable result that would confirm it")
    refutes_if: str = Field(description="Concrete observable result that would refute it")


class Decomposition(BaseModel):
    checks: list[SubCheck] = Field(description="2-3 sub-hypotheses, each with one validating query")


class CheckVerdict(BaseModel):
    verdict: Verdict
    rationale: str = Field(description="Quote the numbers that decided it; mention coverage/resolution limits")
    terminal: bool = Field(default=False, description="True if this reaches trigger level (nothing deeper to test)")
    refutes_parent_premise: bool = False
    corrected_sql: str | None = Field(default=None, description="Only for inconclusive: one corrected query "
                                      "(baseline added, all series covered, finer interval, fixed scope)")


# ---------------------------------------------------------------------------------------------
# Specialist verdicts and results
# ---------------------------------------------------------------------------------------------


class SpecialistVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent: str
    verdict: Literal["found", "not_found", "inconclusive", "not_applicable"] = "inconclusive"
    headline: str = Field(description="One sentence: what this specialist concluded, with numbers")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    suggested_hypothesis: str | None = None


class ImpactSummary(BaseModel):
    level: Literal["none", "low", "medium", "high", "critical", "unknown"] = "unknown"
    summary: str = ""
    affected: list[str] = Field(default_factory=list, description="Workloads/services/endpoints/users affected")
    evidence_ids: list[str] = Field(default_factory=list)


class ChangeRef(BaseModel):
    title: str
    ts: str | None = None
    url: str | None = None
    author: str | None = None
    version: str | None = None


class ConclusionCard(BaseModel):
    """The verdict-first card. The LLM fills this object; code renders Slack blocks from it."""

    model_config = ConfigDict(extra="ignore")

    run_id: str = ""
    title: str = Field(description="Short incident title")
    verdict: str = Field(description="One-sentence verdict, calibrated to confidence")
    root_cause: str = Field(default="", description="Trigger-level cause if known, else mechanism (say which)")
    causal_level: CausalLevel = "symptom"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    impact: ImpactSummary = Field(default_factory=ImpactSummary)
    trigger_change: ChangeRef | None = None
    mitigation: list[str] = Field(default_factory=list, description="Supported, safe actions; most important first")
    hypotheses: list[dict[str, Any]] = Field(default_factory=list, description="[{id, statement, confidence, "
                                             "status, evidence_ids}]")
    evidence_links: list[dict[str, str]] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    status: Literal["preliminary", "final", "insufficient_data", "error"] = "final"


@dataclass
class RunStats:
    started: float = field(default_factory=time.time)
    finished: float | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    sql_queries: int = 0
    sql_errors: int = 0
    api_requests: int = 0
    escalations: int = 0
    pushbacks: dict[str, int] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["elapsed_s"] = round((self.finished or time.time()) - self.started, 1)
        return d


@dataclass
class ToolEvent:
    """One tool call as seen by the trace / benchmark trajectory."""

    agent: str
    name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool = False
    evidence_id: str | None = None
    elapsed_ms: int = 0
    ts: float = field(default_factory=time.time)


@dataclass
class RunResult:
    """What an investigation or chat run produced. Slack and the benchmarks render from this."""

    run_id: str
    kind: Literal["investigation", "chat", "health"]
    answer_markdown: str                       # the report (investigation) or the answer (chat)
    card: ConclusionCard | None = None
    evidence: list[EvidenceRecord] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    specialist_verdicts: list[SpecialistVerdict] = field(default_factory=list)
    tool_events: list[ToolEvent] = field(default_factory=list)
    staged_writes: list[dict[str, Any]] = field(default_factory=list)
    stats: RunStats = field(default_factory=RunStats)
    warnings: list[str] = field(default_factory=list)
    status: Literal["ok", "insufficient_data", "timeout", "error", "stopped"] = "ok"
    alert: Alert | None = None
