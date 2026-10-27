"""Recursive deepening with a separate verdict judge (OpsIntelligence 3.6.4, 3.6.5, 3.10.3, 7.1, Part 5).

Each round decomposes the focus hypothesis into 2-3 sub-hypotheses one causal level deeper, runs one
validating SQL query per check through the run's engine (with a duplicate guard), and has a separate
verdict model judge the *claim* against its confirm/refute criteria.

Lessons encoded:
- 1 Oct 2026 fix: measurement confidence is not a verdict (503 of 582 sub-hypotheses "validated", none
  refuted). Code makes errors and empties without a positive control inconclusive, and a missing or
  invalid judgement falls back to inconclusive: nothing is confirmed by default.
- 3.6.5: an empty result whose positive control returned data elsewhere is real absence, and reaches
  the judge as such (it can confirm, e.g. "pool timeouts absent before the deploy").
- Content API: the judge sees the parent's and ancestors' established facts and the checks already
  judged; a refutation drawn from a truncated/partial result is downgraded to inconclusive by code
  (k of N cannot refute a claim about all N). Results are re-rendered for the judge so every
  truncation is explicit, never silently clipped.
- One corrected re-check, judged with both results visible. Empties, errors and downgraded
  refutations get one too, through a correction-only judge call (closes the 7.1 gap "empty results
  get no corrected retry").
- the high-memory case: a confident refutation of a parent's premise caps the parent; the first time a
  top-level hypothesis falls, hypotheses are re-formed once from the earliest onset, and re-formed
  hypotheses get one decomposition even below the usual confidence floor.
- the profile-lookup outage: a leader that used up its attempt without confirmation is exhausted and deepening moves on,
  so the real trigger gets tested while rounds remain.
- 3.10.3: decompositions may only use tables already described or queried in this run (their
  verified query shapes are shown); other tables are rejected by code.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from sqlglot import exp

from omniops.agent.hypotheses import (
    LEVEL_RANK,
    alert_brief,
    board_add,
    board_lookup,
    board_records,
    earliest_onset,
    evidence_lines,
    load_prompt,
    measured_changes,
    statement_key,
)
from omniops.agent.sanitize import sanitize_sql
from omniops.agent.types import (
    CheckVerdict,
    Decomposition,
    EvidenceRecord,
    Hypothesis,
    HypothesisSet,
    SubCheck,
    ToolEvent,
)
from omniops.engine import pushdown

if TYPE_CHECKING:
    from omniops.agent.context import RunContext

MAX_CHECKS = 3
DECOMPOSE_FLOOR = 0.3          # usual confidence floor for decomposing a hypothesis
ROUND_MIN_S = 30.0             # seconds beyond the report reserve that a round needs
JUDGE_RESULT_CHARS = 12_000    # per result shown to the judge; re-rendered so truncation stays explicit
DESCRIBE_CHARS = 2500
MAX_DESCRIBED = 8
# Engine markers of partial coverage: thinned rows, truncated source data, failed fan-out requests,
# capped fan-out, clamped windows. A refutation drawn from such a result is downgraded (k of N).
_PARTIAL_MARKERS = ("[truncated] showing", "source data truncated", "result has at least", "partial:",
                    "capped at", "clamped")
_PARTIAL_REASON = ("the result has partial coverage (truncated, capped or clamped), and k of N series cannot "
                   "refute a claim about all N")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


@dataclass
class CheckOutcome:
    hypothesis_id: str
    parent_id: str
    statement: str
    sql: str
    evidence_ids: list[str]
    verdict: CheckVerdict
    decided_by: Literal["judge", "code"]
    rechecked: bool = False


@dataclass
class DeepeningReport:
    rounds: int = 0
    outcomes: list[CheckOutcome] = field(default_factory=list)
    focus_path: list[str] = field(default_factory=list)
    exhausted: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    reformed: bool = False
    reformed_ids: list[str] = field(default_factory=list)
    reached_trigger: str | None = None
    stopped: str = ""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Compact lines for the report and card prompts."""
        lines = [f"DEEPENING: {self.rounds} round(s); focus {' -> '.join(self.focus_path) or 'none'}; "
                 f"stopped: {self.stopped or 'n/a'}"
                 + (f"; trigger reached at {self.reached_trigger}" if self.reached_trigger else "")]
        for o in self.outcomes:
            lines.append(f"- {o.hypothesis_id} [{o.verdict.verdict}{', re-checked' if o.rechecked else ''}] "
                         f"{o.statement[:160]} ({', '.join(o.evidence_ids) or 'no evidence'}): "
                         f"{' '.join(o.verdict.rationale.split())[:240]}")
        if self.demoted:
            lines.append(f"demoted (premise refuted): {', '.join(self.demoted)}")
        if self.exhausted:
            lines.append(f"exhausted without confirmation: {', '.join(self.exhausted)}")
        if self.reformed:
            lines.append(f"hypotheses re-formed once: {', '.join(self.reformed_ids) or 'no new hypotheses'}")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)


