#!/usr/bin/env bash
# Run one executor over the tasks, one task at a time, and print a results table.
# Safe to re-run: tasks that already have a graded run for this executor are skipped.
#   bin/bench.sh <executor-id> [task ...]   tasks: ids or prefixes (L1, L2b); default all
#   bin/bench.sh <executor-id> --status     results so far, nothing is run
#   bin/bench.sh <executor-id> --again ...  run even tasks that already have a result
# BENCH_RECORD=1 (provider custom) also logs all model traffic to each run's traffic.jsonl.
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
. "$BENCH/bin/lib.sh"
bench_load_env
[ $# -ge 1 ] && [[ "$1" != -* ]] || { sed -n '2,8p' "$0"; exit 2; }
EX="$1"; shift
STATUS=0 AGAIN=0 SEL=()
for a in "$@"; do
  case "$a" in --status) STATUS=1 ;; --again) AGAIN=1 ;; *) SEL+=("$a") ;; esac
done
executor_fields "$EX" >/dev/null || { echo "unknown executor $EX: add it to matrix.yaml" >&2; exit 2; }

TASKS=()
for t in "$BENCH"/tasks/*/; do
  id="$(basename "$t")"
  if [ ${#SEL[@]} -eq 0 ]; then TASKS+=("$id"); continue; fi
  for s in "${SEL[@]}"; do [[ "$id" == "$s"* ]] && { TASKS+=("$id"); break; }; done
done
[ ${#TASKS[@]} -gt 0 ] || { echo "no task matches: ${SEL[*]}" >&2; exit 2; }

# newest graded run of this executor on task $1
last_result() { ls -1d "$BENCH/runs/$1/$EX-"*/result.json 2>/dev/null | sort | tail -1; }

table() {
  python3 -I - "$EX" "$@" <<'EOF'
import json,sys
ex,paths=sys.argv[1],sys.argv[2:]
print(f"\n{ex}\n")
print(f"  {'task':<16} {'result':<10} {'time':>7}  {'stop':<8} {'patch':>6}")
solved=done=0
for p in paths:
    tid,_,path=p.partition("=")
    if not path:
        print(f"  {tid:<16} {'-':<10}"); continue
    r=json.load(open(path)); done+=1; solved+=r["status"]=="RESOLVED"
    print(f"  {tid:<16} {r['status']:<10} {r['duration_s']/60:6.1f}m  {r['stop']:<8} {r['patch_lines']:>5}l")
print(f"\n  solved {solved}/{done}" + (f" ({len(paths)-done} not run)" if done<len(paths) else ""))
EOF
}

collect() { ROWS=(); for id in "${TASKS[@]}"; do ROWS+=("$id=$(last_result "$id")"); done; }
if [ $STATUS = 1 ]; then collect; table "${ROWS[@]}"; exit 0; fi

CHECK="$("$BENCH/bin/doctor.sh" "$EX" 2>&1)" || { echo "$CHECK"; exit 1; }

TODO=(); MIN=0
for id in "${TASKS[@]}"; do
  if [ $AGAIN = 0 ] && [ -n "$(last_result "$id")" ]; then echo "[bench] $id: already done (--again to repeat)"; continue; fi
  TODO+=("$id"); MIN=$(( MIN + $(. "$BENCH/tasks/$id/task.env"; echo "$TIME_MIN") ))
done
[ ${#TODO[@]} -gt 0 ] && echo "[bench] $EX: ${#TODO[@]} task(s), at most ~${MIN} min of agent time plus grading"
for id in "${TODO[@]}"; do
  "$BENCH/bin/run.sh" "$BENCH/tasks/$id" "$EX"
  case $? in 0) ;; 2) echo "[bench] $id: run did not produce a result; see the messages above" ;; esac
done
collect; table "${ROWS[@]}"
