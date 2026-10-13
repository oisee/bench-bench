#!/usr/bin/env bash
# One benchmark run. New runs default to two independent critic rounds before
# grading; set BENCH_INTERNAL_REVIEW_ROUNDS=0 to reproduce the old single-session protocol.
#   bin/run.sh tasks/<task-id> <executor-id>        (executor ids: matrix.yaml)
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
case "${BENCH_INTERNAL_REVIEW_ROUNDS:-2}" in
  2) exec "$BENCH/bin/run-reviewed.sh" "$@" ;;
  0) ;;
  *) echo 'BENCH_INTERNAL_REVIEW_ROUNDS must be 0 or 2' >&2; exit 2 ;;
esac
T="$(realpath "$1")"; TID="$(basename "$T")"; EX="$2"
source "$T/task.env"

read -r PROVIDER MODEL HARNESS EFFORT < <(python3 -I - "$BENCH/matrix.yaml" "$EX" <<'EOF'
import re,sys
for line in open(sys.argv[1]):
    m=re.search(r'\{id:\s*([^,]+),\s*provider:\s*([^,]+),\s*model:\s*("?[^,"]+"?),\s*harness:\s*([^,]+),\s*effort:\s*([^}]+)\}',line)
    if m and not line.lstrip().startswith('#') and m.group(1).strip()==sys.argv[2]:
        print(m.group(2).strip(), m.group(3).strip().strip('"'), m.group(4).strip(), m.group(5).strip()); break
else: sys.exit(1)
EOF
) || { echo "unknown executor $EX" >&2; exit 2; }

RUN="$BENCH/runs/$TID/$EX-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
cp -a --reflink=auto "$BENCH/.cache/templates/$TID" "$RUN/work"
BRIEF="$(cat "$T/brief.md")"
cp "$T/brief.md" "$RUN/brief.md"
echo "[run] $TID / $EX ($HARNESS · $PROVIDER · $MODEL · $EFFORT) -> $RUN"

case "$HARNESS" in
  pi)     ARGS=(-p "$BRIEF") ;;
  claude) ARGS=(-p "$BRIEF" --dangerously-skip-permissions --output-format stream-json --verbose) ;;
  codex)  ARGS=(exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json "$BRIEF") ;;
esac

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