def _norm_sql(sql: str) -> str:
    return " ".join(_COMMENTS.sub(" ", sql).lower().split()).rstrip(";").strip()


def _sql_tables(sql: str) -> set[str]:
    tree = pushdown.parse(sql)
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table)
            if t.name and t.name not in ctes and not isinstance(t.this, (exp.Anonymous, exp.Func))}


def _llm_timeout(ctx: RunContext) -> float:
    return max(15.0, min(240.0, ctx.remaining() - ctx.deps.settings.report_reserve_s))


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 60] + f"\n[clipped {len(text) - n + 60} chars]"


# ---------------------------------------------------------------------------------------------
# Context for the deepening and verdict models
# ---------------------------------------------------------------------------------------------


def _allowed_tables(ctx: RunContext, focus: Hypothesis) -> dict[str, str]:
    """Tables described or queried in this run, with their describe text (columns + verified shapes).
    The focus's own evidence tables come first, so they always get a full description."""
    catalog = ctx.engine.catalog
    names: list[str] = []
    for eid in focus.supporting + focus.contradicting:
        rec = board_lookup(ctx.board, eid)
        if rec is not None and not rec.error:
            names += [t for t in rec.tables if t not in names]
    for ev in ctx.tool_events:
        if ev.name == "describe_table" and not ev.is_error:
            t = ev.arguments.get("table") or ev.arguments.get("name")
            if isinstance(t, str) and t not in names:
                names.append(t)
    for r in board_records(ctx.board):
        if not r.error:
            names += [t for t in r.tables if t not in names]
    if not names:   # nothing described yet: the role-bound tables seeds would have used
        for role in ("metrics", "logs", "traces", "k8s_state", "k8s_events", "change_events"):
            names += [b.table for _, b in catalog.role_bindings(role) if b.table not in names]
    out: dict[str, str] = {}
    for name in names:
        if catalog.get(name) is not None:
            full = len(out) < MAX_DESCRIBED
            out[name] = _clip(catalog.describe(name), DESCRIBE_CHARS) if full else f"TABLE {name}"
    return out


def _evidence_block(ctx: RunContext, ids: list[str], width: int = 500) -> list[str]:
    out = []
    for eid in ids:
        rec = board_lookup(ctx.board, eid)
        if rec is not None:
            out.append(f"- {rec.line(width)}")
    return out


def _established_facts(ctx: RunContext, hid: str) -> list[str]:
    """Evidence cited by the hypothesis's ancestors (parent first): facts one coarse query cannot overturn."""
    ids: list[str] = []
    for anc in ctx.tracker.ancestors(hid):
        ids += [e for e in anc.supporting if e not in ids]
    return _evidence_block(ctx, ids)


def _judged_checks(ctx: RunContext, exclude: str = "") -> list[str]:
    tr = ctx.tracker
    out = []
    for hid in tr.checks:
        h = tr.get(hid)
        if h is None or hid == exclude or not tr.is_judged(hid) or h.status not in {"confirmed", "refuted",
                                                                                     "inconclusive"}:
            continue
        why = h.verdict_notes[-1] if h.verdict_notes else ""
        out.append(f"- {hid} [{h.status}] {h.statement[:200]} :: {' '.join(why.split())[:300]}")
    return out


def _chain(ctx: RunContext, h: Hypothesis) -> list[str]:
    return [f"- {a.id} [{a.causal_level} | {a.category} | {a.confidence:.2f} | {a.status}] {a.statement}"
            for a in ctx.tracker.ancestors(h.id)]


