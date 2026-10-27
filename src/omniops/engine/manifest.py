"""Declarative source manifests: the contract between observability APIs and SQL tables.

A manifest describes one platform (Datadog, Dynatrace, Grafana, ...): how to authenticate, and a
list of virtual tables. Each table says which HTTP request(s) to make, which SQL predicates are
pushed down into request parameters, how pagination works, and how the JSON response becomes rows.

Design rules (from the OpsIntelligence retrospective):
- Everything a model would otherwise have to remember lives here, not in prompts: required
  filters, time formats, retention clamps, sort order, storage tiers, pitfalls.
- Every table carries example queries (verified shapes) and offline fixtures, so a data-shape bug
  fixed once is fixed for every run.
- "HTTP 200 with an error in the body" is declared as an error rule and becomes a loud failure.

See docs/MANIFEST_SPEC.md for the full reference with worked examples.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SQL_TYPES = {"VARCHAR", "BIGINT", "DOUBLE", "BOOLEAN", "TIMESTAMP", "JSON", "DATE", "INTERVAL"}
FILTER_OPS = {"=", "!=", "in", "like", ">", ">=", "<", "<=", "between"}
TRANSFORMS = {
    "epoch_s", "epoch_ms", "epoch_us", "epoch_ns", "auto_time", "iso", "json", "str", "int",
    "float", "bool", "lower", "upper", "ns_to_ms", "us_to_ms", "s_to_ms", "join", "first",
    "length", "duration_to_s", "from_json", "b64decode", "k8s_quantity", "redact",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------------
# Source-level settings
# --------------------------------------------------------------------------------------------


class AuthSpec(_Strict):
    """How requests authenticate. String values are Jinja templates (e.g. ``{{ env.DD_API_KEY }}``)."""

    type: Literal[
        "none",
        "bearer",
        "headers",
        "basic",
        "query",
        "aws_sigv4",
        "oauth2_client_credentials",
        "gcp_service_account",
        "azure_ad",
        "kubeconfig",
    ] = "none"
    token: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)
    username: str | None = None
    password: str | None = None
    # oauth2_client_credentials / azure_ad
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    audience: str | None = None
    # aws_sigv4
    service: str | None = None
    region: str | None = None
    # gcp_service_account
    credentials_file: str | None = None
    # kubeconfig
    kubeconfig: str | None = None
    context: str | None = None


class RateLimitSpec(_Strict):
    concurrency: int = 4
    requests_per_minute: int | None = None
    retry_statuses: list[int] = Field(default_factory=lambda: [429, 502, 503, 504])
    retry_body_patterns: list[str] = Field(default_factory=list)
    max_retries: int = 3
    notes: str = ""


class ErrorRule(_Strict):
    """Turn a 2xx response with an error payload into a loud failure.

    The rule fires when the value at ``path`` exists and (``equals`` matches - for a wildcard path,
    any element matching) or (``non_empty`` and the value is non-empty). ``message`` is a path to
    the error text.
    """

    path: str
    equals: Any = None
    non_empty: bool = False
    message: str | None = None
    hint: str = ""


class EnvVar(_Strict):
    name: str
    description: str = ""
    default: str | None = None
    secret: bool = False


# --------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------


class PollSpec(_Strict):
    """Repeat a step until a condition holds (async query jobs: Splunk, Sumo, Logs Insights...).

    ``until`` is a Jinja expression evaluated with ``response`` bound to the parsed body.
    """

    until: str
    interval_s: float = 1.0
    timeout_s: float = 60.0
    fail_if: str | None = None


class RequestSpec(_Strict):
    """One HTTP call. Every string is a Jinja template rendered with the request context.

    A template that is exactly ``{{ expr }}`` renders to the expression's native value, so
    ``{{ time.start | epoch_ms }}`` stays an integer inside a JSON body. A value that renders to
    ``None`` (or an empty string for query params) is omitted.
    """

    name: str = "main"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = ""
    url: str | None = None
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    body_format: Literal["json", "form", "text", "graphql"] = "json"
    response_format: Literal["json", "ndjson", "text", "csv"] = "json"
    accept_status: list[int] = Field(default_factory=list)
    accept_status_rows: list[int] = Field(default_factory=list)  # 4xx whose body is meaningful data (health checks)
    capture: dict[str, str] = Field(default_factory=dict)
    poll: PollSpec | None = None
    when: str | None = None
    timeout_s: float = 60.0


class PaginationSpec(_Strict):
    """Pagination styles.

    - ``cursor``: read ``cursor_path`` from the response, send it as ``cursor_param`` (a
      ``query.<name>`` or ``body.<dotted.path>`` target). Stops when the cursor is empty.
    - ``offset``: send ``offset_param`` (and ``limit_param`` = ``page_size``), advancing by rows
      returned. Stops on a short page, or when ``total_path`` is reached.
    - ``page``: send ``page_param`` starting at ``start``; stops on a short or empty page.
    - ``link_header``: follow RFC 8288 ``Link: <...>; rel="next"`` headers.
    - ``next_url``: follow an absolute or relative URL found at ``next_url_path``.
    - ``time_cursor``: APIs without a token (Loki): ``cursor_path`` is a wildcard path to the page's
      timestamps; the next request sends the oldest (``time_direction: backward``) or newest
      (``forward``) one as ``cursor_param``. Stops on a short page or an unchanged cursor.
    ``has_more_path`` (optional) is a boolean that must be true to continue.
    """

    type: Literal["none", "cursor", "offset", "page", "link_header", "next_url", "time_cursor"] = "none"
    cursor_path: str | None = None
    cursor_param: str | None = None
    offset_param: str | None = None
    limit_param: str | None = None
    page_param: str | None = None
    page_size: int = 100
    start: int = 0
    total_path: str | None = None
    has_more_path: str | None = None
    next_url_path: str | None = None
    time_direction: Literal["backward", "forward"] = "backward"
    cursor_exclusive: bool = False      # follow-up pages send ONLY the cursor (+ cursor_keep_params) - Dynatrace nextPageKey
    cursor_keep_params: list[str] = Field(default_factory=list)
    max_pages: int = 5


# --------------------------------------------------------------------------------------------
# Responses -> rows
# --------------------------------------------------------------------------------------------


class ExpandStep(_Strict):
    """One step of row expansion applied to every current row scope.

    Exactly one of ``explode`` / ``zip`` / ``columnar`` / ``entries`` must be set. The produced
    element is bound to the alias ``as`` and is addressable in column paths as ``@alias``.

    - ``explode``: path to an array; one row per element.
    - ``zip``: {key: path} of parallel arrays; one row per index, element = {key: arr[i]}.
    - ``columnar``: {columns: path-to-names, data: path-to-2D-array, orientation: row|column};
      element = {column_name: value}.
    - ``entries``: path to an object; one row per (key, value) pair, element = {key, value}.
    """

    explode: str | None = None
    zip: dict[str, str] | None = None
    columnar: dict[str, str] | None = None
    entries: str | None = None
    as_: str = Field(alias="as")
    keep_empty: bool = False
    parse: bool = False  # json.loads string values (e.g. X-Ray segment Documents) before expanding

    @model_validator(mode="after")
    def _one_kind(self) -> ExpandStep:
        kinds = [k for k in ("explode", "zip", "columnar", "entries") if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"expand step needs exactly one of explode/zip/columnar/entries, got {kinds}")
        if self.columnar is not None:
            missing = {"columns", "data"} - set(self.columnar)
            if missing:
                raise ValueError(f"columnar needs keys {missing}")
            if self.columnar.get("orientation", "row") not in {"row", "column"}:
                raise ValueError("columnar.orientation must be 'row' or 'column'")
        return self


class TextSpec(_Strict):
    """For text responses (pod logs, Prometheus exposition...): one row per line."""

    regex: str | None = None
    skip_empty: bool = True


class ResponseSpec(_Strict):
    """How a response body becomes row scopes.

    Path syntax (see ``omniops.engine.jsonpath``): ``$`` is the response root; a path without a
    leading ``$``/``@`` is relative to the current row element; ``@alias`` addresses an expanded
    element; ``[*]`` iterates; ``[#i]`` is the index of the current ``rows_path`` element.
    """

    rows_path: str = "$"
    expand: list[ExpandStep] = Field(default_factory=list)
    dynamic: str | None = None
    dynamic_prefix: str = ""
    dynamic_max_columns: int = 60
    text: TextSpec | None = None
    errors: list[ErrorRule] = Field(default_factory=list)


class ColumnSpec(_Strict):
    name: str
    type: str = "VARCHAR"
    path: str | None = None
    expr: str | None = None
    transform: str | None = None
    transform_arg: str | None = None
    description: str = ""
    filter_only: bool = False

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        v = v.upper()
        if v not in SQL_TYPES:
            raise ValueError(f"column type {v!r} not in {sorted(SQL_TYPES)}")
        return v

    @field_validator("transform")
    @classmethod
    def _transform_ok(cls, v: str | None) -> str | None:
        if v is not None and v not in TRANSFORMS:
            raise ValueError(f"transform {v!r} not in {sorted(TRANSFORMS)}")
        return v

    @model_validator(mode="after")
    def _path_or_expr(self) -> ColumnSpec:
        if self.path is None and self.expr is None and not self.filter_only:
            self.path = self.name
        return self


# --------------------------------------------------------------------------------------------
# Filters and time
# --------------------------------------------------------------------------------------------


class FilterSpec(_Strict):
    """A SQL column whose WHERE predicate is pushed down into the request.

    The value is available in templates as ``filters.<param>``. ``in`` lists fan out into
    several requests (bounded by ``max_fan_out``) unless ``fan_out`` is false, in which case
    the template receives the whole list.
    """

    column: str
    param: str | None = None
    required: bool = False
    ops: list[str] = Field(default_factory=lambda: ["="])
    type: str = "VARCHAR"
    description: str = ""
    default: Any = None
    fan_out: bool = True
    max_fan_out: int = 20
    variants: Literal["hostname", "case"] | None = None
    examples: list[str] = Field(default_factory=list)

    @field_validator("ops")
    @classmethod
    def _ops_ok(cls, v: list[str]) -> list[str]:
        bad = [o for o in v if o not in FILTER_OPS]
        if bad:
            raise ValueError(f"unsupported filter ops {bad}; allowed {sorted(FILTER_OPS)}")
        return v

    @property
    def key(self) -> str:
        return self.param or self.column


class TimeSpec(_Strict):
    """Time-range pushdown. Predicates on ``column`` become ``time.start``/``time.end``.

    Without a predicate the window defaults to the run's trigger window (or ``default_lookback``
    before the anchor). ``retention`` clamps the start; ends in the future are clamped to now;
    ``max_window`` splits or clamps long ranges; ``target_points`` sizes ``time.step_s``.
    """

    column: str = "ts"
    default_lookback: str = "1h"
    max_window: str | None = None
    retention: str | None = None
    target_points: int = 300
    min_step: str = "10s"
    chunk: str | None = None
    required: bool = True
    reapply: bool = True          # False: the API is authoritative (instant queries stamp rows with the eval time)
    control_lookback: str = "24h"  # how far back the positive control looks for an empty result


class ExampleSpec(_Strict):
    sql: str
    description: str = ""
    verified: bool = False


class TableTest(_Strict):
    """Live boot-time smoke query. Fails loudly when the API shape drifts."""

    sql: str
    min_rows: int = 0
    when_env: list[str] = Field(default_factory=list)


class FixtureSpec(_Strict):
    """Offline test: a recorded/documented response and what the transform must produce."""

    file: str
    filters: dict[str, Any] = Field(default_factory=dict)
    expect_rows: int | None = None
    expect_min_rows: int | None = None
    expect_columns: dict[str, Any] = Field(default_factory=dict)
    expect_error: str | None = None  # substring of the error the transform must raise (2xx error rules)
    note: str = ""


class TableSpec(_Strict):
    name: str
    title: str = ""
    description: str
    kind: Literal[
        "timeseries", "logs", "spans", "traces", "events", "entities", "alerts", "incidents",
        "changes", "profiles", "text", "generic", "write",
    ] = "generic"
    request: RequestSpec | None = None
    steps: list[RequestSpec] = Field(default_factory=list)
    python: str | None = None
    filters: list[FilterSpec] = Field(default_factory=list)
    require_one_of: list[list[str]] = Field(default_factory=list)
    time: TimeSpec | None = None
    pagination: PaginationSpec = Field(default_factory=PaginationSpec)
    response: ResponseSpec = Field(default_factory=ResponseSpec)
    columns: list[ColumnSpec] = Field(default_factory=list)
    max_rows: int = 5000
    sort_hint: str = ""
    pitfalls: list[str] = Field(default_factory=list)
    examples: list[ExampleSpec] = Field(default_factory=list)
    tests: list[TableTest] = Field(default_factory=list)
    fixtures: list[FixtureSpec] = Field(default_factory=list)
    ui_link: str | None = None
    docs_url: str | None = None
    zero_fill: bool = False
    positive_control: bool = True

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not TABLE_NAME_RE.match(v):
            raise ValueError(f"table name {v!r} must match {TABLE_NAME_RE.pattern}")
        return v

    @model_validator(mode="after")
    def _shape(self) -> TableSpec:
        modes = sum(x is not None and x != [] for x in (self.request, self.steps, self.python))
        if modes != 1:
            raise ValueError(f"table {self.name}: set exactly one of request / steps / python")
        cols = [c.name for c in self.columns]
        dupes = {c for c in cols if cols.count(c) > 1}
        if dupes:
            raise ValueError(f"table {self.name}: duplicate columns {sorted(dupes)}")
        names = set(cols)
        for f in self.filters:
            if f.column not in names:
                raise ValueError(
                    f"table {self.name}: filter column {f.column!r} is not a declared column "
                    "(declare it, with filter_only: true if the API does not return it)"
                )
        if self.time is not None and self.time.column not in names:
            raise ValueError(f"table {self.name}: time column {self.time.column!r} not declared")
        for group in self.require_one_of:
            for c in group:
                if c not in names:
                    raise ValueError(f"table {self.name}: require_one_of column {c!r} not declared")
        return self

    @property
    def all_steps(self) -> list[RequestSpec]:
        return list(self.steps) if self.steps else ([self.request] if self.request else [])

    def filter_for(self, column: str) -> FilterSpec | None:
        return next((f for f in self.filters if f.column == column), None)


# --------------------------------------------------------------------------------------------
# Roles: the tool-agnostic layer
# --------------------------------------------------------------------------------------------

ROLE_NAMES = {
    "alert_rules",       # monitor / alert rule definitions (lookup by id)
    "active_alerts",     # currently firing alerts
    "alert_history",     # state transitions of alerts
    "metrics",           # native metric query (PromQL, DQL, NRQL, MQL...)
    "logs",              # raw log search
    "log_counts",        # log volume aggregated over time
    "traces",            # trace / span search
    "trace_detail",      # one trace by id
    "service_map",       # service dependencies
    "services",          # service inventory
    "hosts",             # host / node inventory
    "change_events",     # deploys, config changes, feature flags
    "code_changes",      # commits / PRs / releases / diffs
    "incidents",         # incident records
    "oncall",            # who is on call
    "rum",               # real-user monitoring
    "errors",            # error tracking issues (Sentry-like)
    "k8s_state",         # pods, deployments, events
    "k8s_events",
    "k8s_logs",
    "dashboards",
    "slo",
    "tickets",
    "docs",
    "chat_history",
    "profiles",
}


class RoleBinding(_Strict):
    """Maps a semantic role onto one table, with canonical column names and default filters.

    ``filters`` are templates rendered with ``service``, ``env``, ``host``, ``error_only`` etc.
    so seeds and specialist agents can query any platform without vendor knowledge.
    ``columns`` maps canonical names (ts, service, message, level, title, url, ...) to columns.
    """

    table: str
    filters: dict[str, str] = Field(default_factory=dict)
    columns: dict[str, str] = Field(default_factory=dict)
    query_language: str | None = None
    notes: str = ""


class SourceManifest(_Strict):
    source: str
    display_name: str
    vendor: str = ""
    description: str = ""
    docs_url: str = ""
    extends: str | None = None
    categories: list[str] = Field(default_factory=list)
    base_url: str = ""
    auth: AuthSpec = Field(default_factory=AuthSpec)
    headers: dict[str, str] = Field(default_factory=dict)
    env: list[EnvVar] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)
    rate_limit: RateLimitSpec = Field(default_factory=RateLimitSpec)
    retention: dict[str, str] = Field(default_factory=dict)
    errors: list[ErrorRule] = Field(default_factory=list)
    ui_base_url: str | None = None
    pitfalls: list[str] = Field(default_factory=list)
    tables: list[TableSpec] = Field(default_factory=list)
    roles: dict[str, RoleBinding | list[RoleBinding]] = Field(default_factory=dict)
    query_languages: dict[str, str] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if not TABLE_NAME_RE.match(v):
            raise ValueError(f"source id {v!r} must match {TABLE_NAME_RE.pattern}")
        return v

    @model_validator(mode="after")
    def _tables(self) -> SourceManifest:
        names = [t.name for t in self.tables]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"{self.source}: duplicate tables {sorted(dupes)}")
        for t in self.tables:
            if not t.name.startswith(self.source + "_") and self.extends is None:
                raise ValueError(f"{self.source}: table {t.name!r} must be prefixed with '{self.source}_'")
        for role, binding in self.roles.items():
            if role not in ROLE_NAMES:
                raise ValueError(f"{self.source}: unknown role {role!r}; allowed {sorted(ROLE_NAMES)}")
            for b in binding if isinstance(binding, list) else [binding]:
                if b.table not in names:
                    raise ValueError(f"{self.source}: role {role} points at unknown table {b.table!r}")
        return self

    def table(self, name: str) -> TableSpec | None:
        return next((t for t in self.tables if t.name == name), None)

    def role_bindings(self, role: str) -> list[RoleBinding]:
        b = self.roles.get(role)
        if b is None:
            return []
        return b if isinstance(b, list) else [b]
