#!/usr/bin/env bash
# Run executors one after another (one provider = one lane, never parallel within a provider).
#   bin/lane.sh "<task-dir> ..." "<executor-id> ..."
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
for ex in $2; do for t in $1; do "$BENCH/bin/run.sh" "$t" "$ex"; done; done