def _decompose_message(ctx: RunContext, focus: Hypothesis, allowed: dict[str, str], error: str = "") -> str:
    records = board_records(ctx.board)
    proven = [r for r in records if r.sql and not r.error and r.row_count > 0 and set(r.tables) & set(allowed)]
    priors = [r for r in records if r.kind == "memory" or r.agent == "prior_incidents"]
    blocks = [
        alert_brief(ctx),
        (f"FOCUS HYPOTHESIS: {focus.id} [{focus.causal_level} | {focus.category} | {focus.confidence:.2f} | "
         f"{focus.status}] {focus.statement}"),
        "FOCUS EVIDENCE:", *(_evidence_block(ctx, focus.supporting + focus.contradicting) or ["- none cited"]),
        "ANCESTORS (parent first):", *(_chain(ctx, focus) or ["- none (top-level hypothesis)"]),
        "ESTABLISHED FACTS (do not re-test):", *(_established_facts(ctx, focus.id) or ["- none"]),
        "ALREADY JUDGED CHECKS (do not repeat):", *(_judged_checks(ctx) or ["- none"]),
        "RULED OUT (negative facts):", *([f"- {x}" for x in ctx.tracker.negative_facts()] or ["- none"]),
    ]
    if priors:
        blocks += ["PRIOR INCIDENT LEADS (test whether the prior trigger class fired before this onset):",
                   *[f"- {r.line(400)}" for r in priors[:5]]]
    if proven:
        blocks += ["SQL THAT RETURNED DATA IN THIS RUN (proven shapes):",
                   *[f"- {' '.join(r.sql.split())[:400]}" for r in proven[-10:] if r.sql]]
    blocks += ["TABLES YOU MAY USE (you cannot inspect any other schema here):", *allowed.values()]
    blocks.append(f"Return 2-{MAX_CHECKS} checks for {focus.id} with ids {focus.id}.1, {focus.id}.2 ...")
    if error:
        blocks.append(f"PREVIOUS ATTEMPT REJECTED: {error}. Fix it.")
    return "\n".join(blocks)


def _result_text(rec: EvidenceRecord) -> str:
    head = f"RESULT {rec.id} (query: {' '.join((rec.sql or '').split())[:600]})"
    if rec.error:
        lesson = rec.data.get("lesson")
        return f"{head}\nERROR: {rec.error}" + (f"\nLESSON: {lesson}" if lesson else "")
    if rec.row_count == 0 and rec.empty_verdict == "absent":
        head += ("\nPOSITIVE CONTROL: the same query returned data in another window, so this 0-row result is "
                 "REAL ABSENCE (absent), not a broken filter. Judge it as a measurement of absence.")
    elif rec.row_count == 0:
        head += "\nEMPTY WITHOUT POSITIVE CONTROL: 0 rows here proves nothing; the filter may be wrong."
    view = rec.data.get("judge_view") or _clip(rec.rendered or rec.summary, JUDGE_RESULT_CHARS)
    return f"{head}\n{view}"


def _judge_message(ctx: RunContext, child: Hypothesis, chk: SubCheck, recs: list[EvidenceRecord],
                   prior: CheckVerdict | None, correction_only: str | None) -> str:
    blocks = [
        alert_brief(ctx),
        f"CLAIM UNDER TEST: {child.id} [{child.causal_level}] {child.statement}",
        f"CONFIRMS IF: {chk.confirms_if}",
        f"REFUTES IF: {chk.refutes_if}",
        "PARENT CHAIN (parent first):", *(_chain(ctx, child) or ["- none"]),
        "ESTABLISHED FACTS (the parent's and ancestors' cited evidence):",
        *(_established_facts(ctx, child.id) or ["- none"]),
        "ALREADY JUDGED CHECKS:", *(_judged_checks(ctx, exclude=child.id) or ["- none"]),
    ]
    if prior is not None and len(recs) > 1:
        blocks += ["ORIGINAL " + _result_text(recs[0]),
                   f"FIRST VERDICT: {prior.verdict}: {prior.rationale}",
                   "CORRECTED " + _result_text(recs[-1]),
                   "Judge the claim with BOTH results visible. This is the only re-check: corrected_sql must be null."]
    else:
        blocks.append(_result_text(recs[-1]))
    if correction_only:
        blocks.append(f"MODE: CORRECTION ONLY. Code has already recorded this check as INCONCLUSIVE "
                      f"({correction_only}). Your verdict will be recorded as inconclusive whatever you answer. "
                      "Propose corrected_sql only if one specific fix is likely to decide the claim (verify the "
                      "label/tag value exists, use a window known to have data, fix the scope or table, or "
                      "aggregate per member so every pod/host/series fits in the result); otherwise null.")
    return "\n".join(blocks)


