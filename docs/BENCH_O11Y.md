# OmniOps on Grafana o11y-bench

[o11y-bench](https://o11ybench.ai/) runs agents in a Harbor task container next to a Grafana stack
(Grafana, Prometheus, Loki, Tempo) and grades the final response with an LLM judge plus
deterministic state checks (saved dashboards). OmniOps runs it the way users run OmniOps:
**the task arrives as a Slack DM, the data comes from SQL over the stack's HTTP APIs, and there
is no MCP.**

## How a trial runs

```
Harbor (host)                                   task container (Python 3.14 + uv)
-------------                                   ---------------------------------
agents/omniops_agent.py:OmniOpsHarborAgent
  setup(): upload dist/omniops/*.whl  ------->  uv venv /opt/omniops-venv; uv pip install -r requirements.txt wheel
                                                python -c 'import omniops.bench.o11ybench.runner'   (fails setup if broken)
  run():   upload /app/instruction.txt -------> python -m omniops.bench.o11ybench.runner | tee /logs/agent/command-0/stdout.txt
                                                  1. sources: grafana + prometheus/loki/tempo (direct URLs,
                                                     else Grafana datasource proxies) from GRAFANA_URL etc.
                                                  2. clock: O11Y_SCENARIO_TIME_ISO pinned for the whole process
                                                  3. preflight: one smoke query per source (warnings only)
                                                  4. SlackSimulator(auto_approve_writes=True): the real Bolt app
                                                  5. DM "Current time: <scenario>\n\n<task>" -> chat flow -> SQL
                                                  6. staged dashboard writes -> approval card -> simulated user
                                                     clicks Approve -> engine executes the write
                                                  7. /logs/agent/trajectory.json (ATIF-v1.7), flushed per tool call
           download trajectory.json <---------
           context tokens / cost from final_metrics; non-zero runner exit = infrastructure failure
```

- **Slack entry.** The runner never calls the agent directly: the task is DMed through the Slack
  simulator, which drives the production Bolt handlers (de-dup, status trail, watchdog, paging,
  approval cards). A benchmark score therefore measures the path users get (retrospective 4.1:
  canaries must test the production path).
- **SQL over HTTP, no MCP.** o11y-bench tasks configure the `mcp-grafana` server; the agent declares
  `mcp_servers` support only so Harbor accepts those tasks, then drops the servers. Data access is
  OmniOps' SQL engine over Grafana's HTTP API (dashboards, datasources, alerting, writes) and
  Prometheus/Loki/Tempo HTTP APIs. The container env already carries `GRAFANA_URL`,
  `PROMETHEUS_URL`, `LOKI_URL`, `TEMPO_URL`; Grafana runs anonymous Admin, so no token is needed.
  `OMNIOPS_O11YBENCH_SOURCES=grafana,grafana_prometheus,grafana_loki,grafana_tempo` forces the
  Grafana proxies instead of the direct APIs.
- **One clock.** o11y-bench sets one scenario clock per job (`O11Y_SCENARIO_TIME_ISO`); telemetry
  ends at it and facts are evaluated at it, while trials may start hours later. The runner pins the
  process clock to it (every wall-clock anchor, chat runs and approval engines included) and the
  DM states it as `Current time: ...`, the marker o11y-bench's regrade docs point at. The Harbor
  agent forwards the host's value only when it is set, so the task's compose value is never masked.
- **Staged writes.** Dashboard edits are staged (`OMNIOPS_WRITE_MODE=stage`), posted as an approval
  card and executed only when approved. In the benchmark the simulated on-call user approves; the
  trajectory records the approval as a user step, and the approval card's outcome ("Approved by
  ... and executed (1 row(s)) ...") is appended to the final answer, as the user sees both in Slack.
  The card echoes the write's inputs (a whole dashboard JSON) after the result columns, so each
  appended outcome keeps its first 500 characters with a sized `[N characters omitted]` marker.
- **What the grader reads.** The grader parses every `/logs/agent/*.json` and judges the last agent
  message with text after the last user message, unless it also calls tools; a reply shorter than
  200 characters gets the nearest earlier long agent text prepended. The trajectory therefore has
  one text-less agent step per tool call (call + observation, so the judge sees the real activity)
  and a final agent step holding the bot's final Slack reply (Slack links converted to markdown)
  with no tool calls. The runner re-reads the written file with that rule and warns if it would
  select anything else. Case files live under `/logs/agent/omniops/`, never next to the
  trajectory.
- **Budgets nest** under Harbor's 600 s agent timeout: chat budget 360 s + watchdog 120 s < runner
  timeout 540 s < 600 s (`OMNIOPS_CHAT_BUDGET_S`, `OMNIOPS_BENCH_TIMEOUT_S` override).
- **Exit codes.** 0 for every graded outcome (including wrong, empty or timed-out answers);
  2 configuration (no instruction, no source, no model key), 3 model never reached (zero tokens,
  zero tool calls, no answer), 4 Slack simulator crash. Non-zero exits fail the trial as an agent
  error so infrastructure problems are never scored as wrong answers.

## Commands

```bash
# once (and after every OmniOps change): build the wheel, install the agent, prepare o11y-bench
scripts/o11ybench_setup.sh                 # --skip-sync / --skip-preflight
export MODEL_API_KEY=...                   # Meta Model API key (forwarded into the container)

scripts/o11ybench_run.sh smoke             # 6 tasks, one per category, --n-attempts 1
scripts/o11ybench_run.sh full              # 64 tasks, --n-attempts 3
scripts/o11ybench_run.sh task query-cpu-metrics list-datasources --n-attempts 2
scripts/o11ybench_run.sh smoke --dry-run   # print the commands only
scripts/o11ybench_run.sh smoke -- --reasoning-effort high   # extra args go to `o11y_bench job`
```

The run script calls `uv run python -m o11y_bench job --model meta/muse-wonder-1.0
--agent-import-path agents.omniops_agent:OmniOpsHarborAgent --n-attempts N --n-concurrent 4
--job-name omniops-<model>-<mode>-k<N> [--task-name ...]` inside the o11y-bench checkout
(`O11Y_BENCH_DIR`, default `../vendor/o11y-bench`). In `task` mode the default job name also carries a
digest of the task list (`...-task-t<digest>-k<N>`): an existing job directory is resumed from its saved
`config.json`, whose task filter wins over `--task-name`, so the script refuses to reuse a job (default
or `--job-name`) that was created for another task list instead of silently skipping the new tasks. `--model provider/model` maps to
`OMNIOPS_MODEL_HEAVY` (`meta/muse-wonder-1.0` -> `muse-wonder-1.0`); the other roles keep the
OmniOps lineup. Host `OMNIOPS_*` variables (protocol, base URL, other tiers, budgets) are forwarded
into the container, except host paths. Stack URLs (`GRAFANA_URL`, `PROMETHEUS_URL`, `LOKI_URL`,
`TEMPO_URL`) are never taken from the host: each task seeds its own o11y-stack sidecar and the
container env already points at it (an intentional override goes through `--ak extra_env=...`). Results land in
`<o11y-bench>/jobs/<job-name>/` (`run_report.html`, per trial `agent/trajectory.json`,
`agent/command-0/stdout.txt`, `agent/omniops/`, `verifier/grading_details.json`).

## Judges

**Official (default):** the judge the tasks ship with (`grading/judge.py`, `DEFAULT_GRADING_MODEL
= claude-haiku-5-5@high`, pinned in each generated `task.toml`). Needs `ANTHROPIC_API_KEY`. Only
these scores are comparable with the leaderboard.

**Meta-only (`--judge meta`): NON-OFFICIAL.** For setups without an Anthropic key, the Anthropic SDK
the judge uses is pointed at Meta's Anthropic-compatible Messages endpoint
(`ANTHROPIC_BASE_URL=https://api.meta.ai`, key `$MODEL_API_KEY`). What reading the vendored code
showed, and why the script works the way it does:

1. The verifier runs inside the trial with `[verifier.env]` = `ANTHROPIC_API_KEY` and a *pinned*
   `GRADING_MODEL` only. A host `ANTHROPIC_BASE_URL`/`GRADING_MODEL` never reaches it, and setting
   `ANTHROPIC_API_KEY=$MODEL_API_KEY` for the job would send the Meta key to api.anthropic.com.
   So the job runs with a non-secret placeholder key (`omniops-official-judge-disabled`): the
   in-container judge call fails fast, the trial is marked `status: error`, and the verifier still
   saves `verifier/truth.json` and the state-check results.
2. The trials are then regraded **on the host**, where the env does apply:
   `ANTHROPIC_BASE_URL=https://api.meta.ai ANTHROPIC_API_KEY=$MODEL_API_KEY GRADING_MODEL=<spec>
   uv run python -m o11y_bench regrade --jobs-dir jobs --job-name <job> --offline`.
   The job directory gets `NON_OFFICIAL_JUDGE.txt` and a `-metajudge` job-name suffix.
3. `GRADING_MODEL=<model>[@effort]` maps to request parameters in `evaluate_with_llm`:
   - `@high` (any effort) -> `output_config: {"effort": "high"}` and `max_tokens` 8192 + 16384:
     Anthropic's effort parameter. If Meta's endpoint does not implement it, the request is
     rejected with HTTP 400, which the judge does not retry (`JudgeError: judge request rejected`),
     so every rubric item errors.
   - `@none` -> `thinking: {"type": "disabled"}` (Anthropic-only; Muse also rejects effort "none").
   - no suffix -> neither field: `model`, `max_tokens: 8192`, `system` (one text block with
     `cache_control: ephemeral`), `messages`, streamed. This is the setting that carries no
     Anthropic-only effort/thinking field: **`GRADING_MODEL=muse-spark-1.3`**.
4. Whether `muse-spark-1.3@high` is accepted cannot be decided from the code, so the script decides
   it by running the judge's own `evaluate_with_llm` once per candidate before spending on trials:
   `muse-spark-1.3@high`, then `muse-spark-1.3`; each with `x-api-key` auth and, if refused, Bearer
   auth (`ANTHROPIC_AUTH_TOKEN`). The first setting that returns a parseable evaluation is used.
   Run the probe alone with `scripts/o11ybench_run.sh judge-probe`; set `GRADING_MODEL` to pin one.
   `META_ANTHROPIC_BASE_URL` overrides the endpoint (the SDK appends `/v1/messages`).
5. Every setting also sends the system block's `cache_control` (judge.py, not configurable). If the
   endpoint refused even the plain setting, the script stops before any trial runs; grade with the
   official judge instead.

**Exact settings, in the order the script tries them** (the probe prints the one that worked as
`working setting: GRADING_MODEL=... auth=... ANTHROPIC_BASE_URL=...`):

| # | GRADING_MODEL | Auth env | Anthropic-only fields sent |
|---|---|---|---|
| 1 | `muse-spark-1.3@high` | `ANTHROPIC_API_KEY=$MODEL_API_KEY` (x-api-key) | `output_config.effort`, `cache_control` |
| 2 | `muse-spark-1.3@high` | `ANTHROPIC_AUTH_TOKEN=$MODEL_API_KEY` (Bearer) | `output_config.effort`, `cache_control` |
| 3 | `muse-spark-1.3` | x-api-key | `cache_control` only |
| 4 | `muse-spark-1.3` | Bearer | `cache_control` only |

Never use `@none` (Anthropic's `thinking: disabled`; Muse rejects effort "none"). Setting 3/4 is
the one that does not depend on Meta implementing Anthropic's effort parameter.

Every NON-OFFICIAL job stays re-gradable with the official judge, from the saved transcripts:

```bash
cd ../vendor/o11y-bench
ANTHROPIC_API_KEY=... uv run python -m o11y_bench regrade --jobs-dir jobs --job-name <job> --offline
```

## Cost estimate per task (agent side)

Pricing (`llm/models.py`, USD per 1M tokens): `muse-wonder-1.0` 4.00 input / 0.20 cached input /
20.00 output (reasoning is billed as output); `muse-spark-1.3` 1.25 / 0.15 / 4.25. A chat run is
one heavy-model tool loop whose prompt grows with every tool result; prefix caching typically
covers about three quarters of the input.

| Task shape | Turns | Input (cached) | Output | Cost |
|---|---|---|---|---|
| lookup (`list-datasources`, simple PromQL) | 3-5 | ~60k (~40k) | ~4k | ~$0.15 |
| typical query / trace task | 8-15 | ~350k (~260k) | ~20k | ~$0.80 |
| investigation or dashboard build | 15-25 | ~0.8-1M (~0.7M) | ~40-60k | $1.50-2.50 |

Smoke (6 trials) is about $3-8; full (64 tasks x 3 = 192 trials) about $100-250. These are
estimates: actual tokens and cost are in each trajectory's `final_metrics` and in Harbor's
`result.json` (`cost_usd`). The judge adds one call per trial.

## Caveats

- **Auto-approval is benchmark-only.** In production a human must click Approve; here the
  simulated user approves every staged write so dashboard state checks can pass. The approval is
  visible in the trajectory as a user step.
- **Install time.** Each trial installs the wheel and its locked dependencies into the container
  (network required; tasks set `allow_internet = true`). This happens in the agent's `setup()`,
  before `run()`; a failed install fails setup with the installer's output tail.
- **Rebuild after changes.** The container runs the wheel built by `o11ybench_setup.sh`; re-run it
  after any OmniOps change.
- **Concurrency.** Each trial boots its own stack; keep `--n-concurrent` modest on a laptop.
- **Partial runs.** The trajectory is rewritten atomically after every tool call, so a trial killed
  by Harbor still has a valid trajectory (with no final answer: graded `no_answer`).
- **Question whitespace.** The Slack layer normalises whitespace in DM text before the chat flow
  sees it (newlines become spaces), so multi-line task statements reach the model on one line; the
  trajectory's user step keeps the DM exactly as sent.
- **Leaderboard.** Only jobs graded with the official judge can be submitted.

## Tests

`uv run --no-sync pytest tests/test_bench_o11y* -q` (offline; HTTP mocked with respx):

- `test_bench_o11y_slack.py`: the runner through the real Slack simulator and Bolt app: DM ->
  scripted tool loop with real SQL over the Grafana manifest -> staged dashboard save -> simulated
  approval -> `POST /api/dashboards/db` exactly once -> valid ATIF whose graded final answer is the
  bot's reply plus the write outcome.
- `test_bench_o11y_runner.py`: stub simulator/LLM: live flushing per tool call, exit codes,
  source selection (direct APIs first, Grafana proxies, override), `main()` against a mocked stack.
- `test_bench_o11y_trajectory.py`: the final-answer rule, checked against the vendored grader's own
  `Transcript.final_answer` and ATIF parser (extracted with `ast`, since it needs Python 3.14).
- `test_bench_o11y_harbor_agent.py`: the agent file imports nothing but the standard library and
  harbor; env passthrough, wheel install, runner command, metrics, failure exits.
- `test_bench_o11y_scripts.py`: dry runs of both scripts (task selection, judge wiring, no secret
  in the output).
