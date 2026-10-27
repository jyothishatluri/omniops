"""Artifact leads: extraction from raw output, own-platform filtering, follow-up, disposition and gaps."""

from __future__ import annotations

from omniops.agent.evidence import EvidenceBoard
from omniops.agent.leads import LeadTracker, platforms_of
from omniops.agent.types import EvidenceRecord

RAW = """
change event: rolled out by https://github.com/acme/shop/actions/runs/987654321, PR https://github.com/acme/shop/pull/42
commit https://github.com/acme/shop/commit/0a1b2c3d4e5f60718293a4b5c6d7e8f901234567 (tag v1.4.2)
MR https://gitlab.example.com/platform/payments/-/merge_requests/17 pipeline https://gitlab.example.com/platform/payments/-/pipelines/5512
argo https://argocd.example.com/applications/argocd/checkout?view=tree
ticket OPS-4521 (see https://acme.atlassian.net/browse/PAY-77), runbook https://acme.atlassian.net/wiki/spaces/SRE/pages/123456/Checkout+5xx
thread https://acme.slack.com/archives/C024BE91L/p1727784000123456 page https://acme.pagerduty.com/incidents/Q1ABCD2EFGH3IJ
approved change CHG0031337
noise: UTF-8 HTTP-500 ISO-8601 SEV-1 sha256 abcdef
"""


def kinds(tracker: LeadTracker) -> dict[str, str]:
    return {x.ref: x.kind for x in tracker.all()}


def test_every_cross_platform_reference_is_extracted_once() -> None:
    t = LeadTracker()
    new = t.scan(RAW, origin=["loki_logs", "loki"], evidence_id="E3")
    assert kinds(t) == {
        "github:acme/shop/runs/987654321": "github_run",
        "github:acme/shop#42": "github_pr",
        "github:acme/shop@0a1b2c3": "github_commit",
        "gitlab:platform/payments!17": "gitlab_mr",
        "gitlab:platform/payments/pipelines/5512": "gitlab_pipeline",
        "argocd:checkout": "argocd_app",
        "jira:PAY-77": "jira_issue",
        "jira:OPS-4521": "jira_issue",
        "confluence:123456": "confluence_page",
        "slack:C024BE91L/1727784000.123456": "slack_message",
        "pagerduty:Q1ABCD2EFGH3IJ": "pagerduty_incident",
        "change:CHG0031337": "change",
    }
    assert [x.id for x in new] == [f"L{i}" for i in range(1, 13)]
    assert all(x.seen_in == ["E3"] and x.status == "unfollowed" for x in new)
    run = t.resolve("github:acme/shop/runs/987654321")
    assert run is not None and run.url == "https://github.com/acme/shop/actions/runs/987654321"
    # Seeing the same references again adds a sighting, never a second lead.
    assert t.scan(RAW, origin=["loki_logs"], evidence_id="E4") == [] and run.seen_in == ["E3", "E4"]


def test_own_platform_references_do_not_count_and_unconfigured_platforms_are_unresolvable() -> None:
    assert platforms_of(["github_commits", "argocd_applications", "jira_issues"]) == {"github", "argocd", "jira"}
    t = LeadTracker(available_platforms={"github", "jira"})
    t.scan(RAW, origin=["github_workflow_runs", "github"], evidence_id="E1")
    assert not any(x.platform == "github" for x in t.all())
    statuses = {x.ref: x.status for x in t.all()}
    assert statuses["jira:OPS-4521"] == "unfollowed" and statuses["argocd:checkout"] == "unresolvable"
    restricted = LeadTracker(jira_projects=["OPS"])
    restricted.scan("OPS-1 and ABC-2", evidence_id="E1")
    assert kinds(restricted) == {"jira:OPS-1": "jira_issue"}


