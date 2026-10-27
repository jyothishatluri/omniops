"""Slack Block Kit rendering against Slack's limits (4.4): cards, paged reports, approvals, text view."""

from __future__ import annotations

import json
import re
from typing import Any

from omniops.agent.types import ChangeRef, ConclusionCard, ImpactSummary
from omniops.slack.render import (
    ACTION_APPROVE_WRITE,
    ACTION_CANCEL_WRITE,
    ACTION_SHOW_REPORT,
    NOTIFICATION_MAX,
    PAGE_MAX_BLOCKS,
    SECTION_TEXT_MAX,
    approval_blocks,
    blocks_to_text,
    card_blocks,
    insufficient_data_pages,
    notification_text,
    report_pages,
    text_chunks,
    to_mrkdwn,
    validate_blocks,
)


def _card(**kw: Any) -> ConclusionCard:
    card = ConclusionCard(
        run_id="run-7", title="Checkout 5xx above 5%", status="final", confidence=0.82, causal_level="trigger",
        verdict="The root cause is the strict token flag enabled at 14:02 UTC [E3].",
        root_cause="Argo CD sync 4f1c2ab set paymentStrictTokenCheck to on; payment rejects tokens [E2, E3].",
        impact=ImpactSummary(level="high", summary="Checkout fails for 38.5% of requests [E4]",
                             affected=["payment", "checkout"], evidence_ids=["E4"]),
        trigger_change=ChangeRef(title="Enable strict token check", url="https://argocd.test/app?rev=4f1c2ab",
                                 author="ops-bot", version="4f1c2ab", ts="2026-10-01 14:02 UTC"),
        mitigation=["Set paymentStrictTokenCheck to off in shop/flagd-config", "Verify checkout 5xx < 1% [E4]"],
        hypotheses=[{"id": f"H{i}", "statement": f"hypothesis {i}", "confidence": 0.9 - i * 0.1, "status": "open",
                     "evidence_ids": ["E2"]} for i in range(1, 6)]
        + [{"id": "H9", "statement": "refuted one", "confidence": 0.95, "status": "refuted"}],
        evidence_links=[{"label": f"E{i}: panel {i}", "url": f"https://grafana.test/d/{i}?a=1&b=2"}
                        for i in range(1, 15)],
        gaps=["Tempo was unavailable"])
    return card.model_copy(update=kw)


def _sections(blocks: list[dict[str, Any]]) -> list[str]:
    return [b["text"]["text"] for b in blocks if b["type"] == "section" and b.get("text")]


def _action_ids(blocks: list[dict[str, Any]]) -> list[str]:
    return [e["action_id"] for b in blocks if b["type"] == "actions" for e in b["elements"]]


# ---------------------------------------------------------------------------------------------
# Conclusion card
# ---------------------------------------------------------------------------------------------


def test_card_blocks_are_valid_and_verdict_first() -> None:
    blocks = card_blocks(_card())
    assert validate_blocks(blocks) == []
    json.dumps(blocks)
    assert blocks[0]["type"] == "header" and "Checkout 5xx above 5%" in blocks[0]["text"]["text"]
    assert blocks[1]["text"]["text"].startswith("*Verdict:* The root cause is")
    fields = next(b for b in blocks if b.get("fields"))["fields"]
    assert any("82% (high)" in f["text"] for f in fields)
    assert any("trigger (the concrete change or fault)" in f["text"] for f in fields)
    text = "\n".join(_sections(blocks))
    assert "<https://argocd.test/app?rev=4f1c2ab|Enable strict token check>" in text
    assert "4f1c2ab · ops-bot · 2026-10-01 14:02 UTC" in text
    assert "• Set paymentStrictTokenCheck to off in shop/flagd-config" in text
    # top three non-refuted hypotheses with their confidence
    assert "`H1` 80%" in text and "`H3` 60%" in text and "`H4`" not in text and "H9" not in text
    # at most ten evidence links, escaped ampersands inside the URL are kept usable
    ev = next(s for s in _sections(blocks) if s.startswith("*Evidence*"))
    assert ev.count("<https://") == 10 and "panel 11" not in ev
    ids = _action_ids(blocks)
    assert ids == ["omniops_show_report", "omniops_feedback_up", "omniops_feedback_down"]
    assert len(ids) == len(set(ids))
    assert all(e["value"] == "run-7" for b in blocks if b["type"] == "actions" for e in b["elements"])


def test_card_escapes_text_and_survives_huge_fields() -> None:
    card = _card(verdict="p99 > 2s & rising <script> " + "x" * 5000, root_cause="r " * 4000,
                 mitigation=["m" * 4000] * 8, title="T" * 400)
    blocks = card_blocks(card)
    assert validate_blocks(blocks) == []
    assert "&gt; 2s &amp; rising &lt;script&gt;" in blocks[1]["text"]["text"]
    assert all(len(s) <= SECTION_TEXT_MAX for s in _sections(blocks))


