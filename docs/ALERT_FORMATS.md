# Alert formats in Slack

How each monitoring platform's notification arrives in Slack, and how `agent/alert.py` turns it into
an `Alert` (`agent/types.py`). Every platform has a fixture in `tests/fixtures/slack_alerts/` (a Slack
Events API `message` event plus the expected fields), checked by `tests/test_alerts_parse.py`.

**Provenance levels** used below:

- **verified**: built from the vendor's published template or source code (exact wording).
- **documented fields**: the fields, links and variables are documented; the exact block/attachment
  layout is reconstructed. The parser keys on the documented parts (URLs, ids, status words), not on
  layout, so a layout change degrades to the LLM fallback instead of a wrong parse.

## How a message becomes an Alert

1. **Eligibility.** `message` or `app_mention` events (also inside an `event_callback` envelope).
   Edits, deletions, joins, topic changes and the bot's own posts are ignored.
2. **Automated or human** (`is_automated`): `subtype == "bot_message"` (incoming webhooks), or a
   `bot_id` / `app_id` / `bot_profile` (Slack apps). Humans go to the customer-report path.
3. **Platform detection** (`detect_platform`): a score per platform from the sender identity
   (`username`, `bot_profile.name`, attachment `author_name`: 4), the title link's domain/path (3),
   any other link (2) and text markers in the footer/title/first lines (2). Highest score >= 2 wins.
4. **Deterministic parser** for that platform fills rule id, rule name, raw status, severity, scope
   (service / env / host / namespace / pod / group), query, threshold, value, trigger time and the
   link window. If it finds none of status, rule id or occurrence id, the format has changed: the
   message goes to the fallback instead of producing a half-empty alert.
5. **Fallback for unknown bots**: the `alert_parse` model (prompt `agent/prompts/alert_parse.md`)
   with the message quoted as untrusted data and a numbered LINKS list. Every value it returns must
   occur in the message (else dropped); its URL must be one of the message's links. Without a model,
   or if the call fails, a regex parse keeps the alert.
6. **Human messages** go to the `triage` model first (uncontrolled text: retrospective 3.1
   exception). Only a genuine, current incident report (confidence >= 0.5) becomes
   `Alert(platform="human", customer_report=True)`, with `reported_onset` when the person says how
   long it has been happening. Keyword fallback without a model.

Payloads are read leniently: a missing or malformed list (attachments, blocks, fields, elements) is
empty and a non-string top-level field (`text`, `ts`, `channel`, `user`, ...) is dropped, so a strange
event degrades to fewer fields, never to an exception.

Slack details the parser handles: `&amp; &lt; &gt;` escaping, `<url|label>` links, `<!date^epoch^...>`
tokens (rendered as UTC), `<@U..>` / `<!channel>` / `<!subteam^..>` mentions, mrkdwn emphasis
(`*x*`, `**x**`) and code, Block Kit (`header`, `section` text and fields, `context`, `actions` URLs,
`rich_text`) and legacy attachments (`pretext`, `title`, `title_link`, `text`, `fields`, `footer`,
`image_url`, `actions`).

### Status vocabulary

`Alert.status` is canonical; the platform's own word is kept in `raw_status`.

| canonical | raw words (case-insensitive) |
|---|---|
| firing | Triggered, Re-Triggered, Renotify, FIRING, ALARM, Alert, Fired, Activated, Open, Active, Critical, Problem, Investigating, Acknowledged, New, Regressed, Declared, Reported ... |
| resolved | Recovered, RESOLVED, OK, Closed, Normal, Inactive, Deactivated, Merged, ResolvedCritical / ResolvedWarning / ResolvedMissingData, Silenced ... |
| warning | Warn, Warning, Re-Warn |
| nodata | No Data, INSUFFICIENT_DATA, MissingData |

`is_recovery(alert)` is `status == "resolved"`: automated recovery notices are skipped, and
`retriggered` marks Re-Triggered / Renotify notices (retrospective Part 5, flapping monitors).

### Flap key

