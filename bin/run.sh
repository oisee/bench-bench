#!/usr/bin/env bash
# One benchmark run: a single agent session, then grading (the baseline protocol).
# Set BENCH_INTERNAL_REVIEW_ROUNDS=2 to opt in to the two-critic builder protocol
# (docs/internal-review-protocol.md); its 3-run pilot showed no solve gain at 2-10x time.
# Set BENCH_RECORD=1 (provider custom or zai) to log every model request/response to RUN/traffic.jsonl.
# It also snapshots the work tree as each request arrives (RUN/snap.git, outside the sandbox; BENCH_SNAPSHOTS=0
# turns that off); bin/first-green.py RUN TASK grades them afterwards for time to first green.
# Set BENCH_TIME_SCALE=N to multiply the task's time limit (slow local hardware); such runs are
# recorded with protocol time-scaled-v1 and time_scale=N so they never mix with standard-limit runs.
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
SCALE="${BENCH_TIME_SCALE:-1}"
[[ "$SCALE" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "BENCH_TIME_SCALE must be a number" >&2; exit 2; }
LIMIT_S=$(awk -v m="$TIME_MIN" -v s="$SCALE" 'BEGIN { printf "%d", m * 60 * s }')

read -r PROVIDER MODEL HARNESS EFFORT < <(executor_fields "$EX") || { echo "unknown executor $EX (see matrix.yaml)" >&2; exit 2; }
[ -d "$BENCH/.cache/templates/$TID" ] || { echo "no template for $TID: run bin/setup.sh first" >&2; exit 2; }

RUN="$BENCH/runs/$TID/$EX-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
cp -a --reflink=auto "$BENCH/.cache/templates/$TID" "$RUN/work"
BRIEF="$(cat "$T/brief.md")"
cp "$T/brief.md" "$RUN/brief.md"
echo "[run] $TID / $EX ($HARNESS · $PROVIDER · $MODEL · $EFFORT, limit $((LIMIT_S / 60)) min) -> $RUN"

harness_args "$HARNESS" "$BRIEF" || exit 2

# A custom endpoint's key never enters the sandbox: the agent gets a dummy key and the proxy (outside the sandbox)
# swaps in the real one, so the agent cannot print it into its tree, transcript or traffic.
REAL_KEY=""
[ "$PROVIDER" = custom ] && [ -n "${CUSTOM_API_KEY:-}" ] && [ "$CUSTOM_API_KEY" != none ] && REAL_KEY="$CUSTOM_API_KEY"
if [ "$PROVIDER" = zai ] && [ "${BENCH_RECORD:-0}" = 1 ]; then   # z.ai: recorded the same way, the proxy holds its key
  REAL_KEY="${ZAI_API_KEY:-$(key_from_rc ZAI_API_KEY)}"
  [ -n "$REAL_KEY" ] || { echo "ZAI_API_KEY not set" >&2; exit 1; }
fi
if [ "${BENCH_RECORD:-0}" = 1 ] || [ -n "$REAL_KEY" ]; then
  case "$PROVIDER" in custom|zai) ;; *) echo 'BENCH_RECORD=1 needs provider custom or zai' >&2; exit 2 ;; esac
  RECORD=$([ "${BENCH_RECORD:-0}" = 1 ] && echo 1 || echo 0)
  if [ "$PROVIDER" = zai ]; then UP="$ZAI_ORIGIN$([ "$HARNESS" = claude ] && echo "$ZAI_ANTHROPIC_PATH" || echo "$ZAI_CODEX_PATH")"
  elif [ "$HARNESS" = claude ]; then UP="$(custom_anthropic_url)"; else UP="${CUSTOM_BASE_URL%/}"; fi
  [[ "$UP" =~ ^(https?://[^/]+)(.*)$ ]] || { echo "cannot parse endpoint URL $UP" >&2; exit 2; }
  ORIGIN="${BASH_REMATCH[1]}"; UPATH="${BASH_REMATCH[2]}"
  # snapshots of the work tree per request, outside the sandbox, for bin/first-green.py (BENCH_SNAPSHOTS=0 turns them off)
  [ "$RECORD" = 1 ] && [ "${BENCH_SNAPSHOTS:-1}" = 1 ] && export BENCH_SNAPSHOT_RUN="$RUN"
  coproc PROXY { export BENCH_PROXY_RECORD="$RECORD" BENCH_UPSTREAM_KEY="$REAL_KEY"
                 exec python3 -I "$BENCH/bin/record-proxy.py" "$ORIGIN" "$RUN/traffic.jsonl" 2> "$RUN/proxy.err"; }
  read -r -t 10 PORT <&"${PROXY[0]}" || { echo 'record proxy did not start (see proxy.err)' >&2; exit 2; }
  trap 'kill "$PROXY_PID" 2>/dev/null' EXIT
  if [ "$PROVIDER" = zai ]; then export ZAI_ORIGIN="http://127.0.0.1:$PORT" ZAI_API_KEY=bench-sandbox-dummy-key
  elif [ "$HARNESS" = claude ]; then export CUSTOM_ANTHROPIC_BASE_URL="http://127.0.0.1:$PORT$UPATH"
  else export CUSTOM_BASE_URL="http://127.0.0.1:$PORT$UPATH"; fi
  [ -n "$REAL_KEY" ] && [ "$PROVIDER" = custom ] && export CUSTOM_API_KEY=bench-sandbox-dummy-key   # must match DUMMY_KEY in record-proxy.py
  if [ "$RECORD" = 1 ]; then echo "[run] recording model traffic via 127.0.0.1:$PORT -> $RUN/traffic.jsonl"
  else echo "[run] endpoint key held by the proxy on 127.0.0.1:$PORT (not recording)"; fi
fi

export BENCH_AGENT_TOOL="${AGENT_TOOL:-}"   # native ABAP tasks: osg-abap, read-only at /agent-tool in the sandbox
[ -n "${ABAP_PACKAGE:-}" ] && export BENCH_NO_WEB=1   # their answers are public code: no web tools (sandbox.sh)
T0=$(date +%s)
SNAP="${BENCH_SNAPSHOT_RUN:+$RUN/snap.git}"
[ -n "$SNAP" ] && [ -d "$SNAP" ] && echo "$T0" > "$SNAP/t0"   # duration origin for bin/first-green.py
timeout --kill-after=30 "$LIMIT_S" "$BENCH/bin/sandbox.sh" --run "$RUN" --harness "$HARNESS" \
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

if [ "$SCALE" != 1 ]; then
  python3 -I -c 'import json,sys;p=sys.argv[1];d=json.load(open(p));d.update(protocol="time-scaled-v1",time_scale=sys.argv[2],time_limit_s=int(sys.argv[3]));json.dump(d,open(p,"w"),indent=2)' \
    "$RUN/meta.json" "$SCALE" "$LIMIT_S"
fi
if [ -n "${ABAP_PACKAGE:-}" ]; then   # native ABAP tasks: their own protocol, the package commit and the tier
  python3 -I -c 'import json,sys;p=sys.argv[1];d=json.load(open(p));d.update(protocol="abap-native-v1",abap_package=sys.argv[2],abap_tier=sys.argv[3],time_limit_s=int(sys.argv[4]),abap_set=sys.argv[5]);d.update({k:v for k,v in (("task_uid",sys.argv[6]),("env_version",sys.argv[7]),("content_digest",sys.argv[8])) if v});json.dump(d,open(p,"w"),indent=2)' \
    "$RUN/meta.json" "$ABAP_PACKAGE" "${ABAP_TIER:-}" "$LIMIT_S" "${ABAP_SET:-corpus}" "${TASK_UID:-}" "${ENV_VERSION:-}" "${CONTENT_DIGEST:-}"
fi
# stop the proxy before the final snapshot and finalize: no request snapshot may race them, and finalize rewrites
# traffic.jsonl/live.txt when it masks secrets
if [ -n "${PROXY_PID:-}" ]; then kill "$PROXY_PID" 2>/dev/null; wait "$PROXY_PID" 2>/dev/null
  [ -n "$SNAP" ] && rm -f "$SNAP/index.lock"; fi   # a snapshot cut short by the kill leaves its lock
# the tree that finalize grades, timed at the end of the session
[ -n "$SNAP" ] && [ -d "$SNAP" ] && { python3 -I "$BENCH/bin/snapshot.py" take "$RUN" final $((T0 + DUR)) > /dev/null || echo '[run] final snapshot failed' >&2; }
BENCH_SECRET="$REAL_KEY" "$BENCH/bin/finalize.sh" "$RUN" "$T" "$EX" "$RC" "$DUR"