def test_followed_by_later_sql_or_own_platform_data_but_not_by_its_own_record() -> None:
    t = LeadTracker()
    t.scan(RAW, origin=["loki_logs"], evidence_id="E1")
    assert t.observe(sql="SELECT * FROM github_commits WHERE sha LIKE '0a1b2c3%'", evidence_id="E1") == []
    followed = t.observe(sql="SELECT * FROM github_commits WHERE sha LIKE '0a1b2c3%'", evidence_id="E2")
    assert [x.ref for x in followed] == ["github:acme/shop@0a1b2c3"] and followed[0].followed_by == "E2"
    # Data returned by the lead's own platform follows it even when the SQL did not name it.
    via_data = t.observe(sql="SELECT * FROM jira_issues WHERE project = 'OPS'", text="key=OPS-4521 status=Done",
                         origin=["jira_issues"], evidence_id="E3")
    assert [x.ref for x in via_data] == ["jira:OPS-4521"]
    # Data from another platform that merely mentions the key does not.
    assert t.observe(sql="SELECT 1", text="CHG0031337", origin=["loki_logs"], evidence_id="E4") == []
    assert t.resolve("change:CHG0031337").status == "unfollowed"  # type: ignore[union-attr]


def test_unfollowed_block_dismissal_and_report_gaps() -> None:
    t = LeadTracker(available_platforms={"github", "jira"}, max_leads=3)
    t.scan(RAW, origin=["loki_logs"], evidence_id="E1")
    assert len(t.all()) == 3 and t.dropped == 9
    block = t.unfollowed_block()
    assert block.startswith("UNFOLLOWED ARTIFACT LEADS") and "silence is not a disposition" in block
    assert "- L1 [GitHub Actions run] https://github.com/acme/shop/actions/runs/987654321 (seen in E1)" in block
    assert t.dismiss("L2", "") is None                                   # a reason is required
    assert t.dismiss("https://github.com/acme/shop/pull/42", "PR only bumps docs").status == "dismissed"  # type: ignore[union-attr]
    t.observe(sql="SELECT * FROM github_workflow_runs WHERE run_id = 987654321", evidence_id="E2")
    gaps = t.gaps()
    assert gaps[0] == "Artifact lead L2 (GitHub pull request https://github.com/acme/shop/pull/42) was not followed " \
                      "(dismissed: PR only bumps docs)."
    assert gaps[1].startswith("Artifact lead L3 (GitHub commit") and gaps[1].endswith("was not followed, seen in E1.")
    assert gaps[2] == "9 further artifact reference(s) were not tracked (cap 3)."
    t.dismiss("L3", "same release as the run")
    assert t.unfollowed_block() == ""


def test_unresolvable_leads_are_grouped_per_platform_in_gaps() -> None:
    t = LeadTracker(available_platforms=set())
    t.scan("OPS-1 OPS-2 OPS-3 OPS-4 OPS-5 OPS-6 CHG0012345", evidence_id="E1")
    assert t.unfollowed_block() == ""
    assert t.gaps() == [
        ("6 artifact lead(s) could not be followed: no jira source is configured (L1 OPS-1, L2 OPS-2, L3 OPS-3, "
         "L4 OPS-4, L5 OPS-5 and 1 more)."),
        "1 artifact lead(s) could not be followed: no change source is configured (L7 CHG0012345)."]


def test_board_sync_scans_seed_records_once_and_skips_derived_kinds() -> None:
    board = EvidenceBoard()
    seed = board.add(EvidenceRecord(id="", kind="seed", agent="seed", title="change events", summary="1 change",
                                    rendered="| title | url |\n| deploy | https://github.com/acme/shop/pull/42 |",
                                    tables=["datadog_events"]))
    board.add(EvidenceRecord(id="", kind="specialist", agent="code_change", title="verdict",
                             summary="found: PR https://github.com/acme/shop/pull/99 broke it"))
    board.add(EvidenceRecord(id="", kind="query", agent="primary", title="failed", summary="FAILED",
                             error="boom", rendered="https://github.com/acme/shop/pull/7"))
    t = LeadTracker()
    assert t.sync_board(board) == 1 and [x.ref for x in t.all()] == ["github:acme/shop#42"]
    assert t.all()[0].seen_in == [seed.id] and t.all()[0].agents == {"seed"}
    follow = board.add(EvidenceRecord(id="", kind="deepening", agent="deepening", title="pr files", summary="ok",
                                      sql="SELECT * FROM github_pull_files WHERE repo = 'acme/shop' AND number = 42",
                                      tables=["github_pull_files"]))
    assert t.sync_board(board) == 1 and t.all()[0].status == "followed" and t.all()[0].followed_by == follow.id
    assert t.sync_board(board) == 0
