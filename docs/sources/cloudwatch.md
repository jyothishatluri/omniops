# Amazon CloudWatch (metrics & alarms) — source `cloudwatch`

Manifest: `src/omniops/sources/manifests/cloudwatch.yaml` · Fixtures: `src/omniops/sources/fixtures/cloudwatch/`
API reference: https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/Welcome.html

## Tables

| SQL table | HTTP request | Official reference |
|---|---|---|
| `cloudwatch_metrics_insights` | `POST /` · `X-Amz-Target: GraniteServiceVersion20100801.GetMetricData` · one `MetricDataQuery` with `Expression` (Metrics Insights SQL or `SEARCH()`) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html |
| `cloudwatch_metric_data` | `POST /` · `GetMetricData` · one `MetricDataQuery` with `MetricStat` (namespace, metric, dimensions via `kv_list`, stat, period, unit, AccountId) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html · https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_MetricDataQuery.html |
| `cloudwatch_metrics` | `POST /` · `GraniteServiceVersion20100801.ListMetrics` | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_ListMetrics.html |
| `cloudwatch_alarms` | `POST /` · `GraniteServiceVersion20100801.DescribeAlarms` (all three `AlarmTypes`) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_DescribeAlarms.html · https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_MetricAlarm.html |
| `cloudwatch_alarm_contributors` | `POST /` · `GraniteServiceVersion20100801.DescribeAlarmContributors` | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_DescribeAlarmContributors.html |
| `cloudwatch_alarm_mute_rules` | `POST /` · `GraniteServiceVersion20100801.ListAlarmMuteRules` | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_ListAlarmMuteRules.html |
| `cloudwatch_alarm_history` | `POST /` · `GraniteServiceVersion20100801.DescribeAlarmHistory` | https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_DescribeAlarmHistory.html |
| `cloudwatch_promql_query_range` | `POST /api/v1/query_range` (form body `query, start, end, step`) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL-API-QueryMetrics.html |
| `cloudwatch_promql_query` | `POST /api/v1/query` (form body `query, time`) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL-API-QueryMetrics.html |
| `cloudwatch_promql_series` | `POST /api/v1/series` (form body `match[], start, end`) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL-API-GetSeries.html |
| `cloudwatch_promql_labels` | `POST /api/v1/labels` | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL-API-GetLabels.html |
| `cloudwatch_promql_label_values` | `GET /api/v1/label/{label}/values` (label URL-encoded, `@` → `%40`) | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL-API-GetLabels.html |

Roles bound: `metrics` → `cloudwatch_metrics_insights` (Metrics Insights / SEARCH) and
`cloudwatch_promql_query_range` (PromQL); `active_alerts` → `cloudwatch_alarms` (state = ALARM);
`alert_rules` → `cloudwatch_alarms` (`expression` column = what the alarm evaluates);
`alert_history` → `cloudwatch_alarm_history` (StateUpdate).

## Protocol and auth

