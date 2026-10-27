# AWS CloudTrail — source `cloudtrail`

Manifest: `src/omniops/sources/manifests/cloudtrail.yaml` · Fixtures: `src/omniops/sources/fixtures/cloudtrail/`
API reference: https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/Welcome.html

## Tables

| SQL table | HTTP request (AWS JSON 1.1, `POST /`) | Official reference |
|---|---|---|
| `cloudtrail_events` | `X-Amz-Target: com.amazonaws.cloudtrail.v20131101.CloudTrail_20131101.LookupEvents`; `CloudTrailEvent` JSON string parsed (`expand parse: true`) | https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_LookupEvents.html |
| `cloudtrail_lake_query` | step 1 `...CloudTrail_20131101.StartQuery` (capture `QueryId`) → step 2 `...CloudTrail_20131101.GetQueryResults`, polled until `QueryStatus = FINISHED`, paged by `NextToken` | https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_StartQuery.html · https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_GetQueryResults.html |

Role bound: `change_events` → `cloudtrail_events` with `read_only = 'false'` (mutating API calls:
deployments, configuration, IAM, scaling, network changes); `status` maps to `error_code`.

## Protocol and auth

AWS JSON 1.1: `POST https://cloudtrail.<region>.amazonaws.com/`,
`Content-Type: application/x-amz-json-1.1`,
`X-Amz-Target: com.amazonaws.cloudtrail.v20131101.CloudTrail_20131101.<Operation>`
(service model: protocol `json`, jsonVersion 1.1, that targetPrefix —
https://github.com/boto/botocore/blob/develop/botocore/data/cloudtrail/2013-11-01/service-2.json).
SigV4 signing name `cloudtrail`; credentials from the AWS chain (`omniops[aws]`).

## Base URLs / regions

`https://cloudtrail.<region>.amazonaws.com` (FIPS `cloudtrail-fips.<region>.amazonaws.com`, China
`.amazonaws.com.cn`); override with `AWS_ENDPOINT_URL_CLOUDTRAIL`. Event history is per region; global
services (IAM, CloudFront, Route 53, STS global endpoint) are recorded in us-east-1.

## Rate limits

LookupEvents: **2 requests per second per account per region** (hard limit, "a throttling error
occurs" — HTTP 400 `ThrottlingException`). Up to 50 events per page (`MaxResults` 1–50). The manifest
runs one request at a time, ≤ 100 requests/minute, retries only throttling bodies
(`retry_body_patterns`: ThrottlingException / Rate exceeded / MaxConcurrentQueriesException) and stops
after 6 pages (300 newest events). CloudTrail Lake: max 10 concurrent queries.

## Retention

Event history: 90 days of **management** events (and CloudTrail Insights events for trails with
Insights enabled, via `EventCategory = insight`). Data events are not available through LookupEvents.
CloudTrail Lake: per event data store retention (up to years); **Lake is closed to new customers since
2026-05-31** (existing customers continue) — https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-lake-service-availability-change.html

## Time formats

- LookupEvents `StartTime` / `EndTime`: timestamps → **epoch seconds** in the JSON protocol (inclusive).
- Response `EventTime`: epoch seconds (docs example `1424476529.0`); `CloudTrailEvent` is the full
  record as a JSON **string** whose `eventTime` is ISO-8601 (`2015-02-20T23:55:29Z`).
- Lake: time bounds live inside the SQL (`eventTime > '2026-10-01 09:00:00'`); result values are strings
  (`eventTime` like `2026-10-01 11:41:07.000`, parsed by `auto_time`).

## Lookup semantics (important)

- `LookupAttributes` may contain **only one** attribute. The manifest sends the first present of
  `resource_name`, `resource_type`, `event_id`, `event_name`, `username`, `access_key_id`,
  `event_source`, `read_only`; all other predicates are applied in SQL to the returned rows (they are
  real columns), so results stay correct but may be truncated by the page cap — use a selective one.
- Insights events support only EventId / EventName / EventSource lookups; with `event_category =
  'insight'` the other attributes are not sent.
- `resource_name` / `resource_type` are lookup-only (an event can affect several resources); the
  returned lists are `resource_names` / `resource_types`. If both are given, only `resource_name` is sent.
- `event_name LIKE ...` / `!=` are not sent to AWS (exact match only) and are evaluated in SQL.
- `ReadOnly` is the string `"true"`/`"false"` on the wire; exposed as BOOLEAN `read_only`.

## Pitfalls

- Busy accounts: `read_only = false` alone is dominated by automation (autoscaling, CloudFormation,
  service-linked roles, log agents). Filter `invoked_by IS NULL`, `user_type <> 'AWSService'`,
  `username`/`event_name` in SQL, or push `event_source` / `resource_name`.
- Assumed-role callers: `username` is the role **session** name; the role itself is `session_issuer`.
- Lambda event names carry API-version suffixes (`UpdateFunctionCode20150331v2`,
  `UpdateFunctionConfiguration20150331v2`); ECS deploys are `UpdateService` / `RegisterTaskDefinition`;
  CodeDeploy `CreateDeployment`; CloudFormation `UpdateStack` / `ExecuteChangeSet`.
- Failed calls are recorded too — `error_code IS NOT NULL` means nothing changed.
- Events typically appear within about 5 minutes of the call.

## Fixtures

| File | Provenance |
|---|---|
| `lookup_events.json` | The returned event of "Viewing recent management events with the AWS CLI" (https://docs.aws.amazon.com/awscloudtrail/latest/userguide/view-cloudtrail-events-cli.html); the doc pretty-prints `CloudTrailEvent` over several lines (invalid JSON) — re-serialized as the single-line string the API returns, content unchanged |
| `lookup_events_change.json` | Built strictly from the LookupEvents response schema, wrapping the documented `StopInstances` record from https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-log-file-examples.html (AWS example key, no secrets) |
| `lookup_events_assumed_role_failed.json` | Built from the LookupEvents schema and the documented record fields (AssumedRole userIdentity with sessionIssuer, errorCode/errorMessage); values illustrative |
| `lake_get_query_results.json` | Built strictly from the GetQueryResults schema (QueryResultRows = array of arrays of string maps); no documented example |
