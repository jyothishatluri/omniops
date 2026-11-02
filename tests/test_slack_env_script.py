"""scripts/slack_env.py: .env rewriting and value checks (no Slack calls, no real .env)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "slack_env", Path(__file__).resolve().parents[1] / "scripts" / "slack_env.py")
slack_env = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(slack_env)


def test_rewrite_replaces_first_line_drops_duplicates_and_appends() -> None:
    lines = ["# Slack", "SLACK_BOT_TOKEN=", "export SLACK_APP_TOKEN='old'", "MODEL_API_KEY=keep",
             "SLACK_BOT_TOKEN=shadow"]
    out = slack_env.rewrite(lines, {"SLACK_BOT_TOKEN": "xoxb-new", "SLACK_APP_TOKEN": "xapp-new",
                                    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T1/B2/c3"})
    assert out == ["# Slack", "SLACK_BOT_TOKEN=xoxb-new", "SLACK_APP_TOKEN=xapp-new", "MODEL_API_KEY=keep",
                   "SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T1/B2/c3"]


def test_present_keys_ignores_empty_values_and_comments() -> None:
    lines = ["SLACK_BOT_TOKEN=", "SLACK_APP_TOKEN=\"\"", "# SLACK_WEBHOOK_URL=x", "MODEL_API_KEY=abc"]
    assert slack_env.present_keys(lines) == {"MODEL_API_KEY"}


def test_arrow_keys_and_control_characters_are_dropped_from_pasted_values() -> None:
    assert slack_env.TERMINAL_NOISE.sub("", "\x1b[A\x1b[Bxoxb-1-2\r") == "xoxb-1-2"
    assert slack_env.TERMINAL_NOISE.sub("", "\x1b[A") == ""


def test_value_checks_reject_wrong_shapes_before_calling_slack() -> None:
    with pytest.raises(ValueError, match="xoxb-"):
        slack_env.check_bot("xapp-1-2")
    with pytest.raises(ValueError, match="xapp-"):
        slack_env.check_app("xoxb-1-2")
    with pytest.raises(ValueError, match="hooks.slack.com"):
        slack_env.check_webhook("https://example.com/hook")
    assert slack_env.check_webhook("https://hooks.slack.com/services/T0CC/B0CD/abc123")
    assert slack_env.check_channel("C0CCU715ACD,#omniops-alerts")
    with pytest.raises(ValueError):
        slack_env.check_channel("not a channel!")
