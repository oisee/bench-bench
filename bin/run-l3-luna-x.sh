#!/usr/bin/env bash
# Corrected L3 eligibility: Luna xhigh / Codex qualified after the L2a grader fix
# (data/l3-corrected-eligibility.json) but was never run on L3. Baseline protocol,
# one run per task, sequential, same memory cap as the original L3 queue.
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
EX=oai-luna-codex-x
for task in L3a-minz39 L3b-abapiti95; do
  if ls "$BENCH"/runs/$task/$EX-*/result.json >/dev/null 2>&1; then echo "[skip] $task / $EX already graded"; continue; fi
  echo "[next] $task / $EX (MemoryHigh=14G MemoryMax=18G)"
  BENCH_INTERNAL_REVIEW_ROUNDS=0 systemd-run --user --scope -q -p MemoryHigh=14G -p MemoryMax=18G \
    "$BENCH/bin/run.sh" "$BENCH/tasks/$task" "$EX"
done
python3 -I "$BENCH/bin/export.py" && python3 -I "$BENCH/bin/summary-report.py"
