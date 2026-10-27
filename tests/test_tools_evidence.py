"""The evidence board and the deterministic record builders, through their public API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from omniops.agent.evidence import EvidenceBoard, is_discovery_table, record_error, record_from_result
from omniops.agent.types import EvidenceRecord, SpecialistVerdict
from omniops.engine.engine import UnknownTableError
from omniops.engine.results import QueryResult
from omniops.engine.transform import ApiBodyError

T0 = datetime(2026, 10, 1, 11, 45, tzinfo=UTC)


def result(rows: list[tuple], *, tables: tuple[str, ...] = ("loki_logs",), notes: list[str] | None = None,
           columns: tuple[str, ...] = ("ts", "message"), **kw: object) -> QueryResult:
    return QueryResult(sql="SELECT ts, message FROM loki_logs WHERE query = '{app=\"api\"}'", columns=list(columns),
                       types=["TIMESTAMP WITH TIME ZONE" if c == "ts" else "VARCHAR" for c in columns], rows=rows,
                       total_rows=len(rows), tables=list(tables), notes=notes or [],
                       windows={tables[0]: ("2026-10-01T11:45:00Z", "2026-10-01T12:05:00Z")}, **kw)  # type: ignore[arg-type]


def rec(title: str, *, meaningful: bool = True, links: list[dict[str, str]] | None = None, agent: str = "primary",
        pinned: bool = False, confidence: float = 1.0) -> EvidenceRecord:
    return EvidenceRecord(id="", kind="query", agent=agent, title=title, summary=f"{title} summary",
                          meaningful=meaningful, links=links or [], pinned=pinned, confidence=confidence)


# ---------------------------------------------------------------------------------------------
# record_from_result / record_error
# ---------------------------------------------------------------------------------------------


def test_summary_is_deterministic_digest_with_whole_rows_and_links_from_data() -> None:
    rows = [(T0, f"deploy failed: https://github.com/acme/shop/actions/runs/{i}.") for i in range(5)]
    r = record_from_result(result(rows, digests=["api: onset 11:58 (up)"],
                                  links=[{"label": "loki_logs", "url": "http://grafana.test/explore?x=1"}]),
                           agent="primary", purpose="api error logs")
    assert r.id == "" and r.title == "api error logs" and r.meaningful and r.confidence == 1.0
    lines = r.summary.splitlines()
    assert lines[0] == ("5 row(s) x 2 col(s) from loki_logs; window loki_logs: 2026-10-01T11:45:00Z -> "
                        "2026-10-01T12:05:00Z")
    assert "digest: api: onset 11:58 (up)" in lines and "rows (first 3 of 5 rows):" in lines
    assert "message=deploy failed: https://github.com/acme/shop/actions/runs/0." in r.summary
    assert r.window == ("2026-10-01T11:45:00Z", "2026-10-01T12:05:00Z") and r.row_count == 5
    urls = [lk["url"] for lk in r.links]
    assert urls[0] == "http://grafana.test/explore?x=1"
    assert "https://github.com/acme/shop/actions/runs/0" in urls          # trailing '.' stripped
    assert len(urls) == len(set(urls)) == 6


def test_summary_window_line_carries_what_the_step_buckets_cover() -> None:
    note = "step 300s (4 point(s), each counting the step ending at its ts); buckets cover A -> B"
    r = record_from_result(result([(T0, "x")], window_notes={"loki_logs": note}), agent="primary", purpose="p")
    assert r.summary.splitlines()[0] == ("1 row(s) x 2 col(s) from loki_logs; window loki_logs: "
                                         f"2026-10-01T11:45:00Z -> 2026-10-01T12:05:00Z ({note})")
    assert r.window == ("2026-10-01T11:45:00Z", "2026-10-01T12:05:00Z")


def test_confidence_drops_for_partial_and_truncated_results() -> None:
    rows = [(T0, "x")]
    partial = record_from_result(result(rows, notes=["loki_logs: PARTIAL: 1 of 3 request(s) failed: 503"]),
                                 agent="primary", purpose="p")
    assert partial.confidence == 0.7 and "partial: some requests failed" in partial.summary.splitlines()[0]
    capped = record_from_result(result(rows, notes=["loki_logs: fetched rows capped at 20000 of 31000"]),
                                agent="primary", purpose="p")
    assert capped.confidence == 0.8 and capped.data["truncated"] and capped.meaningful


def test_empty_results_absent_is_meaningful_inconclusive_is_not() -> None:
    absent = record_from_result(result([], empty_verdict="absent"), agent="primary", purpose="errors")
    assert absent.meaningful and absent.empty_verdict == "absent"
    assert "absent: 0 rows in window, positive control returned data elsewhere" in absent.summary
    unknown = record_from_result(result([], empty_verdict="inconclusive"), agent="primary", purpose="typo")
    assert not unknown.meaningful and unknown.empty_verdict == "inconclusive" and unknown.confidence <= 0.4
    filtered = record_from_result(result([], fetched_rows={"loki_logs": 40}), agent="primary", purpose="filtered")
    assert not filtered.meaningful and "returned 40 row(s) but the SQL predicates/joins removed them" in filtered.summary


def test_aggregate_over_zero_source_rows_is_an_inconclusive_empty() -> None:
    """max()/arg_max() of an empty series returns one all-NULL row: not a measurement (3.6.5)."""
    nulls = record_from_result(result([(None, None)], columns=("peak", "peak_at"), fetched_rows={"loki_logs": 0}),
                               agent="primary", purpose="peak")
    assert not nulls.meaningful and nulls.empty_verdict == "inconclusive" and nulls.row_count == 0
    assert "aggregate ran over 0 source rows" in nulls.summary and nulls.confidence <= 0.4
    # count(*) over a 0-row fetch (a typo'd label) is not a measured zero without a positive control.
    zero = record_from_result(result([(0, None)], columns=("n", "peak_at"), fetched_rows={"loki_logs": 0}),
                              agent="primary", purpose="count")
    assert not zero.meaningful and zero.row_count == 0 and zero.empty_verdict == "inconclusive"
    assert "no positive control confirmed the filter" in zero.summary and zero.confidence <= 0.4
    # ... with the control confirming the filter it is a real zero (absent), and counts.
    controlled = record_from_result(result([(0, None)], columns=("n", "peak_at"), fetched_rows={"loki_logs": 0},
                                           empty_verdict="absent"), agent="primary", purpose="count")
    assert controlled.meaningful and controlled.row_count == 1 and controlled.empty_verdict == "absent"
    # max() after the SQL predicates removed every fetched row: one all-NULL row, still not a measurement.
    removed = record_from_result(result([(None, None)], columns=("peak", "peak_at"), fetched_rows={"loki_logs": 3}),
                                 agent="primary", purpose="peak")
    assert not removed.meaningful and removed.empty_verdict == "inconclusive" and removed.row_count == 0
    assert "source returned 3 row(s)" in removed.summary
    counted = record_from_result(result([(7, None)], columns=("n", "peak_at"), fetched_rows={"loki_logs": 7}),
                                 agent="primary", purpose="count")
    assert counted.meaningful and counted.empty_verdict is None and counted.row_count == 1


def test_discovery_only_lookups_and_staged_writes_never_count() -> None:
    assert is_discovery_table("prometheus_label_values") and is_discovery_table("grafana_dashboards")
    assert is_discovery_table("datadog_metric_metadata") and not is_discovery_table("loki_logs")
    meta = record_from_result(result([("job",)], tables=("prometheus_label_values",), columns=("value",)),
                              agent="primary", purpose="labels")
    assert not meta.meaningful and meta.data["discovery_only"] and "discovery-only" in meta.summary
    staged = record_from_result(result([("W1", "staged", "x")], tables=("grafana_annotation_create",),
                                       columns=("write_id", "status", "summary"),
                                       staged_write={"id": "W1", "summary": "annotate"}),
                                agent="primary", purpose="annotate")
    assert not staged.meaningful and "awaiting human approval, not evidence" in staged.summary


def test_error_stub_keeps_error_lesson_and_inputs() -> None:
    stub = record_error(UnknownTableError("table 'x' does not exist"), agent="code_change", purpose="guess",
                        sql="SELECT * FROM x", tables=["x"])
    assert stub.error == "UnknownTableError: table 'x' does not exist" and stub.summary.startswith("FAILED: ")
    assert stub.data["lesson"].startswith("search the catalog") and stub.sql == "SELECT * FROM x"
    assert not stub.meaningful and stub.confidence == 0.0
    body = record_error(ApiBodyError("API reported an error in a 2xx body: parse error at char 12",
                                     hint="PromQL syntax"), agent="primary", purpose="bad promql")
    assert body.data["lesson"].startswith("PromQL syntax; the API rejected the request in its response body")


# ---------------------------------------------------------------------------------------------
# EvidenceBoard
# ---------------------------------------------------------------------------------------------


def test_ids_shadow_range_and_meaningful_count() -> None:
    board = EvidenceBoard(shadow_agents=["shadow_rca"])
    a, b = board.add(rec("a")), board.add(rec("b", meaningful=False))
    s = board.add(rec("shadow", agent="shadow_rca"))
    c = board.add(rec("weak", confidence=0.2))
    err = board.add(record_error("boom", agent="primary", purpose="failed"))
    assert [a.id, b.id, s.id, c.id, err.id] == ["E1", "E2", "S1", "E3", "E4"]
    assert board.add(a) is a and a.id == "E1" and len(board) == 5      # re-adding is a no-op
    assert board.get("S1") is s and "S1" not in board.valid_ids() and len(board.all()) == 5
    assert [r.id for r in board.meaningful()] == ["E1"] and board.meaningful_count() == 1


def test_compact_view_pins_first_and_marks_omissions() -> None:
    board = EvidenceBoard()
    board.add(rec("seed alert rule"), pinned=True)
    for i in range(6):
        board.add(rec(f"query {i}"))
    board.add(rec("late seed", pinned=True))
    view = board.compact_view(max_recent=3, max_pinned=1).splitlines()
    assert view[0] == "EVIDENCE (8 records, 8 meaningful)"
    assert view[1] == "PINNED:" and view[2].startswith("E1 (primary; seed alert rule)")
    assert view[3] == "[omitted] 1 more pinned record(s): E8"
    assert view[4] == "RECENT:" and view[5].startswith("[omitted] 3 older record(s) not shown (E2..E4)")
    assert [v.split(" ")[0] for v in view[6:]] == ["E5", "E6", "E7"]
    assert [r.id for r in board.pinned()] == ["E1", "E8"]
    assert EvidenceBoard().compact_view() == "EVIDENCE: none yet (0 records)."


def test_link_bucket_keyed_by_first_citation_deduped_and_capped() -> None:
    board = EvidenceBoard()
    shared = {"label": "run", "url": "https://ci.test/run/1"}
    board.add(rec("a", links=[shared, {"label": "pr", "url": "https://git.test/pr/2"}]))
    board.add(rec("b", links=[dict(shared), {"label": "dash", "url": "https://grafana.test/d/x"}]))
    board.add(rec("discovery", meaningful=False, links=[{"label": "meta", "url": "https://grafana.test/meta"}]))
    bucket = board.link_bucket()
    assert bucket == {"E1": [shared, {"label": "pr", "url": "https://git.test/pr/2"}],
                      "E2": [{"label": "dash", "url": "https://grafana.test/d/x"}]}
    assert board.link_bucket(max_entries=2) == {"E1": bucket["E1"]}
    assert sum(len(v) for v in board.link_bucket(max_chars=40).values()) == 1
    text = board.link_bucket_text()
    assert "E1 | run | https://ci.test/run/1" in text and text.count("https://ci.test/run/1") == 1
    assert EvidenceBoard().link_bucket_text().startswith("LINKS: none")


def test_publish_specialist_cites_only_existing_ids() -> None:
    board = EvidenceBoard()
    board.add(rec("deploys"))
    found = board.publish_specialist(SpecialistVerdict(
        agent="code_change", verdict="found", headline="deploy 1.4.2 at 11:57 precedes the spike",
        confidence=0.7, evidence_ids=["E1", "E9"], suggested_hypothesis="deploy 1.4.2 broke checkout"))
    assert found.id == "E2" and found.kind == "specialist" and found.meaningful and found.confidence == 0.7
    assert found.data["evidence_ids"] == ["E1"] and "unknown evidence ids dropped: E9" in found.summary
    assert "suggested hypothesis: deploy 1.4.2 broke checkout" in found.summary
    vague = board.publish_specialist(SpecialistVerdict(agent="dependency", verdict="inconclusive",
                                                       headline="nothing clear", confidence=0.9))
    assert not vague.meaningful and vague.confidence == 0.4


async def test_wait_for_new_wakes_on_add_and_times_out() -> None:
    board = EvidenceBoard(shadow_agents=["shadow_rca"])
    board.add(rec("first"))
    start = len(board)

    async def land() -> None:
        await asyncio.sleep(0.05)
        board.add(rec("shadow", agent="shadow_rca"))     # shadow records never wake the primary loop
        await asyncio.sleep(0.05)
        board.add(rec("specialist evidence", agent="code_change"))

    task = asyncio.create_task(land())
    got = await board.wait_for_new(start, timeout=2.0)
    await task
    assert [r.title for r in got] == ["specialist evidence"]
    assert await board.wait_for_new(len(board), timeout=0.05) == []
