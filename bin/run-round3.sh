#!/usr/bin/env bash
# Round 3 (the last planned spend): effort and executable-feedback conditions, one run each, sequential.
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
cap() { systemd-run --user --scope -q -p MemoryHigh=14G -p MemoryMax=18G "$@"; }
BENCH_ROUND=3 BENCH_INTERNAL_REVIEW_ROUNDS=0 cap "$BENCH/bin/run.sh" "$BENCH/tasks/L3b-abapiti95" az-sol61-codex-hi
cap "$BENCH/bin/run-visible.sh" gate    "$BENCH/tasks/L3b-abapiti95" az-sol61-codex-gate
cap "$BENCH/bin/run-visible.sh" visible "$BENCH/tasks/L2b-abapiti87" oai-luna-codex-m-vis
cap "$BENCH/bin/run-visible.sh" visible "$BENCH/tasks/L2b-abapiti87" oai-luna-codex-h-vis
python3 -I "$BENCH/bin/export.py"
