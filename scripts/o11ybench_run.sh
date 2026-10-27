#!/usr/bin/env bash
# Run Grafana o11y-bench with OmniOps (Harbor agent agents.omniops_agent:OmniOpsHarborAgent).
#
#   scripts/o11ybench_run.sh smoke  [options]    one task per category, --n-attempts 1
#   scripts/o11ybench_run.sh full   [options]    every task, --n-attempts 3
#   scripts/o11ybench_run.sh task NAME [NAME...] [options]
#   scripts/o11ybench_run.sh judge-probe         which Meta judge setting works (see --judge meta)
#
# Options:
#   --model PROVIDER/MODEL   Harbor model (default meta/muse-wonder-1.0 -> OMNIOPS_MODEL_HEAVY)
#   --judge official|meta    official (default): the task's own judge, needs ANTHROPIC_API_KEY.
#                            meta: NON-OFFICIAL. Trials run with the official judge disabled, then
#                            are regraded offline on this host with the Anthropic SDK pointed at
#                            Meta's Anthropic-compatible Messages endpoint (MODEL_API_KEY).
#   --n-attempts N           override the mode's attempts
#   --n-concurrent N         concurrent trials (default 4)
#   --job-name NAME          default omniops-<model>-<mode>[-t<task-list digest>]-k<attempts>[-metajudge]
#   --jobs-dir DIR           default <o11y-bench>/jobs
#   --dry-run                print the commands, run nothing
#   -- ARGS                  passed to `python -m o11y_bench job`
#
# Env: O11Y_BENCH_DIR, MODEL_API_KEY, OMNIOPS_* (forwarded into the task container),
#      ANTHROPIC_API_KEY (official judge), GRADING_MODEL / META_ANTHROPIC_BASE_URL (meta judge).
# Key values are never printed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="${O11Y_BENCH_DIR:-$ROOT/../vendor/o11y-bench}"
AGENT_IMPORT_PATH="agents.omniops_agent:OmniOpsHarborAgent"
META_BASE_URL="${META_ANTHROPIC_BASE_URL:-https://api.meta.ai}"
# judge.py: "@<effort>" adds output_config.effort (Anthropic-only); no suffix sends neither effort
# nor thinking. Probed in this order unless GRADING_MODEL is set.
META_JUDGE_CANDIDATES=("muse-spark-1.3@high" "muse-spark-1.3")
# A non-secret placeholder: with --judge meta the in-container verifier must not receive a real key
# (it would send it to api.anthropic.com); its judge call fails fast and is regraded on the host.
JUDGE_DISABLED_KEY="omniops-official-judge-disabled"
SMOKE_PREFERRED=(query-cpu-metrics logql-top-5xx-endpoint traceql-error-span-analysis incident-triage
                 dashboard-add-deployment-annotation list-datasources)
# uv / mise from their usual user install dirs, after the caller's own PATH.
for d in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME"/Library/Python/*/bin; do
  [ -d "$d" ] && PATH="$PATH:$d"
done
export PATH

die() { echo "ERROR: $*" >&2; exit 1; }
usage() { sed -n '2,24p' "$0"; }

[ $# -ge 1 ] || { usage; exit 2; }
MODE="$1"; shift
TASKS=()
if [ "$MODE" = "task" ]; then
  while [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; do TASKS+=("$1"); shift; done
  [ ${#TASKS[@]} -gt 0 ] || die "task mode needs at least one task name"
fi
case "$MODE" in smoke|full|task|judge-probe) ;; -h|--help) usage; exit 0 ;; *) usage; exit 2 ;; esac

MODEL="meta/muse-wonder-1.0"
JUDGE="official"
N_ATTEMPTS=""
N_CONCURRENT=4
JOB_NAME=""
JOBS_DIR=""
DRY_RUN=0
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --judge) JUDGE="$2"; shift 2 ;;
    --n-attempts) N_ATTEMPTS="$2"; shift 2 ;;
    --n-concurrent) N_CONCURRENT="$2"; shift 2 ;;
    --job-name) JOB_NAME="$2"; shift 2 ;;
    --jobs-dir) JOBS_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --) shift; EXTRA=("$@"); break ;;
    *) die "unknown option: $1" ;;
  esac
done
case "$JUDGE" in official|meta) ;; *) die "--judge must be official or meta" ;; esac
[ -d "$BENCH_DIR/tasks-spec" ] || die "o11y-bench not found at $BENCH_DIR (set O11Y_BENCH_DIR)"
BENCH_DIR="$(cd "$BENCH_DIR" && pwd)"
JOBS_DIR="${JOBS_DIR:-$BENCH_DIR/jobs}"

run() {  # print, then run unless --dry-run (arguments only: no key values reach the log)
  printf '+'; printf ' %q' "$@"; printf '\n'
  [ "$DRY_RUN" -eq 1 ] || "$@"
}

