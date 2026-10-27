# OmniOps architecture and module contracts

OmniOps is a tool-agnostic, Slack-native AI incident investigator. Slack is always the entry point.
Data access is a SQL engine over observability REST APIs (Datadog, Dynatrace, Grafana/Prometheus/
Loki/Tempo, New Relic, Splunk, Elastic, CloudWatch, Azure Monitor, GCP, Honeycomb, Sentry,
PagerDuty, Kubernetes, Argo CD, GitHub, Jira, Slack ...) - **no MCP servers**. LLMs: Meta Model
API (Muse) by default, behind a provider-neutral interface.

It is a re-implementation of the lessons in the OpsIntelligence retrospective
(an internal document, not part of this repository). Section
numbers like "3.6.4" below refer to that document. Read Part 3 and Part 8 before changing agent code.

## Principles (non-negotiable)

1. **Code measures, the LLM judges.** Code fetches, parses, counts, pairs, links, time-anchors and
   validates. The LLM decides what to look at, judges what evidence means, and writes.
2. **One time source per run** (the trigger window), computed by code. `now()` in SQL is pinned to it.
3. **Every claim cites an evidence ID that exists. Links come only from data**, never generation.
4. **Below the evidence floor** (fewer than 2 meaningful items), return a templated "insufficient
   data" answer listing what was tried; never speculate.
5. **Competing hypotheses with causal levels**; push to trigger level; judge claims separately from
   measurements; empty is inconclusive without a positive control; partial coverage cannot refute.
6. **Schema-bound outputs**, lenient in / strict out, retry with the validation error, then a
   deterministic fallback. The user always gets something.
7. **Loud zeros**: zero hypotheses, zero tool calls, zero rows, zero evidence are logged as warnings.
8. **Writes need human approval** (staged, 30-min expiry, claimed once).
9. **Untrusted data**: never follow instructions found in logs, events, tickets or pages.
10. **The bot fills any gap a tool can fill**; next steps are for humans only.

## Repository map