def test_insufficient_data_and_preliminary_cards() -> None:
    card = _card(status="insufficient_data", confidence=0.0, verdict="Insufficient data: nothing is a guess.",
                 root_cause="", trigger_change=None, hypotheses=[], gaps=["Check access to loki_logs"])
    blocks = card_blocks(card)
    assert validate_blocks(blocks) == []
    text = blocks_to_text(blocks)
    assert "What a human should check" in text and "Confidence" not in text
    assert _action_ids(blocks)[0] == ACTION_SHOW_REPORT
    pre = card_blocks(_card(status="preliminary"))
    assert _action_ids(pre) == ["omniops_stop"]


# ---------------------------------------------------------------------------------------------
# Paged report
# ---------------------------------------------------------------------------------------------


def _huge_report() -> str:
    parts = ["## TL;DR", "**The root cause** is a [bad flag](https://grafana.test/d/x?a=1&b=2) [E3].", ""]
    for i in range(120):
        parts += [f"*Section {i}*", f"- bullet {i} with p99 > 2s & a link <https://grafana.test/d/{i}|panel {i}>",
                  f"Paragraph {i}: " + " ".join(f"word{i}_{j}" for j in range(60)), ""]
    parts += ["*Wide table*", "| service | 5xx | p99 |", "|---|---:|---|"]
    parts += [f"| svc-{i} | {i}.5% | {i * 10}ms |" for i in range(150)]
    parts += ["", "*Long line*", "token " * 1200, "", "```sql", *[f"SELECT {i} FROM t;" for i in range(400)], "```",
              "", "*End marker* FINAL-LINE"]
    return "\n".join(parts)


def test_huge_report_pages_within_slack_limits_and_never_clips() -> None:
    md = _huge_report()
    pages = report_pages(md, run_id="run-7")
    assert len(pages) > 3
    for n, page in enumerate(pages, 1):
        assert validate_blocks(page) == [], f"page {n}"
        assert len(page) <= PAGE_MAX_BLOCKS
        assert all(len(s) <= SECTION_TEXT_MAX for s in _sections(page))
        json.dumps(page)
        has_buttons = any(b["type"] == "actions" for b in page)
        assert has_buttons is (n == len(pages))                         # buttons only on the last page
    assert _action_ids(pages[-1]) == ["omniops_feedback_up", "omniops_feedback_down"]
    assert "Page 1 of" in blocks_to_text(pages[0])
    text = blocks_to_text(pages)
    # nothing is clipped: every paragraph word, table row, SQL line and the last line survive
    for i in (0, 57, 119):
        assert f"word{i}_59" in text and f"[panel {i}](https://grafana.test/d/{i})" in text
    assert "SELECT 399 FROM t;" in text and "FINAL-LINE" in text
    assert text.count("token") >= 1200
    assert "p99 > 2s & a link" in text                                   # entities unescaped for readers
    assert "[bad flag](https://grafana.test/d/x?a=1&b=2)" in text
    # pipe tables degrade to monospace code blocks; markdown headings/bold become mrkdwn
    raw = "\n".join(s for p in pages for s in _sections(p))
    assert "|---" not in raw and "**" not in raw and not re.search(r"^#", raw, re.MULTILINE)
    assert re.search(r"```\nservice\s+\| 5xx\s+\| p99", raw)
    assert all(row in text for row in ("svc-0", "svc-149 | 149.5%"))
    assert raw.count("```") % 2 == 0


def test_short_report_is_one_page_and_round_trips() -> None:
    md = ("*TL;DR*\nThe most likely contributing factor is the flag [E3] <https://a.test/x?y=1|flag diff>.\n\n"
          "*Mitigation*\n1. Turn `paymentStrictTokenCheck` off.\n2. Verify 5xx < 1%.")
    pages = report_pages(md, run_id="run-7", header="Full report")
    assert len(pages) == 1 and pages[0][0]["type"] == "header"
    text = blocks_to_text(pages)
    assert text.startswith("Full report\n\n*TL;DR*\nThe most likely contributing factor is the flag [E3] "
                           "[flag diff](https://a.test/x?y=1).")
    assert "1. Turn `paymentStrictTokenCheck` off.\n2. Verify 5xx < 1%." in text
    assert "Page 1" not in text
    assert report_pages(md, buttons=False, run_id="r")[0][-1]["type"] != "actions"


def test_insufficient_data_pages_and_text_chunks() -> None:
    md = ":grey_question: *Insufficient data to determine a cause*\n\n*What was tried*\n• E1 alert: 1 rows"
    pages = insufficient_data_pages(md, run_id="run-7")
    assert pages[0][0]["type"] == "header" and "Insufficient data" in pages[0][0]["text"]["text"]
    assert validate_blocks(pages[0]) == []
    chunks = text_chunks(_huge_report())
    assert all(len(c) <= SECTION_TEXT_MAX for c in chunks) and "FINAL-LINE" in chunks[-1]


