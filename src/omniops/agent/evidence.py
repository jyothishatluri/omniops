"""The evidence board: every tool result becomes a citable record with a run-wide unique id.

Retrospective lessons encoded here:
- 3.4  Evidence is the citation currency: one record per result, ids E1, E2 ... (shadow agents get
       S1, S2 ... so they never collide), records pair with results by id never by position, an
       evidence floor counts only meaningful items (not empty without a control, not errors,
       confidence >= 0.3, not discovery-only lookups), and evidence is never lost to a failure:
       errors keep a deterministic stub.
- 3.1  Code measures: the summary is a deterministic digest (row count, tables, window, digests,
       a few whole rows, notes, empty verdict) - no LLM summarization in the critical path.
- 3.2  Seeds are pinned so long runs never push them out of the compact view.
- 3.5  Links are data: the link bucket is keyed by evidence id, built only from URLs that came
       back from data-bearing queries, each URL listed once (it used to repeat per evidence id).
- 3.9.2/3.9.3  Upstream caps (60 links / 12k chars), digests first so key facts survive clipping,
       every omission is explicit and sized.
- 3.6.5  Empty is inconclusive without a positive control; "absent" needs one.
- 4.1  Loud zeros: an empty board or an all-error board is visible, never silent.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from omniops.agent.types import EvidenceRecord
from omniops.engine.results import fmt_cell

if TYPE_CHECKING:
    from omniops.agent.types import SpecialistVerdict
    from omniops.engine.results import QueryResult

MEANINGFUL_MIN_CONFIDENCE = 0.3   # 3.4 evidence floor: below this an item does not count
# Kinds that never count toward the evidence floor (3.4: the floor counts measured items). The alert is
# what is being investigated, memory and human leads are things to verify, and a specialist verdict
# restates the measurements it cites, which count on their own.
NOT_MEASURED_KINDS = frozenset({"alert", "memory", "lead", "specialist"})
SUMMARY_ROWS = 3
SUMMARY_CELL_CAP = 160
MAX_ROW_LINKS = 10
MAX_RECORD_REQUESTS = 20          # the (already redacted) HTTP requests kept with each record for case files

# Metadata / catalog lookups (3.4: "early runs fetched only definitions and called it an
# investigation"). Matched against table-name suffixes so it works for every platform.
_DISCOVERY_SUFFIXES = (
    "label_values", "label_names", "labels", "tag_values", "tag_keys", "tags", "metadata",
    "metric_metadata", "metric_descriptors", "metric_names", "metrics_list", "list_metrics",
    "metrics_catalog", "series", "datasources", "dashboards", "dashboard_search", "dashboard_list",
    "folders", "fields", "field_caps", "field_names", "mappings", "columns", "schema", "schemas",
    "indices", "indexes", "index_patterns", "data_views", "event_types", "keyset", "monitors",
    "monitor_search", "rules", "alert_rules", "services_list", "service_catalog", "catalog",
    "saved_searches", "sourcetypes",
)
_DISCOVERY_RE = re.compile(r"(?:^|_)(?:" + "|".join(sorted(_DISCOVERY_SUFFIXES, key=len, reverse=True)) + r")$")
_URL_RE = re.compile(r"https?://[^\s\"'<>|\\]+")
_URL_TRAIL = ".,;:)]}'\""
_LABEL_COLS = ("title", "name", "summary", "subject", "message", "key")
_TRUNCATION_NOTE = re.compile(r"truncated|capped|at least \d+ rows", re.IGNORECASE)


def is_discovery_table(table: str) -> bool:
    """True for metadata/catalog listings (label values, metric metadata, dashboards, fields ...)."""
    return bool(_DISCOVERY_RE.search(table.lower()))


# ---------------------------------------------------------------------------------------------
# Record builders (deterministic; no LLM)
# ---------------------------------------------------------------------------------------------


def _cell(v: Any) -> str:
    s = fmt_cell(v)
    if len(s) > SUMMARY_CELL_CAP:
        return s[:SUMMARY_CELL_CAP] + f"...[+{len(s) - SUMMARY_CELL_CAP} chars]"
    return s


def _row_text(columns: list[str], row: tuple[Any, ...]) -> str:
    return ", ".join(f"{c}={_cell(v)}" for c, v in zip(columns, row))


def _row_links(result: QueryResult, seen: set[str]) -> list[dict[str, str]]:
    """URLs that came back in the data itself (change events, tickets, PRs ...)."""
    out: list[dict[str, str]] = []
    label_i = next((i for i, c in enumerate(result.columns) if c.lower() in _LABEL_COLS), None)
    for row in result.rows:
        for i, v in enumerate(row):
            if not isinstance(v, str) or "http" not in v:
                continue
            for m in _URL_RE.finditer(v):
                url = m.group(0).rstrip(_URL_TRAIL)
                if url in seen:
                    continue
                seen.add(url)
                label = result.columns[i]
                if label_i is not None and label_i != i and isinstance(row[label_i], str):
                    label = f"{label}: {' '.join(row[label_i].split())[:60]}"
                out.append({"label": label, "url": url})
                if len(out) >= MAX_ROW_LINKS:
                    return out
    return out


def _confidence(notes: list[str]) -> tuple[float, bool, list[str]]:
    """Measurement confidence: 1.0 direct; lower for partial fetches, truncation, clamped windows."""
    conf, flags = 1.0, []
    truncated = any(_TRUNCATION_NOTE.search(n) for n in notes)
    if any("PARTIAL" in n for n in notes):
        conf = min(conf, 0.7)
        flags.append("partial: some requests failed")
    if truncated:
        conf = min(conf, 0.8)
        flags.append("truncated")
    if any("clamped" in n for n in notes):
        conf = min(conf, 0.85)
        flags.append("window clamped")
    return conf, truncated, flags


def counts_toward_floor(rec: EvidenceRecord) -> bool:
    """A measured, meaningful item for the evidence floor (3.4): data or controlled absence from a query,
    seed or deepening check; never the alert, memory, leads, verdicts, errors or weak readings."""
    return (rec.meaningful and not rec.error and rec.confidence >= MEANINGFUL_MIN_CONFIDENCE
            and rec.kind not in NOT_MEASURED_KINDS)


def record_from_result(result: QueryResult, *, agent: str, purpose: str, kind: str = "query") -> EvidenceRecord:
    """Turn a QueryResult into an (unnumbered) evidence record; ``EvidenceBoard.add`` assigns the id.

    Meaningful: rows > 0 and not discovery-only, or empty with a positive control ("absent").
    Empty-inconclusive results, staged writes and discovery-only lookups are kept but do not count.
    An aggregate over zero input rows is an empty, not a measurement (3.6.5): ``count(*)`` of a
    0-row fetch (a typo'd label gives ``n=0``) unless the positive control confirmed the filter, and
    an all-NULL result (``max(value)`` of an empty series, or of fetched rows the predicates removed).
    """
    tables = list(result.tables)
    fetched = sum(result.fetched_rows.values())
    null_only = (bool(result.rows) and bool(tables) and not result.staged_write
                 and (fetched == 0 or len(result.rows) == 1)
                 and all(v is None for row in result.rows for v in row))
    zero_input = (bool(result.rows) and bool(tables) and bool(result.fetched_rows) and fetched == 0
                  and not null_only and not result.staged_write and result.empty_verdict != "absent")
    discovery = bool(tables) and all(is_discovery_table(t) for t in tables)
    conf, truncated, flags = _confidence(result.notes)
    head = f"{result.total_rows} row(s) x {len(result.columns)} col(s) from {', '.join(tables) or 'sql'}"
    windows = [f"{t}: {s} -> {e}" for t, (s, e) in result.windows.items()]
    if windows:
        head += "; window " + "; ".join(windows)
    if flags:
        head += f" [{', '.join(flags)}]"
    lines = [head]
    empty_verdict: str | None = None
    meaningful = result.total_rows > 0 and not discovery and not null_only and not zero_input
    if result.staged_write:
        meaningful = False
        lines.append(f"staged write {result.staged_write.get('id', '')}: {result.staged_write.get('summary', '')} "
                     "- awaiting human approval, not evidence")
    elif null_only and fetched:
        empty_verdict = "inconclusive"
        lines.append(f"EMPTY inconclusive: every cell is NULL although the source returned {fetched} row(s); the "
                     "SQL predicates/joins most likely removed them all before the aggregate; check predicate values "
                     "(exact label values, case) before calling it absent")
    elif null_only:
        empty_verdict = "inconclusive"
        lines.append("EMPTY inconclusive: the aggregate ran over 0 source rows (every cell NULL); not a measured "
                     "value and not proof of absence")
    elif zero_input:
        empty_verdict = "inconclusive"
        lines.append("EMPTY inconclusive: the aggregate ran over 0 source rows (the API returned nothing) and no "
                     "positive control confirmed the filter; a count of 0 here is not a measured zero and not "
                     "proof of absence")
    elif result.total_rows and fetched == 0 and result.fetched_rows and result.empty_verdict == "absent":
        empty_verdict = "absent"
        lines.append("aggregate over 0 source rows; the positive control returned data elsewhere, so the filter "
                     "is valid and this is a measured zero (absent)")
    elif result.total_rows == 0:
        if result.empty_verdict == "absent":
            empty_verdict, meaningful = "absent", True
            lines.append("EMPTY absent: 0 rows in window, positive control returned data elsewhere")
        elif result.empty_verdict is None and fetched:
            empty_verdict = "inconclusive"
            lines.append(f"EMPTY inconclusive: the source returned {fetched} row(s) but the SQL predicates/joins "
                         "removed them all; check predicate values (exact label values, case) before calling it absent")
        else:
            empty_verdict = "inconclusive"
            lines.append("EMPTY inconclusive: 0 rows and no positive control confirmed the filter; not proof of absence")
    if discovery:
        lines.append("discovery-only lookup (metadata/catalog): orients the search, never counts as evidence")
    lines += [f"digest: {d.strip()}" for d in result.digests[:8]]
    if result.rows:
        shown = result.rows[:SUMMARY_ROWS]
        which = "all rows" if len(shown) == result.total_rows else f"first {len(shown)} of {result.total_rows} rows"
        lines.append(f"rows ({which}):")
        lines += [f"  {_row_text(result.columns, r)}" for r in shown]
    lines += [f"note: {n[:200]}" for n in result.notes[:4]]
    if len(result.notes) > 4:
        lines.append(f"note: {len(result.notes) - 4} more note(s) in the full result")
    seen = {lk["url"] for lk in result.links}
    links = [dict(lk) for lk in result.links] + _row_links(result, seen)
    window = next(iter(result.windows.values()), None)
    return EvidenceRecord(
        id="", kind=kind, agent=agent, title=purpose or f"SQL on {', '.join(tables) or 'sql'}",  # type: ignore[arg-type]
        summary="\n".join(lines), sql=result.sql, tables=tables, rendered=result.render(),
        row_count=0 if null_only or zero_input else result.total_rows, empty_verdict=empty_verdict, links=links,
        window=(window[0], window[1]) if window else None,
        confidence=conf if meaningful else min(conf, 0.4), meaningful=meaningful,
        data={"columns": list(result.columns), "fetched_rows": dict(result.fetched_rows), "truncated": truncated,
              "discovery_only": discovery, "staged_write": result.staged_write,
              "requests": list(result.requests[:MAX_RECORD_REQUESTS])},
    )


def record_error(error: BaseException | str, *, agent: str, purpose: str, sql: str | None = None,
                 kind: str = "query", tables: Iterable[str] = (), preview: str = "") -> EvidenceRecord:
    """A stub for a failed step so evidence is never silently lost (3.4): keeps the error and inputs."""
    msg = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    summary = f"FAILED: {msg[:500]}"
    if preview:
        summary += f"\nraw preview: {preview[:500]}"
    return EvidenceRecord(
        id="", kind=kind, agent=agent, title=purpose or "failed step", summary=summary,  # type: ignore[arg-type]
        sql=sql, tables=list(tables), error=msg[:1000], confidence=0.0, meaningful=False,
        data={"lesson": getattr(error, "lesson", None)} if isinstance(error, BaseException) else {},
    )


# ---------------------------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------------------------


class EvidenceBoard:
    """Run-wide evidence store. Ids are allocated here only; shadow agents get the S range."""

    def __init__(self, shadow_agents: Iterable[str] = ()) -> None:
        self.shadow_agents = set(shadow_agents)
        self._records: list[EvidenceRecord] = []
        self._by_id: dict[str, EvidenceRecord] = {}
        self._counters = {"E": 0, "S": 0}
        self._changed = asyncio.Event()

    def __len__(self) -> int:
        return len(self._records)

    # -- writing ----------------------------------------------------------------------------

    def add(self, record: EvidenceRecord, *, pinned: bool | None = None, shadow: bool | None = None
            ) -> EvidenceRecord:
        """Assign the next id (E# or S#), store, and wake waiters. Re-adding the same object is a no-op."""
        if record.id and self._by_id.get(record.id) is record:
            return record
        is_shadow = shadow if shadow is not None else record.agent in self.shadow_agents
        prefix = "S" if is_shadow else "E"
        self._counters[prefix] += 1
        record.id = f"{prefix}{self._counters[prefix]}"
        if pinned is not None:
            record.pinned = pinned
        self._records.append(record)
        self._by_id[record.id] = record
        self._changed.set()
        self._changed = asyncio.Event()
        return record

    def add_result(self, result: QueryResult, *, agent: str, purpose: str, kind: str = "query",
                   pinned: bool = False, shadow: bool | None = None) -> EvidenceRecord:
        return self.add(record_from_result(result, agent=agent, purpose=purpose, kind=kind), pinned=pinned,
                        shadow=shadow)

    def add_error(self, error: BaseException | str, *, agent: str, purpose: str, sql: str | None = None,
                  kind: str = "query", tables: Iterable[str] = (), shadow: bool | None = None) -> EvidenceRecord:
        return self.add(record_error(error, agent=agent, purpose=purpose, sql=sql, kind=kind, tables=tables),
                        shadow=shadow)

    def pin(self, eid: str) -> bool:
        rec = self._by_id.get(eid)
        if rec is None:
            return False
        rec.pinned = True
        return True

    def publish_specialist(self, verdict: SpecialistVerdict, *, shadow: bool | None = None) -> EvidenceRecord:
        """A specialist's structured verdict as one citable record. Cited ids must exist on the board.
        ``shadow`` stores it as an S-record (D8): kept for traces, never shown to the user."""
        cited = [e for e in verdict.evidence_ids if e in self._by_id]
        dropped = [e for e in verdict.evidence_ids if e not in self._by_id]
        summary = f"{verdict.verdict}: {verdict.headline} (confidence {verdict.confidence:.2f}"
        summary += f"; evidence {', '.join(cited)})" if cited else "; no evidence cited)"
        if verdict.suggested_hypothesis:
            summary += f"\nsuggested hypothesis: {verdict.suggested_hypothesis}"
        if dropped:
            summary += f"\nunknown evidence ids dropped: {', '.join(dropped)}"
        decisive = verdict.verdict in ("found", "not_found")
        # A verdict is only as good as what it cites: errors and inconclusive empties measure nothing.
        measured = [e for e in cited if self._by_id[e].meaningful and not self._by_id[e].error]
        if decisive and cited and not measured:
            summary += "\ncites no meaningful measurement (only errors or inconclusive empties); not evidence"
        rec = EvidenceRecord(
            id="", kind="specialist", agent=verdict.agent, title=f"{verdict.agent} specialist verdict",
            summary=summary, confidence=verdict.confidence if decisive else min(verdict.confidence, 0.4),
            meaningful=decisive and bool(measured), data={**verdict.model_dump(), "evidence_ids": cited},
        )
        return self.add(rec, shadow=shadow)

    # -- reading ----------------------------------------------------------------------------

    def get(self, eid: str) -> EvidenceRecord | None:
        return self._by_id.get(eid)

    def all(self) -> list[EvidenceRecord]:
        """Every record, shadow ones included (traces, run results)."""
        return list(self._records)

    def visible(self) -> list[EvidenceRecord]:
        """Records the primary flow may cite (shadow-agent records excluded)."""
        return [r for r in self._records if not r.id.startswith("S")]

    def valid_ids(self) -> set[str]:
        return {r.id for r in self.visible()}

    def pinned(self) -> list[EvidenceRecord]:
        return [r for r in self.visible() if r.pinned]

    def meaningful(self) -> list[EvidenceRecord]:
        return [r for r in self.visible()
                if r.meaningful and not r.error and r.confidence >= MEANINGFUL_MIN_CONFIDENCE]

    def meaningful_count(self) -> int:
        """The evidence-floor count: measured items only (see ``counts_toward_floor``)."""
        return sum(1 for r in self.visible() if counts_toward_floor(r))

    def since(self, after_count: int) -> list[EvidenceRecord]:
        """Visible records added after the board held ``after_count`` records (use ``len(board)``)."""
        return [r for r in self._records[after_count:] if not r.id.startswith("S")]

    async def wait_for_new(self, after_count: int, timeout: float) -> list[EvidenceRecord]:
        """Wait up to ``timeout`` s for visible records beyond ``after_count``; returns them (maybe [])."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while not self.since(after_count):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                break
        return self.since(after_count)

    # -- model-facing views -----------------------------------------------------------------

    def compact_view(self, max_recent: int = 30, max_pinned: int = 25, line_chars: int = 600) -> str:
        """Pinned records first (seeds stay visible), then the most recent ones; omissions are explicit."""
        recs = self.visible()
        if not recs:
            return "EVIDENCE: none yet (0 records)."
        pinned = [r for r in recs if r.pinned]
        rest = [r for r in recs if not r.pinned]
        shown_pinned = pinned[:max_pinned]
        shown_recent = rest[-max_recent:] if max_recent > 0 else []
        lines = [f"EVIDENCE ({len(recs)} records, {self.meaningful_count()} meaningful)"]
        if shown_pinned:
            lines.append("PINNED:")
            lines += [r.line(line_chars) for r in shown_pinned]
            if len(pinned) > len(shown_pinned):
                lines.append(f"[omitted] {len(pinned) - len(shown_pinned)} more pinned record(s): "
                             f"{', '.join(r.id for r in pinned[max_pinned:])}")
        if shown_recent:
            lines.append("RECENT:")
            omitted = rest[: len(rest) - len(shown_recent)]
            if omitted:
                lines.append(f"[omitted] {len(omitted)} older record(s) not shown ({omitted[0].id}..{omitted[-1].id}); "
                             "their ids remain valid for citation")
            lines += [r.line(line_chars) for r in shown_recent]
        return "\n".join(lines)

    def link_bucket(self, max_entries: int = 60, max_chars: int = 12_000) -> dict[str, list[dict[str, str]]]:
        """{evidence id: [{label, url}]} from meaningful records only; each URL once (first citation wins)."""
        bucket: dict[str, list[dict[str, str]]] = {}
        seen: set[str] = set()
        used = n = 0
        for rec in self.meaningful():
            for lk in rec.links:
                url = lk.get("url", "")
                if not url or url in seen:
                    continue
                cost = len(rec.id) + len(lk.get("label", "")) + len(url) + 6
                if n >= max_entries or used + cost > max_chars:
                    return bucket
                seen.add(url)
                bucket.setdefault(rec.id, []).append({"label": lk.get("label", ""), "url": url})
                used += cost
                n += 1
        return bucket

    def link_bucket_text(self, max_entries: int = 60, max_chars: int = 12_000) -> str:
        bucket = self.link_bucket(max_entries, max_chars)
        if not bucket:
            return "LINKS: none (no data-bearing evidence carried a URL)."
        lines = ["LINKS (use these exact URLs; place each at the first prose citation of its evidence id):"]
        for eid, links in bucket.items():
            lines += [f"{eid} | {lk['label']} | {lk['url']}" for lk in links]
        return "\n".join(lines)