task_id() { sed -n 's/^id:[[:space:]]*//p' "$1" | head -n 1; }

select_tasks() {
  local category spec id picked
  for category in "$BENCH_DIR"/tasks-spec/*/; do
    picked=""
    for spec in "$category"*.yaml; do
      [ -f "$spec" ] || continue
      id="$(task_id "$spec")"
      if [ "$MODE" = "full" ]; then TASKS+=("$id"); continue; fi
      [ -n "$picked" ] || picked="$id"                        # fallback: first task of the category
      for want in "${SMOKE_PREFERRED[@]}"; do [ "$id" = "$want" ] && picked="$id"; done
    done
    if [ "$MODE" = "smoke" ] && [ -n "$picked" ]; then TASKS+=("$picked"); fi
  done
}

# Drive judge.py's own evaluate_with_llm (the production grading path) with one tiny criterion.
probe_spec() {  # $1 = GRADING_MODEL spec, $2 = auth: api_key | bearer
  local auth_env=(ANTHROPIC_API_KEY="$MODEL_API_KEY")
  [ "$2" = "bearer" ] && auth_env=(-u ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN="$MODEL_API_KEY")
  (cd "$BENCH_DIR" && env "${auth_env[@]}" ANTHROPIC_BASE_URL="$META_BASE_URL" \
    uv run python - "$1" <<'PY'
import sys

from grading.judge import JudgeError, evaluate_with_llm
from grading.models import JudgeCriterion, Message, Transcript

spec = sys.argv[1]
transcript = Transcript(messages=[
    Message(role="user", content="Which datasources are configured?"),
    Message(role="assistant", content="Two: Prometheus (type prometheus) and Loki (type loki)."),
])
criterion = "The final response names exactly two datasources."
try:
    result = evaluate_with_llm(transcript, spec, {0: JudgeCriterion(criterion, 1.0, criterion)})
except JudgeError as exc:
    print(f"  {spec}: rejected - {str(exc)[:300]}")
    raise SystemExit(1)
except Exception as exc:  # noqa: BLE001 - any failure means this setting does not work
    print(f"  {spec}: failed - {type(exc).__name__}: {str(exc)[:300]}")
    raise SystemExit(1)
print(f"  {spec}: ok (score {result[0].score})")
PY
  )
}

META_SPEC=""
META_AUTH=""
choose_meta_judge() {
  [ -n "${MODEL_API_KEY:-}" ] || die "--judge meta needs MODEL_API_KEY"
  local candidates=("${META_JUDGE_CANDIDATES[@]}") spec auth
  [ -n "${GRADING_MODEL:-}" ] && candidates=("$GRADING_MODEL")
  echo "Probing Meta's Anthropic-compatible endpoint $META_BASE_URL with judge.py:"
  for spec in "${candidates[@]}"; do
    for auth in api_key bearer; do
      if probe_spec "$spec" "$auth"; then META_SPEC="$spec"; META_AUTH="$auth"; return 0; fi
    done
  done
  die "no judge setting worked against $META_BASE_URL; grade later with the official judge (regrade)"
}

if [ "$MODE" = "judge-probe" ]; then
  choose_meta_judge
  echo "working setting: GRADING_MODEL=$META_SPEC auth=$META_AUTH ANTHROPIC_BASE_URL=$META_BASE_URL"
  exit 0
fi

[ "$MODE" = "task" ] || select_tasks
[ ${#TASKS[@]} -gt 0 ] || die "no tasks selected"
if [ -z "$N_ATTEMPTS" ]; then
  if [ "$MODE" = "full" ]; then N_ATTEMPTS=3; else N_ATTEMPTS=1; fi
fi
# Harbor's task filter as saved in a job's config.json: the sorted task names (none = every task).
expected_task_filter() { [ "$MODE" = "full" ] || printf '%s\n' "${TASKS[@]}" | sort -u; }
if [ -z "$JOB_NAME" ]; then
  slug="$(printf '%s' "$MODEL" | tr '/.' '--')"
  # task mode: a digest of the task list, so another list never lands in (and resumes) this job
  digest=""
  [ "$MODE" = "task" ] && digest="-t$(expected_task_filter | cksum | cut -d' ' -f1)"
  JOB_NAME="omniops-$slug-$MODE$digest-k$N_ATTEMPTS"
  [ "$JUDGE" = "meta" ] && JOB_NAME="$JOB_NAME-metajudge"
fi

# An existing job dir is resumed from its saved config.json, whose task filter wins over --task-name:
# refuse another task list instead of silently skipping the new tasks (and regrading the old trials).
check_job_tasks() {
  local cfg="$JOBS_DIR/$JOB_NAME/config.json" saved
  [ -f "$cfg" ] || return 0
  command -v python3 >/dev/null 2>&1 || { echo "WARNING: python3 not found; $cfg not checked" >&2; return 0; }
  saved="$(python3 -c 'import json, sys
ds = (json.load(open(sys.argv[1])).get("datasets") or [{}])[0] or {}
print("\n".join(sorted(set(ds.get("task_names") or []))))' "$cfg")" || die "cannot read $cfg"
  [ "$saved" = "$(expected_task_filter)" ] \
    || die "job $JOB_NAME already exists with another task list ($cfg); resuming it would not run the requested tasks. Use another --job-name."
}
check_job_tasks

JOB_CMD=(uv run python -m o11y_bench job --model "$MODEL" --agent-import-path "$AGENT_IMPORT_PATH"
         --n-attempts "$N_ATTEMPTS" --n-concurrent "$N_CONCURRENT" --jobs-dir "$JOBS_DIR" --job-name "$JOB_NAME")
if [ "$MODE" != "full" ]; then
  for t in "${TASKS[@]}"; do JOB_CMD+=(--task-name "$t"); done
fi
[ ${#EXTRA[@]} -eq 0 ] || JOB_CMD+=("${EXTRA[@]}")

echo "o11y-bench: $MODE, ${#TASKS[@]} task(s) x $N_ATTEMPTS attempt(s), model $MODEL, judge $JUDGE, job $JOB_NAME"
if [ "$DRY_RUN" -eq 0 ]; then
  [ -n "${MODEL_API_KEY:-}" ] || [ -n "${OMNIOPS_LLM_API_KEY_ENV:-}" ] || die "MODEL_API_KEY is not set"
  ls "$BENCH_DIR"/agents/dist/omniops/omniops-*.whl >/dev/null 2>&1 \
    || die "the OmniOps agent is not installed: run scripts/o11ybench_setup.sh"
fi

cd "$BENCH_DIR"
if [ "$JUDGE" = "official" ]; then
  [ -n "${ANTHROPIC_API_KEY:-}" ] || [ "$DRY_RUN" -eq 1 ] \
    || die "the official judge needs ANTHROPIC_API_KEY (or use --judge meta: NON-OFFICIAL)"
  run uv run python -m scripts.sync_tasks
  run "${JOB_CMD[@]}"
  exit 0
fi

# --judge meta (NON-OFFICIAL): pick a working judge setting before spending on trials.
if [ "$DRY_RUN" -eq 1 ]; then
  META_SPEC="${GRADING_MODEL:-${META_JUDGE_CANDIDATES[0]}}"; META_AUTH="api_key"
else
  choose_meta_judge
fi
run uv run python -m scripts.sync_tasks
run env ANTHROPIC_API_KEY="$JUDGE_DISABLED_KEY" "${JOB_CMD[@]}"
REGRADE=(uv run python -m o11y_bench regrade --jobs-dir "$JOBS_DIR" --job-name "$JOB_NAME" --offline)
AUTH_VAR="ANTHROPIC_API_KEY"
[ "$META_AUTH" = "bearer" ] && AUTH_VAR="ANTHROPIC_AUTH_TOKEN"
printf '+ env ANTHROPIC_BASE_URL=%s %s=$MODEL_API_KEY GRADING_MODEL=%s' "$META_BASE_URL" "$AUTH_VAR" "$META_SPEC"
printf ' %q' "${REGRADE[@]}"; printf '\n'
if [ "$DRY_RUN" -eq 0 ]; then
  if [ "$META_AUTH" = "bearer" ]; then
    env -u ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN="$MODEL_API_KEY" ANTHROPIC_BASE_URL="$META_BASE_URL" \
      GRADING_MODEL="$META_SPEC" "${REGRADE[@]}"
  else
    env ANTHROPIC_API_KEY="$MODEL_API_KEY" ANTHROPIC_BASE_URL="$META_BASE_URL" GRADING_MODEL="$META_SPEC" \
      "${REGRADE[@]}"
  fi
  # quoted: the project path has a space, and the saved command must survive copy-paste
  q_bench="$(printf '%q' "$BENCH_DIR")" q_jobs_dir="$(printf '%q' "$JOBS_DIR")" q_job_name="$(printf '%q' "$JOB_NAME")"
  cat > "$JOBS_DIR/$JOB_NAME/NON_OFFICIAL_JUDGE.txt" <<EOF
NON-OFFICIAL GRADES: judged by $META_SPEC through $META_BASE_URL (Anthropic Messages compatible,
auth $META_AUTH), not by the judge the tasks ship with. Not comparable with the leaderboard.
Regrade with the official judge (saved transcripts and truth are reused):
  cd $q_bench && ANTHROPIC_API_KEY=... uv run python -m o11y_bench regrade --jobs-dir $q_jobs_dir --job-name $q_job_name --offline
EOF
  echo "NON-OFFICIAL scores written; see $JOBS_DIR/$JOB_NAME/NON_OFFICIAL_JUDGE.txt"
fi
