# Supported sources

Generated from `src/omniops/sources/manifests/*.yaml` by `scripts/gen_sources_doc.py`; do not edit by hand.

**56 sources, 806 SQL tables.** A source is enabled when its required environment variables are set (see `.env.example`). Every table wraps a documented REST endpoint; the model discovers tables with `search_catalog`, reads `describe_table`, and queries them with SQL.

## Overview

| Platform | Source id | Tables | Roles | Required / secret env | Notes |
|---|---|---:|---|---|---|
| Amazon Managed Service for Prometheus | `amazon_prometheus` | 11 | active_alerts, alert_rules, metrics | `AMP_WORKSPACE_ID`, `AMP_REGION` | [API docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-Prometheus-Compatible-Apis.html) · [notes](sources/amazon_prometheus.md) |
| AWS CloudTrail (event history) | `cloudtrail` | 2 | change_events | `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | [API docs](https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/Welcome.html) · [notes](sources/cloudtrail.md) |
| Amazon CloudWatch (metrics & alarms) | `cloudwatch` | 12 | active_alerts, alert_history, alert_rules, metrics | `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | [API docs](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/Welcome.html) · [notes](sources/cloudwatch.md) |
| Amazon CloudWatch Logs | `cloudwatch_logs` | 6 | log_counts, logs | `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | [API docs](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/Welcome.html) · [notes](sources/cloudwatch_logs.md) |
| AWS X-Ray | `xray` | 9 | service_map, services, trace_detail, traces | `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | [API docs](https://docs.aws.amazon.com/xray/latest/api/Welcome.html) · [notes](sources/xray.md) |
| Argo CD | `argocd` | 8 | change_events | `ARGOCD_URL`, `ARGOCD_TOKEN` | [API docs](https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/) · [notes](sources/argocd.md) |
| Confluence | `confluence` | 5 | docs | `CONFLUENCE_URL`, `CONFLUENCE_API_TOKEN` | [API docs](https://developer.atlassian.com/cloud/confluence/rest/v2/intro/) · [notes](sources/confluence.md) |
| Jira | `jira` | 22 | active_alerts, alert_history, incidents, oncall, tickets | `JIRA_URL`, `JIRA_API_TOKEN` | [API docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/) · [notes](sources/jira.md) |
| Opsgenie | `opsgenie` | 24 | active_alerts, alert_history, incidents, oncall, services | `OPSGENIE_API_KEY` | [API docs](https://docs.opsgenie.com/docs/api-overview) · [notes](sources/opsgenie.md) |
| Chronosphere (Cortex XCOR) | `chronosphere` | 9 | active_alerts, alert_history, alert_rules, change_events, metrics | `CHRONOSPHERE_DOMAIN`, `CHRONOSPHERE_API_TOKEN` | [API docs](https://docs.chronosphere.io/tooling/prometheus-api) · [notes](sources/chronosphere.md) |
| Splunk AppDynamics (Controller REST API) | `appdynamics` | 14 | active_alerts, alert_history, alert_rules, change_events, errors, hosts, incidents, metrics, services, trace_detail, traces | `APPD_CONTROLLER_URL`, `APPD_ACCOUNT_NAME`, `APPD_API_CLIENT_NAME`, `APPD_API_CLIENT_SECRET` | [API docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis) · [notes](sources/appdynamics.md) |
| Jaeger | `jaeger` | 6 | service_map, services, trace_detail, traces | `JAEGER_URL`, `JAEGER_TOKEN`, `JAEGER_PASSWORD` | [API docs](https://www.jaegertracing.io/docs/latest/architecture/apis/) · [notes](sources/jaeger.md) |
| Coralogix | `coralogix` | 12 | active_alerts, alert_history, alert_rules, incidents, log_counts, logs, metrics, slo, traces | `CORALOGIX_API_KEY`, `CORALOGIX_DOMAIN` | [API docs](https://coralogix.com/docs/dataprime/API/direct-archive-query-http/) · [notes](sources/coralogix.md) |
| Datadog | `datadog` | 49 | active_alerts, alert_history, alert_rules, change_events, dashboards, errors, hosts, incidents, k8s_events, k8s_state, log_counts, logs, metrics, oncall, rum, service_map, services, slo, trace_detail, traces | `DD_API_KEY`, `DD_APP_KEY`, `DD_BEARER_TOKEN` | [API docs](https://docs.datadoghq.com/api/latest/) · [notes](sources/datadog.md) |
| Dynatrace (Environment API v2) | `dynatrace` | 17 | active_alerts, alert_history, change_events, hosts, incidents, log_counts, logs, metrics, service_map, services, slo | `DT_ENV_URL`, `DT_API_TOKEN` | [API docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api) · [notes](sources/dynatrace.md) |
| Dynatrace Grail (DQL) | `dynatrace_grail` | 10 | active_alerts, alert_history, change_events, hosts, incidents, log_counts, logs, metrics, service_map, services, trace_detail, traces | `DT_PLATFORM_URL`, `DT_PLATFORM_TOKEN` | [API docs](https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1) · [notes](sources/dynatrace_grail.md) |
| Edge Delta | `edgedelta` | 13 | active_alerts, alert_history, alert_rules, change_events, errors, hosts, k8s_events, log_counts, logs, metrics, services, traces | `ED_API_TOKEN`, `ED_ORG_ID` | [API docs](https://docs.edgedelta.com/api/) · [notes](sources/edgedelta.md) |
| Elasticsearch | `elasticsearch` | 17 | active_alerts, alert_history, errors, hosts, log_counts, logs, metrics, service_map, services, trace_detail, traces | `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`, `ELASTICSEARCH_PASSWORD` | [API docs](https://www.elastic.co/docs/api/doc/elasticsearch/) · [notes](sources/elasticsearch.md) |
| Kibana | `kibana` | 15 | alert_rules, change_events, incidents, slo | `KIBANA_URL`, `KIBANA_API_KEY`, `KIBANA_PASSWORD` | [API docs](https://www.elastic.co/docs/api/doc/kibana/) · [notes](sources/kibana.md) |
| Sentry | `sentry` | 26 | alert_rules, change_events, dashboards, errors, log_counts, logs, metrics, rum, services, trace_detail, traces | `SENTRY_AUTH_TOKEN`, `SENTRY_ORG` | [API docs](https://docs.sentry.io/api/) · [notes](sources/sentry.md) |
| GitHub | `github` | 23 | change_events, code_changes, tickets | `GITHUB_TOKEN` | [API docs](https://docs.github.com/en/rest?apiVersion=2022-11-28) · [notes](sources/github.md) |
| GitLab | `gitlab` | 22 | change_events, code_changes | `GITLAB_TOKEN` | [API docs](https://docs.gitlab.com/api/rest/) · [notes](sources/gitlab.md) |
| Google Cloud Error Reporting | `gcp_error_reporting` | 4 | errors | `GCP_PROJECT_ID` | [API docs](https://docs.cloud.google.com/error-reporting/reference/rest) · [notes](sources/gcp_error_reporting.md) |
| Google Cloud Logging | `gcp_logging` | 4 | change_events, k8s_logs, logs | `GCP_PROJECT_ID` | [API docs](https://docs.cloud.google.com/logging/docs/reference/v2/rest) · [notes](sources/gcp_logging.md) |
| Google Cloud Monitoring | `gcp_monitoring` | 9 | active_alerts, alert_history, alert_rules, log_counts, metrics, services, slo | `GCP_PROJECT_ID` | [API docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest) · [notes](sources/gcp_monitoring.md) |
| Google Cloud Trace | `gcp_trace` | 3 | trace_detail, traces | `GCP_PROJECT_ID` | [API docs](https://docs.cloud.google.com/trace/docs/reference/v1/rest) · [notes](sources/gcp_trace.md) |
| Google Cloud Managed Service for Prometheus | `google_prometheus` | 7 | metrics | `GMP_PROJECT_ID` | [API docs](https://cloud.google.com/stackdriver/docs/managed-prometheus/query-api-ui) · [notes](sources/google_prometheus.md) |
| Prometheus via Grafana | `grafana_prometheus` | 9 | active_alerts, alert_rules, hosts, metrics | `GRAFANA_URL`, `GRAFANA_TOKEN`, `GRAFANA_PASSWORD` | [API docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#data-source-proxy-calls) · [notes](sources/grafana_prometheus.md) |
| Grafana | `grafana` | 29 | active_alerts, alert_history, alert_rules, change_events, dashboards | `GRAFANA_URL`, `GRAFANA_TOKEN`, `GRAFANA_PASSWORD` | [API docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/) · [notes](sources/grafana.md) |
| Grafana Loki (via Grafana datasource proxy) | `grafana_loki` | 15 | active_alerts, alert_rules, log_counts, logs, services | `GRAFANA_URL`, `GRAFANA_TOKEN`, `GRAFANA_PASSWORD` | [API docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/) · [notes](sources/grafana_loki.md) |
| Grafana Tempo (via Grafana datasource proxy) | `grafana_tempo` | 9 | services, trace_detail, traces | `GRAFANA_URL`, `GRAFANA_TOKEN`, `GRAFANA_PASSWORD` | [API docs](https://grafana.com/docs/tempo/latest/api_docs/) · [notes](sources/grafana_tempo.md) |
| Grafana Loki | `loki` | 15 | active_alerts, alert_rules, log_counts, logs, services | `LOKI_URL`, `LOKI_PASSWORD`, `LOKI_TOKEN` | [API docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/) · [notes](sources/loki.md) |
| Grafana Mimir / Grafana Cloud Metrics | `mimir` | 14 | active_alerts, alert_rules, metrics | `MIMIR_URL`, `MIMIR_PASSWORD`, `MIMIR_TOKEN` | [API docs](https://grafana.com/docs/mimir/latest/references/http-api/) · [notes](sources/mimir.md) |
| Grafana Pyroscope | `pyroscope` | 8 | profiles, services | `PYROSCOPE_URL`, `PYROSCOPE_PASSWORD`, `PYROSCOPE_TOKEN` | [API docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/) · [notes](sources/pyroscope.md) |
| Grafana Tempo | `tempo` | 9 | services, trace_detail, traces | `TEMPO_URL`, `TEMPO_PASSWORD`, `TEMPO_TOKEN` | [API docs](https://grafana.com/docs/tempo/latest/api_docs/) · [notes](sources/tempo.md) |
| Honeycomb | `honeycomb` | 21 | active_alerts, alert_rules, change_events, dashboards, metrics, service_map, services, slo, trace_detail, traces | `HONEYCOMB_API_KEY` | [API docs](https://docs.honeycomb.io/api/introduction) · [notes](sources/honeycomb.md) |
| IBM Instana Observability | `instana` | 20 | active_alerts, alert_history, alert_rules, change_events, hosts, incidents, logs, metrics, service_map, services, trace_detail, traces | `INSTANA_BASE_URL`, `INSTANA_API_TOKEN` | [API docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation) · [notes](sources/instana.md) |
| incident.io | `incidentio` | 27 | active_alerts, alert_history, incidents, oncall | `INCIDENTIO_API_KEY` | [API docs](https://docs.incident.io/api-reference/introduction) · [notes](sources/incidentio.md) |
| Kubernetes | `kubernetes` | 31 | change_events, hosts, k8s_events, k8s_logs, k8s_state | `KUBECONFIG` | [API docs](https://kubernetes.io/docs/reference/kubernetes-api/) · [notes](sources/kubernetes.md) |
| Azure Monitor Logs (Log Analytics + Application Insights) | `azure_log_analytics` | 19 | errors, hosts, k8s_events, k8s_logs, k8s_state, log_counts, logs, service_map, services, trace_detail, traces | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | [API docs](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview) · [notes](sources/azure_log_analytics.md) |
| Azure Monitor (metrics, alerts, activity log, Resource Graph, Service Health) | `azure_monitor` | 21 | active_alerts, alert_history, alert_rules, change_events, hosts, metrics, services | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SUBSCRIPTION_ID` | [API docs](https://learn.microsoft.com/en-us/rest/api/monitor/) · [notes](sources/azure_monitor.md) |
| Azure Monitor managed Prometheus | `azure_prometheus` | 5 | metrics | `AZURE_PROMETHEUS_QUERY_ENDPOINT`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | [API docs](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql) · [notes](sources/azure_prometheus.md) |
| New Relic | `newrelic` | 22 | active_alerts, alert_history, alert_rules, change_events, errors, hosts, incidents, log_counts, logs, metrics, service_map, services, slo, trace_detail, traces | `NEW_RELIC_API_KEY`, `NEW_RELIC_ACCOUNT_ID` | [API docs](https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/) · [notes](sources/newrelic.md) |
| OpenSearch | `opensearch` | 15 | active_alerts, alert_history, alert_rules, log_counts, logs, metrics, service_map, trace_detail, traces | `OPENSEARCH_URL`, `OPENSEARCH_PASSWORD`, `OPENSEARCH_TOKEN` | [API docs](https://docs.opensearch.org/latest/api-reference/) · [notes](sources/opensearch.md) |
| Zipkin | `zipkin` | 8 | service_map, services, trace_detail, traces | `ZIPKIN_URL`, `ZIPKIN_TOKEN`, `ZIPKIN_PASSWORD` | [API docs](https://zipkin.io/zipkin-api/) · [notes](sources/zipkin.md) |
| PagerDuty | `pagerduty` | 26 | active_alerts, alert_history, change_events, incidents, oncall, services | `PAGERDUTY_API_TOKEN` | [API docs](https://docs.pagerduty.com/developer/api/reference/rest) · [notes](sources/pagerduty.md) |
| Prometheus Alertmanager | `alertmanager` | 6 | active_alerts, change_events | `ALERTMANAGER_URL`, `ALERTMANAGER_TOKEN`, `ALERTMANAGER_PASSWORD` | [API docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml) · [notes](sources/alertmanager.md) |
| Prometheus | `prometheus` | 8 | active_alerts, alert_rules, hosts, metrics | `PROMETHEUS_URL`, `PROMETHEUS_TOKEN` | [API docs](https://prometheus.io/docs/prometheus/latest/querying/api/) |
| Slack | `slack` | 11 | chat_history | `SLACK_BOT_TOKEN`, `SLACK_USER_TOKEN` | [API docs](https://docs.slack.dev/reference/methods) · [notes](sources/slack.md) |
| SigNoz | `signoz` | 25 | active_alerts, alert_history, alert_rules, dashboards, errors, hosts, k8s_state, log_counts, logs, metrics, service_map, services, trace_detail, traces | `SIGNOZ_URL`, `SIGNOZ_API_KEY` | [API docs](https://signoz.io/api-reference/) · [notes](sources/signoz.md) |
| Splunk Enterprise / Splunk Cloud Platform | `splunk` | 12 | active_alerts, alert_history, alert_rules, hosts, log_counts, logs, metrics | `SPLUNK_URL`, `SPLUNK_TOKEN` | [API docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/introduction) · [notes](sources/splunk.md) |
| Splunk Observability Cloud | `splunk_o11y` | 24 | active_alerts, alert_history, alert_rules, change_events, dashboards, hosts, incidents, metrics, service_map, services, slo, trace_detail | `SPLUNK_O11Y_REALM`, `SPLUNK_O11Y_TOKEN` | [API docs](https://dev.splunk.com/observability/reference) · [notes](sources/splunk_o11y.md) |
| Sumo Logic | `sumologic` | 7 | active_alerts, alert_history, alert_rules, hosts, log_counts, logs, metrics | `SUMOLOGIC_ACCESS_ID`, `SUMOLOGIC_ACCESS_KEY` | [API docs](https://www.sumologic.com/help/docs/api/) · [notes](sources/sumologic.md) |
| Thanos Query | `thanos` | 12 | active_alerts, alert_rules, hosts, metrics | `THANOS_URL`, `THANOS_TOKEN`, `THANOS_PASSWORD` | [API docs](https://thanos.io/tip/components/query.md/) · [notes](sources/thanos.md) |
| VictoriaLogs | `victorialogs` | 8 | log_counts, logs | `VICTORIALOGS_URL`, `VICTORIALOGS_TOKEN`, `VICTORIALOGS_PASSWORD` | [API docs](https://docs.victoriametrics.com/victorialogs/querying/) · [notes](sources/victorialogs.md) |
| VictoriaMetrics | `victoriametrics` | 11 | active_alerts, alert_rules, hosts, metrics | `VM_URL`, `VM_TOKEN`, `VM_PASSWORD` | [API docs](https://docs.victoriametrics.com/victoriametrics/url-examples/) · [notes](sources/victoriametrics.md) |

## Amazon Managed Service for Prometheus (`amazon_prometheus`)

Amazon Managed Service for Prometheus workspace: PromQL range/instant queries, series, labels, metadata, ruler rules/alerts, and Alertmanager alerts, alert groups and silences (SigV4, service aps).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `amazon_prometheus_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-QueryMetrics.html) |
| `amazon_prometheus_query` | timeseries | GET `/api/v1/query` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-QueryMetrics.html) |
| `amazon_prometheus_series` | entities | GET `/api/v1/series` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetSeries.html) |
| `amazon_prometheus_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetLabels.html) |
| `amazon_prometheus_metric_metadata` | entities | GET `/api/v1/metadata` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetMetricMetadata.html) |
| `amazon_prometheus_alerts` | alerts | GET `/api/v1/alerts` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlerts.html) |
| `amazon_prometheus_rules` | entities | GET `/api/v1/rules` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListRules.html) |
| `amazon_prometheus_alertmanager_alerts` | alerts | GET `/alertmanager/api/v2/alerts` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlertManagerAlerts.html) |
| `amazon_prometheus_alert_groups` | alerts | GET `/alertmanager/api/v2/alerts/groups` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlertManagerAlertGroups.html) |
| `amazon_prometheus_silences` | entities | GET `/alertmanager/api/v2/silences` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-ListAlertManagerSilences.html) |
| `amazon_prometheus_labels` | entities | GET `/api/v1/labels` | [docs](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-APIReference-GetLabels.html) |

## AWS CloudTrail (event history) (`cloudtrail`)

Who changed what in the AWS account: management API calls (deployments, config and IAM changes, scaling, security-group edits) and CloudTrail Insights events from the regional 90-day event history (LookupEvents), plus CloudTrail Lake SQL queries for existing Lake customers.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `cloudtrail_events` | changes | POST `/` |  |
| `cloudtrail_lake_query` | changes | POST `/` → POST `/` |  |

## Amazon CloudWatch (metrics & alarms) (`cloudwatch`)

CloudWatch metrics (GetMetricData with Metrics Insights SQL, SEARCH() or an exact MetricStat; PromQL over the Prometheus-compatible API), metric inventory (ListMetrics), alarms (DescribeAlarms), alarm contributors, alarm mute rules and alarm state history (DescribeAlarmHistory) via SigV4-signed AWS JSON 1.0 / Prometheus HTTP requests.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `cloudwatch_metrics_insights` | timeseries | POST `/` |  |
| `cloudwatch_metric_data` | timeseries | POST `/` |  |
| `cloudwatch_metrics` | entities | POST `/` |  |
| `cloudwatch_alarms` | alerts | POST `/` |  |
| `cloudwatch_alarm_contributors` | alerts | POST `/` |  |
| `cloudwatch_alarm_mute_rules` | entities | POST `/` |  |
| `cloudwatch_alarm_history` | events | POST `/` |  |
| `cloudwatch_promql_query_range` | timeseries | POST `/api/v1/query_range` |  |
| `cloudwatch_promql_query` | timeseries | POST `/api/v1/query` |  |
| `cloudwatch_promql_series` | entities | POST `/api/v1/series` |  |
| `cloudwatch_promql_labels` | entities | POST `/api/v1/labels` |  |
| `cloudwatch_promql_label_values` | entities | GET `/api/v1/label/{{ filters.label \| urlencode }}/values` |  |

## Amazon CloudWatch Logs (`cloudwatch_logs`)

CloudWatch Logs Insights queries (StartQuery + GetQueryResults polling), raw log event search (FilterLogEvents), log group / log stream inventory, field discovery (GetLogGroupFields) and log anomaly detection results (ListAnomalies) via SigV4-signed AWS JSON 1.1.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `cloudwatch_logs_insights` | logs | POST `/` → POST `/` |  |
| `cloudwatch_logs_events` | logs | POST `/` |  |
| `cloudwatch_logs_log_groups` | entities | POST `/` |  |
| `cloudwatch_logs_log_streams` | entities | POST `/` |  |
| `cloudwatch_logs_fields` | entities | POST `/` |  |
| `cloudwatch_logs_anomalies` | events | POST `/` |  |

## AWS X-Ray (`xray`)

X-Ray traces: trace summaries with filter expressions and root-cause analytics, full traces (parsed segment and subsegment documents), the service graph (nodes and edges with request/error/fault/latency statistics), per-service time series statistics, X-Ray Insights anomalies, groups, and Transaction Search trace retrieval.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `xray_trace_summaries` | traces | POST `/TraceSummaries` |  |
| `xray_trace_segments` | spans | POST `/Traces` |  |
| `xray_trace_subsegments` | spans | POST `/Traces` |  |
| `xray_service_graph` | entities | POST `/ServiceGraph` |  |
| `xray_service_edges` | entities | POST `/ServiceGraph` |  |
| `xray_service_statistics` | timeseries | POST `/TimeSeriesServiceStatistics` |  |
| `xray_insights` | incidents | POST `/InsightSummaries` |  |
| `xray_groups` | entities | POST `/Groups` |  |
| `xray_retrieved_traces` | spans | POST `/StartTraceRetrieval` → POST `/ListRetrievedTraces` |  |

## Argo CD (`argocd`)

Argo CD GitOps state: applications (sync + health, source repo/path/targetRevision, destination, last operation), deployment history (status.history: revision, deployedAt, initiator), the last sync's per-resource results, Git revision metadata (author, date, commit message), the live resource tree, managed resources (drift), and application events.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `argocd_applications` | entities | GET `/api/v1/applications` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_history` | changes | GET `/api/v1/applications` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_app_resources` | entities | GET `/api/v1/applications` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_sync_results` | entities | GET `/api/v1/applications` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_revision_metadata` | changes | GET `/api/v1/applications/{{ filters.app }}/revisions/{{ filters.revision }}/metadata` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_resource_tree` | entities | GET `/api/v1/applications/{{ filters.app }}/resource-tree` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_managed_resources` | entities | GET `/api/v1/applications/{{ filters.app }}/managed-resources` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |
| `argocd_app_events` | events | GET `/api/v1/applications/{{ filters.app }}/events` | [docs](https://github.com/argoproj/argo-cd/blob/v3.5.6/assets/swagger.json) |

## Confluence (`confluence`)

Confluence Cloud: CQL full-text search across pages, blog posts, comments and attachments (runbooks, postmortems, architecture docs), one page or blog post with its body as plain text and labels, pages of a space (recently modified first), spaces, and page footer comments.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `confluence_search` | text | GET `/rest/api/search` | [docs](https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-search/#api-wiki-rest-api-search-get) |
| `confluence_page` | text | GET `/api/v2/{{ 'blogposts' if (filters.content_type or 'page') == 'blogpost' else 'pages' }...` | [docs](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/#api-pages-id-get) |
| `confluence_pages` | entities | GET `/api/v2/spaces` → GET `/api/v2/pages` | [docs](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/#api-pages-get) |
| `confluence_spaces` | entities | GET `/api/v2/spaces` | [docs](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-space/#api-spaces-get) |
| `confluence_page_comments` | events | GET `/api/v2/{{ 'blogposts' if (filters.content_type or 'page') == 'blogpost' else 'pages' }...` | [docs](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-comment/#api-pages-id-footer-comments-get) |

## Jira (`jira`)

Jira Cloud REST v3: JQL issue search (tickets, incidents, postmortem actions), one issue with rendered description and changelog, issue changelogs (status/assignee/priority transitions, single and bulk), comments, remote links (PRs, runbooks, Confluence pages linked from a ticket), approximate JQL counts, projects, fields (custom field ids such as Severity) and users; plus Jira Service Management…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `jira_issues` | incidents | GET `/rest/api/3/search/jql` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-get) |
| `jira_incidents` | incidents | GET `/rest/api/3/search/jql` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-get) |
| `jira_issue_count` | generic | POST `/rest/api/3/search/approximate-count` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-approximate-count-post) |
| `jira_issue` | entities | GET `/rest/api/3/issue/{{ filters.key }}` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get) |
| `jira_issue_changelog` | events | GET `/rest/api/3/issue/{{ filters.key }}/changelog` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-changelog-get) |
| `jira_changelogs` | events | POST `/rest/api/3/changelog/bulkfetch` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-changelog-bulkfetch-post) |
| `jira_issue_comments` | events | GET `/rest/api/3/issue/{{ filters.key }}/comment` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/#api-rest-api-3-issue-issueidorkey-comment-get) |
| `jira_issue_remote_links` | entities | GET `/rest/api/3/issue/{{ filters.key }}/remotelink` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-remote-links/#api-rest-api-3-issue-issueidorkey-remotelink-get) |
| `jira_projects` | entities | GET `/rest/api/3/project/search` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-projects/#api-rest-api-3-project-search-get) |
| `jira_fields` | entities | GET `/rest/api/3/field` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-fields/#api-rest-api-3-field-get) |
| `jira_users` | entities | GET `/rest/api/3/user/search` | [docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-user-search/#api-rest-api-3-user-search-get) |
| `jira_jsm_alerts` | alerts | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/a...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-get) |
| `jira_jsm_active_alerts` | alerts | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/a...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-get) |
| `jira_jsm_alert_logs` | events | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/a...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-id-logs-get) |
| `jira_jsm_alert_notes` | events | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/a...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-alerts/#api-api-cloudid-v1-alerts-id-notes-get) |
| `jira_jsm_schedules` | entities | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/s...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-schedules/#api-api-cloudid-v1-schedules-get) |
| `jira_jsm_oncalls` | entities | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/s...` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/s...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-schedule-on-calls/#api-api-cloudid-v1-schedules-scheduleid-on-calls-get) |
| `jira_jsm_teams` | entities | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `https://api.atlassian.com/jsm/ops/api/{{ env.JIRA_CLOUD_ID or captures.cloud_id }}/v1/t...` | [docs](https://developer.atlassian.com/cloud/jira/service-desk-ops/rest/v2/api-group-team/#api-api-cloudid-v1-teams-get) |
| `jira_jsm_incident` | incidents | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `/rest/api/3/issue/{{ filters.key }}` → GET `https://api.atlassian.com/jsm/incidents/cloudId/{{ env.JIRA_CLOUD_ID or captures.cloud_...` | [docs](https://developer.atlassian.com/cloud/incidents/rest/api-group-incident/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-get) |
| `jira_jsm_incident_alerts` | alerts | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `/rest/api/3/issue/{{ filters.key }}` → GET `https://api.atlassian.com/jsm/incidents/cloudId/{{ env.JIRA_CLOUD_ID or captures.cloud_...` | [docs](https://developer.atlassian.com/cloud/incidents/rest/api-group-linked-alerts/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-alert-get) |
| `jira_jsm_incident_responder_alerts` | alerts | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `/rest/api/3/issue/{{ filters.key }}` → GET `https://api.atlassian.com/jsm/incidents/cloudId/{{ env.JIRA_CLOUD_ID or captures.cloud_...` | [docs](https://developer.atlassian.com/cloud/incidents/rest/api-group-responder-alerts/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-responder-alerts-get) |
| `jira_jsm_incident_chat_channels` | entities | GET `{{ (env.JIRA_UI_URL or env.JIRA_URL or '') \| trim('/') }}/_edge/tenant_info` → GET `/rest/api/3/issue/{{ filters.key }}` → GET `https://api.atlassian.com/jsm/incidents/cloudId/{{ env.JIRA_CLOUD_ID or captures.cloud_...` | [docs](https://developer.atlassian.com/cloud/incidents/rest/api-group-chat-tools/#api-jsm-incidents-cloudid-cloudid-v1-incident-issueid-chat-channel-get) |

## Opsgenie (`opsgenie`)

Opsgenie REST API: alerts (search, detail, count, activity logs, notes, notification recipients), incidents (search, detail, logs, notes, associated alerts, incident timeline), on-call (who is on call per schedule, schedule timelines), schedules, escalations, maintenance windows, heartbeats, services, teams and deployments.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `opsgenie_alerts` | alerts | GET `/v2/alerts` | [docs](https://docs.opsgenie.com/docs/alert-api#list-alerts) |
| `opsgenie_open_alerts` | alerts | GET `/v2/alerts` | [docs](https://docs.opsgenie.com/docs/alert-api#list-alerts) |
| `opsgenie_alert_detail` | alerts | GET `/v2/alerts/{{ filters.alert \| urlencode }}` | [docs](https://docs.opsgenie.com/docs/alert-api#get-alert) |
| `opsgenie_alert_count` | generic | GET `/v2/alerts/count` | [docs](https://docs.opsgenie.com/docs/alert-api#count-alerts) |
| `opsgenie_alert_logs` | events | GET `/v2/alerts/{{ filters.alert_id \| urlencode }}/logs` | [docs](https://docs.opsgenie.com/docs/alert-api-continued#list-alert-logs) |
| `opsgenie_alert_notes` | events | GET `/v2/alerts/{{ filters.alert_id \| urlencode }}/notes` | [docs](https://docs.opsgenie.com/docs/alert-api-continued#list-alert-notes) |
| `opsgenie_alert_recipients` | events | GET `/v2/alerts/{{ filters.alert_id \| urlencode }}/recipients` | [docs](https://docs.opsgenie.com/docs/alert-api-continued#list-alert-recipients) |
| `opsgenie_incidents` | incidents | GET `/v1/incidents` | [docs](https://docs.opsgenie.com/docs/incident-api#list-incidents) |
| `opsgenie_incident_detail` | incidents | GET `/v1/incidents/{{ filters.incident \| urlencode }}` | [docs](https://docs.opsgenie.com/docs/incident-api#get-incident) |
| `opsgenie_incident_logs` | events | GET `/v1/incidents/{{ filters.incident_id \| urlencode }}/logs` | [docs](https://docs.opsgenie.com/docs/incident-api#list-incident-logs) |
| `opsgenie_incident_notes` | events | GET `/v1/incidents/{{ filters.incident_id \| urlencode }}/notes` | [docs](https://docs.opsgenie.com/docs/incident-api#list-incident-notes) |
| `opsgenie_incident_alerts` | alerts | GET `/v1/incidents/{{ filters.incident_id \| urlencode }}/associated-alert-ids` | [docs](https://docs.opsgenie.com/docs/incident-api#get-associated-alerts) |
| `opsgenie_incident_timeline` | events | GET `/v2/incident-timelines/{{ filters.incident_id \| urlencode }}/entries` | [docs](https://docs.opsgenie.com/docs/incident-timeline-api#list-incident-timeline-entries) |
| `opsgenie_oncall` | entities | GET `{{ ('/v2/schedules/' ~ (filters.schedule \| urlencode) ~ '/on-calls') if filters.schedu...` | [docs](https://docs.opsgenie.com/docs/who-is-on-call-api#get-on-calls) |
| `opsgenie_schedules` | entities | GET `/v2/schedules` | [docs](https://docs.opsgenie.com/docs/schedule-api#list-schedules) |
| `opsgenie_schedule_timeline` | entities | GET `/v2/schedules/{{ filters.schedule \| urlencode }}/timeline` | [docs](https://docs.opsgenie.com/docs/schedule-api#get-schedule-timeline) |
| `opsgenie_heartbeats` | entities | GET `/v2/heartbeats` | [docs](https://docs.opsgenie.com/docs/heartbeat-api#list-heartbeats) |
| `opsgenie_heartbeat` | entities | GET `/v2/heartbeats/{{ filters.name \| urlencode }}` | [docs](https://docs.opsgenie.com/docs/heartbeat-api#get-heartbeat-request) |
| `opsgenie_services` | entities | GET `/v1/services` | [docs](https://docs.opsgenie.com/docs/service-api#list-services) |
| `opsgenie_teams` | entities | GET `/v2/teams` | [docs](https://docs.opsgenie.com/docs/team-api#list-teams) |
| `opsgenie_deployments` | changes | GET `/v2/deployments/search` | [docs](https://docs.opsgenie.com/docs/deployment-api#search-deployments) |
| `opsgenie_escalations` | entities | GET `/v2/escalations` | [docs](https://docs.opsgenie.com/docs/escalation-api#list-escalations) |
| `opsgenie_maintenance` | events | GET `/v1/maintenance` | [docs](https://docs.opsgenie.com/docs/maintenance-api#list-maintenance) |
| `opsgenie_maintenance_detail` | entities | GET `/v1/maintenance/{{ filters.maintenance_id \| urlencode }}` | [docs](https://docs.opsgenie.com/docs/maintenance-api#get-maintenance) |

## Chronosphere (Cortex XCOR) (`chronosphere`)

Chronosphere / Cortex XCOR: PromQL range and instant queries, series and labels (Prometheus API under /data/metrics), plus monitors (Config API), fired alerts, change events and metric metadata (Data API).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `chronosphere_query_range` | timeseries | GET `/data/metrics/api/v1/query_range` | [docs](https://docs.chronosphere.io/tooling/prometheus-api) |
| `chronosphere_query` | timeseries | GET `/data/metrics/api/v1/query` | [docs](https://docs.chronosphere.io/tooling/prometheus-api) |
| `chronosphere_series` | entities | GET `/data/metrics/api/v1/series` | [docs](https://docs.chronosphere.io/tooling/prometheus-api) |
| `chronosphere_label_values` | entities | GET `/data/metrics/api/v1/label/{{ filters.label }}/values` | [docs](https://docs.chronosphere.io/tooling/prometheus-api) |
| `chronosphere_labels` | entities | GET `/data/metrics/api/v1/labels` | [docs](https://docs.chronosphere.io/tooling/prometheus-api) |
| `chronosphere_alerts` | alerts | POST `/api/v1/data/alerts` | [docs](https://docs-xcor.paloaltonetworks.com/tooling/api-info/data_definition/operations/ListAlerts) |
| `chronosphere_metric_metadata` | entities | GET `/api/v1/data/metrics:metadata/{{ filters.metric \| urlencode }}` | [docs](https://docs-xcor.paloaltonetworks.com/tooling/api-info/data_definition/operations/ReadMetricMetadata) |
| `chronosphere_change_events` | changes | GET `/api/v1/data/events` | [docs](https://docs-xcor.paloaltonetworks.com/tooling/api-info/data_definition/operations/ListEvents) |
| `chronosphere_monitors` | entities | GET `/api/v1/config/monitors` | [docs](https://docs-xcor.paloaltonetworks.com/tooling/api-info/definition/operations/ListMonitors) |

## Splunk AppDynamics (Controller REST API) (`appdynamics`)

AppDynamics Controller REST API: application model (business applications, business transactions, tiers, nodes, backends), metric browser tree and metric data (metric paths with wildcards), health rule violations, events (deployments, config changes, restarts, errors, diagnostic sessions), transaction snapshots, health rule definitions, anomaly detection with suspected causes, and the Controller …

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `appdynamics_applications` | entities | GET `/controller/rest/applications` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/application-model-api) |
| `appdynamics_business_transactions` | entities | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/application-model-api) |
| `appdynamics_tiers` | entities | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/application-model-api) |
| `appdynamics_nodes` | entities | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/application-model-api) |
| `appdynamics_backends` | entities | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/application-model-api) |
| `appdynamics_metric_tree` | entities | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/metric-and-snapshot-api) |
| `appdynamics_metric_data` | timeseries | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/metric-and-snapshot-api) |
| `appdynamics_health_rule_violations` | alerts | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/alert-and-respond-api/events-api) |
| `appdynamics_events` | events | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/alert-and-respond-api/events-api) |
| `appdynamics_snapshots` | spans | GET `/controller/rest/applications/{{ (filters.application or env.APPD_APPLICATION) \| urlen...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/metric-and-snapshot-api) |
| `appdynamics_health_rules` | entities | GET `/controller/alerting/rest/v1/applications/{{ filters.application_id or env.APPD_APPLICA...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/alert-and-respond-api/health-rule-api) |
| `appdynamics_anomalies` | alerts | GET `/controller/anomaly/rest/api/v1/applications/{{ filters.application_id or env.APPD_APPL...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/anomaly-violation-api) |
| `appdynamics_anomaly_causes` | incidents | GET `/controller/anomaly/rest/api/v1/applications/{{ filters.application_id or env.APPD_APPL...` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/anomaly-violation-api) |
| `appdynamics_audit_history` | changes | GET `/controller/ControllerAuditHistory` | [docs](https://help.splunk.com/en/appdynamics-saas/extend-splunk-appdynamics/26.10.0/extend-splunk-appdynamics/splunk-appdynamics-apis/controller-audit-history-api) |

## Jaeger (`jaeger`)

Jaeger query API v3 - services, operations, trace search (spans and summaries), trace by id, plus the service dependency graph.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `jaeger_services` | entities | GET `/api/v3/services` | [docs](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_operations` | entities | GET `/api/v3/operations` | [docs](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_traces` | spans | GET `/api/v3/traces` | [docs](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_trace_summaries` | traces | GET `/api/v3/trace-summaries` | [docs](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_trace` | traces | GET `/api/v3/traces/{{ filters.trace_id \| trim \| lower }}` | [docs](https://github.com/jaegertracing/jaeger-idl/blob/main/swagger/api_v3/query_service.openapi.yaml) |
| `jaeger_dependencies` | entities | GET `/api/dependencies` | [docs](https://www.jaegertracing.io/docs/latest/architecture/apis/#service-dependencies-graph) |

## Coralogix (`coralogix`)

Coralogix DataPrime and Lucene queries over logs, spans and system datasets (alert history), Prometheus-compatible metrics (PromQL), alert definitions with their current alerting status, incidents, cases and SLOs.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `coralogix_dataprime` | logs | POST `/api/v1/dataprime/query` | [docs](https://coralogix.com/docs/dataprime/API/direct-archive-query-http/) |
| `coralogix_lucene` | logs | POST `/api/v1/dataprime/query` | [docs](https://coralogix.com/docs/dataprime/API/direct-archive-query-http/) |
| `coralogix_alert_history` | alerts | POST `/api/v1/dataprime/query` | [docs](https://coralogix.com/docs/user-guides/data-layer/system_dataspace/alerts-history/) |
| `coralogix_metrics_query_range` | timeseries | GET `/metrics/api/v1/query_range` | [docs](https://coralogix.com/docs/user-guides/data-query/metrics-api/) |
| `coralogix_metrics_query` | timeseries | GET `/metrics/api/v1/query` | [docs](https://coralogix.com/docs/user-guides/data-query/metrics-api/) |
| `coralogix_metrics_series` | entities | GET `/metrics/api/v1/series` | [docs](https://coralogix.com/docs/user-guides/data-query/metrics-api/) |
| `coralogix_metrics_label_values` | entities | GET `/metrics/api/v1/label/{{ filters.label }}/values` | [docs](https://coralogix.com/docs/user-guides/data-query/metrics-api/) |
| `coralogix_alert_defs` | alerts | GET `/mgmt/openapi/5/alerts/alerts/v3` | [docs](https://coralogix.com/docs/api-reference/v5/alert-definitions-service/get-a-list-of-all-accessible-alert-definitions/) |
| `coralogix_alert_def` | entities | GET `/mgmt/openapi/5/alerts/alerts/v3/{{ filters.id \| urlencode }}` | [docs](https://coralogix.com/docs/api-reference/v5/alert-definitions-service/get-alert-definition-by-id/) |
| `coralogix_incidents` | incidents | POST `/mgmt/openapi/5/incidents/incidents/v1` | [docs](https://coralogix.com/docs/api-reference/v5/incidents-service/list-incidents-with-filters/) |
| `coralogix_cases` | incidents | POST `/mgmt/openapi/5/cases/cases/v1` | [docs](https://coralogix.com/docs/api-reference/v5/cases-service/list-cases-with-filters/) |
| `coralogix_slos` | entities | GET `/mgmt/openapi/5/slo/slos/v1` | [docs](https://coralogix.com/docs/api-reference/v5/slos-service/list-slos/) |

## Datadog (`datadog`)

Datadog REST API v1/v2: metrics, logs (indexes, online archives and Flex), APM spans, RUM, monitors and per-group monitor states, events (incl. Change Tracking change events), incidents, Software Catalog, APM service dependencies and APM golden-signal metrics, hosts, containers, SLOs and SLO history, dashboards (widgets exploded), downtimes, Synthetics results, Error Tracking issues, DORA deploym…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `datadog_metrics_timeseries` | timeseries | POST `/api/v2/query/timeseries` | [docs](https://docs.datadoghq.com/api/latest/metrics/query-timeseries-data-across-multiple-products/) |
| `datadog_metrics_query` | timeseries | GET `/api/v1/query` | [docs](https://docs.datadoghq.com/api/latest/metrics/query-timeseries-points/) |
| `datadog_metrics_scalar` | timeseries | POST `/api/v2/query/scalar` | [docs](https://docs.datadoghq.com/api/latest/metrics/query-scalar-data-across-multiple-products/) |
| `datadog_apm_metrics` | timeseries | POST `/api/v2/query/timeseries` | [docs](https://docs.datadoghq.com/api/latest/metrics/query-timeseries-data-across-multiple-products/) |
| `datadog_apm_metrics_top` | timeseries | POST `/api/v2/query/scalar` | [docs](https://docs.datadoghq.com/api/latest/metrics/query-scalar-data-across-multiple-products/) |
| `datadog_metrics_active` | entities | GET `/api/v1/metrics` | [docs](https://docs.datadoghq.com/api/latest/metrics/get-active-metrics-list/) |
| `datadog_metrics` | entities | GET `/api/v2/metrics` | [docs](https://docs.datadoghq.com/api/latest/metrics/get-a-list-of-metrics/) |
| `datadog_metric_metadata` | entities | GET `/api/v1/metrics/{{ filters.metric }}` | [docs](https://docs.datadoghq.com/api/latest/metrics/get-metric-metadata/) |
| `datadog_metric_tags` | entities | GET `/api/v2/metrics/{{ filters.metric }}/all-tags` | [docs](https://docs.datadoghq.com/api/latest/metrics/list-tags-by-metric-name/) |
| `datadog_logs` | logs | POST `/api/v2/logs/events/search` | [docs](https://docs.datadoghq.com/api/latest/logs/search-logs-post/) |
| `datadog_log_counts` | logs | POST `/api/v2/logs/analytics/aggregate` | [docs](https://docs.datadoghq.com/api/latest/logs/aggregate-events/) |
| `datadog_log_timeseries` | logs | POST `/api/v2/logs/analytics/aggregate` | [docs](https://docs.datadoghq.com/api/latest/logs/aggregate-events/) |
| `datadog_log_indexes` | entities | GET `/api/v1/logs/config/indexes` | [docs](https://docs.datadoghq.com/api/latest/logs-indexes/get-all-indexes/) |
| `datadog_log_exclusion_filters` | entities | GET `/api/v1/logs/config/indexes` | [docs](https://docs.datadoghq.com/api/latest/logs-indexes/get-all-indexes/) |
| `datadog_spans` | spans | POST `/api/v2/spans/events/search` | [docs](https://docs.datadoghq.com/api/latest/spans/search-spans/) |
| `datadog_span_counts` | spans | POST `/api/v2/spans/analytics/aggregate` | [docs](https://docs.datadoghq.com/api/latest/spans/aggregate-spans/) |
| `datadog_span_timeseries` | spans | POST `/api/v2/spans/analytics/aggregate` | [docs](https://docs.datadoghq.com/api/latest/spans/aggregate-spans/) |
| `datadog_rum_events` | events | POST `/api/v2/rum/events/search` | [docs](https://docs.datadoghq.com/api/latest/rum/search-rum-events/) |
| `datadog_rum_counts` | events | POST `/api/v2/rum/analytics/aggregate` | [docs](https://docs.datadoghq.com/api/latest/rum/aggregate-rum-events/) |
| `datadog_rum_timeseries` | events | POST `/api/v2/rum/analytics/aggregate` | [docs](https://docs.datadoghq.com/api/latest/rum/aggregate-rum-events/) |
| `datadog_monitors` | alerts | GET `/api/v1/monitor` | [docs](https://docs.datadoghq.com/api/latest/monitors/get-all-monitors/) |
| `datadog_monitor_by_id` | alerts | GET `/api/v1/monitor/{{ filters.monitor_id }}` | [docs](https://docs.datadoghq.com/api/latest/monitors/get-a-monitors-details/) |
| `datadog_monitor_states` | alerts | GET `/api/v1/monitor/{{ filters.monitor_id }}` | [docs](https://docs.datadoghq.com/api/latest/monitors/get-a-monitors-details/) |
| `datadog_monitor_search` | alerts | GET `/api/v1/monitor/search` | [docs](https://docs.datadoghq.com/api/latest/monitors/monitors-search/) |
| `datadog_monitor_group_search` | alerts | GET `/api/v1/monitor/groups/search` | [docs](https://docs.datadoghq.com/api/latest/monitors/monitors-group-search/) |
| `datadog_downtimes` | entities | GET `/api/v2/downtime` | [docs](https://docs.datadoghq.com/api/latest/downtimes/get-all-downtimes/) |
| `datadog_events` | events | POST `/api/v2/events/search` | [docs](https://docs.datadoghq.com/api/latest/events/search-events/) |
| `datadog_audit_events` | changes | POST `/api/v2/audit/events/search` | [docs](https://docs.datadoghq.com/api/latest/audit/search-audit-logs-events/) |
| `datadog_incidents` | incidents | GET `/api/v2/incidents/search` | [docs](https://docs.datadoghq.com/api/latest/incidents/search-for-incidents/) |
| `datadog_teams` | entities | GET `/api/v2/team` | [docs](https://docs.datadoghq.com/api/latest/teams/get-all-teams/) |
| `datadog_oncall` | entities | GET `/api/v2/team` → GET `/api/v2/on-call/teams/{{ filters.team_id or captures.team_id }}/on-call` | [docs](https://docs.datadoghq.com/api/latest/on-call/get-team-on-call-users/) |
| `datadog_dora_deployments` | changes | POST `/api/v2/dora/deployments` | [docs](https://docs.datadoghq.com/api/latest/delivery-performance/get-a-list-of-deployment-events/) |
| `datadog_dora_failures` | incidents | POST `/api/v2/dora/failures` | [docs](https://docs.datadoghq.com/api/latest/delivery-performance/get-a-list-of-incident-events/) |
| `datadog_service_definitions` | entities | GET `/api/v2/services/definitions` | [docs](https://docs.datadoghq.com/api/latest/service-definition/get-all-service-definitions/) |
| `datadog_catalog_entities` | entities | GET `/api/v2/catalog/entity` | [docs](https://docs.datadoghq.com/api/latest/software-catalog/get-a-list-of-entities/) |
| `datadog_catalog_relations` | entities | GET `/api/v2/catalog/relation` | [docs](https://docs.datadoghq.com/api/latest/software-catalog/get-a-list-of-entity-relations/) |
| `datadog_service_dependencies` | entities | GET `/api/v1/service_dependencies` | [docs](https://docs.datadoghq.com/api/latest/service-dependencies/get-all-apm-service-dependencies/) |
| `datadog_service_neighbors` | entities | GET `/api/v1/service_dependencies/{{ filters.service }}` | [docs](https://docs.datadoghq.com/api/latest/service-dependencies/get-one-apm-services-dependencies/) |
| `datadog_apm_services` | entities | GET `/api/v2/apm/services` | [docs](https://docs.datadoghq.com/api/latest/apm/get-service-list/) |
| `datadog_hosts` | entities | GET `/api/v1/hosts` | [docs](https://docs.datadoghq.com/api/latest/hosts/get-all-hosts-for-your-organization/) |
| `datadog_containers` | entities | GET `/api/v2/containers` | [docs](https://docs.datadoghq.com/api/latest/containers/get-all-containers/) |
| `datadog_slos` | entities | GET `/api/v1/slo/search` | [docs](https://docs.datadoghq.com/api/latest/service-level-objectives/search-for-slos/) |
| `datadog_slo_history` | timeseries | GET `/api/v1/slo/{{ filters.slo_id }}/history` | [docs](https://docs.datadoghq.com/api/latest/service-level-objectives/get-an-slos-history/) |
| `datadog_slo_status` | timeseries | GET `/api/v2/slo/{{ filters.slo_id }}/status` | [docs](https://docs.datadoghq.com/api/latest/service-level-objectives/get-slo-status/) |
| `datadog_dashboards` | entities | GET `/api/v1/dashboard` | [docs](https://docs.datadoghq.com/api/latest/dashboards/get-all-dashboards/) |
| `datadog_dashboard_widgets` | entities | GET `/api/v1/dashboard/{{ filters.dashboard_id }}` | [docs](https://docs.datadoghq.com/api/latest/dashboards/get-a-dashboard/) |
| `datadog_synthetics_tests` | entities | GET `/api/v1/synthetics/tests` | [docs](https://docs.datadoghq.com/api/latest/synthetics/get-the-list-of-all-synthetic-tests/) |
| `datadog_synthetics_results` | events | GET `/api/v2/synthetics/tests/{{ filters.public_id }}/results` | [docs](https://docs.datadoghq.com/api/latest/synthetics/get-a-tests-latest-results/) |
| `datadog_error_tracking_issues` | incidents | POST `/api/v2/error-tracking/issues/search` | [docs](https://docs.datadoghq.com/api/latest/error-tracking/search-error-tracking-issues/) |

## Dynatrace (Environment API v2) (`dynatrace`)

Dynatrace Environment API v2: metric queries (metric selector), metric descriptors, monitored entities and Smartscape relationships, Davis problems (root cause, evidence, impact, comments), events (deployments, config changes, restarts), classic log search/aggregation, SLOs, audit log and releases.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `dynatrace_metrics` | timeseries | GET `/api/v2/metrics/query` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/get-data-points) |
| `dynatrace_metric_descriptors` | entities | GET `/api/v2/metrics` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/get-all-metrics) |
| `dynatrace_entities` | entities | GET `/api/v2/entities` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list) |
| `dynatrace_relationships_out` | entities | GET `/api/v2/entities` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list) |
| `dynatrace_relationships_in` | entities | GET `/api/v2/entities` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list) |
| `dynatrace_entity_types` | entities | GET `/api/v2/entityTypes` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-all-entity-types) |
| `dynatrace_problems` | incidents | GET `/api/v2/problems` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list) |
| `dynatrace_problem_evidence` | events | GET `/api/v2/problems/{{ filters.problem_id \| urlencode }}` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problem-details) |
| `dynatrace_problem_impacts` | generic | GET `/api/v2/problems/{{ filters.problem_id \| urlencode }}` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problem-details) |
| `dynatrace_problem_comments` | text | GET `/api/v2/problems/{{ filters.problem_id \| urlencode }}/comments` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/comments/get-all) |
| `dynatrace_events` | changes | GET `/api/v2/events` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-events) |
| `dynatrace_event_types` | entities | GET `/api/v2/eventTypes` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/events-v2/get-event-types) |
| `dynatrace_logs` | logs | GET `/api/v2/logs/search` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/log-monitoring-v2/get-search-logs) |
| `dynatrace_log_counts` | timeseries | GET `/api/v2/logs/aggregate` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/log-monitoring-v2/get-aggregate-logs) |
| `dynatrace_slos` | entities | GET `/api/v2/slo` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/service-level-objectives-classic/get-all) |
| `dynatrace_audit_logs` | changes | GET `/api/v2/auditlogs` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/audit-logs/get-log) |
| `dynatrace_releases` | changes | GET `/api/v2/releases` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/environment-api/releaseapi/get-releaseall) |

## Dynatrace Grail (DQL) (`dynatrace_grail`)

Dynatrace Grail through the platform Query API: logs, spans/traces, Davis problems and events, SDLC/deployment events, metrics (timeseries), Smartscape topology and arbitrary DQL.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `dynatrace_grail_query` | generic | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post) |
| `dynatrace_grail_logs` | logs | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post) |
| `dynatrace_grail_log_counts` | timeseries | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post) |
| `dynatrace_grail_spans` | spans | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post) |
| `dynatrace_grail_problems` | incidents | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis) |
| `dynatrace_grail_events` | events | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/semantic-dictionary/model/davis) |
| `dynatrace_grail_changes` | changes | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/semantic-dictionary/model/sdlc-events) |
| `dynatrace_grail_entities` | entities | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/smartscape-commands) |
| `dynatrace_grail_topology` | entities | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/smartscape-commands) |
| `dynatrace_grail_metrics` | timeseries | POST `/platform/storage/query/v1/query:execute` → GET `/platform/storage/query/v1/query:poll` | [docs](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/metric-commands) |

## Edge Delta (`edgedelta`)

Edge Delta Observability: log search (CQL), log-count and trace graphs, metrics, events (monitor alerts, pattern anomalies, Kubernetes events), log patterns with sentiment and change, spans, monitors and their states, field discovery, and pipeline config history.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `edgedelta_logs` | logs | GET `/v1/orgs/{{ env.ED_ORG_ID }}/logs/log_search/search` | [docs](https://docs.edgedelta.com/api/) |
| `edgedelta_log_counts` | timeseries | POST `/v1/orgs/{{ env.ED_ORG_ID }}/graph` | [docs](https://github.com/edgedelta/edx/blob/main/internal/cli/logs.go) |
| `edgedelta_metrics` | timeseries | GET `/v1/orgs/{{ env.ED_ORG_ID }}/metrics_v2/graph` | [docs](https://docs.edgedelta.com/query-metrics-api/) |
| `edgedelta_events` | events | GET `/v1/orgs/{{ env.ED_ORG_ID }}/events/search` | [docs](https://docs.edgedelta.com/api/) |
| `edgedelta_patterns` | events | GET `/v1/orgs/{{ env.ED_ORG_ID }}/clustering/stats` | [docs](https://docs.edgedelta.com/api/) |
| `edgedelta_traces` | spans | GET `/v1/orgs/{{ env.ED_ORG_ID }}/traces` | [docs](https://github.com/edgedelta/edgedelta-mcp-server/blob/main/pkg/tools/search.go) |
| `edgedelta_monitors` | alerts | GET `/v1/orgs/{{ env.ED_ORG_ID }}/monitors` | [docs](https://github.com/edgedelta/edx/blob/main/skills/ed-monitors/SKILL.md) |
| `edgedelta_monitor` | entities | GET `/v1/orgs/{{ env.ED_ORG_ID }}/monitors/{{ filters.id \| urlencode }}` | [docs](https://github.com/edgedelta/edx/blob/main/internal/cli/monitors.go) |
| `edgedelta_monitor_states` | alerts | GET `/v1/orgs/{{ env.ED_ORG_ID }}/monitor_states` | [docs](https://github.com/edgedelta/edx/blob/main/internal/cli/monitors.go) |
| `edgedelta_facet_options` | entities | GET `/v1/orgs/{{ env.ED_ORG_ID }}/facet_options` | [docs](https://github.com/edgedelta/edgedelta-mcp-server/blob/main/pkg/tools/facet.go) |
| `edgedelta_facet_keys` | entities | GET `/v1/orgs/{{ env.ED_ORG_ID }}/facet_keys` | [docs](https://github.com/edgedelta/edx/blob/main/internal/cli/metrics.go) |
| `edgedelta_pipelines` | entities | GET `/v1/orgs/{{ env.ED_ORG_ID }}/confs` | [docs](https://docs.edgedelta.com/api/) |
| `edgedelta_pipeline_history` | changes | GET `/v1/orgs/{{ env.ED_ORG_ID }}/pipelines/{{ filters.conf_id \| urlencode }}/history` | [docs](https://docs.edgedelta.com/api/) |

## Elasticsearch (`elasticsearch`)

Elasticsearch search/ES\|QL/aggregation APIs over logs-*, traces-apm*, metrics-* and the Kibana alerts-as-data indices, plus index/data-stream/cluster inventory.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `elasticsearch_search` | logs | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_LOGS_INDEX }}/_search` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-search) |
| `elasticsearch_esql` | generic | POST `/_query` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-esql-query) |
| `elasticsearch_log_counts` | timeseries | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_LOGS_INDEX }}/_search` | [docs](https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-datehistogram-aggregation) |
| `elasticsearch_metric_histogram` | timeseries | POST `/{{ filters.index_pattern or 'metrics-*' }}/_search` | [docs](https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-datehistogram-aggregation) |
| `elasticsearch_top_terms` | generic | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_LOGS_INDEX }}/_search` | [docs](https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-terms-aggregation) |
| `elasticsearch_indices` | entities | GET `/_cat/indices/{{ filters.index_pattern or '*' }}` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cat-indices) |
| `elasticsearch_data_streams` | entities | GET `/_data_stream/{{ filters.name or '*' }}` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-get-data-stream) |
| `elasticsearch_data_stream_stats` | entities | GET `/_data_stream/{{ filters.name or '*' }}/_stats` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-data-streams-stats-1) |
| `elasticsearch_field_caps` | entities | GET `/{{ filters.index_pattern or env.ELASTICSEARCH_LOGS_INDEX }}/_field_caps` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-field-caps) |
| `elasticsearch_cluster_health` | entities | GET `/_cluster/health{{ ('/' ~ filters.index_pattern) if filters.index_pattern else '' }}` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cluster-health) |
| `elasticsearch_nodes` | entities | GET `/_cat/nodes` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cat-nodes) |
| `elasticsearch_shards` | entities | GET `/_cat/shards{{ ('/' ~ filters.index_pattern) if filters.index_pattern else '' }}` | [docs](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cat-shards) |
| `elasticsearch_alerts` | alerts | POST `/{{ filters.index_pattern or '.alerts-*' }}/_search` | [docs](https://www.elastic.co/docs/explore-analyze/alerting/alerts/query-alerts) |
| `elasticsearch_apm_traces` | spans | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_APM_TRACES_INDEX }}/_search` | [docs](https://www.elastic.co/docs/solutions/observability/apm/transactions) |
| `elasticsearch_apm_errors` | logs | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_APM_ERRORS_INDEX }}/_search` | [docs](https://www.elastic.co/docs/solutions/observability/apm/errors) |
| `elasticsearch_apm_service_stats` | entities | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_APM_TRACES_INDEX }}/_search` | [docs](https://www.elastic.co/docs/reference/aggregations/search-aggregations-bucket-terms-aggregation) |
| `elasticsearch_apm_dependencies` | entities | POST `/{{ filters.index_pattern or env.ELASTICSEARCH_APM_TRACES_INDEX }}/_search` | [docs](https://www.elastic.co/docs/solutions/observability/apm/spans) |

## Kibana (`kibana`)

Kibana public APIs: alerting rules and their last run, maintenance windows, cases (incident records), SLOs with error budgets, Synthetics monitors, APM deployment annotations, data views and Security detection alerts.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `kibana_alerting_rules` | entities | GET `/api/alerting/rules/_find` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-alerting-rules-find) |
| `kibana_alerting_rule` | entities | GET `/api/alerting/rule/{{ filters.id }}` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-alerting-rule-id) |
| `kibana_maintenance_windows` | changes | GET `/api/maintenance_window/_find` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-maintenance-window-find) |
| `kibana_cases` | incidents | GET `/api/cases/_find` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-findcasesdefaultspace) |
| `kibana_case_comments` | events | GET `/api/cases/{{ filters.case_id }}/comments/_find` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-findcasecommentsdefaultspace) |
| `kibana_case_alerts` | alerts | GET `/api/cases/{{ filters.case_id }}/alerts` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-getcasealertsdefaultspace) |
| `kibana_case_activity` | events | GET `/api/cases/{{ filters.case_id }}/user_actions/_find` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-findcaseactivitydefaultspace) |
| `kibana_slos` | entities | GET `/api/observability/slos` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-findslosop) |
| `kibana_synthetics_monitors` | entities | GET `/api/synthetics/monitors` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-synthetic-monitors) |
| `kibana_apm_annotations` | changes | GET `/api/apm/services/{{ filters.service \| urlencode }}/annotation/search` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-getannotation) |
| `kibana_data_views` | entities | GET `/api/data_views` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-getalldataviewsdefault) |
| `kibana_security_alerts` | alerts | POST `/api/detection_engine/signals/search` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-searchalerts) |
| `kibana_connectors` | entities | GET `/api/actions/connectors` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-actions-connectors) |
| `kibana_status` | entities | GET `/api/status` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-get-status) |
| `kibana_alerting_health` | entities | GET `/api/alerting/_health` | [docs](https://www.elastic.co/docs/api/doc/kibana/operation/operation-getalertinghealth) |

## Sentry (`sentry`)

Sentry error tracking and tracing API: issues with counts in the incident window, the events and stack traces behind them, which release/host/environment they hit, Explore queries over errors / spans / logs, releases with their deploys and commits, crash-free release health, feature-flag changes, crons and alert detectors.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `sentry_issues` | incidents | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/issues/` | [docs](https://docs.sentry.io/api/events/list-an-organizations-issues/) |
| `sentry_issue` | incidents | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/issues/{{ filters.issue_id }}/` | [docs](https://docs.sentry.io/api/events/retrieve-an-issue/) |
| `sentry_issue_events` | events | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/issues/{{ filters.issue_id }}/events/` | [docs](https://docs.sentry.io/api/events/list-an-issues-events/) |
| `sentry_issue_event` | events | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/issues/{{ filters.issue_id }}/events/{{ filte...` | [docs](https://docs.sentry.io/api/events/retrieve-an-issue-event/) |
| `sentry_issue_tags` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/issues/{{ filters.issue_id }}/tags/{{ filters...` | [docs](https://docs.sentry.io/api/events/retrieve-tag-details/) |
| `sentry_issue_by_short_id` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/shortids/{{ filters.short_id }}/` | [docs](https://docs.sentry.io/api/organizations/resolve-a-short-id/) |
| `sentry_event_lookup` | events | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/eventids/{{ filters.event_id }}/` | [docs](https://docs.sentry.io/api/organizations/resolve-an-event-id/) |
| `sentry_events` | logs | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/events/` | [docs](https://docs.sentry.io/api/explore/query-explore-events-in-table-format/) |
| `sentry_events_timeseries` | timeseries | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/events-timeseries/` | [docs](https://docs.sentry.io/api/explore/query-explore-events-in-timeseries-format/) |
| `sentry_trace_item_attributes` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/trace-items/attributes/` | [docs](https://docs.sentry.io/api/discover/list-trace-item-attributes/) |
| `sentry_trace_meta` | traces | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/trace-meta/{{ filters.trace_id }}/` | [docs](https://docs.sentry.io/api/discover/retrieve-trace-metadata/) |
| `sentry_releases` | changes | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/releases/` | [docs](https://docs.sentry.io/api/releases/list-an-organizations-releases/) |
| `sentry_release_health` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/releases/{{ filters.version \| urlencode }}/` | [docs](https://docs.sentry.io/api/releases/retrieve-an-organizations-release/) |
| `sentry_release_deploys` | changes | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/releases/{{ filters.version \| urlencode }}/d...` | [docs](https://docs.sentry.io/api/releases/list-a-releases-deploys/) |
| `sentry_release_commits` | changes | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/releases/{{ filters.version \| urlencode }}/c...` | [docs](https://docs.sentry.io/api/releases/list-an-organization-releases-commits/) |
| `sentry_sessions` | timeseries | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/sessions/` | [docs](https://docs.sentry.io/api/releases/retrieve-release-health-session-statistics/) |
| `sentry_flag_logs` | changes | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/flags/logs/` | [docs](https://docs.sentry.io/api/flags/list-an-organizations-flag-logs/) |
| `sentry_detectors` | alerts | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/detectors/` | [docs](https://docs.sentry.io/api/monitors/fetch-an-organizations-monitors/) |
| `sentry_workflows` | alerts | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/workflows/` | [docs](https://docs.sentry.io/api/monitors/fetch-alerts/) |
| `sentry_monitors` | alerts | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/monitors/` | [docs](https://docs.sentry.io/api/crons/retrieve-monitors-for-an-organization/) |
| `sentry_monitor_checkins` | events | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/monitors/{{ filters.monitor }}/checkins/` | [docs](https://docs.sentry.io/api/crons/retrieve-checkins-for-a-monitor/) |
| `sentry_projects` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/projects/` | [docs](https://docs.sentry.io/api/organizations/list-an-organizations-projects/) |
| `sentry_environments` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/environments/` | [docs](https://docs.sentry.io/api/environments/list-an-organizations-environments/) |
| `sentry_org_stats` | timeseries | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/stats_v2/` | [docs](https://docs.sentry.io/api/organizations/retrieve-event-counts-for-an-organization-v2/) |
| `sentry_dashboards` | entities | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/dashboards/` | [docs](https://docs.sentry.io/api/dashboards/list-an-organizations-custom-dashboards/) |
| `sentry_replays` | events | GET `/api/0/organizations/{{ env.SENTRY_ORG }}/replays/` | [docs](https://docs.sentry.io/api/replays/list-an-organizations-replays/) |

## GitHub (`github`)

GitHub REST API: repositories, commits with per-file patches, compare (release diffs), pull requests and their files, merged-PR change feed, deployments and deployment statuses, releases, tags, Actions workflow runs, jobs and job logs, check runs and commit statuses, code / issue / commit search, and file contents at any ref. Works with github.com and GitHub Enterprise Server.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `github_repos` | entities | GET `{{ ('/users/' ~ filters.user ~ '/repos') if filters.user else (('/orgs/' ~ (filters.org...` | [docs](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#list-organization-repositories) |
| `github_commits` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#list-commits) |
| `github_commit_files` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#get-a-commit) |
| `github_commit_pulls` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#list-pull-requests-associated-with-a-commit) |
| `github_compare_commits` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#compare-two-commits) |
| `github_compare_files` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#compare-two-commits) |
| `github_pulls` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28#list-pull-requests) |
| `github_merged_pulls` | changes | GET `/search/issues` | [docs](https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-issues-and-pull-requests) |
| `github_pull_files` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28#list-pull-requests-files) |
| `github_deployments` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/deployments/deployments?apiVersion=2022-11-28#list-deployments) |
| `github_deployment_statuses` | events | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/deployments/statuses?apiVersion=2022-11-28#list-deployment-statuses) |
| `github_releases` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases) |
| `github_tags` | entities | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#list-repository-tags) |
| `github_workflow_runs` | changes | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#list-workflow-runs-for-a-repository) |
| `github_workflow_jobs` | events | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#list-jobs-for-a-workflow-run) |
| `github_job_logs` | logs | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#download-job-logs-for-a-workflow-run) |
| `github_check_runs` | events | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/checks/runs?apiVersion=2022-11-28#list-check-runs-for-a-git-reference) |
| `github_commit_statuses` | events | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/commits/statuses?apiVersion=2022-11-28#get-the-combined-status-for-a-specific-reference) |
| `github_search_code` | text | GET `/search/code` | [docs](https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-code) |
| `github_search_issues` | generic | GET `/search/issues` | [docs](https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-issues-and-pull-requests) |
| `github_search_commits` | changes | GET `/search/commits` | [docs](https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-commits) |
| `github_contents` | entities | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#get-repository-content) |
| `github_file_lines` | text | GET `/repos/{{ filters.repo if '/' in filters.repo else env.GITHUB_OWNER ~ '/' ~ filters.rep...` | [docs](https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#get-repository-content) |

## GitLab (`gitlab`)

GitLab REST API v4: projects, commits (with stats and per-file diffs), compare (release diffs), merge requests and their diffs, merged-MR change feed for a group, deployments, environments, pipelines, jobs and job logs, releases, tags, repository files (raw lines, metadata, blame) and code (blob) search. Works with GitLab.com and self-managed instances.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `gitlab_projects` | entities | GET `{% if filters.group or env.GITLAB_GROUP %}/groups/{{ (filters.group or env.GITLAB_GROUP...` | [docs](https://docs.gitlab.com/api/groups/#list-projects) |
| `gitlab_commits` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/commits/#list-repository-commits) |
| `gitlab_commit` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/commits/#retrieve-a-commit) |
| `gitlab_commit_diff` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/commits/#retrieve-commit-diff) |
| `gitlab_commit_merge_requests` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/commits/#list-merge-requests-associated-with-a-commit) |
| `gitlab_compare_commits` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/repositories/#compare-branches-tags-or-commits) |
| `gitlab_compare_diffs` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/repositories/#compare-branches-tags-or-commits) |
| `gitlab_merge_requests` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/merge_requests/#list-project-merge-requests) |
| `gitlab_merged_merge_requests` | changes | GET `{% if filters.project %}/projects/{{ ((filters.project \| string) if ('/' in (filters.p...` | [docs](https://docs.gitlab.com/api/merge_requests/#list-group-merge-requests) |
| `gitlab_merge_request_diffs` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/merge_requests/#list-merge-request-diffs) |
| `gitlab_deployments` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/deployments/#list-all-project-deployments) |
| `gitlab_environments` | entities | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/environments/#list-all-environments) |
| `gitlab_pipelines` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/pipelines/#list-project-pipelines) |
| `gitlab_pipeline_jobs` | events | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/jobs/#list-all-jobs-by-pipeline) |
| `gitlab_jobs` | events | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/jobs/#list-all-jobs-for-a-project) |
| `gitlab_job_log` | logs | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/jobs/#retrieve-a-log-file-for-a-job) |
| `gitlab_releases` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/releases/#list-releases) |
| `gitlab_tags` | entities | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/tags/#list-all-project-repository-tags) |
| `gitlab_repository_file` | entities | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/repository_files/#retrieve-a-file-from-a-repository) |
| `gitlab_file_lines` | text | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/repository_files/#retrieve-a-raw-file-from-a-repository) |
| `gitlab_file_blame` | changes | GET `/projects/{{ ((filters.project \| string) if ('/' in (filters.project \| string) or (fi...` | [docs](https://docs.gitlab.com/api/repository_files/#retrieve-file-blame-history-from-a-repository) |
| `gitlab_search_blobs` | text | GET `{% if filters.project %}/projects/{{ ((filters.project \| string) if ('/' in (filters.p...` | [docs](https://docs.gitlab.com/api/search/#search-a-project) |

## Google Cloud Error Reporting (`gcp_error_reporting`)

Error Reporting v1beta1: error groups with counts, affected users/services, first/last seen and a representative stack trace; per-group occurrence counts over time; individual error events; the error group record (resolution status incl. MUTED, tracking issues).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `gcp_error_reporting_groups` | events | GET `/v1beta1/projects/{{ env.GCP_PROJECT_ID }}{{ ('/locations/' ~ env.GCP_ERROR_REPORTING_L...` | [docs](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groupStats/list) |
| `gcp_error_reporting_group_counts` | timeseries | GET `/v1beta1/projects/{{ env.GCP_PROJECT_ID }}{{ ('/locations/' ~ env.GCP_ERROR_REPORTING_L...` | [docs](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groupStats/list) |
| `gcp_error_reporting_events` | events | GET `/v1beta1/projects/{{ env.GCP_PROJECT_ID }}{{ ('/locations/' ~ env.GCP_ERROR_REPORTING_L...` | [docs](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.events/list) |
| `gcp_error_reporting_group` | entities | GET `/v1beta1/projects/{{ env.GCP_PROJECT_ID }}{{ ('/locations/' ~ env.GCP_ERROR_REPORTING_L...` | [docs](https://docs.cloud.google.com/error-reporting/reference/rest/v1beta1/projects.groups/get) |

## Google Cloud Logging (`gcp_logging`)

Cloud Logging v2: log entry search (entries:list with the Logging query language, newest first), log names, log buckets (retention) and log-based metric definitions. Includes Cloud Audit Logs (admin activity = deploys/config changes).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `gcp_logging_entries` | logs | POST `/v2/entries:list` | [docs](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/entries/list) |
| `gcp_logging_logs` | entities | GET `/v2/projects/{{ filters.project or env.GCP_PROJECT_ID }}/logs` | [docs](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.logs/list) |
| `gcp_logging_buckets` | entities | GET `/v2/projects/{{ filters.project or env.GCP_PROJECT_ID }}/locations/-/buckets` | [docs](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.locations.buckets/list) |
| `gcp_logging_log_metrics` | entities | GET `/v2/projects/{{ filters.project or env.GCP_PROJECT_ID }}/metrics` | [docs](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/projects.metrics/list) |

## Google Cloud Monitoring (`gcp_monitoring`)

Cloud Monitoring v3: time series (timeSeries.list with filter + aggregation, points exploded to rows), series discovery (view=HEADERS), metric descriptors, alerting policies with their conditions, alerts (open/closed incidents), alert snoozes, uptime check configs, SLOs and the Monitoring service inventory.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `gcp_monitoring_time_series` | timeseries | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/timeSeries` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list) |
| `gcp_monitoring_time_series_headers` | entities | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/timeSeries` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list) |
| `gcp_monitoring_metric_descriptors` | entities | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/metricDescriptors` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.metricDescriptors/list) |
| `gcp_monitoring_alert_policies` | entities | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/alertPolicies` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alertPolicies/list) |
| `gcp_monitoring_alerts` | alerts | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/alerts` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.alerts/list) |
| `gcp_monitoring_uptime_checks` | entities | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/uptimeCheckConfigs` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.uptimeCheckConfigs/list) |
| `gcp_monitoring_slos` | entities | GET `/v3/{{ ('projects/' ~ env.GCP_PROJECT_ID ~ '/services/' ~ filters.service_id) if filter...` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/services.serviceLevelObjectives/list) |
| `gcp_monitoring_services` | entities | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/services` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/services/list) |
| `gcp_monitoring_snoozes` | events | GET `/v3/projects/{{ filters.project or env.GCP_PROJECT_ID }}/snoozes` | [docs](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.snoozes/list) |

## Google Cloud Trace (`gcp_trace`)

Cloud Trace v1: trace search (root spans with latency and HTTP status), span search across traces, and full trace detail by trace id.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `gcp_trace_traces` | traces | GET `/v1/projects/{{ env.GCP_PROJECT_ID }}/traces` | [docs](https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces/list) |
| `gcp_trace_spans` | spans | GET `/v1/projects/{{ env.GCP_PROJECT_ID }}/traces` | [docs](https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces/list) |
| `gcp_trace_trace` | spans | GET `/v1/projects/{{ env.GCP_PROJECT_ID }}/traces/{{ filters.trace_id }}` | [docs](https://docs.cloud.google.com/trace/docs/reference/v1/rest/v1/projects.traces/get) |

## Google Cloud Managed Service for Prometheus (`google_prometheus`)

Google Managed Prometheus / Cloud Monitoring PromQL API for a metrics scope: range and instant queries, series, labels, label values, metadata and exemplars (OAuth2 / service account).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `google_prometheus_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/query_range) |
| `google_prometheus_query` | timeseries | GET `/api/v1/query` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/query) |
| `google_prometheus_series` | entities | GET `/api/v1/series` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/series) |
| `google_prometheus_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1.label/values) |
| `google_prometheus_metric_metadata` | entities | GET `/api/v1/metadata` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1.metadata/list) |
| `google_prometheus_labels` | entities | GET `/api/v1/labels` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/labels) |
| `google_prometheus_exemplars` | timeseries | GET `/api/v1/query_exemplars` | [docs](https://cloud.google.com/monitoring/api/ref_v3/rest/v1/projects.location.prometheus.api.v1/query_exemplars) |

## Prometheus via Grafana (`grafana_prometheus`)

Prometheus-compatible metrics API (PromQL range/instant queries, series, labels, metadata, alerts, rules, targets) proxied by Grafana for one Prometheus/Mimir data source.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `grafana_prometheus_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries) |
| `grafana_prometheus_query` | timeseries | GET `/api/v1/query` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#instant-queries) |
| `grafana_prometheus_series` | entities | GET `/api/v1/series` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#finding-series-by-label-matchers) |
| `grafana_prometheus_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#querying-label-values) |
| `grafana_prometheus_metric_metadata` | entities | GET `/api/v1/metadata` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#querying-metric-metadata) |
| `grafana_prometheus_alerts` | alerts | GET `/api/v1/alerts` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#alerts) |
| `grafana_prometheus_rules` | entities | GET `/api/v1/rules` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#rules) |
| `grafana_prometheus_targets` | entities | GET `/api/v1/targets` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#targets) |
| `grafana_prometheus_labels` | entities | GET `/api/v1/labels` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#getting-label-names) |

## Grafana (`grafana`)

Grafana HTTP API: dashboards and their panel queries, data sources, unified /api/ds/query over any data source (Prometheus, Loki, Tempo, SQL...), annotations, and Grafana-managed alerting.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `grafana_search` | entities | GET `/api/search` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/folder_dashboard_search/) |
| `grafana_folders` | entities | GET `/api/folders{{ ('/' ~ filters.uid) if filters.uid else '' }}` | [docs](https://grafana.com/docs/grafana/v12.4/developers/http_api/folder/) |
| `grafana_dashboard` | entities | GET `/api/dashboards/uid/{{ filters.uid }}` | [docs](https://grafana.com/docs/grafana/v12.4/developers/http_api/dashboard/#get-dashboard-by-uid) |
| `grafana_dashboard_panels` | entities | GET `/api/dashboards/uid/{{ filters.dashboard_uid }}` | [docs](https://grafana.com/docs/grafana/v12.4/developers/http_api/dashboard/#get-dashboard-by-uid) |
| `grafana_dashboard_variables` | entities | GET `/api/dashboards/uid/{{ filters.dashboard_uid }}` | [docs](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/#templating) |
| `grafana_dashboard_annotation_queries` | entities | GET `/api/dashboards/uid/{{ filters.dashboard_uid }}` | [docs](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/annotate-visualizations/) |
| `grafana_dashboard_versions` | changes | GET `/api/dashboards/uid/{{ filters.dashboard_uid }}/versions` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/dashboard_versions/) |
| `grafana_dashboard_version` | changes | GET `/api/dashboards/uid/{{ filters.dashboard_uid }}/versions/{{ filters.version }}` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/dashboard_versions/#get-dashboard-version) |
| `grafana_library_panels` | entities | GET `/api/library-elements` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/library_element/) |
| `grafana_datasources` | entities | GET `/api/datasources` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#get-all-data-sources) |
| `grafana_datasource` | entities | GET `/api/datasources/uid/{{ filters.uid }}` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#get-a-single-data-source-by-uid) |
| `grafana_datasource_health` | entities | GET `/api/datasources/uid/{{ filters.uid }}/health` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#check-data-source-health) |
| `grafana_ds_query` | generic | POST `/api/ds/query` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/data_source/#query-a-data-source) |
| `grafana_annotations` | events | GET `/api/annotations` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/annotations/#find-annotations) |
| `grafana_annotation_tags` | entities | GET `/api/annotations/tags` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/annotations/#find-annotations-tags) |
| `grafana_alert_rules` | entities | GET `/api/v1/provisioning/alert-rules{{ ('/' ~ filters.uid) if filters.uid else '' }}` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-alert-rules) |
| `grafana_alert_rule_queries` | entities | GET `/api/v1/provisioning/alert-rules{{ ('/' ~ filters.rule_uid) if filters.rule_uid else '' }}` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#alertquery) |
| `grafana_alert_rule_status` | alerts | GET `/api/prometheus/{{ filters.rules_source or 'grafana' }}/api/v1/rules` | [docs](https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/tooling/post.json) |
| `grafana_alert_instances` | alerts | GET `/api/prometheus/grafana/api/v1/alerts` | [docs](https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/tooling/post.json) |
| `grafana_active_alerts` | alerts | GET `/api/alertmanager/{{ filters.alertmanager or 'grafana' }}/api/v2/alerts` | [docs](https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/tooling/post.json) |
| `grafana_silences` | entities | GET `/api/alertmanager/{{ filters.alertmanager or 'grafana' }}/api/v2/silences` | [docs](https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/tooling/post.json) |
| `grafana_alert_state_history` | events | GET `/api/v1/rules/history` | [docs](https://github.com/grafana/grafana/blob/main/pkg/services/ngalert/api/tooling/post.json) |
| `grafana_contact_points` | entities | GET `/api/v1/provisioning/contact-points` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-contactpoints) |
| `grafana_notification_policies` | entities | GET `/api/v1/provisioning/policies` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-policy-tree) |
| `grafana_mute_timings` | entities | GET `/api/v1/provisioning/mute-timings` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/alerting_provisioning/#route-get-mute-timings) |
| `grafana_health` | entities | GET `/api/health` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/other/#health-api) |
| `grafana_frontend_settings` | entities | GET `/api/frontend/settings` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/other/#get-settings) |
| `grafana_dashboard_write` | write | POST `/api/dashboards/db` | [docs](https://grafana.com/docs/grafana/v12.4/developers/http_api/dashboard/#create--update-dashboard) |
| `grafana_annotation_write` | write | POST `/api/annotations` | [docs](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/annotations/#create-annotation) |

## Grafana Loki (via Grafana datasource proxy) (`grafana_loki`)

Loki LogQL logs, metrics, labels, volume, patterns and ruler data queried through a Grafana Loki datasource (no direct Loki access or Loki credentials needed).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `grafana_loki_logs` | logs | GET `/loki/api/v1/query_range` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) |
| `grafana_loki_query_range` | timeseries | GET `/loki/api/v1/query_range` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) |
| `grafana_loki_query` | timeseries | GET `/loki/api/v1/query` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-at-a-single-point-in-time) |
| `grafana_loki_labels` | entities | GET `/loki/api/v1/labels` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-labels) |
| `grafana_loki_label_values` | entities | GET `/loki/api/v1/label/{{ filters.label }}/values` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-label-values) |
| `grafana_loki_series` | entities | GET `/loki/api/v1/series` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-streams) |
| `grafana_loki_index_stats` | generic | GET `/loki/api/v1/index/stats` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-statistics) |
| `grafana_loki_volume` | generic | GET `/loki/api/v1/index/volume` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-volume) |
| `grafana_loki_volume_range` | timeseries | GET `/loki/api/v1/index/volume_range` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-volume) |
| `grafana_loki_patterns` | timeseries | GET `/loki/api/v1/patterns` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#patterns-detection) |
| `grafana_loki_detected_fields` | entities | GET `/loki/api/v1/detected_fields` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#detected-fields) |
| `grafana_loki_detected_field_values` | entities | GET `/loki/api/v1/detected_field/{{ filters.field }}/values` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#detected-field-values) |
| `grafana_loki_rules` | entities | GET `/prometheus/api/v1/rules` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#list-rules) |
| `grafana_loki_alerts` | alerts | GET `/prometheus/api/v1/alerts` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#list-alerts) |
| `grafana_loki_buildinfo` | entities | GET `/loki/api/v1/status/buildinfo` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#show-build-information) |

## Grafana Tempo (via Grafana datasource proxy) (`grafana_tempo`)

Tempo TraceQL search, trace by id, TraceQL metrics and attribute discovery queried through a Grafana Tempo datasource (no direct Tempo access or Tempo credentials needed).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `grafana_tempo_search` | traces | GET `/api/search` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search) |
| `grafana_tempo_search_spans` | spans | GET `/api/search` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search) |
| `grafana_tempo_trace` | traces | GET `/api/v2/traces/{{ filters.trace_id \| trim \| lower }}` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#query-v2) |
| `grafana_tempo_trace_attributes` | traces | GET `/api/v2/traces/{{ filters.trace_id \| trim \| lower }}` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#query-v2) |
| `grafana_tempo_metrics_range` | timeseries | GET `/api/metrics/query_range` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics) |
| `grafana_tempo_metrics_instant` | timeseries | GET `/api/metrics/query` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#instant) |
| `grafana_tempo_metrics_exemplars` | traces | GET `/api/metrics/query_range` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics) |
| `grafana_tempo_tag_names` | entities | GET `/api/v2/search/tags` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search-tags-v2) |
| `grafana_tempo_tag_values` | entities | GET `/api/v2/search/tag/{{ filters.tag \| trim }}/values` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search-tag-values-v2) |

## Grafana Loki (`loki`)

Loki log API - LogQL log search, LogQL metric queries, labels, streams, log volume, patterns, detected fields, ruler rules/alerts.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `loki_logs` | logs | GET `/loki/api/v1/query_range` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) |
| `loki_query_range` | timeseries | GET `/loki/api/v1/query_range` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-within-a-range-of-time) |
| `loki_query` | timeseries | GET `/loki/api/v1/query` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-logs-at-a-single-point-in-time) |
| `loki_labels` | entities | GET `/loki/api/v1/labels` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-labels) |
| `loki_label_values` | entities | GET `/loki/api/v1/label/{{ filters.label }}/values` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-label-values) |
| `loki_series` | entities | GET `/loki/api/v1/series` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-streams) |
| `loki_index_stats` | generic | GET `/loki/api/v1/index/stats` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-statistics) |
| `loki_volume` | generic | GET `/loki/api/v1/index/volume` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-volume) |
| `loki_volume_range` | timeseries | GET `/loki/api/v1/index/volume_range` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#query-log-volume) |
| `loki_patterns` | timeseries | GET `/loki/api/v1/patterns` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#patterns-detection) |
| `loki_detected_fields` | entities | GET `/loki/api/v1/detected_fields` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#detected-fields) |
| `loki_detected_field_values` | entities | GET `/loki/api/v1/detected_field/{{ filters.field }}/values` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#detected-field-values) |
| `loki_rules` | entities | GET `/prometheus/api/v1/rules` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#list-rules) |
| `loki_alerts` | alerts | GET `/prometheus/api/v1/alerts` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#list-alerts) |
| `loki_buildinfo` | entities | GET `/loki/api/v1/status/buildinfo` | [docs](https://grafana.com/docs/loki/latest/reference/loki-http-api/#show-build-information) |

## Grafana Mimir / Grafana Cloud Metrics (`mimir`)

Grafana Mimir (self-hosted, GEM or Grafana Cloud Metrics): PromQL range/instant queries, series, labels, metadata, exemplars, cardinality, ruler rules/alerts and Alertmanager alerts/silences, per tenant.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `mimir_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#range-query) |
| `mimir_query` | timeseries | GET `/api/v1/query` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#instant-query) |
| `mimir_series` | entities | GET `/api/v1/series` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#get-series-by-label-matchers) |
| `mimir_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#get-label-values) |
| `mimir_metric_metadata` | entities | GET `/api/v1/metadata` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#get-metric-metadata) |
| `mimir_alerts` | alerts | GET `/api/v1/alerts` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#list-prometheus-alerts) |
| `mimir_rules` | entities | GET `/api/v1/rules` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#list-prometheus-rules) |
| `mimir_labels` | entities | GET `/api/v1/labels` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#get-label-names) |
| `mimir_exemplars` | timeseries | GET `/api/v1/query_exemplars` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#exemplar-query) |
| `mimir_active_series` | entities | GET `/api/v1/cardinality/active_series` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#get-active-series-by-selector) |
| `mimir_cardinality_label_names` | entities | GET `/api/v1/cardinality/label_names` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#label-names-cardinality) |
| `mimir_cardinality_label_values` | entities | GET `/api/v1/cardinality/label_values` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#label-values-cardinality) |
| `mimir_alertmanager_alerts` | alerts | GET `{{ (env.MIMIR_ALERTMANAGER_URL or (env.MIMIR_URL.rstrip('/') ~ env.MIMIR_ALERTMANAGER_P...` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#alertmanager-api) |
| `mimir_silences` | entities | GET `{{ (env.MIMIR_ALERTMANAGER_URL or (env.MIMIR_URL.rstrip('/') ~ env.MIMIR_ALERTMANAGER_P...` | [docs](https://grafana.com/docs/mimir/latest/references/http-api/#alertmanager-api) |

## Grafana Pyroscope (`pyroscope`)

Continuous profiling - profile types, label discovery, profile time series per group (with exemplars linking to traces), top functions of a merged flame graph, and before/after flame graph diffs.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `pyroscope_profile_types` | entities | POST `/querier.v1.QuerierService/ProfileTypes` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceprofiletypes) |
| `pyroscope_label_names` | entities | POST `/querier.v1.QuerierService/LabelNames` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierservicelabelnames) |
| `pyroscope_label_values` | entities | POST `/querier.v1.QuerierService/LabelValues` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierservicelabelvalues) |
| `pyroscope_series` | entities | POST `/querier.v1.QuerierService/Series` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceseries) |
| `pyroscope_select_series` | timeseries | POST `/querier.v1.QuerierService/SelectSeries` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceselectseries) |
| `pyroscope_exemplars` | profiles | POST `/querier.v1.QuerierService/SelectSeries` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceselectseries) |
| `pyroscope_top_functions` | profiles | python: `top_functions` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierserviceselectmergestacktraces) |
| `pyroscope_diff_functions` | profiles | python: `diff_functions` | [docs](https://grafana.com/docs/pyroscope/latest/reference-server-api/#querierv1querierservicediff) |

## Grafana Tempo (`tempo`)

Tempo tracing API - TraceQL trace/span search, trace by id (all spans), TraceQL metrics (range, instant, exemplars), attribute names and values.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `tempo_search` | traces | GET `/api/search` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search) |
| `tempo_search_spans` | spans | GET `/api/search` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search) |
| `tempo_trace` | traces | GET `/api/v2/traces/{{ filters.trace_id \| trim \| lower }}` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#query-v2) |
| `tempo_trace_attributes` | traces | GET `/api/v2/traces/{{ filters.trace_id \| trim \| lower }}` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#query-v2) |
| `tempo_metrics_range` | timeseries | GET `/api/metrics/query_range` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics) |
| `tempo_metrics_instant` | timeseries | GET `/api/metrics/query` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#instant) |
| `tempo_metrics_exemplars` | traces | GET `/api/metrics/query_range` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#traceql-metrics) |
| `tempo_tag_names` | entities | GET `/api/v2/search/tags` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search-tags-v2) |
| `tempo_tag_values` | entities | GET `/api/v2/search/tag/{{ filters.tag \| trim }}/values` | [docs](https://grafana.com/docs/tempo/latest/api_docs/#search-tag-values-v2) |

## Honeycomb (`honeycomb`)

Honeycomb observability API: run aggregate queries (COUNT, P99, HEATMAP, breakdowns, filters) over trace spans / events with the async Query Data API, read deploy markers, triggers (alert rules + currently-triggered state), SLOs with burn rate / budget, burn alerts, anomaly signals, service dependency maps, dataset schemas and boards.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `honeycomb_query` | traces | POST `/1/queries/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` → POST `/1/query_results/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` → GET `/1/query_results/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}/{{...` | [docs](https://docs.honeycomb.io/api/query-data/create-a-query-result) |
| `honeycomb_query_series` | timeseries | POST `/1/queries/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` → POST `/1/query_results/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` → GET `/1/query_results/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}/{{...` | [docs](https://docs.honeycomb.io/api/query-data/get-query-result) |
| `honeycomb_query_specs` | entities | GET `/1/queries/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}/{{ filte...` | [docs](https://docs.honeycomb.io/api/queries/get-a-query) |
| `honeycomb_query_annotations` | entities | GET `/1/query_annotations/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/query-annotations/list-query-annotations) |
| `honeycomb_datasets` | entities | GET `/1/datasets` | [docs](https://docs.honeycomb.io/api/datasets/list-all-datasets) |
| `honeycomb_columns` | entities | GET `/1/columns/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/columns/list-all-columns) |
| `honeycomb_dataset_definitions` | entities | GET `/1/dataset_definitions/{{ filters.dataset }}` | [docs](https://docs.honeycomb.io/api/dataset-definitions/get-all-dataset-definitions) |
| `honeycomb_calculated_fields` | entities | GET `/1/derived_columns/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/calculated-fields/list-all-calculated-fields) |
| `honeycomb_auth` | entities | GET `/1/auth` | [docs](https://docs.honeycomb.io/api/auth/list-authorizations) |
| `honeycomb_markers` | changes | GET `/1/markers/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/markers/list-all-markers) |
| `honeycomb_triggers` | alerts | GET `/1/triggers/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/triggers/list-all-triggers) |
| `honeycomb_slos` | entities | GET `/1/slos/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/slos/get-all-slos) |
| `honeycomb_slo_status` | alerts | GET `/1/slos/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}/{{ filters....` | [docs](https://docs.honeycomb.io/api/slos/get-an-slo) |
| `honeycomb_slo_history` | timeseries | POST `/1/reporting/slos/historical` | [docs](https://docs.honeycomb.io/api/reporting/get-slo-history) |
| `honeycomb_slo_hourly_counts` | timeseries | GET `/1/slos/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}/{{ filters....` | [docs](https://docs.honeycomb.io/api/slos/get-slo-hourly-counts-history) |
| `honeycomb_slo_counts` | timeseries | GET `/1/slos/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}/{{ filters....` | [docs](https://docs.honeycomb.io/api/slos/get-slo-realtime-counts) |
| `honeycomb_burn_alerts` | alerts | GET `/1/burn_alerts/{{ filters.dataset or env.HONEYCOMB_DEFAULT_DATASET or '__all__' }}` | [docs](https://docs.honeycomb.io/api/burn-alerts/list-all-burn-alerts-for-an-slo) |
| `honeycomb_signals` | alerts | GET `/1/signals` | [docs](https://docs.honeycomb.io/api/anomaly-detection/list-all-signals) |
| `honeycomb_signal_anomalies` | events | GET `/1/signals/{{ filters.signal_id }}/historical_anomalies` | [docs](https://docs.honeycomb.io/api/anomaly-detection/list-historical-anomalies) |
| `honeycomb_service_map` | entities | POST `/1/maps/dependencies/requests` → GET `/1/maps/dependencies/requests/{{ captures.request_id }}` | [docs](https://docs.honeycomb.io/api/service-maps/get-map-dependencies) |
| `honeycomb_boards` | entities | GET `/1/boards` | [docs](https://docs.honeycomb.io/api/boards/list-all-boards) |

## IBM Instana Observability (`instana`)

IBM Instana REST API: application perspectives, services and endpoints with their metrics (calls, errors, latency percentiles, HTTP status classes), grouped call/trace analytics, trace search and trace detail, service topology, events (incidents, issues, changes), infrastructure snapshots and metrics, releases (deployment markers), logs and Smart Alert configurations.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `instana_applications` | entities | GET `/api/application-monitoring/applications` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getApplications) |
| `instana_services` | entities | GET `/api/application-monitoring/services` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getServices) |
| `instana_service_metrics` | timeseries | POST `/api/application-monitoring/metrics/services` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getServicesMetrics) |
| `instana_application_metrics` | timeseries | POST `/api/application-monitoring/metrics/applications` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getApplicationMetrics) |
| `instana_endpoint_metrics` | timeseries | POST `/api/application-monitoring/metrics/endpoints` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getEndpointsMetrics) |
| `instana_metrics` | timeseries | POST `/api/application-monitoring/v2/metrics` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getApplicationDataMetricsV2) |
| `instana_metric_catalog` | entities | GET `/api/application-monitoring/catalog/metrics` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getApplicationCatalogMetrics) |
| `instana_call_groups` | timeseries | POST `/api/application-monitoring/analyze/call-groups` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getCallGroup) |
| `instana_traces` | traces | POST `/api/application-monitoring/analyze/traces` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getTraces) |
| `instana_trace_groups` | timeseries | POST `/api/application-monitoring/analyze/trace-groups` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getTraceGroups) |
| `instana_trace_calls` | spans | GET `/api/application-monitoring/v2/analyze/traces/{{ filters.trace_id \| urlencode }}` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getTraceDownload) |
| `instana_service_map` | entities | GET `/api/application-monitoring/topology/services` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getServicesMap) |
| `instana_events` | events | GET `/api/events` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getEvents) |
| `instana_event_detail` | incidents | GET `/api/events/{{ filters.event_id \| urlencode }}` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getEvent) |
| `instana_snapshots` | entities | GET `/api/infrastructure-monitoring/snapshots` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getSnapshots) |
| `instana_infrastructure_metrics` | timeseries | POST `/api/infrastructure-monitoring/metrics` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getInfrastructureMetrics) |
| `instana_releases` | changes | GET `/api/releases` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getAllReleases) |
| `instana_logs` | logs | POST `/api/logging/logs/getLogs/v1` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#getLogs) |
| `instana_smart_alert_configs` | entities | GET `/api/events/settings/application-alert-configs` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#findAllActiveApplicationAlertConfigs) |
| `instana_smart_alert_config` | entities | GET `/api/events/settings/application-alert-configs/{{ filters.alert_id \| urlencode }}` | [docs](https://developer.ibm.com/apis/catalog/instana--instana-rest-api/api/API--instana--instana-rest-api-documentation#findApplicationAlertConfig) |

## incident.io (`incidentio`)

incident.io API: incidents (list with status-category / severity / type / mode filters, and detail), incident updates, timeline items, activity log, participants, follow-ups, attached external resources, related incidents, post-mortem documents and their text, alerts (incident.io Alerts / On-call) and their links to incidents, escalations, maintenance windows, on-call schedules (current shifts, r…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `incidentio_incidents` | incidents | GET `/v2/incidents` | [docs](https://docs.incident.io/api-reference/incidents-v2/list) |
| `incidentio_incident` | incidents | GET `/v2/incidents/{{ filters.incident }}` | [docs](https://docs.incident.io/api-reference/incidents-v2/show) |
| `incidentio_incident_updates` | events | GET `/v2/incident_updates` | [docs](https://docs.incident.io/api-reference/incident-updates-v2/list) |
| `incidentio_timeline_items` | events | GET `/v2/incident_timeline_items` | [docs](https://docs.incident.io/api-reference/incident-timeline-items-v2/list) |
| `incidentio_activity_log` | events | GET `/v2/incident_activity_log_entries` | [docs](https://docs.incident.io/api-reference/incident-activity-log-entries-v2/list) |
| `incidentio_incident_participants` | entities | GET `/v2/incident_participants` | [docs](https://docs.incident.io/api-reference/incident-participants-v2/list) |
| `incidentio_follow_ups` | generic | GET `/v3/follow_ups` | [docs](https://docs.incident.io/api-reference/follow-ups-v3/list) |
| `incidentio_incident_attachments` | entities | GET `/v1/incident_attachments` | [docs](https://docs.incident.io/api-reference/incident-attachments-v1/list) |
| `incidentio_incident_relationships` | incidents | GET `/v1/incident_relationships` | [docs](https://docs.incident.io/api-reference/incident-relationships-v1/list) |
| `incidentio_postmortem_documents` | entities | GET `/v1/postmortem_documents` | [docs](https://docs.incident.io/api-reference/postmortemdocuments-v1/list) |
| `incidentio_postmortem_content` | text | GET `/v1/postmortem_documents/{{ filters.document_id }}/content` | [docs](https://docs.incident.io/api-reference/postmortemdocuments-v1/show-content) |
| `incidentio_alerts` | alerts | GET `/v2/alerts` | [docs](https://docs.incident.io/api-reference/alerts-v2/list) |
| `incidentio_firing_alerts` | alerts | GET `/v2/alerts` | [docs](https://docs.incident.io/api-reference/alerts-v2/list) |
| `incidentio_incident_alerts` | alerts | GET `/v2/incident_alerts` | [docs](https://docs.incident.io/api-reference/incident-alerts-v2/list) |
| `incidentio_escalations` | events | GET `/v2/escalations` | [docs](https://docs.incident.io/api-reference/escalations-v2/list) |
| `incidentio_maintenance_windows` | events | GET `/v1/maintenance_windows` | [docs](https://docs.incident.io/api-reference/maintenancewindows-v1/list) |
| `incidentio_oncall_now` | entities | GET `/v2/schedules` | [docs](https://docs.incident.io/api-reference/schedules-v2/list) |
| `incidentio_schedules` | entities | GET `/v2/schedules` | [docs](https://docs.incident.io/api-reference/schedules-v2/list) |
| `incidentio_schedule_entries` | entities | GET `/v2/schedule_entries` | [docs](https://docs.incident.io/api-reference/schedule-entries-v2/list) |
| `incidentio_schedule_overrides` | entities | GET `/v2/schedule_overrides` | [docs](https://docs.incident.io/api-reference/schedule-overrides-v2/list) |
| `incidentio_severities` | entities | GET `/v1/severities` | [docs](https://docs.incident.io/api-reference/severities-v1/list) |
| `incidentio_incident_statuses` | entities | GET `/v1/incident_statuses` | [docs](https://docs.incident.io/api-reference/incident-statuses-v1/list) |
| `incidentio_incident_types` | entities | GET `/v1/incident_types` | [docs](https://docs.incident.io/api-reference/incident-types-v1/list) |
| `incidentio_custom_fields` | entities | GET `/v2/custom_fields` | [docs](https://docs.incident.io/api-reference/custom-fields-v2/list) |
| `incidentio_users` | entities | GET `/v2/users` | [docs](https://docs.incident.io/api-reference/users-v2/list) |
| `incidentio_catalog_types` | entities | GET `/v3/catalog_types` | [docs](https://docs.incident.io/api-reference/catalog-types-v3/list-types) |
| `incidentio_catalog_entries` | entities | GET `/v3/catalog_entries` | [docs](https://docs.incident.io/api-reference/catalog-entries-v3/list-entries) |

## Kubernetes (`kubernetes`)

Live Kubernetes state over the API server: pods (one row per container status), container logs (incl. the previous crashed instance), events, workloads (deployments, replicasets = rollout history, statefulsets, daemonsets, jobs, cronjobs), services and endpoint slices, configmaps, secret metadata (key names only), network policies, storage (PVC/PV/StorageClass), quotas and limit ranges, HPAs, nod…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `kubernetes_pods` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#get-list) |
| `kubernetes_pod_logs` | logs | GET `/api/v1/namespaces/{{ filters.namespace }}/pods/{{ filters.pod }}/log` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#get-read-log) |
| `kubernetes_container_env` | entities | python: `container_env` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/pod-v1/#Container) |
| `kubernetes_events` | events | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/event-v1/#get-list) |
| `kubernetes_events_v1` | events | GET `/apis/events.k8s.io/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/events/event-v1/#get-list) |
| `kubernetes_deployments` | entities | GET `/apis/apps/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filt...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/#get-list) |
| `kubernetes_replicasets` | entities | GET `/apis/apps/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filt...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/apps/replica-set-v1/#get-list) |
| `kubernetes_statefulsets` | entities | GET `/apis/apps/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filt...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/apps/stateful-set-v1/#get-list) |
| `kubernetes_daemonsets` | entities | GET `/apis/apps/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filt...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/apps/daemon-set-v1/#get-list) |
| `kubernetes_jobs` | entities | GET `/apis/batch/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not fil...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/batch/job-v1/#get-list) |
| `kubernetes_cronjobs` | entities | GET `/apis/batch/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not fil...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/batch/cron-job-v1/#get-list) |
| `kubernetes_workload_containers` | entities | GET `{{ '/apis/batch/v1' if (filters.kind or 'deployments') \| lower in ['jobs', 'job'] else...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/#DeploymentSpec) |
| `kubernetes_services` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/service-v1/#get-list) |
| `kubernetes_endpointslices` | entities | GET `/apis/discovery.k8s.io/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace ...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/discovery/endpoint-slice-v1/#get-list) |
| `kubernetes_networkpolicies` | entities | GET `/apis/networking.k8s.io/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/networking/network-policy-v1/#get-list) |
| `kubernetes_ingresses` | entities | GET `/apis/networking.k8s.io/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/networking/ingress-v1/#get-list) |
| `kubernetes_configmaps` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/config-map-v1/#get-list) |
| `kubernetes_secrets` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/secret-v1/#get-list) |
| `kubernetes_persistentvolumeclaims` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/persistent-volume-claim-v1/#get-list) |
| `kubernetes_persistentvolumes` | entities | GET `/api/v1/persistentvolumes` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/persistent-volume-v1/#get-list) |
| `kubernetes_storageclasses` | entities | GET `/apis/storage.k8s.io/v1/storageclasses` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/storage/storage-class-v1/#get-list) |
| `kubernetes_resourcequotas` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/resource-quota-v1/#get-list) |
| `kubernetes_limitranges` | entities | GET `/api/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not filters.na...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/limit-range-v1/#get-list) |
| `kubernetes_horizontalpodautoscalers` | entities | GET `/apis/autoscaling/v2{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and n...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/autoscaling/horizontal-pod-autoscaler-v2/#get-list) |
| `kubernetes_poddisruptionbudgets` | entities | GET `/apis/policy/v1{{ ('/namespaces/' ~ filters.namespace) if (filters.namespace and not fi...` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/policy/pod-disruption-budget-v1/#get-list) |
| `kubernetes_nodes` | entities | GET `/api/v1/nodes` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/node-v1/#get-list) |
| `kubernetes_namespaces` | entities | GET `/api/v1/namespaces` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/core/namespace-v1/#get-list) |
| `kubernetes_validating_webhooks` | entities | GET `/apis/admissionregistration.k8s.io/v1/validatingwebhookconfigurations` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/admissionregistration/validating-webhook-configuration-v1/#get-list) |
| `kubernetes_mutating_webhooks` | entities | GET `/apis/admissionregistration.k8s.io/v1/mutatingwebhookconfigurations` | [docs](https://kubernetes.io/docs/reference/kubernetes-api/admissionregistration/mutating-webhook-configuration-v1/#get-list) |
| `kubernetes_pod_metrics` | entities | GET `/apis/metrics.k8s.io/{{ env.K8S_METRICS_API_VERSION or 'v1beta1' }}{{ ('/namespaces/' ~...` | [docs](https://kubernetes.io/docs/reference/external-api/metrics.v1beta1/) |
| `kubernetes_node_metrics` | entities | GET `/apis/metrics.k8s.io/{{ env.K8S_METRICS_API_VERSION or 'v1beta1' }}/nodes` | [docs](https://kubernetes.io/docs/reference/external-api/metrics.v1beta1/) |

## Azure Monitor Logs (Log Analytics + Application Insights) (`azure_log_analytics`)

KQL over a Log Analytics workspace: free-form queries (workspace, cross-workspace, resource-centric, Basic/Auxiliary plans), workspace-based Application Insights requests / dependencies / exceptions / traces with RED statistics, dependency map, availability tests and end-to-end trace lookup, AKS container logs (ContainerLogV2), pod state (KubePodInventory) and Kubernetes events, agent heartbeats,…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `azure_log_analytics_query` | logs | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/{{ 's...` | [docs](https://learn.microsoft.com/en-us/rest/api/logsquery/query/execute?view=rest-logsquery-v1) |
| `azure_log_analytics_resource_query` | logs | POST `/v1/{{ filters.resource_id.strip('/') }}/query` | [docs](https://learn.microsoft.com/en-us/rest/api/logsquery/query/execute-with-resource-id?view=rest-logsquery-v1) |
| `azure_log_analytics_app_requests` | spans | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests) |
| `azure_log_analytics_app_request_stats` | timeseries | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests) |
| `azure_log_analytics_app_dependencies` | spans | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appdependencies) |
| `azure_log_analytics_app_dependency_map` | entities | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appdependencies) |
| `azure_log_analytics_app_exceptions` | logs | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appexceptions) |
| `azure_log_analytics_app_exception_groups` | alerts | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appexceptions) |
| `azure_log_analytics_app_traces` | logs | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apptraces) |
| `azure_log_analytics_log_counts` | timeseries | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apptraces) |
| `azure_log_analytics_trace` | traces | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/app/transaction-search-and-diagnostics) |
| `azure_log_analytics_app_services` | entities | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests) |
| `azure_log_analytics_container_logs` | logs | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/containerlogv2) |
| `azure_log_analytics_kube_events` | events | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/kubeevents) |
| `azure_log_analytics_kube_pod_inventory` | entities | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/kubepodinventory) |
| `azure_log_analytics_app_availability` | timeseries | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appavailabilityresults) |
| `azure_log_analytics_heartbeat` | entities | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/heartbeat) |
| `azure_log_analytics_table_usage` | timeseries | POST `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/usage) |
| `azure_log_analytics_schema` | entities | GET `/v1/workspaces/{{ filters.workspace_id or env.AZURE_LOG_ANALYTICS_WORKSPACE_ID }}/metadata` | [docs](https://learn.microsoft.com/en-us/rest/api/logsquery/metadata/get?view=rest-logsquery-v1) |

## Azure Monitor (metrics, alerts, activity log, Resource Graph, Service Health) (`azure_monitor`)

Azure Resource Manager APIs of Azure Monitor and friends: platform metrics per resource or per region across a subscription, metric definitions and namespaces, fired alerts with their history and summary counts, metric / log search / activity log alert rules, alert processing rules (suppression), autoscale settings, the activity log (control-plane changes), Resource Graph inventory and resource c…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `azure_monitor_metrics` | timeseries | GET `/{{ filters.resource_id.strip('/') }}/providers/Microsoft.Insights/metrics` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list) |
| `azure_monitor_subscription_metrics` | timeseries | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list-at-subscription-scope) |
| `azure_monitor_metric_definitions` | entities | GET `/{{ filters.resource_id.strip('/') }}/providers/Microsoft.Insights/metricDefinitions` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/metric-definitions/list) |
| `azure_monitor_metric_namespaces` | entities | GET `/{{ filters.resource_id.strip('/') }}/providers/microsoft.insights/metricNamespaces` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/metric-namespaces/list) |
| `azure_monitor_alerts` | alerts | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-all?view=rest-alerts-management-alerts-2019-03-01) |
| `azure_monitor_alert` | alerts | GET `{{ filters.alert_id.strip('/') if '/' in filters.alert_id else ('subscriptions/' ~ env....` | [docs](https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-by-id?view=rest-alerts-management-alerts-2019-03-01) |
| `azure_monitor_alert_history` | events | GET `{{ (filters.alert_id.strip('/') if '/' in filters.alert_id else ('subscriptions/' ~ env...` | [docs](https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-history?view=rest-alerts-management-alerts-2019-03-01) |
| `azure_monitor_alerts_summary` | alerts | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/alerts-management/alerts/alerts/get-summary?view=rest-alerts-management-alerts-2019-03-01) |
| `azure_monitor_metric_alert_rules` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/metric-alerts/list-by-subscription) |
| `azure_monitor_log_alert_rules` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/scheduled-query-rules/list-by-subscription) |
| `azure_monitor_activity_log_alert_rules` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/activity-log-alerts/list-by-subscription-id) |
| `azure_monitor_alert_processing_rules` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/alerts-management/processing-rules/alert-processing-rules/list-by-subscription?view=rest-alerts-management-processing-rules-2021-08-08) |
| `azure_monitor_autoscale_settings` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/autoscale-settings/list-by-subscription) |
| `azure_monitor_activity_log` | changes | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/monitor/activity-logs/list) |
| `azure_monitor_resource_graph` | generic | POST `/providers/Microsoft.ResourceGraph/resources` | [docs](https://learn.microsoft.com/en-us/rest/api/azureresourcegraph/resourcegraph/resources/resources) |
| `azure_monitor_resources` | entities | POST `/providers/Microsoft.ResourceGraph/resources` | [docs](https://learn.microsoft.com/en-us/azure/governance/resource-graph/samples/starter) |
| `azure_monitor_resource_changes` | changes | POST `/providers/Microsoft.ResourceGraph/resources` | [docs](https://learn.microsoft.com/en-us/azure/governance/resource-graph/changes/get-resource-changes) |
| `azure_monitor_service_health_events` | incidents | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/resourcehealth/events/list-by-subscription-id) |
| `azure_monitor_service_health_impacted_resources` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}/providers/Mic...` | [docs](https://learn.microsoft.com/en-us/rest/api/resourcehealth/impacted-resources/list-by-subscription-id-and-event-id) |
| `azure_monitor_resource_health` | entities | GET `/subscriptions/{{ filters.subscription_id or env.AZURE_SUBSCRIPTION_ID }}{{ ('/resource...` | [docs](https://learn.microsoft.com/en-us/rest/api/resourcehealth/availability-statuses/list-by-subscription-id) |
| `azure_monitor_resource_health_history` | events | GET `/{{ filters.resource_id.strip('/') }}/providers/Microsoft.ResourceHealth/availabilitySt...` | [docs](https://learn.microsoft.com/en-us/rest/api/resourcehealth/availability-statuses/list) |

## Azure Monitor managed Prometheus (`azure_prometheus`)

Azure Monitor workspace (managed Prometheus): PromQL range and instant queries, series, label names and metric names, authenticated with a Microsoft Entra app (client credentials).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `azure_prometheus_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#range-queries) |
| `azure_prometheus_query` | timeseries | GET `/api/v1/query` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#instant-queries) |
| `azure_prometheus_series` | entities | GET `/api/v1/series` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#series) |
| `azure_prometheus_labels` | entities | GET `/api/v1/labels` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#labels) |
| `azure_prometheus_metric_names` | entities | GET `/api/v1/label/__name__/values` | [docs](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/prometheus-api-promql#label-values) |

## New Relic (`newrelic`)

New Relic via NerdGraph (GraphQL, no MCP): NRQL over every NRDB event type (Transaction, Span, Log, Metric, NrAiIncident, ChangeTrackingEvent, Deployment ...), entity search with golden metrics/tags/ relationships, alert issues and incidents (former violations), alert policies, NRQL conditions and muting rules, change tracking events/deployments, service levels (SLIs/SLOs) and distributed traces.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `newrelic_nrql` | timeseries | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-nrql-tutorial/) |
| `newrelic_logs` | logs | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-nrql-tutorial/) |
| `newrelic_log_counts` | timeseries | POST `/graphql` | [docs](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/) |
| `newrelic_spans` | spans | POST `/graphql` | [docs](https://docs.newrelic.com/attribute-dictionary/?event=Span) |
| `newrelic_trace` | traces | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-distributed-trace-data-tutorial/) |
| `newrelic_errors` | events | POST `/graphql` | [docs](https://docs.newrelic.com/attribute-dictionary/?event=TransactionError) |
| `newrelic_apm_golden_signals` | timeseries | POST `/graphql` | [docs](https://docs.newrelic.com/attribute-dictionary/?event=Transaction) |
| `newrelic_alert_events` | alerts | POST `/graphql` | [docs](https://docs.newrelic.com/attribute-dictionary/?event=NrAiIncident) |
| `newrelic_entities` | entities | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-entities-api-tutorial/) |
| `newrelic_entity_golden_metrics` | entities | POST `/graphql` → POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/golden-metrics-entities-nerdgraph-api-tutorial/) |
| `newrelic_entity_relationships` | entities | POST `/graphql` → POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-entities-api-tutorial/) |
| `newrelic_issues` | alerts | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/) |
| `newrelic_incidents` | incidents | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/) |
| `newrelic_issue_events` | events | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/) |
| `newrelic_incident_events` | events | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/) |
| `newrelic_alert_policies` | entities | POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-alerts-policies/) |
| `newrelic_nrql_conditions` | entities | POST `/graphql` → POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-nrql-condition-alerts/) |
| `newrelic_muting_rules` | entities | POST `/graphql` | [docs](https://docs.newrelic.com/docs/alerts/get-notified/muting-rules-suppress-notifications/) |
| `newrelic_change_events` | changes | POST `/graphql` | [docs](https://docs.newrelic.com/docs/change-tracking/query-data/) |
| `newrelic_deployments` | changes | POST `/graphql` → POST `/graphql` | [docs](https://docs.newrelic.com/docs/change-tracking/query-data/) |
| `newrelic_change_markers` | changes | POST `/graphql` | [docs](https://docs.newrelic.com/docs/change-tracking/query-data/) |
| `newrelic_service_levels` | entities | POST `/graphql` → POST `/graphql` | [docs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-slm/) |

## OpenSearch (`opensearch`)

OpenSearch search / PPL / SQL / aggregation APIs over log and trace-analytics indices, the Alerting plugin (monitors and alerts) and index/cluster inventory.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `opensearch_search` | logs | POST `/{{ filters.index_pattern or env.OPENSEARCH_LOGS_INDEX }}/_search` | [docs](https://docs.opensearch.org/latest/api-reference/search-apis/search/) |
| `opensearch_ppl` | generic | POST `/_plugins/_ppl` | [docs](https://docs.opensearch.org/latest/sql-and-ppl/sql-and-ppl-api/index/) |
| `opensearch_sql` | generic | POST `/_plugins/_sql` | [docs](https://docs.opensearch.org/latest/sql-and-ppl/sql-and-ppl-api/index/) |
| `opensearch_log_counts` | timeseries | POST `/{{ filters.index_pattern or env.OPENSEARCH_LOGS_INDEX }}/_search` | [docs](https://docs.opensearch.org/latest/aggregations/bucket/date-histogram/) |
| `opensearch_metric_histogram` | timeseries | POST `/{{ filters.index_pattern or env.OPENSEARCH_LOGS_INDEX }}/_search` | [docs](https://docs.opensearch.org/latest/aggregations/bucket/date-histogram/) |
| `opensearch_top_terms` | generic | POST `/{{ filters.index_pattern or env.OPENSEARCH_LOGS_INDEX }}/_search` | [docs](https://docs.opensearch.org/latest/aggregations/bucket/terms/) |
| `opensearch_alerting_monitors` | entities | GET `/_plugins/_alerting/monitors/_search` | [docs](https://docs.opensearch.org/latest/observing-your-data/alerting/api/) |
| `opensearch_alerts` | alerts | GET `/_plugins/_alerting/monitors/alerts` | [docs](https://docs.opensearch.org/latest/observing-your-data/alerting/api/) |
| `opensearch_trace_spans` | spans | POST `/{{ filters.index_pattern or env.OPENSEARCH_TRACES_INDEX }}/_search` | [docs](https://github.com/opensearch-project/data-prepper/blob/main/docs/schemas/trace-analytics/otel-v1-apm-span-index-template.md) |
| `opensearch_service_map` | entities | POST `/{{ filters.index_pattern or env.OPENSEARCH_SERVICE_MAP_INDEX }}/_search` | [docs](https://github.com/opensearch-project/data-prepper/blob/main/docs/schemas/trace-analytics/otel-v1-apm-service-map-index-template.md) |
| `opensearch_indices` | entities | GET `/_cat/indices/{{ filters.index_pattern or '*' }}` | [docs](https://docs.opensearch.org/latest/api-reference/cat/cat-indices/) |
| `opensearch_cluster_health` | entities | GET `/_cluster/health{{ ('/' ~ filters.index_pattern) if filters.index_pattern else '' }}` | [docs](https://docs.opensearch.org/latest/api-reference/cluster-api/cluster-health/) |
| `opensearch_nodes` | entities | GET `/_cat/nodes` | [docs](https://docs.opensearch.org/latest/api-reference/cat/cat-nodes/) |
| `opensearch_data_streams` | entities | GET `/_data_stream{{ ('/' ~ filters.name) if filters.name else '' }}` | [docs](https://docs.opensearch.org/latest/api-reference/data-stream/data-stream-info/) |
| `opensearch_data_stream_stats` | entities | GET `/_data_stream{{ ('/' ~ filters.name) if filters.name else '' }}/_stats` | [docs](https://docs.opensearch.org/latest/api-reference/data-stream/data-stream-stats/) |

## Zipkin (`zipkin`)

Zipkin v2 API - services, span names, trace search, trace by id, service dependency links, tag autocomplete.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `zipkin_services` | entities | GET `/api/v2/services` | [docs](https://zipkin.io/zipkin-api/#/default/get_services) |
| `zipkin_span_names` | entities | GET `/api/v2/spans` | [docs](https://zipkin.io/zipkin-api/#/default/get_spans) |
| `zipkin_traces` | spans | GET `/api/v2/traces` | [docs](https://zipkin.io/zipkin-api/#/default/get_traces) |
| `zipkin_trace` | traces | GET `/api/v2/trace/{{ filters.trace_id \| trim \| lower }}` | [docs](https://zipkin.io/zipkin-api/#/default/get_trace__traceId_) |
| `zipkin_trace_many` | traces | GET `/api/v2/traceMany` | [docs](https://zipkin.io/zipkin-api/#/default/get_traceMany) |
| `zipkin_dependencies` | entities | GET `/api/v2/dependencies` | [docs](https://zipkin.io/zipkin-api/#/default/get_dependencies) |
| `zipkin_autocomplete_keys` | entities | GET `/api/v2/autocompleteKeys` | [docs](https://zipkin.io/zipkin-api/#/default/get_autocompleteKeys) |
| `zipkin_autocomplete_values` | entities | GET `/api/v2/autocompleteValues` | [docs](https://zipkin.io/zipkin-api/#/default/get_autocompleteValues) |

## PagerDuty (`pagerduty`)

PagerDuty REST API v2: incidents (list and detail), incident log entries (the full incident timeline, including the original trigger payload), alerts grouped into an incident, incident notes, related / past / outlier incidents and correlated change events, change events (deploys, builds, config changes), the audit trail of PagerDuty configuration changes, services, business services, service depe…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `pagerduty_incidents` | incidents | GET `/incidents` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incidents) |
| `pagerduty_incident_detail` | incidents | GET `/incidents/{{ filters.incident }}` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-incident) |
| `pagerduty_incident_log_entries` | events | GET `/incidents/{{ filters.incident_id }}/log_entries` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incident-log-entries) |
| `pagerduty_log_entries` | events | GET `/log_entries` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/log-entries/list-log-entries) |
| `pagerduty_incident_alerts` | alerts | GET `/incidents/{{ filters.incident_id }}/alerts` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incident-alerts) |
| `pagerduty_incident_notes` | events | GET `/incidents/{{ filters.incident_id }}/notes` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/list-incident-notes) |
| `pagerduty_related_change_events` | changes | GET `/incidents/{{ filters.incident_id }}/related_change_events` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/change-events/list-incident-related-change-events) |
| `pagerduty_past_incidents` | incidents | GET `/incidents/{{ filters.incident_id }}/past_incidents` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-past-incidents) |
| `pagerduty_related_incidents` | incidents | GET `/incidents/{{ filters.incident_id }}/related_incidents` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-related-incidents) |
| `pagerduty_outlier_incident` | incidents | GET `/incidents/{{ filters.incident_id }}/outlier_incident` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/incidents/get-outlier-incident) |
| `pagerduty_change_events` | changes | GET `{{ ('/services/' ~ filters.service_id ~ '/change_events') if filters.service_id else '/...` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/change-events/list-change-events) |
| `pagerduty_audit_records` | changes | GET `/audit/records` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/audit/list-audit-records) |
| `pagerduty_services` | entities | GET `/services` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/services/list-services) |
| `pagerduty_service_dependencies` | entities | GET `/service_dependencies/technical_services/{{ filters.service_id }}` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/service-dependencies/get-technical-service-service-dependencies) |
| `pagerduty_business_services` | entities | GET `/business_services` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/business-services/list-business-services) |
| `pagerduty_maintenance_windows` | events | GET `/maintenance_windows` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/maintenance-windows/list-maintenance-windows) |
| `pagerduty_oncalls` | entities | GET `/oncalls` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/on-calls/list-on-calls) |
| `pagerduty_schedules` | entities | GET `/schedules` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/schedules/list-schedules) |
| `pagerduty_schedule_entries` | entities | GET `/schedules/{{ filters.schedule_id }}` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/schedules/get-schedule) |
| `pagerduty_schedule_overrides` | entities | GET `/schedules/{{ filters.schedule_id }}/overrides` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/schedules/list-schedule-overrides) |
| `pagerduty_escalation_policies` | entities | GET `/escalation_policies` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/escalation-policies/list-escalation-policies) |
| `pagerduty_users` | entities | GET `/users` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/users/list-users) |
| `pagerduty_teams` | entities | GET `/teams` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/teams/list-teams) |
| `pagerduty_priorities` | entities | GET `/priorities` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/priorities/list-priorities) |
| `pagerduty_analytics_incidents` | incidents | POST `/analytics/raw/incidents` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/analytics/get-analytics-incidents) |
| `pagerduty_analytics_services` | timeseries | POST `/analytics/metrics/incidents/services` | [docs](https://docs.pagerduty.com/developer/api/reference/rest/analytics/get-analytics-metrics-incidents-service) |

## Prometheus Alertmanager (`alertmanager`)

Alertmanager API v2 - routed alerts with silence/inhibition state, alert groups per receiver, silences, receivers and cluster status/config.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `alertmanager_alerts` | alerts | GET `/api/v2/alerts` | [docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L155) |
| `alertmanager_alert_groups` | alerts | GET `/api/v2/alerts/groups` | [docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L224) |
| `alertmanager_silences` | events | GET `/api/v2/silences` | [docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L49) |
| `alertmanager_silence` | events | GET `/api/v2/silence/{{ filters.id }}` | [docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L114) |
| `alertmanager_status` | entities | GET `/api/v2/status` | [docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L21) |
| `alertmanager_receivers` | entities | GET `/api/v2/receivers` | [docs](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml#L32) |

## Prometheus (`prometheus`)

Prometheus-compatible metrics API (PromQL range/instant queries, series, labels, alerts, rules, targets).

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `prometheus_query_range` | timeseries | GET `/api/v1/query_range` |  |
| `prometheus_query` | timeseries | GET `/api/v1/query` |  |
| `prometheus_series` | entities | GET `/api/v1/series` |  |
| `prometheus_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` |  |
| `prometheus_metric_metadata` | entities | GET `/api/v1/metadata` |  |
| `prometheus_alerts` | alerts | GET `/api/v1/alerts` |  |
| `prometheus_rules` | entities | GET `/api/v1/rules` |  |
| `prometheus_targets` | entities | GET `/api/v1/targets` |  |

## Slack (`slack`)

Slack Web API, read-only: channel message history in a time window, thread replies, pinned items, channel list and info, users and bots, plus message search (search.messages with a user token, and the Real-time Search API assistant.search.context). Use it to read the incident channel, find earlier discussions of the same alert, and see what humans and alert bots said.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `slack_channel_history` | events | GET `/conversations.history` | [docs](https://docs.slack.dev/reference/methods/conversations.history) |
| `slack_thread_replies` | events | GET `/conversations.replies` | [docs](https://docs.slack.dev/reference/methods/conversations.replies) |
| `slack_pins` | entities | GET `/pins.list` | [docs](https://docs.slack.dev/reference/methods/pins.list) |
| `slack_search_messages` | events | GET `/search.messages` | [docs](https://docs.slack.dev/reference/methods/search.messages) |
| `slack_search_context` | events | POST `/assistant.search.context` | [docs](https://docs.slack.dev/reference/methods/assistant.search.context) |
| `slack_channels` | entities | GET `/conversations.list` | [docs](https://docs.slack.dev/reference/methods/conversations.list) |
| `slack_channel_info` | entities | GET `/conversations.info` | [docs](https://docs.slack.dev/reference/methods/conversations.info) |
| `slack_users` | entities | GET `/users.list` | [docs](https://docs.slack.dev/reference/methods/users.list) |
| `slack_user` | entities | GET `/users.info` | [docs](https://docs.slack.dev/reference/methods/users.info) |
| `slack_bot` | entities | GET `/bots.info` | [docs](https://docs.slack.dev/reference/methods/bots.info) |
| `slack_auth` | entities | POST `/auth.test` | [docs](https://docs.slack.dev/reference/methods/auth.test) |

## SigNoz (`signoz`)

SigNoz (OpenTelemetry-native APM) query API: logs, spans and trace aggregations, metrics (builder and PromQL), trace waterfalls, the services list with RED stats, top operations, the service map, active alerts, alert rules, alert-rule state history, metric and attribute discovery, host and pod infrastructure lists, dashboards, planned downtimes, exceptions and ingestion stats.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `signoz_logs` | logs | POST `/api/v5/query_range` | [docs](https://signoz.io/docs/logs-management/logs-api/search-logs/) |
| `signoz_log_counts` | timeseries | POST `/api/v5/query_range` | [docs](https://signoz.io/docs/logs-management/logs-api/aggregate-logs/) |
| `signoz_spans` | spans | POST `/api/v5/query_range` | [docs](https://signoz.io/docs/traces-management/trace-api/search-traces/) |
| `signoz_trace_aggregates` | timeseries | POST `/api/v5/query_range` | [docs](https://signoz.io/docs/traces-management/trace-api/aggregate-traces/) |
| `signoz_metrics` | timeseries | POST `/api/v5/query_range` | [docs](https://signoz.io/docs/metrics-management/query-range-api/) |
| `signoz_promql` | timeseries | GET `/prometheus/api/v1/query_range` | [docs](https://signoz.io/api-reference/#/operations/PrometheusQueryRange) |
| `signoz_trace` | traces | POST `/api/v4/traces/{{ filters.trace_id \| urlencode }}/waterfall` | [docs](https://signoz.io/api-reference/#/operations/GetWaterfallV4) |
| `signoz_services` | entities | POST `/api/v2/services` | [docs](https://github.com/SigNoz/signoz/blob/main/pkg/modules/services/implservices/module.go) |
| `signoz_top_operations` | entities | POST `/api/v2/service/top_operations` | [docs](https://github.com/SigNoz/signoz/blob/main/pkg/modules/services/implservices/module.go) |
| `signoz_service_map` | entities | POST `/api/v1/dependency_graph` | [docs](https://github.com/SigNoz/signoz/blob/main/pkg/query-service/app/http_handler.go) |
| `signoz_alerts` | alerts | GET `/api/v1/alerts` | [docs](https://signoz.io/api-reference/#/operations/GetAlerts) |
| `signoz_rules` | entities | GET `/api/v3/rules` | [docs](https://signoz.io/api-reference/#/operations/ListRulesV3) |
| `signoz_rule` | entities | GET `/api/v2/rules/{{ filters.rule_id \| urlencode }}` | [docs](https://signoz.io/api-reference/#/operations/GetRuleByID) |
| `signoz_rule_history` | alerts | GET `/api/v2/rules/{{ filters.rule_id \| urlencode }}/history/timeline` | [docs](https://signoz.io/api-reference/#/operations/GetRuleHistoryTimeline) |
| `signoz_rule_top_contributors` | alerts | GET `/api/v2/rules/{{ filters.rule_id \| urlencode }}/history/top_contributors` | [docs](https://signoz.io/api-reference/#/operations/GetRuleHistoryTopContributors) |
| `signoz_metric_names` | entities | GET `/api/v2/metrics` | [docs](https://signoz.io/api-reference/#/operations/ListMetrics) |
| `signoz_metric_attributes` | entities | GET `/api/v2/metrics/attributes` | [docs](https://signoz.io/api-reference/#/operations/GetMetricAttributes) |
| `signoz_field_keys` | entities | GET `/api/v1/fields/keys` | [docs](https://signoz.io/api-reference/#/operations/GetFieldsKeys) |
| `signoz_field_values` | entities | GET `/api/v1/fields/values` | [docs](https://signoz.io/api-reference/#/operations/GetFieldsValues) |
| `signoz_hosts` | entities | POST `/api/v2/infra_monitoring/hosts` | [docs](https://signoz.io/api-reference/#/operations/ListHosts) |
| `signoz_pods` | entities | POST `/api/v2/infra_monitoring/pods` | [docs](https://signoz.io/api-reference/#/operations/ListPods) |
| `signoz_dashboards` | entities | GET `/api/v2/dashboards` | [docs](https://signoz.io/api-reference/#/operations/ListDashboardsV2) |
| `signoz_downtime_schedules` | changes | GET `/api/v1/downtime_schedules` | [docs](https://signoz.io/api-reference/#/operations/ListDowntimeSchedules) |
| `signoz_exceptions` | events | POST `/api/v5/query_range` | [docs](https://signoz.io/docs/userguide/writing-clickhouse-traces-query/) |
| `signoz_ingestion_stats` | generic | GET `/api/v1/stats` | [docs](https://signoz.io/docs/telemetry-stats-api/) |

## Splunk Enterprise / Splunk Cloud Platform (`splunk`)

Splunk platform REST API: SPL searches (logs, stats, timecharts, tstats, mstats metrics), results of existing search jobs, saved searches / alert definitions and their run history, fired (triggered) alerts, indexes, the metrics catalog, system messages and splunkd health.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `splunk_search` | logs | POST `/{{ env.SPLUNK_SEARCH_NAMESPACE or 'services' }}/search/jobs` → GET `/{{ env.SPLUNK_SEARCH_NAMESPACE or 'services' }}/search/jobs/{{ captures.sid }}` → GET `/{{ env.SPLUNK_SEARCH_NAMESPACE or 'services' }}/search/v2/jobs/{{ captures.sid }}/results` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) |
| `splunk_job_results` | logs | GET `/{{ env.SPLUNK_SEARCH_NAMESPACE or 'services' }}/search/v2/jobs/{{ filters.sid \| urlen...` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) |
| `splunk_saved_searches` | entities | GET `/servicesNS/-/-/saved/searches{{ ('/' ~ (filters.name \| urlencode)) if filters.name el...` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) |
| `splunk_saved_search_history` | events | GET `/servicesNS/-/-/saved/searches/{{ filters.saved_search \| urlencode }}/history` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) |
| `splunk_fired_alerts` | alerts | GET `/servicesNS/-/-/alerts/fired_alerts/{{ (filters.alert_name \| urlencode) if filters.ale...` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) |
| `splunk_fired_alert_summary` | alerts | GET `/servicesNS/-/-/alerts/fired_alerts` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/search-endpoints/search-endpoint-descriptions) |
| `splunk_indexes` | entities | GET `/services/data/indexes` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/introspection-endpoints) |
| `splunk_metric_names` | entities | GET `/services/catalog/metricstore/metrics` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/metrics-catalog-endpoints) |
| `splunk_metric_dimensions` | entities | GET `/services/catalog/metricstore/dimensions` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/metrics-catalog-endpoints) |
| `splunk_metric_dimension_values` | entities | GET `/services/catalog/metricstore/dimensions/{{ filters.dimension \| urlencode }}/values` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/metrics-catalog-endpoints) |
| `splunk_messages` | events | GET `/services/messages` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/system-endpoints) |
| `splunk_health` | entities | GET `/services/server/health/splunkd/details` | [docs](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-reference/10.6/introspection-endpoints) |

## Splunk Observability Cloud (`splunk_o11y`)

Splunk Observability Cloud (Infrastructure Monitoring / APM / Synthetics, ex-SignalFx): alert incidents, detectors (alert rules), detector event history, custom events (deploys), audit trail (who changed detectors/dashboards/integrations), muting rules, metric/MTS/dimension metadata, MTS data points, SignalFlow analytics, APM service topology and traces, SLOs, synthetic tests/runs, dashboards and…

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `splunk_o11y_incidents` | alerts | GET `/v2/incident{{ ('/' ~ (filters.incident_id \| urlencode)) if filters.incident_id else '...` | [docs](https://dev.splunk.com/observability/reference/api/incidents/latest) |
| `splunk_o11y_detector_incidents` | alerts | GET `/v2/detector/{{ filters.detector_id \| urlencode }}/incidents` | [docs](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_detectors` | entities | GET `/v2/detector` | [docs](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_detector` | entities | GET `/v2/detector/{{ filters.id \| urlencode }}` | [docs](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_detector_events` | events | GET `/v2/detector/{{ filters.detector_id \| urlencode }}/events` | [docs](https://dev.splunk.com/observability/reference/api/detectors/latest) |
| `splunk_o11y_events` | changes | GET `/v2/event/find` | [docs](https://dev.splunk.com/observability/reference/api/retrieve_events_v2/latest) |
| `splunk_o11y_audit_events` | changes | GET `/v2/audit/events` | [docs](https://dev.splunk.com/observability/reference/api/audit_events/latest) |
| `splunk_o11y_muting_rules` | entities | GET `/v2/alertmuting` | [docs](https://dev.splunk.com/observability/reference/api/incidents/latest) |
| `splunk_o11y_metric_timeseries` | entities | GET `/v2/metrictimeseries` | [docs](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_mts` | entities | GET `/v2/metrictimeseries/{{ filters.id \| urlencode }}` | [docs](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_metrics` | entities | GET `/v2/metric` | [docs](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_dimensions` | entities | GET `/v2/dimension` | [docs](https://dev.splunk.com/observability/reference/api/metrics_metadata/latest) |
| `splunk_o11y_timeseries_window` | timeseries | GET `/v1/timeserieswindow` | [docs](https://dev.splunk.com/observability/reference/api/retrieve_timeserieswindow/latest) |
| `splunk_o11y_signalflow` | timeseries | python: `signalflow_execute` | [docs](https://dev.splunk.com/observability/reference/api/signalflow/latest) |
| `splunk_o11y_services` | entities | POST `/v2/apm/topology` | [docs](https://dev.splunk.com/observability/reference/api/apm_service_topology/latest) |
| `splunk_o11y_service_map` | entities | POST `/v2/apm/topology` | [docs](https://dev.splunk.com/observability/reference/api/apm_service_topology/latest) |
| `splunk_o11y_service_dependencies` | entities | POST `/v2/apm/topology/{{ filters.service \| urlencode }}` | [docs](https://dev.splunk.com/observability/reference/api/apm_service_topology/latest) |
| `splunk_o11y_trace` | spans | GET `/v2/apm/trace/{{ filters.trace_id \| urlencode }}/latest` | [docs](https://dev.splunk.com/observability/reference/api/trace_id/latest) |
| `splunk_o11y_slos` | entities | POST `/v2/slo/search` | [docs](https://dev.splunk.com/observability/reference/api/slo/latest) |
| `splunk_o11y_synthetic_tests` | entities | GET `/v2/synthetics/tests` | [docs](https://dev.splunk.com/observability/reference/api/synthetics_tests/latest) |
| `splunk_o11y_synthetic_runs` | events | GET `/v2/synthetics/tests/{{ filters.test_id }}/runs` | [docs](https://dev.splunk.com/observability/reference/api/synthetics_tests/latest) |
| `splunk_o11y_dashboards` | entities | GET `/v2/dashboard` | [docs](https://dev.splunk.com/observability/reference/api/dashboards/latest) |
| `splunk_o11y_chart` | entities | GET `/v2/chart/{{ filters.id \| urlencode }}` | [docs](https://dev.splunk.com/observability/reference/api/charts/latest) |
| `splunk_o11y_charts` | entities | GET `/v2/chart` | [docs](https://dev.splunk.com/observability/reference/api/charts/latest) |

## Sumo Logic (`sumologic`)

Sumo Logic log search (Search Job API: raw messages and aggregate records), triggered-alert history from the System Event Index, metrics queries, monitor definitions and their current status, and collector/source health events.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `sumologic_search_messages` | logs | POST `/v2/search/jobs` → GET `/v2/search/jobs/{{ captures.job_id }}` → GET `/v2/search/jobs/{{ captures.job_id }}/messages` | [docs](https://www.sumologic.com/help/docs/api/search-job/) |
| `sumologic_search_records` | timeseries | POST `/v2/search/jobs` → GET `/v2/search/jobs/{{ captures.job_id }}` → GET `/v2/search/jobs/{{ captures.job_id }}/records` | [docs](https://www.sumologic.com/help/docs/api/search-job/) |
| `sumologic_alert_events` | alerts | POST `/v2/search/jobs` → GET `/v2/search/jobs/{{ captures.job_id }}` → GET `/v2/search/jobs/{{ captures.job_id }}/messages` | [docs](https://www.sumologic.com/help/docs/manage/security/audit-indexes/system-event-index/) |
| `sumologic_metrics_query` | timeseries | POST `/v1/metricsQueries` | [docs](https://www.sumologic.com/help/docs/api/metrics-query/) |
| `sumologic_monitors` | alerts | GET `/v1/monitors/search` | [docs](https://www.sumologic.com/help/docs/api/monitors-management/) |
| `sumologic_monitor` | entities | GET `/v1/monitors/{{ filters.id \| urlencode }}` | [docs](https://www.sumologic.com/help/docs/api/monitors-management/) |
| `sumologic_health_events` | events | GET `/v1/healthEvents` | [docs](https://www.sumologic.com/help/docs/api/health-events/) |

## Thanos Query (`thanos`)

Thanos Querier / Query Frontend: Prometheus-compatible PromQL API across many Prometheus instances with deduplication, partial-response control, downsampling, rules/alerts/targets fan-out and store status.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `thanos_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_query` | timeseries | GET `/api/v1/query` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_series` | entities | GET `/api/v1/series` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_metric_metadata` | entities | GET `/api/v1/metadata` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_alerts` | alerts | GET `/api/v1/alerts` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_rules` | entities | GET `/api/v1/rules` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_targets` | entities | GET `/api/v1/targets` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_labels` | entities | GET `/api/v1/labels` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_exemplars` | timeseries | GET `/api/v1/query_exemplars` | [docs](https://thanos.io/tip/components/query.md/#query-api-overview) |
| `thanos_stores` | entities | GET `/api/v1/stores` | [docs](https://thanos.io/tip/components/query.md/#store-filtering) |
| `thanos_tsdb_status` | entities | GET `/api/v1/status/tsdb` | [docs](https://prometheus.io/docs/prometheus/latest/querying/api/#tsdb-stats) |

## VictoriaLogs (`victorialogs`)

VictoriaLogs LogsQL API: raw log search, log hit counts over time, stats (instant and range), facets, streams and field names/values, per tenant.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `victorialogs_logs` | logs | GET `/select/logsql/query` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-logs) |
| `victorialogs_hits` | timeseries | GET `/select/logsql/hits` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-hits-stats) |
| `victorialogs_stats_range` | timeseries | GET `/select/logsql/stats_query_range` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-log-range-stats) |
| `victorialogs_stats` | timeseries | GET `/select/logsql/stats_query` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-log-stats) |
| `victorialogs_facets` | generic | GET `/select/logsql/facets` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-facets) |
| `victorialogs_streams` | entities | GET `/select/logsql/streams` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-streams) |
| `victorialogs_field_names` | entities | GET `/select/logsql/field_names` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-field-names) |
| `victorialogs_field_values` | entities | GET `/select/logsql/field_values` | [docs](https://docs.victoriametrics.com/victorialogs/querying/#querying-field-values) |

## VictoriaMetrics (`victoriametrics`)

VictoriaMetrics single-node or cluster (vmselect): MetricsQL/PromQL range and instant queries, series, labels, metadata, raw-sample export, TSDB cardinality stats, scrape targets and vmalert alerts/rules.

| SQL table | Kind | HTTP request | API reference |
|---|---|---|---|
| `victoriametrics_query_range` | timeseries | GET `/api/v1/query_range` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1query_range) |
| `victoriametrics_query` | timeseries | GET `/api/v1/query` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1query) |
| `victoriametrics_series` | entities | GET `/api/v1/series` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1series) |
| `victoriametrics_label_values` | entities | GET `/api/v1/label/{{ filters.label }}/values` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1labelvalues) |
| `victoriametrics_metric_metadata` | entities | GET `/api/v1/metadata` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1metadata) |
| `victoriametrics_alerts` | alerts | GET `{{ (env.VMALERT_URL or source.base_url).rstrip('/') }}/api/v1/alerts` | [docs](https://docs.victoriametrics.com/victoriametrics/vmalert/#web) |
| `victoriametrics_rules` | entities | GET `{{ (env.VMALERT_URL or source.base_url).rstrip('/') }}/api/v1/rules` | [docs](https://docs.victoriametrics.com/victoriametrics/vmalert/#web) |
| `victoriametrics_targets` | entities | GET `{{ (env.VMAGENT_URL or env.VM_URL).rstrip('/') }}/api/v1/targets` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1targets) |
| `victoriametrics_labels` | entities | GET `/api/v1/labels` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1labels) |
| `victoriametrics_export` | timeseries | GET `/api/v1/export` | [docs](https://docs.victoriametrics.com/victoriametrics/url-examples/#apiv1export) |
| `victoriametrics_tsdb_status` | entities | GET `/api/v1/status/tsdb` | [docs](https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#tsdb-stats) |