# ---------------------------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------------------------


async def _execute(ctx: RunContext, title: str, sql: str, seen: dict[str, str], report: DeepeningReport
                   ) -> EvidenceRecord:
    """Run one check query (or reuse the identical query's evidence) and publish it to the board."""
    from omniops.agent.evidence import record_error, record_from_result

    key = _norm_sql(sql)
    if key in seen:
        prev = board_lookup(ctx.board, seen[key])
        if prev is not None:
            report.notes.append(f"duplicate guard: {title.split(':')[0]} reuses {prev.id} (identical query)")
            return prev
    t0 = time.monotonic()
    try:
        res = await ctx.engine.query(sql, purpose=title)
        rec = record_from_result(res, agent="deepening", purpose=title, kind="deepening")
        rec.data["judge_view"] = res.render(budget=JUDGE_RESULT_CHARS)
    except Exception as exc:  # noqa: BLE001 - any failure is an error record, judged inconclusive by code
        rec = record_error(exc, agent="deepening", purpose=title, sql=sql, kind="deepening")
        ctx.stats.sql_errors += 1
    ctx.stats.sql_queries += 1
    eid = board_add(ctx.board, rec)
    rec.id = rec.id or eid
    seen[key] = eid
    ctx.tool_events.append(ToolEvent(agent="deepening", name="run_sql", arguments={"sql": sql, "purpose": title},
                                     output=_clip(rec.rendered or rec.summary, 4000), is_error=bool(rec.error),
                                     evidence_id=eid, elapsed_ms=int((time.monotonic() - t0) * 1000)))
    if rec.error:
        ctx.warn(f"deepening: {title.split(':')[0]} query failed ({rec.error[:200]})")
    elif rec.row_count == 0 and rec.empty_verdict != "absent":
        ctx.warn(f"deepening: {title.split(':')[0]} returned 0 rows (inconclusive without a positive control)")
    return rec


def _deterministic_reason(rec: EvidenceRecord) -> str | None:
    if rec.error:
        return f"the query failed ({rec.error[:200]}); an error is not evidence either way"
    if rec.row_count == 0 and rec.empty_verdict != "absent":
        return "0 rows without a positive control; an empty result is inconclusive, not proof of absence"
    return None


def _partial(rec: EvidenceRecord) -> bool:
    text = f"{rec.summary}\n{rec.data.get('judge_view') or rec.rendered}".lower()
    return any(m in text for m in _PARTIAL_MARKERS)


def _guard(v: CheckVerdict, recs: list[EvidenceRecord], recheck_done: bool) -> tuple[CheckVerdict, bool]:
    """Code-side rules the judge cannot override. Returns the verdict and whether a refutation was
    downgraded for partial coverage (Content API: refuted from a digest of 6 of ~15 pods)."""
    upd: dict[str, Any] = {}
    if v.verdict != "inconclusive" or recheck_done:
        upd["corrected_sql"] = None
    if v.verdict != "confirmed":
        upd["terminal"] = False
    if v.verdict != "refuted":
        upd["refutes_parent_premise"] = False
    downgraded = v.verdict == "refuted" and _partial(recs[-1])
    if downgraded:
        upd.update(verdict="inconclusive", refutes_parent_premise=False,
                   rationale=f"{v.rationale} [code: refutation downgraded - {_PARTIAL_REASON}]")
    return (v.model_copy(update=upd) if upd else v), downgraded


async def _ask_judge(ctx: RunContext, child: Hypothesis, chk: SubCheck, recs: list[EvidenceRecord],
                     prior: CheckVerdict | None, correction_only: str | None = None) -> CheckVerdict | None:
    return await ctx.deps.llm.structured(
        role="verdict", system=load_prompt("verdict"),
        user=_judge_message(ctx, child, chk, recs, prior, correction_only),
        schema=CheckVerdict, timeout_s=_llm_timeout(ctx))