`Alert.key()` is `platform:rule_id-or-name:group`. Parsers choose `group` so it is identical across a
monitor's Triggered -> Recovered -> Re-Triggered cycle: Datadog's `{group}`, the alert's non-meta
labels (sorted `k=v`; with several alerts in one notification only the labels they share), the
Alertmanager subject remainder, the Dynatrace impacted entity, the Azure target resource, the GCP pod.

### Time (retrospective 3.3)

- Link windows come from the notification's own links: `from_ts/to_ts`, `from/to`, `start/end`,
  `startTime/endTime`, `earliest/latest`, Kibana rison `time:(from:..,to:..)`, Dynatrace `gtf=c_<ms>_<ms>`,
  including parameters inside URL fragments. Values are epochs first (unit by magnitude: s / ms / us /
  ns), then ISO 8601. Sliding (`now-1h`), reversed, wider than 24 h, stale (not covering the trigger or
  message time) and future windows are ignored, with the reason in `alert.time_notes`.
- Trigger times: Go/RFC 3339 with any precision, .NET 7-digit fractions, Datadog
  `Tue Oct 20 2026 14:02:00 UTC`, Sumo `10/20/2026 02:02:00 PM UTC`, CloudWatch `20/10/26 14:01:00`.
  Zone abbreviations other than UTC/GMT/Z are refused (no fixed offsets). A trigger time more than 5
  minutes after the message is rejected.
- `agent/timeframe.py` `resolve_anchor(alert, wall_now)` picks the run's window: link window, then
  trigger time, then message time (15 min before to 5 min after, clamped to now). A link window is
  widened to contain the trigger window, so a late notification reaches back to the trigger and a
  short evaluation window ending at the trigger (Kibana, Sumo Logic, Edge Delta) still covers the
  minutes after it; a trigger more than 24 h before the link (a re-notification) does not stretch it.
  Customer reports: 60 min before the report, back to a stated onset. Future starts are rejected, ends
  clamped; no staleness guard. `describe()` renders the shared time rule with local labels from
  `OMNIOPS_DISPLAY_TZ` (tz database).

---

## Datadog

- **Arrives as**: the Datadog app for Slack (`bot_profile.name` "Datadog") or the legacy Slack webhook
  integration (bot message, username "Datadog"). Routed with `@slack-<channel>` or
  `@slack-<account>-<channel>` in the monitor message.
- **Shape**: attachment titled `[<Status> on {<group>}] [<Pn>] <monitor name>` (status `Triggered`,
  `Re-Triggered`, `Recovered`, `Warn`, `No Data`, `Renotify`), linked to the monitor status page; the
  monitor's own message as text (template variables `{{value}}`, `{{threshold}}`,
  `{{last_triggered_at}}` rendered); optional snapshot graph (`image_url`), related tags, recipient
  handles and monitor action buttons (all per-channel options in the integration tile). Notifications
  sent through notification rules can be threaded per alert cycle.
- **Rule id**: `/monitors/<id>` in the title link (any `datadoghq.com` / `datadoghq.eu` /
  `ddog-gov.com` host). `event_id=` becomes `incident_id`.
- **Status / priority**: the bracketed status and `[P1]`..`[P5]`.
- **Window**: `from_ts` / `to_ts` (epoch ms) on the monitor link: 15 minutes before to 5 minutes after
  the trigger. Trigger: the event body's "The monitor was last triggered at Tue Oct 20 2026 14:02:00
  UTC.", or a template's `{{last_triggered_at}}` / `{{last_triggered_at_epoch}}` (epoch ms). These keep
  the first trigger time across re-notifications until the state changes.
- **Scope**: `{host:web-01,env:prod}` group and related tags (`service:`, `env:`, `host:`, ...).
- **Query**: an inline-code expression in the message, e.g. `` `avg(last_5m):...{...} > 90` ``.
- **Fixtures**: `datadog_triggered.json`, `datadog_recovered.json`, `datadog_retriggered.json`.
- **Provenance**: documented fields (docs.datadoghq.com/integrations/slack, monitors/notify/variables).

## Grafana Alerting

- **Arrives as**: Slack contact point, bot message with username "Grafana" (default), footer
  `Grafana v<version>`, color `#D63232` firing / `#36a64f` resolved.
