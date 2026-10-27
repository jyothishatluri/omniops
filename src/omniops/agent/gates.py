"""Readiness and depth gates: code decides when the model may finish (OpsIntelligence 3.6.2, 3.6.3, 7.1).

Lessons encoded:
- 3.6.3 readiness gate: cross-signal coverage for the incident pattern (latency: APM + metrics;
  errors: logs + APM + metrics; saturation: infrastructure + metrics; dependency: APM + logs +
  metrics; availability: monitors + metrics + logs), meaningful evidence exists, hypotheses exist,
  and the top two are more than 0.08 apart. Blockers say "query these tables", naming real tables
  from catalog roles. A signal is only required when the catalog has a source for it, and pushbacks
  are capped, because coverage can be impossible to satisfy.
- 3.6.2 depth gate: while the leader is a symptom or mechanism and budget remains, push one level
  deeper with concrete query shapes (slow traces and child spans, the app's SQL statements against a
  baseline, a dependency's slowest resources, deploys and release diffs). The old gate only fired
  below 10 evidence items, so it never fired: this one keys on the leader's causal level.
- 7.1 "why now?" for capacity failures: Kubernetes termination reasons, config/flag changes,
  co-tenant writes on shared datastores and same-clock-time recurrence are part of the shapes.
- 7.1 "the readiness gate never requires change or code evidence": when the catalog has a change
  source (deploys, config, flags, commits), every pattern requires that it was looked at. An empty
  change list counts as looked-at (change tables rarely have a positive control), an error does not.
- 3.4 evidence floor: fewer than 2 meaningful items (not errors, not inconclusive empties, measured
  at >= 0.3) means a templated insufficient-data answer, never speculation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from omniops.agent.evidence import NOT_MEASURED_KINDS
from omniops.agent.hypotheses import LIVE, MEASURED_MIN, TIE_GAP, board_records
from omniops.agent.types import EvidenceRecord, Hypothesis

if TYPE_CHECKING:
    from omniops.agent.context import RunContext
    from omniops.engine.catalog import Catalog

Pattern = Literal["latency", "errors", "saturation", "dependency", "availability", "unknown"]
Signal = Literal["metrics", "logs", "apm", "infrastructure", "monitors", "changes"]

REQUIRED_SIGNALS: dict[str, tuple[Signal, ...]] = {
    "latency": ("apm", "metrics"),
    "errors": ("logs", "apm", "metrics"),
    "saturation": ("infrastructure", "metrics"),
    "dependency": ("apm", "logs", "metrics"),
    "availability": ("monitors", "metrics", "logs"),
    "unknown": ("metrics", "logs"),
}
ALWAYS_REQUIRED: tuple[Signal, ...] = ("changes",)
SIGNAL_LABELS: dict[str, str] = {"metrics": "metrics", "logs": "logs", "apm": "APM/traces",
                                 "infrastructure": "infrastructure (hosts/Kubernetes)", "monitors": "monitors/alerts",
                                 "changes": "change evidence (deploys, config/flag changes, commits)"}
SIGNAL_ROLES: dict[str, tuple[str, ...]] = {
    "metrics": ("metrics",),
    "logs": ("logs", "log_counts", "k8s_logs"),
    "apm": ("traces", "trace_detail", "service_map"),
    "infrastructure": ("hosts", "k8s_state", "k8s_events", "profiles"),
    "monitors": ("alert_rules", "active_alerts", "alert_history", "slo"),
    "changes": ("change_events", "code_changes"),
}
SIGNAL_KINDS: dict[str, frozenset[str]] = {
    "metrics": frozenset({"timeseries"}), "logs": frozenset({"logs"}), "apm": frozenset({"spans", "traces"}),
    "infrastructure": frozenset({"profiles"}), "monitors": frozenset({"alerts"}),
    "changes": frozenset({"changes"}),
}
_INFRA_CATEGORIES = frozenset({"kubernetes", "k8s", "infrastructure", "hosts", "containers"})
# A metrics query can carry infrastructure or APM signal (node/container metrics, span metrics).
_INFRA_SQL = re.compile(r"node_|container_|kube_|kubelet|machine_|cpu|memory|mem_|disk|filesystem|oom|"
                        r"network_|host\b|\bpod\b", re.IGNORECASE)
_APM_SQL = re.compile(r"spanmetrics|traces_span|span_metrics|http_server_(request_)?duration|"
                      r"request_duration|latency_bucket|duration_(milli)?seconds", re.IGNORECASE)

_PATTERN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "availability": ("down", "unavailable", "unreachable", "outage", "health check", "healthcheck", "uptime",
                     "not ready", "crashloop", "crashloopbackoff", "crash loop", "no data", "nodata",
                     "probe failed", "503", "unhealthy", "target down"),
    "latency": ("latency", "slow", "slowness", "p95", "p99", "p90", "p50", "percentile", "response time",
                "duration", "timeout", "timeouts", "timed out", "apdex"),
    "errors": ("error", "errors", "5xx", "500", "exception", "exceptions", "failure", "failures", "failed",
               "failing", "fault"),
    "saturation": ("cpu", "memory", "oom", "oomkilled", "disk", "saturation", "saturated", "throttling",
                   "throttled", "heap", "gc", "garbage", "thread pool", "pool exhausted", "load average",
                   "utilization", "utilisation", "capacity", "evicted", "eviction", "high load", "leak"),
    "dependency": ("dependency", "upstream", "downstream", "database", "db", "redis", "cache", "kafka", "queue",
                   "connection refused", "connection pool", "third-party", "third party", "external api",
                   "postgres", "mysql", "dns"),
}
# More specific patterns win ties.
_PATTERN_PRIORITY = ("dependency", "saturation", "availability", "latency", "errors")
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?")

DEPTH_MIN_S = 60.0   # seconds beyond the report reserve that a deeper round needs


# ---------------------------------------------------------------------------------------------
# Incident pattern
# ---------------------------------------------------------------------------------------------


def _score(text: str) -> dict[str, int]:
    padded = f" {' '.join(_TOKEN.findall(text.lower()))} "
    return {p: sum(padded.count(f" {k} ") for k in kws) for p, kws in _PATTERN_KEYWORDS.items()}


def _pick(scores: dict[str, int]) -> Pattern | None:
    best = max(scores.values(), default=0)
    if best <= 0:
        return None
    return next(p for p in _PATTERN_PRIORITY if scores[p] == best)  # type: ignore[return-value]


def classify_pattern(text: str, evidence: Iterable[EvidenceRecord] = ()) -> Pattern:
    """latency | errors | saturation | dependency | availability | unknown, from the alert/question text;
    the evidence titles decide only when the text says nothing."""
    picked = _pick(_score(text or ""))
    if picked:
        return picked
    ev_text = " ".join(f"{r.title} {r.summary[:200]}" for r in evidence if not r.error)
    return _pick(_score(ev_text)) or "unknown"


def incident_text(ctx: RunContext) -> str:
    parts: list[str] = []
    if ctx.alert is not None:
        a = ctx.alert
        parts += [a.title, a.rule_name or "", a.query or "", a.text[:1500]]
    if ctx.question:
        parts.append(ctx.question)
    return " ".join(p for p in parts if p)


def incident_pattern(ctx: RunContext) -> Pattern:
    return classify_pattern(incident_text(ctx), board_records(ctx.board))


# ---------------------------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------------------------


def table_signals(catalog: Catalog) -> dict[str, set[str]]:
    """Signals each visible table provides, from its kind, its manifest categories and role bindings."""
    out: dict[str, set[str]] = {}
    for name, entry in catalog.visible().items():
        kind = entry.table.kind
        sig = {s for s, kinds in SIGNAL_KINDS.items() if kind in kinds}
        cats = {c.lower() for c in (entry.manifest.categories if entry.manifest else [])}
        if kind == "timeseries" and cats and "metrics" not in cats:
            # Series derived from a logs or traces backend (LogQL/TraceQL metrics) carry that signal.
            derived = ({"logs"} if "logs" in cats else set()) | ({"apm"} if cats & {"traces", "apm"} else set())
            sig = derived or sig
        if cats & _INFRA_CATEGORIES and kind in {"entities", "events"}:
            sig.add("infrastructure")
        out[name] = sig
    for signal, roles in SIGNAL_ROLES.items():
        for role in roles:
            for _, binding in catalog.role_bindings(role):
                out.setdefault(binding.table, set()).add(signal)
    return out


def available_signals(catalog: Catalog) -> set[str]:
    return {s for sigs in table_signals(catalog).values() for s in sigs}


def record_signals(rec: EvidenceRecord, tsig: dict[str, set[str]]) -> set[str]:
    out: set[str] = set()
    for t in rec.tables:
        out |= tsig.get(t, set())
    if "metrics" in out and rec.sql:
        if _INFRA_SQL.search(rec.sql):
            out.add("infrastructure")
        if _APM_SQL.search(rec.sql):
            out.add("apm")
    return out


def _counts(rec: EvidenceRecord) -> bool:
    """Meaningful evidence: data or real absence; never errors, inconclusive empties or weak readings,
    and never the alert, memory, leads or specialist verdicts (they restate or point at measurements)."""
    if rec.error or rec.kind in NOT_MEASURED_KINDS:
        return False
    if rec.empty_verdict == "absent":
        return True
    return rec.meaningful and rec.confidence >= MEASURED_MIN


def covered_signals(ctx: RunContext) -> set[str]:
    tsig = table_signals(ctx.deps.catalog)
    out: set[str] = set()
    for r in board_records(ctx.board):
        if r.error:
            continue
        sigs = record_signals(r, tsig)
        out |= sigs if _counts(r) else sigs & {"changes"}
    return out


def tables_for_signal(catalog: Catalog, signal: str, limit: int = 3) -> list[str]:
    """Computed cross-source tables first (omni_change_events ...), then role-bound tables (they work with
    default filters), then tables of a matching kind."""
    tsig = table_signals(catalog)
    names = [n for n, e in catalog.visible().items() if e.computed and signal in tsig.get(n, set())]
    for role in SIGNAL_ROLES[signal]:
        names += [b.table for _, b in catalog.role_bindings(role) if b.table not in names]
    names += [n for n, sigs in tsig.items() if signal in sigs and n not in names]
    return names[:limit]


def meaningful_count(ctx: RunContext) -> int:
    return sum(1 for r in board_records(ctx.board) if _counts(r))


def evidence_floor_ok(ctx: RunContext) -> bool:
    return meaningful_count(ctx) >= ctx.deps.settings.evidence_floor


# ---------------------------------------------------------------------------------------------
# Readiness gate
# ---------------------------------------------------------------------------------------------


def readiness_blockers(ctx: RunContext) -> list[str]:
    """What still blocks a final answer, each with a concrete next action. Empty means ready."""
    catalog = ctx.deps.catalog
    pattern = incident_pattern(ctx)
    available = available_signals(catalog)
    covered = covered_signals(ctx)
    blockers: list[str] = []
    for signal in REQUIRED_SIGNALS[pattern] + ALWAYS_REQUIRED:
        if signal in available and signal not in covered:
            tables = tables_for_signal(catalog, signal)
            blockers.append(f"cross-signal coverage for a {pattern} incident is missing {SIGNAL_LABELS[signal]}: "
                            f"query these tables: {', '.join(tables)} (describe_table first; required filters "
                            "apply; compare the incident window with a baseline).")
    n, floor = meaningful_count(ctx), ctx.deps.settings.evidence_floor
    if n < floor:
        blockers.append(f"only {n} meaningful evidence item(s), need {floor}: run data-bearing queries. Errors, "
                        "inconclusive empties and discovery-only lookups (monitor/dashboard/service searches) "
                        "do not count.")
    tracker = ctx.tracker.bind(ctx)
    live = [h for h in tracker.hypotheses.values() if h.status in LIVE or h.status == "exhausted"]
    if not tracker.hypotheses:
        blockers.append("no hypotheses recorded: call record_hypotheses with 2-4 competing, falsifiable "
                        "hypotheses (causal level, cause family, evidence ids, missing evidence, next query).")
    elif not live:
        blockers.append(f"all {len(tracker.hypotheses)} recorded hypotheses are refuted: record new competing "
                        "hypotheses that fit the ruled-out facts, starting from the earliest onset.")
    elif tracker.is_tie():
        pair = tracker.top_two()
        if pair:
            a, b = pair
            blockers.append(
                f"the top two hypotheses are within {TIE_GAP} ({a.id} {a.confidence:.2f} vs {b.id} "
                f"{b.confidence:.2f}): a deferred decision, not an answer. Run discriminating queries in "
                f"parallel: {a.id}: {a.next_query or 'the query that would refute it'}; "
                f"{b.id}: {b.next_query or 'the query that would refute it'}.")
    return blockers


# ---------------------------------------------------------------------------------------------
# Depth gate
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryShape:
    key: str
    roles: tuple[str, ...]          # applicable when any of these roles is bound in the catalog
    text: str
    categories: frozenset[str]      # leader cause families it fits first
    patterns: frozenset[str]        # incident patterns it fits next


QUERY_SHAPES: tuple[QueryShape, ...] = (
    QueryShape("slow_traces", ("traces", "trace_detail"),
               "slowest traces in the window and their child spans: top 20 by duration_ms for the service, then "
               "the full trace of the slowest; > 50 similar child spans in one trace means N+1",
               frozenset({"code_deploy", "dependency", "data", "resource"}),
               frozenset({"latency", "errors", "dependency"})),
    QueryShape("db_statements", ("traces",),
               "the app's own SQL/DB statements ranked by total time (sum of duration_ms by resource for DB "
               "spans), this window vs the same window a day earlier; order by total time, never alphabetically",
               frozenset({"data", "dependency", "code_deploy"}), frozenset({"latency", "saturation", "dependency"})),
    QueryShape("dependency_resources", ("traces", "service_map", "metrics"),
               "the dependency's slowest resources/endpoints: its spans or request metrics grouped by "
               "endpoint, compared with the preceding hour (a nightly job looks the same every day)",
               frozenset({"dependency", "external"}), frozenset({"dependency", "latency"})),
    QueryShape("deploys", ("change_events", "code_changes"),
               "deploys and release diffs: omni_change_events from 48h before to 1h after the onset, then the "
               "code changes between the previous and the new version of the nearest release",
               frozenset({"code_deploy", "config", "data", "other"}),
               frozenset({"latency", "errors", "saturation", "dependency", "availability", "unknown"})),
    QueryShape("k8s_termination", ("k8s_state", "k8s_events"),
               "Kubernetes termination reasons and events: pods with restarts and last terminated reason "
               "(OOMKilled, Error, exit code), Warning events (BackOff, Evicted, Unhealthy, FailedScheduling) "
               "for the namespace around the onset",
               frozenset({"resource", "infrastructure"}), frozenset({"saturation", "availability", "errors"})),
    QueryShape("config_changes", ("change_events", "code_changes", "k8s_state"),
               "config, feature-flag and ConfigMap/Secret changes before the onset (change events of type "
               "config/flag, GitOps sync history, ConfigMap updates)",
               frozenset({"config", "code_deploy"}),
               frozenset({"errors", "availability", "latency", "unknown"})),
    QueryShape("co_tenant_writes", ("metrics", "logs"),
               "co-tenant writes on shared datastores: who else wrote to, deleted from or invalidated the shared "
               "cache/database at the onset minute (commands or writes by client/caller vs the preceding hour)",
               frozenset({"dependency", "resource", "data", "traffic"}),
               frozenset({"saturation", "dependency", "latency"})),
    QueryShape("recurrence", ("metrics", "log_counts"),
               "same-clock-time recurrence: the key metric over the last 7 days at 1h resolution; an onset that "
               "repeats at the same clock time points at a scheduled job, cron or batch",
               frozenset({"resource", "traffic", "dependency"}), frozenset({"saturation", "latency", "errors"})),
    QueryShape("inbound_mix", ("metrics", "traces", "logs"),
               "inbound mix by caller/route this window vs the preceding hour: a surge from one caller is traffic; "
               "flat throughput with rising latency is starvation",
               frozenset({"traffic"}), frozenset({"latency", "saturation"})),
)


def depth_query_shapes(ctx: RunContext, leader: Hypothesis, limit: int = 4) -> list[str]:
    """Query shapes that can run on this catalog, ordered by fit to the leader's family and the pattern."""
    catalog = ctx.deps.catalog
    bound = set(catalog.roles_summary())
    pattern = incident_pattern(ctx)
    usable = [s for s in QUERY_SHAPES if bound & set(s.roles)]
    usable.sort(key=lambda s: (leader.category not in s.categories, pattern not in s.patterns))
    out = []
    for s in usable[:limit]:
        tables: list[str] = []
        for role in s.roles:
            tables += [b.table for _, b in catalog.role_bindings(role) if b.table not in tables]
        if s.key in {"deploys", "config_changes"} and catalog.get("omni_change_events") is not None:
            tables.insert(0, "omni_change_events")
        out.append(f"{s.text} (tables: {', '.join(dict.fromkeys(tables[:4]))})")
    return out