async def _verdict(ctx: RunContext, child: Hypothesis, chk: SubCheck, recs: list[EvidenceRecord],
                   prior: CheckVerdict | None) -> tuple[CheckVerdict, Literal["judge", "code"]]:
    """Judge the latest result. Errors and empties without a positive control are inconclusive by code;
    the judge then only proposes a correction (7.1: empty results deserve one corrected retry too)."""
    reason = _deterministic_reason(recs[-1])
    rationale = reason or ""
    if reason is None:
        judged = await _ask_judge(ctx, child, chk, recs, prior)
        if judged is None:
            ctx.warn(f"deepening: verdict judge returned no valid verdict for {child.id}; recorded inconclusive")
            return CheckVerdict(verdict="inconclusive", rationale="the verdict judge returned no valid verdict; "
                                                                  "nothing is confirmed by default"), "code"
        judged, downgraded = _guard(judged, recs, recheck_done=prior is not None)
        if not downgraded:
            return judged, "judge"
        reason, rationale = _PARTIAL_REASON, judged.rationale
    corrected = None
    if prior is None:   # one correction proposal; its result is judged with both results visible
        fix = await _ask_judge(ctx, child, chk, recs, None, correction_only=reason)
        corrected = fix.corrected_sql if fix else None
    return CheckVerdict(verdict="inconclusive", rationale=rationale, corrected_sql=corrected), "code"


async def _judge_check(ctx: RunContext, child: Hypothesis, chk: SubCheck, first: EvidenceRecord,
                       allowed: dict[str, str], seen: dict[str, str], report: DeepeningReport) -> CheckOutcome:
    recs = [first]
    verdict, by = await _verdict(ctx, child, chk, recs, None)
    rechecked = False
    fix = verdict.corrected_sql
    if verdict.verdict == "inconclusive" and fix:
        fix, fixes, problem = _sanitize(ctx, fix)
        if fixes:
            report.notes.append(f"{child.id} re-check: sanitizer fixed {'; '.join(fixes)}")
        problem = problem or _table_problem(ctx, fix, allowed)
        if problem:
            report.notes.append(f"{child.id}: corrected query rejected ({problem})")
        elif _norm_sql(fix) == _norm_sql(chk.sql):
            report.notes.append(f"{child.id}: corrected query is identical to the original; not re-run")
        elif ctx.remaining() > ctx.deps.settings.report_reserve_s + ROUND_MIN_S / 2:
            second = await _execute(ctx, f"{child.id} re-check: {child.statement[:160]}", fix, seen, report)
            recs.append(second)
            verdict, by = await _verdict(ctx, child, chk, recs, verdict)
            rechecked = True
    if verdict.corrected_sql:
        verdict = verdict.model_copy(update={"corrected_sql": None})
    return CheckOutcome(child.id, child.parent or "", child.statement, chk.sql,
                        list(dict.fromkeys(r.id for r in recs)), verdict, by, rechecked)


# ---------------------------------------------------------------------------------------------
# Decomposition and re-formation
# ---------------------------------------------------------------------------------------------


def _sanitize(ctx: RunContext, sql: str) -> tuple[str, list[str], str | None]:
    """The run_sql sanitizer for check SQL (3.10.1): (sanitized SQL, fixes, problem). Decompositions and
    corrected re-checks make the same "==" / double-quoted literal / smart-quote / None mistakes."""
    known = {c for cols in ctx.engine.catalog.columns_map().values() for c in cols}
    try:
        fixed, fixes = sanitize_sql(sql, known)
    except ValueError as exc:
        return sql, [], f"SQL rejected: {' '.join(str(exc).split())[:200]}"
    return fixed, fixes, None


def _table_problem(ctx: RunContext, sql: str, allowed: dict[str, str]) -> str | None:
    """Checks read only tables described in this run (3.10.3), and never write tables."""
    try:
        tables = _sql_tables(sql)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a validation error fed back to the model
        return f"SQL does not parse: {' '.join(str(exc).split())[:200]}"
    if not tables:
        return "SQL reads no table"
    unknown = sorted(tables - set(allowed))
    if unknown:
        return f"uses tables not described in this run: {', '.join(unknown)} (allowed: {', '.join(allowed)})"
    writes = sorted(t for t in tables if (e := ctx.engine.catalog.get(t)) is not None and e.table.kind == "write")
    if writes:
        return f"write tables are not allowed in checks: {', '.join(writes)}"
    return None