def test_to_mrkdwn_normalises_markdown() -> None:
    out = to_mrkdwn("# Title\n**bold** and [x](https://x.test)\n- item\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert out.splitlines()[:3] == ["*Title*", "*bold* and <https://x.test|x>", "• item"]
    assert "```\na | b\n--+--\n1 | 2\n```" in out


# ---------------------------------------------------------------------------------------------
# Notification text, approvals, validation
# ---------------------------------------------------------------------------------------------


def test_notification_text_is_short() -> None:
    note = notification_text(_card(verdict="v " * 5000))
    assert len(note) <= NOTIFICATION_MAX and note.endswith("…")
    assert notification_text(markdown="*TL;DR*\nThe flag broke payment.\n\nMore.").startswith("*TL;DR* The flag")
    assert notification_text(markdown="") == "OmniOps"


def test_approval_blocks_carry_the_write_id_and_redact_secrets() -> None:
    write = {"id": "w-123", "table": "grafana_dashboards", "summary": "Add panel 'checkout 5xx' to dashboard abc",
             "filters": {"uid": "abc", "api_key": "glsa_supersecret", "panel": {"title": "checkout 5xx"}},
             "expires": 1_790_000_000, "requested_by": "<@U123>"}
    blocks = approval_blocks(write)
    assert validate_blocks(blocks) == []
    actions = next(b for b in blocks if b["type"] == "actions")
    assert [e["action_id"] for e in actions["elements"]] == [ACTION_APPROVE_WRITE, ACTION_CANCEL_WRITE]
    assert all(e["value"] == "w-123" for e in actions["elements"])
    assert actions["elements"][0]["confirm"]["confirm"]["text"] == "Approve"
    dumped = json.dumps(blocks)
    assert "glsa_supersecret" not in dumped and "[redacted]" in dumped
    assert "expires 2026-" in blocks_to_text(blocks) and "grafana_dashboards" in blocks_to_text(blocks)


def test_validate_blocks_catches_what_slack_rejects() -> None:
    bad = [{"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3001}},
           {"type": "actions", "elements": [{"type": "button", "action_id": "a", "text": {"text": "1"}, "value": ""},
                                            {"type": "button", "action_id": "a", "text": {"text": "2"}, "value": ""}]},
           {"type": "bogus"}]
    problems = validate_blocks(bad + [{"type": "divider"}] * 50)
    assert any("3001" in p for p in problems)
    assert any("not unique" in p for p in problems)
    assert any("unknown type bogus" in p for p in problems)
    assert any("> 50" in p for p in problems)


def test_blocks_to_text_renders_what_a_user_sees() -> None:
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Title"}},
              {"type": "section", "text": {"type": "mrkdwn", "text": "a &lt; b &amp; <https://x.test|link> <@U1>"},
               "accessory": {"type": "button", "text": {"type": "plain_text", "text": "Open"}}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": "ctx <!here>"}]},
              {"type": "divider"}]
    assert blocks_to_text(blocks) == "Title\n\na < b & [link](https://x.test) @U1\n[Open]\n\nctx @here\n\n---"


def test_mentions_and_broadcasts_from_model_or_tool_text_never_go_live() -> None:
    """Quoted data (a log line, ticket or alert body) must not mass-ping: only links survive escaping."""
    from omniops.slack.render import escape_mrkdwn

    payload = ("Loki shows the payload: <!channel> deploy now <!here> <!everyone> <!subteam^S0ONCALL|@oncall> "
               "<@U123> <#C1|ops> see <https://grafana.test/d/x|dashboard> or <mailto:ops@acme.test|ops>")
    out = escape_mrkdwn(payload)
    for token in ("<!channel>", "<!here>", "<!everyone>", "<!subteam^", "<@U123>", "<#C1"):
        assert token not in out
    assert "&lt;!channel&gt;" in out and "&lt;!subteam^S0ONCALL|@oncall&gt;" in out
    assert "<https://grafana.test/d/x|dashboard>" in out and "<mailto:ops@acme.test|ops>" in out

    dumped = json.dumps(report_pages(f"*TL;DR* {payload}") + [card_blocks(_card(
        verdict="Log line says <!here> restart", root_cause="<!channel> x", mitigation=["ping <!subteam^S1>"],
        hypotheses=[{"id": "H1", "statement": "<@U9> did it", "confidence": 0.9, "status": "open"}]))])
    for token in ("<!channel>", "<!here>", "<!subteam^", "<@U123>", "<@U9>"):
        assert token not in dumped
    assert "<!here>" not in notification_text(_card(verdict="Log line says <!here> restart"))
    assert "requested by <@U123>" in json.dumps(approval_blocks({"id": "w1", "requested_by": "<@U123>"}))
