# Amazon CloudWatch Logs — source `cloudwatch_logs`

Manifest: `src/omniops/sources/manifests/cloudwatch_logs.yaml` · Fixtures: `src/omniops/sources/fixtures/cloudwatch_logs/`
API reference: https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/Welcome.html

## Tables

| SQL table | HTTP request (AWS JSON 1.1, `POST /`) | Official reference |
|---|---|---|
| `cloudwatch_logs_insights` | step 1 `X-Amz-Target: Logs_20140328.StartQuery` (capture `queryId`) → step 2 `Logs_20140328.GetQueryResults`, polled until `status = Complete`, paged by `nextToken` | https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_StartQuery.html · https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_GetQueryResults.html |
| `cloudwatch_logs_events` | `Logs_20140328.FilterLogEvents` | https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_FilterLogEvents.html |
| `cloudwatch_logs_log_groups` | `Logs_20140328.DescribeLogGroups` | https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_DescribeLogGroups.html |
| `cloudwatch_logs_log_streams` | `Logs_20140328.DescribeLogStreams` (`orderBy: LastEventTime`, `descending: true`) | https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_DescribeLogStreams.html |
| `cloudwatch_logs_fields` | `Logs_20140328.GetLogGroupFields` | https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_GetLogGroupFields.html |
| `cloudwatch_logs_anomalies` | `Logs_20140328.ListAnomalies` | https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_ListAnomalies.html |

Roles bound: `logs` and `log_counts` → `cloudwatch_logs_insights` (default query selects log groups
with `SOURCE logGroups(namePrefix: [...])` built from the service name).

## Protocol and auth

`POST https://logs.<region>.amazonaws.com/`, `Content-Type: application/x-amz-json-1.1`,
`X-Amz-Target: Logs_20140328.<Operation>` — shown verbatim in every API reference "Sample Request".
SigV4 with signing name `logs`; credentials from the standard AWS chain (`omniops[aws]`).
Service model: protocol `json`, jsonVersion `1.1`, targetPrefix `Logs_20140328`.
Errors are HTTP 400 with `__type` (`ThrottlingException`, `LimitExceededException`,
`MalformedQueryException`, `ResourceNotFoundException`, `InvalidParameterException`);
throttling / concurrent-query-limit bodies are retried via `retry_body_patterns`.

## Base URLs / regions

`https://logs.<region>.amazonaws.com` (China `.amazonaws.com.cn`, FIPS `logs-fips.<region>.amazonaws.com`);
override with `AWS_ENDPOINT_URL_CLOUDWATCH_LOGS`. Log groups are regional. Cross-account: pass log
group ARNs (`logGroupIdentifiers` / `logGroupIdentifier`) from a monitoring account.

## Rate limits and query limits

| Item | Limit (per account per region) |
|---|---|
| StartQuery / GetQueryResults | 10 TPS each |
| FilterLogEvents | 10 TPS (25 in us-east-1; 5 in ap-northeast-3, ap-southeast-3, ca-west-1, eu-central-1, il-central-1) |
| DescribeLogGroups | 10 TPS |
| DescribeLogStreams | 25 TPS |
| Concurrent Logs Insights queries | 100 for Logs Insights QL (shared with dashboards and scheduled queries), 15 for PPL/SQL |
| Log groups per StartQuery | 50 (`logGroupIdentifiers`), or `SOURCE logGroups(...)` with ≤ 5 name prefixes (≤ 10,000 groups) |
| Results | `limit` ≤ 100,000 (CWLI), ≤ 10,000 per GetQueryResults page via `nextToken` (CWLI only; token expires after 1 h) |
| Query runtime | queries time out after 60 minutes; results kept 7 days |

Sources: https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/cloudwatch_limits_cwl.html,
the StartQuery / GetQueryResults / DescribeLogStreams reference pages.

## Retention

Per log group (`retentionInDays`; absent = never expire). The manifest declares no global clamp;
check `cloudwatch_logs_log_groups.retention_days` before concluding old logs are absent. DELIVERY-class
groups keep events one day and are not queryable.