- **Shape (default templates)**: title `[FIRING:<n>(, RESOLVED:<m>)] <group label values> (<common
  label values>)`; text `**Firing**` / `**Resolved**`, then per alert `Value: A=.., C=..`, `Labels:`
  ` - k = v`, `Annotations:` ` - k = v`, `Source: <generatorURL>`, `Silence: <url>`, `Dashboard: <url>`,
  `Panel: <url>`.
- **Rule id**: rule UID from `Source:` `/alerting/grafana/<uid>/view`, else the silence matcher
  `__alert_rule_uid__=<uid>`. The title link is the common GeneratorURL, or `/alerting/list` when the
  alerts differ.
- **Status**: `FIRING` / `RESOLVED` from the subject; `alert_count` from `FIRING:<n>`. With a custom
  title template over the default text, the `**Firing**` / `**Resolved**` section header gives it.
- **Window / trigger**: dashboard and panel links carry `from=<StartsAt - 1h ms>&to=<now or EndsAt
  ms>`, so the onset is `from + 1h` and the link window is the dashboard range.
- **Scope**: labels (`service`, `namespace`, `pod`, `severity`, ...); `group` is the sorted non-meta
  labels (`alertname`, `grafana_folder`, `severity` excluded); `summary` from annotations.
- **Fixtures**: `grafana_firing.json`, `grafana_resolved.json`, `grafana_multi.json`.
- **Provenance**: verified (github.com/grafana/alerting `templates/default_template.go`,
  `templates/template_data.go`, `receivers/slack/v1`).

## Prometheus Alertmanager

- **Arrives as**: `slack_configs`, bot message with username "Alertmanager".
- **Shape (defaults)**: title = `__subject` (`[FIRING:<n>] <group label values> (<common label
  values>)`), title link `<ExternalURL>/#/alerts?receiver=<receiver>`, color `danger` / `good`, empty
  text. Most teams add a text template; two common ones are covered: the documentation's
  CommonAnnotations text (`summary: ...` / `description: ...`) and the community per-alert template
  (`*Alert:*`, `*Graph:* <generatorURL>`, `*Runbook:*`, `*Details:*` with `• *k:* `v``).
- **Rule id**: Prometheus rules have no id; the alertname (first group label value, or the
  `alertname` label) is the identity.
- **Query**: `g0.expr` of the GeneratorURL (`<prometheus>/graph?g0.expr=...&g0.tab=1`); the
  threshold is the comparison at the end of the expression.
- **Window**: none in the defaults; the anchor uses the message time.
- **Scope / group**: labels when the template prints them, else the subject remainder.
- **Fixtures**: `alertmanager_default.json`, `alertmanager_resolved_annotations.json`,
  `alertmanager_custom.json`.
- **Provenance**: verified (prometheus/alertmanager `template/default.tmpl`; prometheus.io
  notification template examples). The per-alert template is a widely used community template.

## Dynatrace

- **Arrives as**: (a) Workflows Slack Connector "Send message" (a Slack app, often named "Dynatrace"),
  (b) classic problem-notification Slack integration (incoming webhook).
- **Shape (Workflows tutorial template)**: header `:warning: OPEN - <event.name>` /
  `:white_check_mark: RESOLVED - ...`; `- *Problem link*: <env>/ui/intent/dynatrace.davis.problems/
  view-problem#{"event.id":"<id>","event.kind":"DAVIS_PROBLEM"}|P-<display id>>`,
  `- *Impacted Entities:* ['SERVICE-...']`, `- *Problem duration:*`, quoted description, workflow link.
- **Shape (classic)**: message template with placeholders, default `{State} Problem {ProblemID}:
  {ImpactedEntity}`; problem title linked to `{ProblemURL}` (`<env>/#problems/problemdetails;pid=<PID>`).
- **Ids / trigger**: the problem id (`pid=`, `/problem/` or URL-encoded `event.id`) ends with the start
  time in epoch ms (`-5147385846571529315_1792504920000V2`): that is the trigger time. The display id
  `P-<n>` is kept as a tag.
- **Status**: OPEN / RESOLVED / MERGED (also ACTIVE / CLOSED). **Scope**: the impacted entity (service
  or host by shape); Impact and Severity fields.