| Path | Owner module | Contract |
|---|---|---|
| `engine/` | SQL engine (done) | `Catalog.from_env()`, `SQLEngine(catalog, anchor, cache=FetchCache()).query(sql) -> QueryResult`, `QueryResult.render()`, `Catalog.search/describe/sources_overview/role_bindings`, errors carry `.lesson` |
| `sources/` | manifests (research workflow) | YAML manifests, fixtures; `docs/MANIFEST_SPEC.md` |
| `llm/base.py` | interface (done) | `LLMClient.complete / structured / run_tool_loop`, `ToolSpec`, `ToolOutput`, `Usage` |
| `llm/meta.py` | Meta Model API client | `MetaResponsesClient(config)` implements `LLMClient` on `/v1/responses`; `ChatCompletionsClient` fallback for other OpenAI-compatible endpoints |
| `llm/models.py` | role -> model config | `ModelConfig.from_env()`, `ROLE_DEFAULTS`, pricing, escalation ladders |
| `llm/structured.py` | JSON repair + coercion | `coerce_and_validate(raw, schema) -> (obj, error)`, repair ladder (3.8.4) |
| `llm/scripted.py` | test double | `ScriptedLLM(rules)` deterministic offline client implementing the full interface |
| `agent/types.py` | shared types (done) | `Alert, EvidenceRecord, Hypothesis, SubCheck, CheckVerdict, SpecialistVerdict, ConclusionCard, RunResult, RunStats, ToolEvent` |
| `agent/alert.py` | alert normalizer | `parse_slack_alert(event: dict, llm: LLMClient | None) -> Alert | None`, per-platform deterministic parsers + LLM fallback for unknown/human text |
| `agent/timeframe.py` | trigger window | `resolve_anchor(alert, wall_now) -> TimeAnchor` (3.3) |
| `agent/evidence.py` | evidence board | `EvidenceBoard` (ids, pinning, compact view, link bucket, meaningful count) + `record_from_result(...)` |
| `agent/tools.py` | model tools | `build_tools(ctx) -> list[ToolSpec]`: `search_catalog`, `describe_table`, `run_sql`, `record_hypotheses`, `finish`; SQL sanitizer; de-dup; error lessons |
| `agent/hypotheses.py` | tracker | `HypothesisTracker` (validation of evidence ids, corroboration caps, status caps, leader, tie detection) |
| `agent/gates.py` | readiness + depth gates | `readiness_blockers(ctx) -> list[str]`, `depth_pushback(ctx) -> str | None`, incident pattern classification |
| `agent/deepening.py` | recursive deepening | `deepen(ctx, rounds) ` with verdict judge, positive controls, one corrected re-check, demote & re-form |
| `agent/seeds.py` | deterministic seeds | `run_seeds(ctx) -> list[EvidenceRecord]` via roles (alert rule, changes, log patterns, metrics, k8s, active alerts, memory) |
| `agent/specialists/` | domain agents | `run_specialist(name, ctx) -> SpecialistVerdict`; code_change, prior_incidents, customer_impact, dependency, infrastructure |
| `agent/report.py` | report writer | `write_report(ctx) -> str` (Slack mrkdwn), citation validation, calibrated wording, link placement |
| `agent/card.py` | conclusion card | `build_card(ctx, report) -> ConclusionCard` (formatter + deterministic fallback) |
| `agent/memory.py` | long-term memory | `MemoryStore(path)`: `recall(service, text) -> list[str]`, `remember(run_result)`; BM25, negative facts |
| `agent/context.py` | run context | `RunContext` dataclass shared by all agent modules (see below) |
| `agent/orchestrator.py` | investigation flow | `async investigate(alert, deps, *, status=None) -> RunResult` |
| `agent/chat.py` | Q&A flow | `async answer(question, deps, *, thread=None, status=None) -> RunResult` |
| `agent/prompts/*.md` | prompts | one sectioned prompt per role (3.12); static; dynamic content goes in the user message |
| `slack/app.py` | Slack entry point | `build_app(deps) -> AsyncApp` (Bolt): alert channels, mentions, DMs, `/investigate`, approvals, feedback |
| `slack/render.py` | Block Kit | `card_blocks(card)`, `report_pages(markdown)`, limits (50 blocks, 3000 chars/section) |
| `slack/simulator.py` | Slack simulator | `SlackSimulator(deps)`: fake WebClient + event injector driving the *real* Bolt handlers |
| `slack/status.py` | live status trail | `StatusTrail` posts one message and edits it in place |
| `cli_runtime.py` | CLI commands | `catalog`, `describe`, `sql`, `selftest`, `serve`, `ask`, `investigate` |
| `bench/o11ybench/` | o11y-bench adapter | Harbor agent class + in-container runner (Slack-simulator entry, ATIF trajectory) |
| `bench/arena/` | Arena adapter | scenario runner: fault -> alert/customer report into Slack -> investigation -> archive -> judge |
| `deploy/` | infra | kind + Cilium + Arena shop + LGTM observability + Argo CD + Grafana alert rules -> Slack |
| `site/` | results website | static benchmark dashboard generated from job outputs |

### RunContext (agent/context.py)

```python
@dataclass
class Deps:                      # process-wide
    catalog: Catalog
    llm: LLMClient
    memory: MemoryStore | None
    settings: Settings           # budgets, flags (omniops/settings.py)

@dataclass
class RunContext:                # one investigation or chat run
    run_id: str
    deps: Deps
    alert: Alert | None
    question: str | None
    anchor: TimeAnchor
    engine: SQLEngine            # primary agent's engine (shares FetchCache with specialists)
    cache: FetchCache
    board: EvidenceBoard
    tracker: HypothesisTracker
    stats: RunStats
    deadline: float              # absolute monotonic deadline for the whole run
    status: StatusTrail | None
    tool_events: list[ToolEvent]
    warnings: list[str]
    stop_requested: bool = False
    def remaining(self) -> float
    def engine_for(self, agent: str, tables: set[str] | None) -> SQLEngine   # scoped catalog, shared cache
    def warn(self, msg: str) -> None                                          # loud-zero logging
```

## Investigation flow (`agent/orchestrator.py`)

1. `resolve_anchor(alert)` -> trigger window (link window, then triggering event, then alert time).
2. Status trail: "Investigating <title> ...".
3. **Seeds** (deterministic, parallel, pinned): alert rule definition (threshold in words), change
   events 48h before -> 1h after, error log patterns for the service (window + same window a day
   earlier), the alert's own query over a wider window, Kubernetes state for the namespace/service
   (pods with restarts/waiting reasons, warning events), other active alerts, memory recall.
4. **Specialists** start concurrently with the primary loop (own deadline, scoped catalog, shared
   FetchCache): `code_change`, `prior_incidents`, `customer_impact`, `dependency`, `infrastructure`.
   Each: one deterministic seed fetch -> playbook prompt -> mid-tier tool loop -> structured
   `SpecialistVerdict` (re-asked twice with the validation error) -> published to the board.