def depth_pushback(ctx: RunContext) -> str | None:
    """A push one causal level deeper while the leader is a symptom/mechanism and budget remains."""
    leader = ctx.tracker.bind(ctx).leader()
    if leader is None or leader.causal_level == "trigger":
        return None
    if ctx.remaining() < ctx.deps.settings.report_reserve_s + DEPTH_MIN_S:
        return None
    shapes = depth_query_shapes(ctx, leader)
    lines = [
        (f"DEPTH GATE: the leading hypothesis {leader.id} is at {leader.causal_level} level "
         f"(\"{leader.statement[:200]}\"). That says what and where, not why. Before finishing, go one level "
         "deeper to the trigger: the concrete change, statement, code path, limit or fault, with numbers at each "
         "hop of the propagation chain and the specific hosts/pods involved. Ask 'why now?': who or what acted at "
         "the onset minute."),
    ]
    if shapes:
        lines.append("Run these in parallel (describe the tables first; use only real columns):")
        lines += [f"- {s}" for s in shapes]
    else:
        lines.append("No catalog source fits the usual drill-downs; search_catalog for change, trace or "
                     "Kubernetes tables before concluding.")
    lines.append("Then record_hypotheses with the deeper hypothesis (causal_level trigger), or record which "
                 "evidence ruled each trigger candidate out. If none can be found, say 'mechanism (trigger not "
                 "found)' and name what is missing.")
    return "\n".join(lines)


def gate_pushback(ctx: RunContext) -> str | None:
    """The before_final hook body: readiness first, then depth, each capped by settings (3 / 2)."""
    settings = ctx.deps.settings
    pushbacks = ctx.stats.pushbacks
    blockers = readiness_blockers(ctx)
    if blockers:
        n = pushbacks.get("readiness", 0)
        if n < settings.readiness_pushbacks:
            pushbacks["readiness"] = n + 1
            return (f"READINESS GATE ({n + 1}/{settings.readiness_pushbacks}): you cannot finish yet.\n"
                    + "\n".join(f"- {b}" for b in blockers)
                    + "\nMake the tool calls now, in parallel; do not answer in text.")
        if not ctx.extra.get("readiness_cap_warned"):
            ctx.extra["readiness_cap_warned"] = True
            ctx.warn(f"readiness gate: finishing with {len(blockers)} unresolved blocker(s) after "
                     f"{n} pushbacks: {' | '.join(blockers)[:600]}")
    depth = depth_pushback(ctx)
    if depth:
        n = pushbacks.get("depth", 0)
        if n < settings.depth_pushbacks:
            pushbacks["depth"] = n + 1
            return depth
    return None