def _validate_checks(ctx: RunContext, checks: list[SubCheck], allowed: dict[str, str],
                     report: DeepeningReport | None = None) -> tuple[list[SubCheck], list[str]]:
    judged_sql = {_norm_sql(c.sql) for hid, c in ctx.tracker.checks.items() if ctx.tracker.is_judged(hid)}
    ok: list[SubCheck] = []
    problems: list[str] = []
    batch: set[str] = set()
    for chk in checks:
        label = chk.id or "check"
        sql, fixes, problem = _sanitize(ctx, chk.sql)
        if fixes:
            chk = chk.model_copy(update={"sql": sql})
            if report is not None:
                report.notes.append(f"{label}: sanitizer fixed {'; '.join(fixes)}")
        problem = problem or _table_problem(ctx, chk.sql, allowed)
        key = _norm_sql(chk.sql)
        if problem is None and key in judged_sql:
            problem = "repeats an already-judged check's query"
        if problem is None and key in batch:
            problem = "duplicates another check in this decomposition"
        if problem:
            problems.append(f"{label}: {problem}")
            continue
        batch.add(key)
        ok.append(chk)       # the tracker keeps each check at the focus's level or deeper
    return ok[:MAX_CHECKS], problems


async def _decompose(ctx: RunContext, focus: Hypothesis, allowed: dict[str, str], report: DeepeningReport
                     ) -> list[SubCheck]:
    """2-3 validated sub-checks for the focus; one retry with the rejection reasons."""
    if not allowed:
        ctx.warn(f"deepening: no described tables to decompose {focus.id} with")
        return []
    error = ""
    for _ in range(2):
        res = await ctx.deps.llm.structured(role="deepening", system=load_prompt("deepening"),
                                            user=_decompose_message(ctx, focus, allowed, error),
                                            schema=Decomposition, timeout_s=_llm_timeout(ctx))
        if res is None:
            ctx.warn(f"deepening: decomposition of {focus.id} returned no valid checks")
            return []
        checks, problems = _validate_checks(ctx, list(res.checks), allowed, report)
        if problems:
            report.notes.append(f"{focus.id} decomposition: " + "; ".join(problems)[:400])
        if checks:
            return checks
        error = "; ".join(problems) or "no checks returned"
    ctx.warn(f"deepening: decomposition of {focus.id} produced 0 usable checks ({error[:300]})")
    return []


async def _reform(ctx: RunContext, fallen_id: str, report: DeepeningReport) -> list[str]:
    """Re-form hypotheses once after the first top-level hypothesis falls."""
    tracker = ctx.tracker
    tracker.reformed = True
    report.reformed = True
    fallen = tracker.get(fallen_id)
    records = board_records(ctx.board)
    onset = earliest_onset(records)
    refuting = [f"- {o.hypothesis_id} [{o.verdict.verdict}] {o.statement[:200]} ({', '.join(o.evidence_ids)}): "
                f"{' '.join(o.verdict.rationale.split())[:300]}"
                for o in report.outcomes if tracker.root_of(o.hypothesis_id) == fallen_id]
    blocks = [
        alert_brief(ctx),
        f"WHAT WAS REFUTED: top-level hypothesis {fallen_id}: {fallen.statement if fallen else '?'}",
        *refuting,
        "EARLIEST MEASURED ONSET: " + (f"{onset[0].isoformat()} ({onset[1]})" if onset else
                                       "not measured; find it first (the earliest step change in any signal)"),
        "MEASURED CHANGES (largest first):", *(measured_changes(records) or ["- none computed"]),
        "EVIDENCE:", *evidence_lines(ctx),
        "CURRENT " + tracker.render_tree(),
    ]
    res = await ctx.deps.llm.structured(role="hypotheses", system=load_prompt("reform"), user="\n".join(blocks),
                                        schema=HypothesisSet, timeout_s=_llm_timeout(ctx))
    if res is None:
        ctx.warn(f"deepening: re-forming hypotheses after {fallen_id} fell returned 0 hypotheses")
        return []
    before = set(tracker.hypotheses)
    known = {statement_key(h.statement) for h in tracker.hypotheses.values()}
    restated = [h for h in res.hypotheses if statement_key(h.statement) in known]
    if restated:
        report.notes.append(f"re-formation restated {len(restated)} existing hypothesis(es); ignored")
    items = [h.model_dump() | {"id": None, "parent": None, "status": "open"}
             for h in res.hypotheses if h not in restated]
    upd = tracker.update(items, origin="reform")
    new = [i for i in upd.accepted if i not in before]
    report.reformed_ids = new
    report.notes += [n for n in upd.notes if "dropped evidence" in n]
    if not new:
        ctx.warn(f"deepening: re-formation after {fallen_id} fell produced 0 new hypotheses")
    return new


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------