- **Fixtures**: `dynatrace_workflow.json` (verified template), `dynatrace_classic.json` (documented
  fields).

## New Relic

- **Arrives as**: Slack destination in a workflow (New Relic Slack app), default notification or a
  Handlebars template.
- **Shape**: `<Priority> priority issue is <state>` headline, the issue title (`<entity> query result
  is > X for N minutes on '<condition>'`) linked to the issue page, Policy / Condition / Impacted
  entities fields, Acknowledge / Close / View issue buttons.
- **Ids**: issue UUID from `/issues/<uuid>` (`incident_id`); condition id when a `/conditions/<id>`
  link is present. `rule_name` = `<policy> / <condition>`.
- **Status / severity**: issue state (`active`, `acknowledged`, `closed`); priority word.
- **Time**: `createdAt` is epoch ms when a template prints it; the `{{#timezone}}` helper prints
  zone abbreviations, which are refused (no fixed offsets).
- **Fixture**: `newrelic_issue.json`. **Provenance**: documented fields (notification integrations,
  message templates).

## PagerDuty

- **Arrives as**: PagerDuty Slack app. Incident `[#<number>] <title>` linked to
  `https://<subdomain>.pagerduty.com/incidents/<ID>`, Status, Urgency / Priority, Service, Assigned,
  Acknowledge / Resolve buttons.
- **Ids**: incident ID (`incident_id`), incident number tag. **Status**: Triggered / Acknowledged /
  Resolved. **Severity**: priority, else urgency. **Service**: the PagerDuty service.
- **Fixture**: `pagerduty_triggered.json`. **Provenance**: documented fields.

## Opsgenie

- **Arrives as**: Opsgenie Slack app. `#<tinyId>: <message>` linked to
  `https://<org>.app.opsgenie.com/alert/detail/<alertId>/details`, Priority, Source, Tags, Routed Teams,
  action buttons.
- **Ids / trigger**: the alert id ends with the creation time in epoch ms (`<uuid>-1792504920123`).
- **Status**: Open / Acknowledged / Closed (from the lead text or a Status field). **Scope**: `k:v`
  tags. **Fixture**: `opsgenie_alert.json`. **Provenance**: documented fields.

## Sentry

- **Arrives as**: Sentry Slack integration. Issue alerts: the issue title linked to
  `https://<org>.sentry.io/issues/<id>/?referrer=slack&alert_rule_id=<rule>&alert_type=issue`, culprit,
  `State: ... First Seen: ...` context, Resolve / Archive / Assign actions, and the alert rule link
  (`/organizations/<org>/alerts/rules/<project>/<rule id>/details/`). Metric alerts: `Critical: <rule
  name>` / `Resolved: ...` with a `/alerts/rules/details/<id>/` link.
- **Ids**: issue id (`incident_id`), rule id from `alert_rule_id` or the rule link; project slug from
  the rule link (`service`). **Fixture**: `sentry_issue.json`. **Provenance**: documented fields.

## Amazon CloudWatch (via Amazon Q Developer in chat applications, formerly AWS Chatbot)