## Time formats (the #1 trap of this source)

| Where | Unit |
|---|---|
| StartQuery `startTime` / `endTime` | **epoch seconds** (inclusive). Note: StartQuery Example 3 in the docs passes millisecond values; the parameter descriptions say seconds, which is what the manifest sends |
| GetLogGroupFields `time` | **epoch seconds** — centre of a ±8 minute sample window (default: last 15 minutes) |
| FilterLogEvents `startTime` / `endTime` | **epoch milliseconds** |
| FilterLogEvents `timestamp`, `ingestionTime`; DescribeLogGroups `creationTime`; DescribeLogStreams `*Timestamp`/`creationTime`; ListAnomalies `firstSeen`/`lastSeen` | epoch milliseconds |
| GetQueryResults `@timestamp` value | string `YYYY-MM-DD HH:MM:SS.mmm` (UTC) |

## Pitfalls

- Field names are case-sensitive; unknown fields are null, not errors (`filter level = "ERROR"` vs
  `"level":"error"` → 0 rows). `cloudwatch_logs_fields` lists the real field names (sampled ±8 min).
- `stats ... by bin(1m)` omits empty bins: alias `count(*) as value ... by bin(1m) as ts` and remember gaps mean zero.
- Results are capped by `limit` (default 1000 here): an event list is a sample; `records_matched` shows the true total.
- `GetQueryResults` returns partial results while `Running`/`Scheduled`; the manifest polls until
  `Complete` and fails loudly on `Failed`/`Cancelled`/`Timeout`/`Unknown` (or after 120 s). A `Failed`
  body is also an error rule (fixture-tested).
- Logs Insights bills per GB scanned; the engine's positive control is bounded with `control_lookback: 1h`
  (re-query window 1–7 h).
- `FilterLogEvents`: filter-pattern terms are case-sensitive; pages may be partially full or empty
  while `nextToken` is present; default order is oldest-first — the manifest sends
  `startFromHead=false` (newest first), which AWS only accepts for `startTime` ≥ 2024-01-01.
- `FilterLogEvents` returns the original events of transformed log groups; Insights returns transformed ones.
  INFREQUENT_ACCESS groups support Insights but not FilterLogEvents.
- DescribeLogGroups with `logGroupNamePattern` returns only `arn`, `creationTime`, `logGroupName`.
  The docs example uses `storageBytes`, the schema field is `storedBytes` (the manifest reads both).
- `logGroupNamePrefix` and `logGroupNamePattern` are mutually exclusive and case-sensitive.
- DescribeLogStreams: `lastEventTimestamp` is eventually consistent (usually < 1 h); `storedBytes` is
  deprecated (always 0); `logStreamNamePrefix` cannot be combined with `orderBy: LastEventTime`.
- ListAnomalies has no time filter (filter `ts`/`last_seen` in SQL) and only returns results for log
  groups with an anomaly detector.
- With X-Ray Transaction Search enabled, all spans live in the `aws/spans` log group — query them here.

## Fixtures

| File | Provenance |
|---|---|
| `get_query_results.json` | GetQueryResults sample response (API reference) with the literal `...` lines removed |
| `get_query_results_system_fields.json` | Built strictly from the GetQueryResults schema + documented system fields (@timestamp, @message, @logStream, @log, @ptr) |
| `get_query_results_stats.json` | Built strictly from the GetQueryResults schema for a `stats ... as value by bin(1m) as ts` query |
| `get_query_results_failed.json` | Built from the schema with `status: Failed` (expect_error) |
| `filter_log_events.json` | FilterLogEvents "most recent events in reverse order" sample response (API reference) |
| `describe_log_groups.json` | DescribeLogGroups "To list all log groups" sample response; two missing commas added so it parses |
| `describe_log_streams.json` | DescribeLogStreams "To list the log streams for a log group" sample response |
| `get_log_group_fields.json` | GetLogGroupFields sample response |
| `list_anomalies.json` | ListAnomalies sample response; truncated doc sample closed, invalid `\E` escape written as `\t`, patternTokens cut to 3 per anomaly |