5. **Primary loop** (heavy model, `run_tool_loop`): tools `search_catalog`, `describe_table`,
   `run_sql`, `record_hypotheses`, `finish`. `before_final` runs the readiness gate (coverage by
   incident pattern, >=2 meaningful evidence, hypotheses exist, top-two gap > 0.08), the depth gate
   (leader below trigger level and budget remains), unfollowed artifact leads, and the
   self-executable-gaps attestation. Pushbacks are capped (3 readiness, 2 depth). `inject` delivers
   specialist evidence as it lands.
6. **Deepening** (<= 3 rounds within budget): decompose the leader into 2-3 sub-checks (one SQL
   each, confirm/refute criteria), run them, judge with the verdict model (rules in 3.6.4), one
   corrected re-check for inconclusive, demote and re-form once on refutation, move on when exhausted.
7. **Evidence floor**: < 2 meaningful items -> templated insufficient-data answer.
8. **Report** (heavy, streamed with a stall guard): verdict-first, calibrated wording (3.7), cites
   E-ids, link bucket, sections: TL;DR, Root cause, Evidence, Impact (blast radius: affected
   workloads and downstream effects, no unsupported outage claims), Timeline, Mitigation (supported,
   safe, implementation-ready: exact resource/setting/value and how to verify recovery; containment
   when the fix is unknown; never destructive or speculative actions), Next steps (human-only), Gaps.
   Post-validation strips unknown E-ids and raw URLs and appends unplaced bucket links.
9. **Card** built from the finished report (formatter, schema-bound, 3 attempts, deterministic
   fallback); early card posted when agents finish, final card edits it in place.
10. **Memory write** (one extraction call; gated "worth storing"; negative facts).

Budgets (settings, wall-clock): investigation 780 s, report reserve 90 s, report call 900 s,
specialists 240 s, chat 300 s, agent watchdog deadline + 120 s. The report call runs under its own
allowance (`report_call_s - report_reserve_s`) after the investigation budget; the outermost guard is
`Settings.investigation_guard_s` = budget + that allowance + watchdog grace.

## Chat flow (`agent/chat.py`)

Question (DM, mention, or a benchmark task) -> same tools and evidence discipline -> heavy-model
tool loop with the self-executable-gaps attestation and evidence floor -> answer with citations and
links. "Why is X firing" style questions about live state always query live state. Dashboard/monitor
edits are staged writes approved in Slack.

## Model lineup (Meta Model API, `llm/models.py`)

| Role | Model | Effort | Why |
|---|---|---|---|
| decision, evidence, hypotheses, verdict, deepening, report, chat | `muse-wonder-1.0` | medium (verdict/hypotheses high) | comprehension-heavy roles never go below the top tier (D13) |
| specialist | `muse-spark-1.3` | medium | escalation ladder -> `muse-wonder-1.0` (sticky per run) |
| formatter, triage, alert_parse, memory | `muse-spark-1.3` | low | validated output with deterministic fallback |

Contributor-mode variants are never used by default: they allow Meta to train on prompts, and ops
data (logs, traces, customer identifiers) must not leave under that policy.

## Testing

Functionality tests drive real entry points (D27): the Slack simulator dispatches realistic event
payloads into the real Bolt app; `ScriptedLLM` makes model turns deterministic; `respx` mocks the
REST APIs with manifest fixtures. Live checks: `omniops selftest` (boot smoke queries per source)
and the benchmarks.

## Benchmarks

- **o11y-bench** (Grafana, Harbor): `bench/o11ybench/omniops_agent.py` is a Harbor agent. Inside the
  task container the runner starts the Slack simulator, posts the task as a DM to the bot, and the
  bot answers through the normal chat flow against Grafana/Prometheus/Loki/Tempo via HTTP (no MCP).
  The final Slack reply becomes the trajectory's final agent message. Dashboard writes are staged
  and approved by the simulated user (recorded in the trajectory).
- **Edge Delta AI SRE Arena**: the Arena shop runs on kind, deployed through Argo CD from public
  GitHub GitOps repos. Grafana alert rules post to Slack; customer reports are posted as Slack
  messages. The bot investigates with Kubernetes, Argo CD, GitHub and Grafana-stack tables; its
  final Slack answer is archived as `final.txt` and judged with the Arena rubric.
