#!/usr/bin/env bash
# One benchmark run: a single agent session, then grading (the baseline protocol).
# Set BENCH_INTERNAL_REVIEW_ROUNDS=2 to opt in to the two-critic builder protocol
# (docs/internal-review-protocol.md); its 3-run pilot showed no solve gain at 2-10x time.
# Set BENCH_RECORD=1 (provider custom) to log every model request/response to RUN/traffic.jsonl.
#   bin/run.sh tasks/<task-id> <executor-id>        (executor ids: matrix.yaml)
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
. "$BENCH/bin/lib.sh"
bench_load_env
case "${BENCH_INTERNAL_REVIEW_ROUNDS:-0}" in
  2) exec "$BENCH/bin/run-reviewed.sh" "$@" ;;
  0) ;;
  *) echo 'BENCH_INTERNAL_REVIEW_ROUNDS must be 0 or 2' >&2; exit 2 ;;
esac
T="$(realpath "$1")"; TID="$(basename "$T")"; EX="$2"
source "$T/task.env"

read -r PROVIDER MODEL HARNESS EFFORT < <(executor_fields "$EX") || { echo "unknown executor $EX (see matrix.yaml)" >&2; exit 2; }
[ -d "$BENCH/.cache/templates/$TID" ] || { echo "no template for $TID: run bin/setup.sh first" >&2; exit 2; }

RUN="$BENCH/runs/$TID/$EX-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
cp -a --reflink=auto "$BENCH/.cache/templates/$TID" "$RUN/work"
BRIEF="$(cat "$T/brief.md")"
cp "$T/brief.md" "$RUN/brief.md"
echo "[run] $TID / $EX ($HARNESS · $PROVIDER · $MODEL · $EFFORT) -> $RUN"

harness_args "$HARNESS" "$BRIEF" || exit 2

if [ "${BENCH_RECORD:-0}" = 1 ]; then
  [ "$PROVIDER" = custom ] || { echo 'BENCH_RECORD=1 needs provider custom' >&2; exit 2; }
  if [ "$HARNESS" = claude ]; then UP="$(custom_anthropic_url)"; else UP="${CUSTOM_BASE_URL%/}"; fi
  [[ "$UP" =~ ^(https?://[^/]+)(.*)$ ]] || { echo "cannot parse endpoint URL $UP" >&2; exit 2; }
  ORIGIN="${BASH_REMATCH[1]}"; UPATH="${BASH_REMATCH[2]}"
  coproc PROXY { exec python3 -I "$BENCH/bin/record-proxy.py" "$ORIGIN" "$RUN/traffic.jsonl" 2> "$RUN/proxy.err"; }
  read -r -t 10 PORT <&"${PROXY[0]}" || { echo 'record proxy did not start (see proxy.err)' >&2; exit 2; }
  trap 'kill "$PROXY_PID" 2>/dev/null' EXIT
  if [ "$HARNESS" = claude ]; then export CUSTOM_ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT$UPATH"
  else export CUSTOM_BASE_URL="http://127.0.0.1:$PORT$UPATH"; fi
  echo "[run] recording model traffic via 127.0.0.1:$PORT -> $RUN/traffic.jsonl"
fi

T0=$(date +%s)
timeout --kill-after=30 "$((TIME_MIN*60))" "$BENCH/bin/sandbox.sh" --run "$RUN" --harness "$HARNESS" \
  --provider "$PROVIDER" --model "$MODEL" --effort "$EFFORT" -- "${ARGS[@]}" \
  < /dev/null > "$RUN/agent.out" 2> "$RUN/agent.err"
RC=$?
DUR=$(( $(date +%s) - T0 ))
STOP=$([ $RC = 124 ] || [ $RC = 137 ] && echo TIMEOUT || echo DONE)
echo "[run] agent exit=$RC ($STOP) after ${DUR}s"

if [[ ( "$RC" == 126 || "$RC" == 127 ) && "$DUR" -lt 10 && ! -s "$RUN/agent.out" ]]; then
  echo '[run] agent launcher failed; keeping the worktree without a benchmark result' >&2
  exit 2
fi

"$BENCH/bin/finalize.sh" "$RUN" "$T" "$EX" "$RC" "$DUR"
