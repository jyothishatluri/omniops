"""Arena launch messages: what an on-call engineer would see in Slack, never the answer key.

Every Arena investigation starts from Slack (D1). A scenario is launched with one of two messages:

- ``alert``: Grafana alert rules fired after the fault. The firing alerts (polled from Grafana's
  Alertmanager API or received by the local webhook bridge) are rendered exactly like Grafana's Slack
  contact point renders them (attachment title ``[FIRING:n] ...``, the default message template).
- ``customer_report``: nothing alerted. A support-desk message is built *only* from user-visible
  symptoms that code measured: storefront endpoints probed before and after the fault. When nothing
  user-visible changed, the report is deliberately vague. OmniOps triages human posts in an alert
  channel itself; when triage does not pick the report up, the on-call engineer asks the bot in the
  thread, exactly as a person would: ``@OmniOps investigate`` (the runner decides, after a grace
  period, from what the thread shows).

Nothing in this module reads answer keys or fault manifests. ``leak_check`` is a guard the runner
applies to the finished text with judge-only truth it loads separately (defence in depth).

Lessons encoded (OpsIntelligence retrospective):
- 3.1 code measures, the LLM judges: probes, baseline comparison and alert selection are code; the
  message says what was observed (status codes, latencies), not what it means.
- 3.3 one time source: the "since" time is the fault time computed by the harness, and alerts that
  started before it (a previous scenario's leftovers) are excluded and recorded.
- 3.16 untrusted data: launch text carries observed facts only; ground truth stays judge-only
  (Arena integrations: the packet must never reach the investigated product).
- 4.1 zero is a signal: datasource-error alerts are not symptoms; they are excluded loudly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

GRAFANA_ICON = "https://grafana.com/static/assets/img/fav32.png"
GRAFANA_FOOTER = "Grafana v13.2.3"            # the Grafana bundled in grafana/otel-lgtm 0.36.0
FIRING_COLOR = "#D63232"
ALERT_FOLDER = "Platform alerts"              # deploy/arena provisions every rule in this folder
INFRA_ALERTS = {"DatasourceError", "DatasourceNoData"}
SUPPORT_USER = "U0ARENASUPPORT"            # posts customer reports (a human)
ONCALL_USER = "U0ARENAONCALL"              # asks the bot to investigate a human report (a human)
GRAFANA_BOT_ID = "B0ARENAGRAFANA"
INVESTIGATE_REQUEST = "investigate"
SUBJECTS = {"full": "the online shop", "smoke": "the API"}   # how support names the product, per suite

LaunchPath = Literal["alert", "customer_report"]


# ---------------------------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------------------------


@dataclass
class FiringAlert:
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    fingerprint: str = ""
    generator_url: str = ""
    silence_url: str = ""
    dashboard_url: str = ""
    panel_url: str = ""
    values: dict[str, float] = field(default_factory=dict)
    rule_uid: str = ""
    dashboard_uid: str = ""                    # __dashboardUid__ / __panelId__ annotations (Alertmanager API)
    panel_id: str = ""
    state: str = "firing"
    source: str = "alertmanager"               # alertmanager | webhook

    @property
    def name(self) -> str:
        return self.labels.get("alertname", "")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["starts_at"] = self.starts_at.isoformat()
        return d


def alerts_from_alertmanager(payload: Sequence[dict[str, Any]]) -> list[FiringAlert]:
    """Parse ``GET /api/alertmanager/grafana/api/v2/alerts`` (Alertmanager v2 shape)."""
    out = []
    for a in payload:
        status = a.get("status") or {}
        if status.get("state", "active") != "active" or status.get("silencedBy") or status.get("inhibitedBy"):
            continue
        ann = dict(a.get("annotations") or {})
        labels = dict(a.get("labels") or {})
        generator = str(a.get("generatorURL") or "")
        out.append(FiringAlert(
            labels=_public(labels), annotations=_public(ann), starts_at=_parse_time(a.get("startsAt")),
            fingerprint=str(a.get("fingerprint") or ""), generator_url=generator,
            values=_values(ann.get("__values__")),
            rule_uid=str(labels.get("__alert_rule_uid__") or _rule_uid(generator)),
            dashboard_uid=str(ann.get("__dashboardUid__") or ""), panel_id=str(ann.get("__panelId__") or ""),
            source="alertmanager"))
    return out


def alerts_from_webhook(payload: dict[str, Any]) -> list[FiringAlert]:
    """Parse a Grafana webhook contact-point notification (firing alerts only)."""
    out = []
    for a in payload.get("alerts") or []:
        if a.get("status") != "firing":
            continue
        out.append(FiringAlert(
            labels=_public(a.get("labels")), annotations=_public(a.get("annotations")),
            starts_at=_parse_time(a.get("startsAt")), fingerprint=str(a.get("fingerprint") or ""),
            generator_url=str(a.get("generatorURL") or ""), silence_url=str(a.get("silenceURL") or ""),
            dashboard_url=str(a.get("dashboardURL") or ""), panel_url=str(a.get("panelURL") or ""),
            values={k: float(v) for k, v in (a.get("values") or {}).items() if _is_number(v)},
            rule_uid=_rule_uid(str(a.get("generatorURL") or "")), source="webhook"))
    return out


def merge_alerts(*groups: Iterable[FiringAlert]) -> list[FiringAlert]:
    """One alert per fingerprint; the webhook copy wins (it carries Grafana's own URLs and values)."""
    by_key: dict[str, FiringAlert] = {}
    for alert in (a for g in groups for a in g):
        key = alert.fingerprint or json.dumps(alert.labels, sort_keys=True)
        old = by_key.get(key)
        if old is None or (alert.source == "webhook" and old.source != "webhook"):
            by_key[key] = alert
    return sorted(by_key.values(), key=lambda a: (a.starts_at, a.name))


def select_launch_alerts(alerts: Iterable[FiringAlert], since: datetime, *, skew_s: float = 60.0
                         ) -> tuple[list[FiringAlert], list[dict[str, str]]]:
    """Alerts that started after the fault (minus clock skew). Returns (selected, excluded-with-reason)."""
    selected: list[FiringAlert] = []
    excluded: list[dict[str, str]] = []
    floor = since - timedelta(seconds=skew_s)
    for a in alerts:
        if a.name in INFRA_ALERTS:
            excluded.append({"alertname": a.name, "reason": "datasource health alert, not a symptom"})
        elif a.starts_at < floor:
            excluded.append({"alertname": a.name, "reason": f"started {a.starts_at.isoformat()} before the fault"})
        else:
            selected.append(a)
    return selected, excluded


def grafana_slack_message(alerts: Sequence[FiringAlert], *, grafana_url: str, folder: str = ALERT_FOLDER,
                          footer: str = GRAFANA_FOOTER, now: datetime | None = None) -> dict[str, Any]:
    """The Slack payload Grafana's Slack contact point sends for one notification group.

    Mirrors Grafana's ``default.title`` / ``default.message`` templates and attachment layout (title link =
    the common rule URL, else the alert list), with the notification policy grouping every alert of the
    folder together (deploy/arena helper.py ``notifications``).
    """
    if not alerts:
        raise ValueError("no firing alerts to render")
    base = grafana_url.rstrip("/")
    group = {"grafana_folder": folder}
    common = dict(alerts[0].labels)
    for a in alerts[1:]:
        common = {k: v for k, v in common.items() if a.labels.get(k) == v}
    common.setdefault("grafana_folder", folder)
    title = f"[FIRING:{len(alerts)}] {' '.join(v for _, v in sorted(group.items()))} "
    rest = {k: v for k, v in common.items() if k not in group}
    if rest:
        title += "(" + " ".join(v for _, v in sorted(rest.items())) + ")"
    sent = now or datetime.now(UTC)
    body = "**Firing**\n"
    for a in alerts:
        labels = dict(a.labels)
        labels.setdefault("grafana_folder", folder)
        body += "\nValue: " + (", ".join(f"{k}={_go_number(v)}" for k, v in sorted(a.values.items()))
                               if a.values else "[no value]") + "\n"
        body += "Labels:\n" + "".join(f" - {k} = {v}\n" for k, v in sorted(labels.items()))
        body += "Annotations:\n" + "".join(f" - {k} = {v}\n" for k, v in sorted(a.annotations.items()))
        if a.generator_url:
            body += f"Source: {a.generator_url}\n"
        body += f"Silence: {a.silence_url or _silence_url(base, labels, a.rule_uid)}\n"
        dashboard, panel = a.dashboard_url, a.panel_url
        if not dashboard and a.dashboard_uid:
            dashboard, panel = _dashboard_urls(base, a.dashboard_uid, a.panel_id, a.starts_at, sent)
        if dashboard:
            body += f"Dashboard: {dashboard}\n"
        if panel:
            body += f"Panel: {panel}\n"
    ts = int(sent.timestamp())
    sources = {a.generator_url for a in alerts}
    title_link = sources.pop() if len(sources) == 1 and "" not in sources else f"{base}/alerting/list"
    attachment = {"color": FIRING_COLOR, "title": title, "title_link": title_link, "text": body,
                  "fallback": title, "footer": footer, "footer_icon": GRAFANA_ICON, "ts": ts,
                  "mrkdwn_in": ["pretext"]}
    return {"username": "Grafana", "icon_url": GRAFANA_ICON, "text": "", "attachments": [attachment]}


# ---------------------------------------------------------------------------------------------
# User-visible symptoms (customer report path)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Journey:
    path: str                  # GET path on the user-facing service (read-only)
    phrase: str                # how a customer would name it


# What a shopper does on the storefront, and how support would describe it. Read-only GETs only.
STOREFRONT_JOURNEYS = (
    Journey("/", "the storefront home page"),
    Journey("/api/products", "browsing products"),
    Journey("/api/recommendations?productIds=&sessionId=arena-probe&currencyCode=USD", "product recommendations"),
    Journey("/api/cart?sessionId=arena-probe&currencyCode=USD", "viewing the shopping cart"),
)
SMOKE_JOURNEYS = (Journey("/health", "the API"),)


@dataclass
class EndpointProbe:
    path: str
    status: int                # 0 = no response (connection failed / timed out)
    latency_ms: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400


@dataclass
class Symptom:
    phrase: str
    kind: Literal["failing", "unreachable", "slow"]
    detail: str


def user_visible_symptoms(journeys: Sequence[Journey], baseline: Sequence[EndpointProbe] | None,
                          current: Sequence[EndpointProbe]) -> list[Symptom]:
    """Compare the post-fault probe with the healthy baseline; report only what got worse."""
    before = {p.path: p for p in baseline or []}
    now = {p.path: p for p in current}
    out: list[Symptom] = []
    for j in journeys:
        cur = now.get(j.path)
        if cur is None:
            continue
        base = before.get(j.path)
        if base is not None and not base.ok:
            continue                       # already broken while healthy: not a new symptom
        if cur.status == 0:
            out.append(Symptom(j.phrase, "unreachable", "it does not load at all"))
        elif not cur.ok:
            out.append(Symptom(j.phrase, "failing", f"it returns an error (HTTP {cur.status})"))
        elif _slow(cur, base):
            usual = f" instead of the usual {base.latency_ms / 1000:.1f} s" if base else ""
            out.append(Symptom(j.phrase, "slow", f"it is very slow (about {cur.latency_ms / 1000:.1f} s{usual})"))
    return out


def customer_report(symptoms: Sequence[Symptom], *, since: datetime, subject: str) -> str:
    """A support-desk message built only from measured user-visible symptoms."""
    when = since.astimezone(UTC).strftime("%H:%M")
    if not symptoms:
        return (f"Hi team, support desk here. Since around {when} UTC a few customers have said {subject} "
                "seems off, but they could not point to a specific error and nothing has alerted as far as "
                "we know. Can someone check whether anything is unhealthy?")
    lines = "\n".join(f"• {s.phrase}: {s.detail}" for s in symptoms)
    return (f"Hi team, support desk here. Since around {when} UTC customers using {subject} have been "
            f"telling us:\n{lines}\nNothing has alerted on our side as far as we know. Can someone take a look?")


# ---------------------------------------------------------------------------------------------
# The launch message
# ---------------------------------------------------------------------------------------------


@dataclass
class LaunchMessage:
    path: LaunchPath
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    username: str | None = None
    icon_url: str | None = None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    excluded_alerts: list[dict[str, str]] = field(default_factory=list)
    symptoms: list[dict[str, str]] = field(default_factory=list)
    since: str = ""

    def visible_text(self) -> str:
        """Everything a reader (and the bot) can see in the message."""
        parts = [self.text] + [str(a.get(k) or "") for a in self.attachments for k in ("title", "text")]
        return "\n".join(p for p in parts if p)

    @property
    def human_root(self) -> bool:
        """A person posted the root: if OmniOps' triage does not take it up, a person asks the bot."""
        return self.path == "customer_report"

    def slack_event(self, *, channel: str, ts: str) -> dict[str, Any]:
        """The Events API ``message`` event Slack delivers for this post."""
        event: dict[str, Any] = {"type": "message", "channel": channel, "ts": ts, "event_ts": ts,
                                 "channel_type": "channel", "text": self.text}
        if self.path == "alert":
            # Slack stores attachment text HTML-escaped and drops the color's '#'
            attachments = [dict(a, id=i, color=str(a.get("color", "")).lstrip("#"),
                                **{k: _slack_escape(str(a[k])) for k in ("title", "text", "fallback") if k in a})
                           for i, a in enumerate(self.attachments, 1)]
            event.update(subtype="bot_message", bot_id=GRAFANA_BOT_ID, username=self.username or "Grafana",
                         icons={"image_48": self.icon_url or GRAFANA_ICON}, attachments=attachments)
        else:
            event["user"] = SUPPORT_USER
        return event

    @staticmethod
    def mention_text(bot_user_id: str) -> str:
        """What the on-call engineer writes in the report's thread (no content beyond the request)."""
        return f"<@{bot_user_id}> {INVESTIGATE_REQUEST}"

    def mention_event(self, *, channel: str, thread_ts: str, ts: str, bot_user_id: str) -> dict[str, Any]:
        """The ``message`` event of the on-call engineer's thread reply (Slack, and the simulator, also
        deliver the matching ``app_mention``)."""
        return {"type": "message", "channel": channel, "ts": ts, "event_ts": ts, "thread_ts": thread_ts,
                "channel_type": "channel", "user": ONCALL_USER, "text": self.mention_text(bot_user_id)}

    def post_kwargs(self) -> dict[str, Any]:
        """``chat.postMessage`` arguments (username/icon need the chat:write.customize scope)."""
        kw: dict[str, Any] = {"text": self.text}
        if self.attachments:
            kw["attachments"] = self.attachments
        if self.username:
            kw["username"] = self.username
        if self.icon_url:
            kw["icon_url"] = self.icon_url
        return kw

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_launch(alerts: Sequence[FiringAlert], symptoms: Sequence[Symptom], *, since: datetime,
                 grafana_url: str, subject: str, excluded: Sequence[dict[str, str]] = (),
                 now: datetime | None = None) -> LaunchMessage:
    """Alert path when alerts fired after the fault, otherwise the customer-report path."""
    if alerts:
        payload = grafana_slack_message(alerts, grafana_url=grafana_url, now=now)
        return LaunchMessage("alert", payload["text"], payload["attachments"], payload["username"],
                             payload["icon_url"], [a.to_dict() for a in alerts], list(excluded),
                             since=since.isoformat())
    return LaunchMessage("customer_report", customer_report(symptoms, since=since, subject=subject),
                         alerts=[], excluded_alerts=list(excluded), symptoms=[asdict(s) for s in symptoms],
                         since=since.isoformat())


# ---------------------------------------------------------------------------------------------
# Leak guard (applied by the runner to the finished text; inputs are judge-only truth)
# ---------------------------------------------------------------------------------------------

_IDENT = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.:][A-Za-z0-9*]+)+\b|\b[a-z]+[A-Z][A-Za-z0-9]+\b"
                    r"|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b\d+(?:Mi|MiB|Gi|mb|MB)\b")


