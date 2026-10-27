"""Arena case records from a Slack thread shaped like the simulator's (every edit kept) and a RunResult."""

from __future__ import annotations

from typing import Any

import pytest

from omniops.agent.types import ConclusionCard, RunResult, ToolEvent
from omniops.bench.arena import records as R

BOT = "U0OMNIOPS"
ROOT_TS = "1793095300.000100"
LONG = "https://grafana.example/explore?panes=" + "x" * 200


def section(text: str, block_id: str | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    if block_id:
        b["block_id"] = block_id
    return b


def meta(kind: str, run_id: str, **payload: Any) -> dict[str, Any]:
    return {"event_type": kind, "event_payload": {"run_id": run_id, **payload}}


def thread() -> list[dict[str, Any]]:
    """Root alert, status trail (edited), early -> final card, two report pages, a notice."""
    status_live = {"text": "Investigating", "blocks": [section(":mag: Investigating ... querying pods", "omniops_status")],
                   "metadata": meta("omniops_status", "r1", state="live"), "edited_at": 1793095301.0}
    early = {"text": "Preliminary", "blocks": [section("*Preliminary:* memory pressure in batch")],
             "metadata": meta("omniops_card", "r1"), "edited_at": 1793095350.0}
    return [
        {"ts": ROOT_TS, "bot_id": "B0ARENAGRAFANA", "subtype": "bot_message", "text": "",
         "attachments": [{"title": "[FIRING:1] Platform alerts", "text": "**Firing**"}], "edits": []},
        {"ts": "1793095301.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [status_live],
         "text": "Done", "blocks": [section(":white_check_mark: Finished in 4 min", "omniops_status"),
                                    {"type": "actions", "elements": [{"type": "button", "text": {"text": "Stop"}}]}],
         "metadata": meta("omniops_status", "r1", state="finished"), "edited_at": 1793095540.0},
        {"ts": "1793095350.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [early],
         "text": "Card", "blocks": [{"type": "header", "text": {"type": "plain_text", "text": "worker OOMKilled"}},
                                    section("*Verdict:* worker exceeds its 64Mi limit &amp; restarts (E3, E5)")],
         "metadata": meta("omniops_card", "r1"), "edited_at": 1793095530.0},
        {"ts": "1793095531.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [], "edited_at": 1793095531.0,
         "text": "Report 1/2", "blocks": [section(f"*Root cause* ... see <{LONG}|Explore query> and <https://g.example/d/a|dash>")],
         "metadata": meta("omniops_report", "r1", page=1)},
        {"ts": "1793095532.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [], "edited_at": 1793095532.0,
         "text": "Report 2/2", "blocks": [section("*Mitigation*\n• stop the worker"),
                                          {"type": "actions", "elements": [{"type": "button", "text": {"text": "Helpful"}}]}],
         "metadata": meta("omniops_report", "r1", page=2)},
        {"ts": "1793095600.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [], "edited_at": 1793095600.0,
         "text": ":repeat: noted", "blocks": [section(":repeat: This alert re-triggered")],
         "metadata": meta("omniops_notice", "r1")},
        {"ts": "1793095700.000100", "user": "U0HUMAN", "text": "thanks!", "edits": []},
    ]


def transcript(msgs: list[dict[str, Any]] | None = None) -> R.SlackTranscript:
    return R.SlackTranscript.from_dicts("C0ARENAOPS", ROOT_TS, msgs if msgs is not None else thread(), bot_user_id=BOT)


def test_final_is_the_latest_runs_card_and_report_as_displayed() -> None:
    final, source = R.render_final(transcript())
    assert source == "slack"
    assert final.index("worker OOMKilled") < final.index("*Root cause*") < final.index("*Mitigation*")
    assert "64Mi limit & restarts" in final                      # Slack entities unescaped, as displayed
    assert "Explore query" in final and LONG not in final          # long link reduced to its label
    assert "[dash](https://g.example/d/a)" in final                # short link kept
    for absent in ("Preliminary", "Finished in", "Investigating", "[Helpful]", "[Stop]", "re-triggered", "FIRING",
                   "thanks"):
        assert absent not in final, absent
    assert final.endswith("\n")


def test_intermediate_keeps_status_trail_superseded_versions_and_other_posts() -> None:
    inter = R.render_intermediate(transcript())
    assert inter.index("(status)\n:mag: Investigating") < inter.index("(superseded)\n*Preliminary:*")
    assert "(status)\n:white_check_mark: Finished in 4 min" in inter
    assert "(other post)\n:repeat: This alert re-triggered" in inter
    assert "*Mitigation*" not in inter and "*Verdict:*" not in inter      # final versions stay in final.txt
    assert inter.splitlines()[0] == "[2026-10-27T10:01:41Z] (status)"     # timestamped from the edit history


def test_an_earlier_runs_answer_is_intermediate_not_final() -> None:
    msgs = thread()
    second = {"ts": "1793096000.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [], "edited_at": 1793096000.0,
              "text": "Card", "blocks": [section("*Verdict:* second run")], "metadata": meta("omniops_card", "r2")}
    final, _ = R.render_final(transcript([*msgs, second]))
    assert "second run" in final and "worker OOMKilled" not in final
    assert "(other post)\nworker OOMKilled" in R.render_intermediate(transcript([*msgs, second]))


def test_without_a_card_the_error_notice_is_the_answer_and_runresult_is_a_labelled_fallback() -> None:
    err = {"ts": "1793095400.000100", "user": BOT, "bot_id": "B0OMNIOPS", "edits": [], "text": "x",
           "blocks": [section(":warning: Something went wrong (run `r1`): timeout")], "metadata": meta("omniops_error", "r1")}
    final, source = R.render_final(transcript([thread()[0], err]))
    assert source == "slack" and "Something went wrong" in final

    result = RunResult(run_id="r1", kind="investigation", answer_markdown="*TL;DR* worker OOM",
                       card=ConclusionCard(title="worker OOMKilled", verdict="Likely memory limit",
                                           mitigation=["stop the worker"]))
    final, source = R.render_final(None, [result])
    assert source == "run_result" and final.startswith("worker OOMKilled\nVerdict: Likely memory limit")
    assert R.render_final(None, []) == ("", "none")


def test_actions_list_every_tool_call_with_full_sql_and_head_tail_digest() -> None:
    big = "\n".join(f"row {i:04d} | worker | OOMKilled" for i in range(400))
    events = [
        ToolEvent(agent="primary", name="search_catalog", arguments={"query": "pod restarts"}, output="k8s_pods ...", ts=2.0),
        ToolEvent(agent="infrastructure", name="run_sql", arguments={"sql": "SELECT name, restarts FROM k8s_pods\n"
                  "WHERE namespace = 'batch'", "purpose": "restarts"}, output=big, evidence_id="E3", elapsed_ms=812, ts=3.0),
        ToolEvent(agent="primary", name="run_sql", arguments={"sql": "SELECT * FROM nope"}, output="Catalog Error: nope",
                  is_error=True, ts=4.0),
    ]
    result = RunResult(run_id="r1", kind="investigation", answer_markdown="x", tool_events=events,
                       staged_writes=[{"id": "w1", "summary": "scale worker to 0"}])
    actions = R.render_actions([result], result_chars=300)
    assert actions.startswith("OmniOps tool activity (runs: r1): 3 tool calls, 2 SQL queries, 1 errors")
    assert "SELECT name, restarts FROM k8s_pods\nWHERE namespace = 'batch'" in actions
    assert "row 0000" in actions and "row 0399" in actions and "characters elided" in actions
    assert "-> E3 (812 ms)" in actions and "ERROR" in actions and '"purpose": "restarts"' in actions
    assert "scale worker to 0" in actions
    assert "zero tool calls" in R.render_actions([RunResult(run_id="r2", kind="chat", answer_markdown="")])


def archive_record(final: str, **over: Any) -> dict[str, Any]:
    rec = {"schema_version": "archive-v1", "run_id": "run1", "case_id": "run1-oom-a1", "product": "OmniOps",
           "scenario": "oom", "suite": "full", "detection": {"status": "not_measured"},
           "final_selection": "operator-selected final delivered answer, imported without rewriting",
           "sources": {"final_answer": final, "intermediate": "status", "actions": "calls"}, "limitations": []}
    rec.update(over)
    return rec


def test_archive_v1_validation_and_case_consistency() -> None:
    final, _ = R.render_final(transcript())
    rec = archive_record(final)
    assert R.validate_archive(rec, scenarios={"oom", "crashloop"}, final_text=final) is rec
    assert R.record_warnings(rec) == []
    with pytest.raises(R.ArchiveError, match="differs from final.txt"):
        R.validate_archive(rec, final_text=final + "edited")
    with pytest.raises(R.ArchiveError, match="explicit detection status"):
        R.validate_archive(archive_record(final, detection={}))
    with pytest.raises(R.ArchiveError, match="unknown scenario"):
        R.validate_archive(archive_record(final, scenario="nope"), scenarios={"oom"})
    with pytest.raises(R.ArchiveError, match="missing text source actions"):
        R.validate_archive(archive_record(final, sources={"final_answer": final, "intermediate": ""}))
    empty = archive_record("", sources={"final_answer": " ", "intermediate": "", "actions": ""})
    assert len(R.record_warnings(R.validate_archive(empty))) == 3
