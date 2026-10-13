#!/usr/bin/env bash
# Review every finished run matching a glob with every critic; one lane per critic provider.
#   bin/review-all.sh 'runs/L1*'
BENCH="$(cd "$(dirname "$0")/.." && pwd)"; cd "$BENCH"
RUNS=$(for d in $1/*/; do [ -f "$d/result.json" ] && echo "${d%/}"; done)
lane() { for cr in $1; do for r in $RUNS; do
  a=$(printf '%s' "$(realpath "$r")" | sha256sum | cut -c1-10)
  [ -f "runs/_review/$a/$cr/review.json" ] && grep -q '"verdict"' "runs/_review/$a/$cr/review.json" && continue
  bin/review.sh "$r" "$cr"; done; done; }
lane "K1-astra K2-sol" & lane "K3-opus" & lane "K4-glm" & wait