def forbidden_from_truths(truths: Iterable[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """Phrases (truth sentences of >= 6 words), identifiers and scenario ids that must not leak."""
    phrases: set[str] = set()
    tokens: dict[str, str] = {}                    # lower-case -> first spelling (matching ignores case)
    names: set[str] = set()
    for t in truths:
        if t.get("scenario"):
            names.add(str(t["scenario"]))
        facts = t.get("ground_truth", t)
        for key in ("cause", "impact", "mitigation"):
            text = str(facts.get(key) or "")
            for sentence in re.split(r"(?<=[.;])\s+", text):
                s = sentence.strip().rstrip(".;")
                if len(s.split()) >= 6:
                    phrases.add(s)
            for m in _IDENT.findall(text):
                if len(m) >= 6:
                    tokens.setdefault(m.lower(), m)
    return sorted(phrases), sorted(tokens.values()), sorted(names)


def leak_check(text: str, *, phrases: Iterable[str] = (), tokens: Iterable[str] = (),
               scenario_ids: Iterable[str] = ()) -> list[str]:
    """Return every forbidden phrase / identifier / scenario id found in ``text`` (case-insensitive)."""
    low = text.lower()
    hits = [f"phrase: {p}" for p in phrases if p.lower() in low]
    hits += [f"identifier: {t}" for t in tokens if t.lower() in low]
    hits += [f"scenario id: {n}" for n in scenario_ids if re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", low)]
    return hits


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _public(d: Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in (d or {}).items() if not str(k).startswith("__")}


def _values(raw: Any) -> dict[str, float]:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return {str(k): float(v) for k, v in (data or {}).items() if _is_number(v)}


def _is_number(v: Any) -> bool:
    try:
        float(v)
    except (TypeError, ValueError):
        return False
    return True


def _go_number(v: float) -> str:
    """Go's %v for float64 (Grafana templates print values this way)."""
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e21 else repr(float(v))


def _parse_time(raw: Any) -> datetime:
    if not raw:
        return datetime.now(UTC)
    s = str(raw).replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)            # Go emits nanoseconds; Python takes microseconds
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _silence_url(base: str, labels: dict[str, str], rule_uid: str = "") -> str:
    """Grafana's SilenceURL: the rule uid plus the alert's own labels when the rule is known."""
    if rule_uid:
        pairs = [("__alert_rule_uid__", rule_uid)] + sorted((k, v) for k, v in labels.items()
                                                            if k not in ("alertname", "grafana_folder"))
    else:
        pairs = sorted(labels.items())
    matchers = "&".join("matcher=" + quote(f"{k}={v}", safe="") for k, v in pairs)
    return f"{base}/alerting/silence/new?alertmanager=grafana&{matchers}&orgId=1"


def _dashboard_urls(base: str, uid: str, panel_id: str, starts_at: datetime, now: datetime) -> tuple[str, str]:
    """Grafana's DashboardURL / PanelURL: ``from`` = StartsAt - 1h, ``to`` = notification time (epoch ms)."""
    span = f"from={int((starts_at - timedelta(hours=1)).timestamp() * 1000)}&orgId=1&to={int(now.timestamp() * 1000)}"
    dashboard = f"{base}/d/{quote(uid, safe='')}?{span}"
    return dashboard, (f"{dashboard}&viewPanel={quote(panel_id, safe='')}" if panel_id else "")


_GENERATOR_UID = re.compile(r"/alerting/grafana/([\w-]+)/view")


def _rule_uid(generator_url: str) -> str:
    m = _GENERATOR_UID.search(generator_url)
    return m.group(1) if m else ""


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slow(cur: EndpointProbe, base: EndpointProbe | None) -> bool:
    if base is None:
        return cur.latency_ms > 3000
    return cur.latency_ms > 2000 and cur.latency_ms > max(3 * base.latency_ms, base.latency_ms + 1500)
