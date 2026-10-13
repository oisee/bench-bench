#!/usr/bin/env bash
# Blind critic screening: every screen critic reviews the same balanced set of
# finished L2b/L3b patches without hidden-test results (REVIEW_BLIND=1).
# One lane per critic provider; within a lane patches are interleaved
# pass/fail so a partial run stays balanced. Re-running skips finished reviews.
#   bin/critic-screen.sh ["<lane critics>" ...]   (default: the four initial lanes)
# SCREEN_N=6 (default) reviews only the first N patches: 3 RESOLVED + 3 FAILED.
# Results: runs/_screen/<anon>/<critic>/review.json; summary: bin/critic-screen.py
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"; cd "$BENCH"
export REVIEW_BLIND=1

# 7 RESOLVED + 7 FAILED round-1 patches, alternating; ground truth stays hidden.
PATCHES=(
  L2b-abapiti87/ant-opus-claude-20261012-110822
  L3b-abapiti95/az-sol61-codex-20261013-053019
  L2b-abapiti87/az-sol61-codex-20261012-113203
  L2b-abapiti87/oai-luna-codex-h-20261012-234315
  L3b-abapiti95/ant-opus-claude-20261013-090112
  L2b-abapiti87/zai-glm53-codex-20261012-232158
  L2b-abapiti87/oai-luna-codex-x-20261012-235415
  L3b-abapiti95/zai-glm53-codex-20261013-113243
  L2b-abapiti87/zai-glm53-claude-20261012-125544
  L2b-abapiti87/ali-qwen37-codex-20261013-023349
  L3b-abapiti95/zai-glm53-claude-20261013-060457
  L3b-abapiti95/zai-glm53-pi-20261013-070528
  L2b-abapiti87/zai-glm53-pi-20261012-114913
  L2b-abapiti87/oai-luna-codex-m-20261012-234236
)

PATCHES=("${PATCHES[@]:0:${SCREEN_N:-6}}")

lane() { for p in "${PATCHES[@]}"; do for cr in $1; do
  r="$BENCH/runs/$p"
  a=$(printf '%s' "$(realpath "$r")" | sha256sum | cut -c1-10)
  # a review left running by an earlier invocation finishes on its own; wait for it
  while pgrep -f "bin/review.sh $r $cr\$" >/dev/null; do sleep 30; done
  grep -qs '"verdict"' "runs/_screen/$a/$cr/review.json" && continue
  systemd-run --user --scope -q -p MemoryHigh=5G -p MemoryMax=6G bin/review.sh "$r" "$cr"
done; done; }
[ $# -gt 0 ] || set -- "S-opus S-fable" "S-sol" "S-glm" "S-glm-pi" "S-qwen-pi"
for l in "$@"; do lane "$l" & done; wait
python3 -I bin/critic-screen.py