async def deepen(ctx: RunContext, *, max_rounds: int | None = None) -> DeepeningReport:
    """Decompose, test and judge the leading hypothesis for up to ``max_rounds`` rounds within budget."""
    tracker = ctx.tracker.bind(ctx)
    settings = ctx.deps.settings
    report = DeepeningReport()
    t0 = time.monotonic()
    rounds = settings.deepening_rounds if max_rounds is None else max_rounds
    seen = {_norm_sql(r.sql): r.id for r in board_records(ctx.board) if r.sql and not r.error}
    decomposed: set[str] = set()
    dead_subtrees: set[str] = set()
    if not tracker.hypotheses:
        ctx.warn("deepening: 0 hypotheses to deepen")
        report.stopped = "no_hypotheses"
        return report
    forced: str | None = None
    focus = tracker.focus(min_confidence=DECOMPOSE_FLOOR)
    while True:
        if report.rounds >= rounds:
            report.stopped = "max_rounds"
            break
        if ctx.stop_requested:
            report.stopped = "stop_requested"
            break
        if ctx.remaining() < settings.report_reserve_s + ROUND_MIN_S:
            report.stopped = "budget"
            break
        if focus is None:
            report.stopped = "no_focus"
            break
        report.rounds += 1
        report.focus_path.append(focus.id)
        decomposed.add(focus.id)
        allowed = _allowed_tables(ctx, focus)
        checks = await _decompose(ctx, focus, allowed, report)
        outcomes: list[CheckOutcome] = []
        fallen: str | None = None
        if checks:
            children = tracker.add_subchecks(focus.id, checks)
            subs = [tracker.checks[c.id] for c in children]
            firsts = await asyncio.gather(*(_execute(ctx, f"{c.id}: {c.statement[:160]}", s.sql, seen, report)
                                            for c, s in zip(children, subs)))
            for child, sub, first in zip(children, subs, firsts):
                # Judged one at a time so each judge sees the checks already judged.
                outcome = await _judge_check(ctx, child, sub, first, allowed, seen, report)
                effect = tracker.apply_verdict(child.id, outcome.verdict, outcome.evidence_ids)
                report.outcomes.append(outcome)
                outcomes.append(outcome)
                if effect.demoted and effect.demoted not in report.demoted:
                    report.demoted.append(effect.demoted)
                fallen = fallen or effect.fallen_top_level
                if effect.reached_trigger:
                    report.reached_trigger = child.id
        if report.reached_trigger:
            report.stopped = "trigger_reached"
            break
        if fallen and not tracker.reformed:
            new = await _reform(ctx, fallen, report)
            if new:
                forced = max(new, key=lambda i: tracker.hypotheses[i].confidence)
        confirmed = [o for o in outcomes if o.verdict.verdict == "confirmed"]
        if confirmed and not fallen:
            best = max(confirmed, key=lambda o: (LEVEL_RANK[tracker.hypotheses[o.hypothesis_id].causal_level],
                                                 tracker.hypotheses[o.hypothesis_id].confidence))
            if best.verdict.terminal:
                # Confirmed but nothing deeper to test, below trigger level: mechanism (trigger not found).
                report.notes.append(f"{best.hypothesis_id}: mechanism confirmed, trigger not found")
                dead_subtrees.add(tracker.root_of(focus.id))
                focus = None
            else:
                focus = tracker.get(best.hypothesis_id)
        else:
            if tracker.mark_exhausted(focus.id, "decomposition found no confirmable sub-hypothesis"):
                report.exhausted.append(focus.id)
            dead_subtrees.add(tracker.root_of(focus.id))
            focus = None
        if forced:
            focus, forced = tracker.get(forced), None
        elif focus is None:
            focus = tracker.focus(min_confidence=DECOMPOSE_FLOOR, exclude_ids=decomposed,
                                  exclude_subtrees=dead_subtrees)
    ctx.stats.timings["deepening_s"] = round(ctx.stats.timings.get("deepening_s", 0.0) + time.monotonic() - t0, 1)
    if not report.outcomes:
        ctx.warn(f"deepening: 0 checks judged ({report.stopped})")
    return report