- Classic API (signing name / endpoint prefix `monitoring`, API version 2010-08-01) now serves three
  protocols: AWS Query (form/XML, the classic one), **AWS JSON 1.0** and Smithy RPC v2 CBOR
  ([protocol FAQ](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch-protocols-faq.html)).
  boto3/botocore ≥ 1.42.7, AWS CLI v2 ≥ 2.32.14, JS SDK v3 ≥ 3.949.0, .NET v3, PHP, Ruby and
  PowerShell use AWS JSON 1.0; Java/Go/Rust/Kotlin/Swift/C++ use CBOR. The service model confirms
  `protocols: [smithy-rpc-v2-cbor, json, query]`, `jsonVersion: 1.0`,
  `targetPrefix: GraniteServiceVersion20100801`, `awsQueryCompatible`
  ([botocore model](https://github.com/boto/botocore/blob/develop/botocore/data/cloudwatch/2010-08-01/service-2.json)).
  Only documented behavioural difference: `null` entries inside lists are propagated in JSON (dropped in Query).
- We send: `POST https://monitoring.<region>.amazonaws.com/`, `Content-Type: application/x-amz-json-1.0`,
  `X-Amz-Target: GraniteServiceVersion20100801.<Operation>`, JSON body exactly as in each operation's
  "Request Syntax", SigV4 (`service: monitoring`, `region: AWS_REGION`, credentials from the standard
  AWS chain — needs `omniops[aws]`/botocore).
- PromQL API on the same host: Prometheus-compatible paths, GET or POST (form-encoded) for
  query/query_range/series/labels, GET only for `label/<name>/values`; SigV4 with service
  `monitoring`. IAM: `cloudwatch:GetMetricData` + `cloudwatch:ListMetrics` for queries,
  `cloudwatch:ListMetrics` for discovery. The manifest uses POST + form body for every path that
  allows it (see engine gap below).
- Errors: HTTP 4xx with `__type` in the JSON protocol; throttling is **HTTP 400 `ThrottlingException`**
  (not 429) — retried via `rate_limit.retry_body_patterns`. PromQL limits return 422 / 429.

## Base URLs / regions

`https://monitoring.<region>.amazonaws.com` (China: `.amazonaws.com.cn`; FIPS:
`monitoring-fips.<region>.amazonaws.com`). Override with `AWS_ENDPOINT_URL_CLOUDWATCH`
(VPC endpoints, LocalStack). Everything is regional; cross-account observability works from a
monitoring account (`IncludeLinkedAccounts`, `OwningAccount`, MetricDataQuery `AccountId`,
Metrics Insights `AWS.AccountId`). PromQL querying is available in 31 commercial regions
(list on https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL.html).

## Rate limits (per account per region)

| API | Limit |
|---|---|
| GetMetricData | 500 TPS; **Metrics Insights 10 expressions/s**, **SEARCH 50/s**; 180,000 datapoints/s for StartTime within 3 h, 396,000/s older; Metrics Insights 4,300,000 datapoints/s |
| GetMetricData request size | ≤ 500 `MetricDataQuery`, ≤ 100,800 datapoints per call (then `NextToken`) |
| ListMetrics | 25 TPS |
| DescribeAlarms / DescribeAlarmContributors | 9 TPS each |
| DescribeAlarmHistory | 20 TPS |
| ListAlarmMuteRules | 50 TPS |
| PromQL | 300 query TPS, 10 discovery TPS, 30 concurrent each; 500 series per result, 10,000 labels per discovery call, 7-day max range (incl. lookbacks), 100,000 series / 300M samples scanned per 24 h window, 20 s execution |

Sources: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_limits.html,
https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-PromQL.html

## Retention and resolution

Sub-minute (high-resolution) points: 3 hours · 1-minute: 15 days · 5-minute: 63 days · 1-hour: 455 days
(15 months); older data is only available aggregated. Metrics Insights and `SEARCH()` see only the
last **two weeks**; `MetricStat` works over the full 455 days. Alarm history survives alarm deletion.
OTLP / PromQL metric retention is not stated in the PromQL docs (open question); the manifest's
source-level 455 d clamp applies and windows are capped at 6 days per request.

## Time formats

- Classic requests: `StartTime`, `EndTime`, `StartDate`, `EndDate` are timestamps → **epoch seconds**
  numbers in the JSON protocol (docs examples: `"StartTime": 1637061900`).
- Classic responses: `Timestamps` arrays and `*Timestamp` fields are epoch seconds (the AWS CLI prints
  them as ISO-8601; the `auto_time` transform accepts both).
- `StartTime` is inclusive and rounded down (to the minute < 15 days, 5 minutes 15–63 days, hour
  beyond); `EndTime` is exclusive.
- `Period` must be 60 or a multiple of 60 (1/5/10/20/30 only for high-resolution metrics); the
  manifest rounds the engine's auto step up to a multiple of 60.
- PromQL: `start`/`end`/`time` accept RFC 3339 or Unix seconds (the manifest sends Unix seconds);
  `step` a duration or float seconds; sample timestamps are float Unix seconds.

## Pitfalls (what silently returns empty or wrong data)

- A metric = namespace + name + **exact full dimension set**; a partial dimension set returns 0 rows.
  Use `cloudwatch_metrics.dimension_spec` (ready-made `Name=Value,...`) for `cloudwatch_metric_data`.
- A `Unit` that the data was not published with returns nothing.
- Periods with no data are omitted (no zero-fill); for count metrics missing ≈ 0, for latency missing = no traffic.
- Metrics Insights: AVG/COUNT/MAX/MIN/SUM only (no percentiles), WHERE supports only `=`, `!=`, `AND`,
  10,000 metrics scanned, 500 series returned, **GROUP BY results are alphabetical unless ORDER BY** —
  always `ORDER BY MAX() DESC LIMIT n` for top-N questions.
- `SEARCH()` does partial token matching; use `MetricName="..."` for exact names.
- `MetricDataResults[].StatusCode` other than `Complete` (PartialData / InternalError / Forbidden)
  means missing points; operation-level `Messages` are turned into a loud error (fixture-tested).
- `DescribeAlarms` / `DescribeAlarmHistory` return **only metric alarms** unless `AlarmTypes` is set;
  the manifest always requests `MetricAlarm`, `CompositeAlarm`, `LogAlarm`.
- PromQL alarms and Metrics Insights GROUP BY alarms are multi-time-series: which series breached is in
  `cloudwatch_alarm_contributors` (only contributors currently in ALARM).
- Alarm **mute rules** silence actions without changing state: check `cloudwatch_alarm_mute_rules` when
  nobody got paged.
- `ChildrenOfAlarmName` / `ParentsOfAlarmName` cannot be combined with other filters.
- Composite-alarm data needs `cloudwatch:DescribeAlarms` / `DescribeAlarmHistory` scoped to `*`.
- `StateValue` is current state; incident timelines come from `cloudwatch_alarm_history`.
- PromQL sees only OTLP-ingested metrics and vended metrics with **OTel enrichment** enabled; every
  selector must name its metric; names with dots/`@` must be quoted inside braces; raw histogram
  series come back as native histograms (no float samples → rows dropped) — use `histogram_*`
  functions; `rate()` on **delta** histograms is silently wrong (use `sum_over_time(x[5m]) / 5m`).
- PromQL results over 500 series are truncated with HTTP 200 + `warnings`; the manifest turns
  `warnings` into a loud error (fixture-tested) so a truncated result is never read as complete.

## Engine gap found

SigV4 signing (`http.py::_sigv4`) passes the httpx-encoded URL to botocore, whose
`_canonical_query_string_url` uses the encoded query string verbatim. httpx encodes spaces as `+`,
while AWS canonicalizes with RFC 3986 `%20`, so a SigV4 **GET whose query values contain spaces**
(PromQL) will very likely fail with `SignatureDoesNotMatch`. Workaround here: POST + form body for
query/query_range/series/labels (documented by AWS). `label/<name>/values` is GET-only: keep `match`
free of spaces. Fix in the engine: pass `params` to `AWSRequest` (or re-encode the query with
`quote(..., safe='-_.~')`) before signing.

## Fixtures

| File | Provenance |
|---|---|
| `get_metric_data_insights.json` | GetMetricData Example 1 sample response (API reference) |
| `get_metric_data_messages.json` | Built from the GetMetricData schema; message text quoted from the Messages description (expect_error) |
| `get_metric_data_metricstat.json` | GetMetricData Example 3 sample response (API reference) |
| `list_metrics.json` | AWS CLI v2 reference example output for `list-metrics` (API reference has request examples only) |
| `describe_alarms.json` | AWS CLI v2 reference example output for `describe-alarms` |
| `describe_alarms_promql.json` | Built from the DescribeAlarms / AlarmPromQLCriteria schema (PromQL metric alarm + composite alarm) |
| `describe_alarm_contributors.json` | Built from the DescribeAlarmContributors / AlarmContributor schema |
| `list_alarm_mute_rules.json` | ListAlarmMuteRules "List all mute rules" sample response (API reference) |
| `describe_alarm_history.json` | AWS CLI v2 reference example output for `describe-alarm-history` |
| `promql_query.json` | QueryMetrics sample response (CloudWatch PromQL docs) |
| `promql_query_truncated.json` | QueryMetrics truncated sample (placeholder replaced by `[]`; expect_error) |
| `promql_query_range.json` | Prometheus HTTP API range-query example (CloudWatch documents "the standard Prometheus JSON envelope" but shows no matrix example) |
| `promql_series.json`, `promql_labels.json`, `promql_label_values.json` | GetSeries / GetLabels sample responses (CloudWatch PromQL docs) |