- **Arrives as**: alarm -> SNS topic -> Amazon Q channel configuration (app "Amazon Q" / "AWS
  Chatbot").
- **Shape**: `CloudWatch Alarm | <alarm name> | <region> | Account: <account>` title linked to the
  console alarm (`#alarmsV2:alarm/<name>`), the state reason, metric fields (Namespace, Metric,
  Dimensions, Period, Statistic), a graph image.
- **Rule id**: alarm name (what `cloudwatch_alarms` filters on). **Status**: the `X -> Y transition`
  in the reason (ALARM / OK / INSUFFICIENT_DATA).
- **Value / threshold / trigger**: from the standard reason `Threshold Crossed: 1 out of the last 1
  datapoints [57.0 (20/10/26 14:01:00)] was greater than or equal to the threshold (25.0)`; the
  datapoint time (dd/MM/yy, UTC) is the trigger time. The metric Namespace is kept as
  `metric_namespace`, never as a Kubernetes namespace; dimensions become tags.
- **Fixtures**: `cloudwatch_alarm.json`, `cloudwatch_ok.json`. **Provenance**: documented fields; the
  reason text is CloudWatch's standard `NewStateReason`.

## Azure Monitor

- **Arrives as**: no native Slack receiver. Action group -> webhook / Logic App / Function posts the
  common alert schema essentials (Logic App Slack connector app).
- **Fields**: Alert rule, Severity (Sev0..Sev4), Monitor condition (Fired / Resolved), Signal type,
  Monitoring service, Fired at (`firedDateTime`, 7 fractional digits), Affected resource
  (`alertTargetIDs`, lower-cased ARM id), Alert ID, portal link.
- **Scope**: resource group, resource type and name from the ARM id; `group` = resource name.
- **Fixture**: `azure_monitor.json`. **Provenance**: documented fields (common alert schema); the Slack
  layout is the relay's own.

## Google Cloud Monitoring

- **Arrives as**: Slack notification channel (app "Google Cloud Monitoring").
- **Shape**: `[ALERT - <severity>] <policy>` / `[RESOLVED ...]`, the generated summary (`<metric> for
  <resource> labels {k=v, ...} with metric labels {k=v} is above the threshold of X with a value of
  Y.` / `returned to normal with a value of Y.`), links to the incident
  (`/monitoring/alerting/incidents/<id>?project=<p>`) and the policy (`/alerting/policies/<id>`).
- **Ids**: policy id (`rule_id`), incident id; project as a tag. **Scope**: resource and metric labels
  (`namespace_name`, `pod_name`, `container_name`, `cluster_name`, ...).
- **Fixture**: `gcp_monitoring.json`. **Provenance**: documented fields (webhook schema 1.2: summary,
  incident.url, policy_name, state, started_at epoch s); the summary format may change, so values are
  read with tolerant patterns.

## Splunk

- **Arrives as**: "Slack Notification Alert" alert action (Splunkbase) on a saved search.
- **Shape**: the configured message (`$name$`, `$result.<field>$` tokens), optionally the first result
  as fields and a link to the results (`/app/search/@go?sid=<sid>`).
- **Ids / trigger**: the search id (`incident_id`); scheduled-search sids embed the scheduled time,
  `..._at_<epoch s>_<n>`. **Scope**: result fields (`host`, `status`, ...); `count` is the value.
- **Fixture**: `splunk_alert.json`. **Provenance**: documented fields.

## Elastic Kibana

- **Arrives as**: Slack connector (webhook: plain text; Web API: text or Block Kit).
- **Shape (default action messages)**: `Elasticsearch query rule '<name>' is active:` / `Rule '<name>'
  is active for group '<group>':`, then `- Value: '<v>'`, `- Conditions Met: '<c>' over '<n>''<unit>'`,
  `- Timestamp: '<ISO>'`, `- Link: '<discover link>'` (quotes are part of the template).
- **Ids / window**: rule id from `/app/discover#/viewAlert/<rule id>?from=<ISO>&to=<ISO>`; the
  from/to (inside the URL fragment) is the evaluated window. **Status**: active / recovered.
- **Fixture**: `kibana_esquery.json`. **Provenance**: verified (Kibana `stack_alerts` rule type
  `defaultActionMessage`); action variables per Elastic docs.

## Honeycomb

- **Arrives as**: trigger with a Slack recipient: the trigger name and status (`.Alert.Status`
  `TRIGGERED` / `OK`), the result versus the threshold (`.Operator`, e.g. "greater than or equal to",
  `.Threshold`), links to the trigger (`.URL`:
  `/<team>/environments/<env>/datasets/<dataset>/triggers/<id>`, environment-wide triggers without the
  dataset) and the query result (`.Result.URL`). `.Alert.Timestamp` is RFC 3339 with nanoseconds.
- **Ids / scope**: trigger id; environment and dataset from the link. **Fixture**:
  `honeycomb_trigger.json`. **Provenance**: documented variables (trigger template variables); the
  native Slack wording is not published.

## Sumo Logic

- **Arrives as**: Slack webhook connection with Sumo's default payload: pretext `{{TriggerType}}
  Alert: {{Name}}`; fields Alert URL, Description, Trigger Time, Time Range, Trigger Condition, Trigger
  Value, Query (`<QueryURL|Query>`).
- **Status / severity**: TriggerType (Critical, Warning, MissingData, ResolvedCritical, ...).
- **Time**: Trigger Time and Time Range in `MM/dd/yyyy hh:mm:ss a UTC`; the time range is the window;
  QueryURL carries `startTime` / `endTime` in epoch ms. **Fixture**: `sumologic_alert.json`.
- **Provenance**: verified payload template (Sumo Logic webhook connection for Slack).

## Coralogix

- **Arrives as**: Slack webhook integration. Alert name linked to Coralogix, description, Severity,
  Application, Subsystem, Alert ID, trigger time; resolved notifications when the condition ends.
- **Scope**: application tag; subsystem as the service. **Fixture**: `coralogix_alert.json`.
  **Provenance**: documented fields.

## Edge Delta

- **Arrives as**: a Slack legacy integration (incoming-webhook app) selected with `@` in the monitor's
  Notifications section. The message is the monitor's markdown notification template; by default it
  carries the monitor name and a scope summary naming the affected group.
- **States**: `ALERT`, `WARNING`, `OK`, `NO DATA`, `RECOVERED` (templates branch with `{{#is_alert}}`,
  `{{#is_recovery}}`, `{{#is_no_data}}`, ... and usually lead with a bold label such as `**ALERT**:`).
- **Ids / links**: `https://app.edgedelta.com/monitors/{{monitor.id}}` gives the rule id; its link text
  (`{{monitor.name}}`) the rule name.
- **Values**: `{{value}}`, `{{comparator}}`, `{{alert_threshold}}`; group-by attributes such as
  `{{service.name}}`, `{{k8s.namespace.name}}`; `{{evaluated_from}}` / `{{evaluated_to}}` (the window)
  and `{{last_triggered_at}}` / `{{first_triggered_at}}` (the trigger). The docs do not state how time
  variables render; any format `parse_time_text` reads (epochs included) is accepted.
- **Fixtures**: `edgedelta_monitor.json`, `edgedelta_recovered.json`. **Provenance**: documented
  variables and states (Monitor Notifications); the line layout is the template author's.

## Grafana OnCall / Grafana IRM

- **Arrives as**: the Grafana OnCall / IRM Slack app (`bot_profile.name` "Grafana OnCall" or "Grafana
  IRM"). Default Slack title template `*<grafana_oncall_link|#<n> <title>>* via <integration>` plus
  `(*<source_link|source>*)`; message template with status, labels, annotations; Acknowledge / Resolve
  / Silence buttons.
- **Ids**: alert group public id from `/a/grafana-irm-app/alert-groups/<id>` (or
  `grafana-oncall-app`), group number tag, rule UID from the source link to Grafana Alerting.
- **Fixture**: `grafana_oncall.json`. **Provenance**: documented fields.

## incident.io

- **Arrives as**: incident.io Slack app announcement / incident channel posts: `INC-<n>: <name>`
  linked to `https://app.incident.io/<org>/incidents/<n>`, Status (Triage, Investigating, Fixing,
  Monitoring, Closed ...), Severity, lead, custom fields such as Affected services, declared time
  (`<!date^epoch^..>`).
- **Fixture**: `incident_io.json`. **Provenance**: documented fields.

## Human messages and unknown bots

- `human_customer_report.json`: a support engineer's report; the triage model decides; the onset is
  the message time minus the stated duration. `human_chatter.json`: not a report, no alert.
- `unknown_bot.json`: an in-house bot; the `alert_parse` model extracts fields, an invented rule id
  and a URL that is not in the message are dropped, and an instruction-like line in the body is
  treated as data.

## Adding a platform

1. Capture a real Slack event (redact ids and hosts) into `tests/fixtures/slack_alerts/<name>.json`
   with `platform`, `description`, `provenance`, `event` and `expected`.
2. Add a `_Platform(...)` entry (identity, link shapes, text markers) and a `_p_<name>` parser that
   reads ids from URLs first, then fields, then text. Return at least a raw status, rule id or
   occurrence id, or the message falls back to the model.
3. Add a section here. Run `uv run --no-sync pytest tests/test_alerts_* -q`.
