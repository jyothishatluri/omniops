"""Paste the Slack values into .env: hidden input, checked against Slack, never printed.

    uv run --no-sync python scripts/slack_env.py

Asks for the OmniOps bot token (xoxb-), its Socket Mode app token (xapp-), the Grafana Alerts app's
incoming webhook URL and the alert channel. Press Enter to keep a value .env already has. Tokens are
checked with auth.test / apps.connections.open (the webhook only by its shape: posting a test message
would land in the alert channel as an alert). .env is rewritten in place with mode 600: each key's
first line is replaced, later duplicates dropped, missing keys appended; other lines are kept as they are.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

ENV = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_CHANNEL = "C0CCU715ACD"          # #omniops-alerts in the test workspace
KEY_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
TERMINAL_NOISE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z~]|[\x00-\x1f\x7f]")   # arrow keys etc. at a hidden prompt
WEBHOOK = re.compile(r"^https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+$")
CHANNEL = re.compile(r"^(C[A-Z0-9]{8,}|#?[a-z0-9][a-z0-9._-]{0,79})$")


def _slack_error(exc: SlackApiError) -> str:
    return str((exc.response.data or {}).get("error") or exc) if exc.response is not None else str(exc)


def check_bot(value: str) -> str:
    if not value.startswith("xoxb-"):
        raise ValueError("a bot token starts with xoxb- (OmniOps > Install App > Bot User OAuth Token)")
    try:
        r = WebClient(token=value).auth_test()
    except SlackApiError as exc:
        raise ValueError(f"Slack rejected it: {_slack_error(exc)}") from None
    return f"workspace {r['team']}, bot @{r['user']}"


def check_app(value: str) -> str:
    if not value.startswith("xapp-"):
        raise ValueError("an app-level token starts with xapp- (OmniOps > Basic Information > App-Level Tokens)")
    try:
        WebClient().apps_connections_open(app_token=value)
    except SlackApiError as exc:
        raise ValueError(f"Slack rejected it: {_slack_error(exc)}") from None
    return "Socket Mode connection allowed"


def check_webhook(value: str) -> str:
    if not WEBHOOK.match(value):
        raise ValueError("expected https://hooks.slack.com/services/T.../B.../... "
                         "(Grafana Alerts > Incoming Webhooks > Copy)")
    return "looks like an incoming webhook URL"


def check_channel(value: str) -> str:
    if not all(CHANNEL.match(c.strip()) for c in value.split(",")):
        raise ValueError("expected channel ids (C0123ABC) or names (#alerts), comma-separated")
    return value


FIELDS: list[tuple[str, str, Callable[[str], str], bool]] = [
    ("SLACK_BOT_TOKEN", "OmniOps bot token (xoxb-...)", check_bot, True),
    ("SLACK_APP_TOKEN", "OmniOps app-level token (xapp-...)", check_app, True),
    ("SLACK_WEBHOOK_URL", "Grafana Alerts webhook URL (https://hooks.slack.com/...)", check_webhook, True),
    ("OMNIOPS_ALERT_CHANNELS", f"Alert channel(s) [{DEFAULT_CHANNEL}]", check_channel, False),
]


def present_keys(lines: list[str]) -> set[str]:
    out = set()
    for line in lines:
        m = KEY_LINE.match(line)
        if m and line.split("=", 1)[1].strip().strip("'\""):
            out.add(m.group(1))
    return out


def ask(key: str, prompt: str, check: Callable[[str], str], secret: bool, have: bool) -> str | None:
    keep = " (Enter keeps the current value)" if have else ""
    while True:
        raw = TERMINAL_NOISE.sub("", (getpass.getpass if secret else input)(f"{prompt}{keep}: ")).strip()
        if not raw:
            if have:
                return None
            if key == "OMNIOPS_ALERT_CHANNELS":
                raw = DEFAULT_CHANNEL
            else:
                print("  required")
                continue
        try:
            print(f"  ok: {check(raw)}")
            return raw
        except ValueError as exc:
            print(f"  not saved: {exc}")


def rewrite(lines: list[str], updates: dict[str, str]) -> list[str]:
    out, done = [], set()
    for line in lines:
        m = KEY_LINE.match(line)
        key = m.group(1) if m else None
        if key in updates:
            if key not in done:
                out.append(f"{key}={updates[key]}")
                done.add(key)
            continue                                    # a later duplicate would shadow the new value
        out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    return out


def main() -> int:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    have = present_keys(lines)
    updates: dict[str, str] = {}
    for key, prompt, check, secret in FIELDS:
        value = ask(key, prompt, check, secret, key in have)
        if value is not None:
            updates[key] = value
    if not updates:
        print("Nothing changed.")
        return 0
    fd, tmp = tempfile.mkstemp(dir=ENV.parent, prefix=".env.")
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(rewrite(lines, updates)) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV)
    print(f"Saved {', '.join(updates)} to {ENV} (mode 600).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; .env was not changed.")
        sys.exit(130)
