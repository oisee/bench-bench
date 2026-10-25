#!/usr/bin/env bash
# Validate a task's grading: baseline must FAIL (f2p red, p2p green), gold patch must be RESOLVED.
#   bin/goldcheck.sh tasks/<id>
set -euo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
T="$(realpath "$1")"; ID="$(basename "$T")"; source "$T/task.env"
SRC="$BENCH/.cache/repos/$REPO.git"
for mode in baseline gold; do
  R="$BENCH/runs/_gold/$ID-$mode"; rm -rf "$R"; mkdir -p "$R"
  cp -a --reflink=auto "$BENCH/.cache/templates/$ID" "$R/work"
  if [ $mode = gold ] && [ -n "${GOLD_PREPARE:-}" ]; then
    # native ABAP tasks: the package builds the workspace with the reference solution in place; it replaces the
    # tree, keeping the template's .git so the patch is taken against the same baseline
    S="$(mktemp -d)"; (cd "$S" && ABAP_TASKS="$ABAP_TASKS" bash -c "$GOLD_PREPARE")
    find "$R/work" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf -- {} +
    cp -a "$S"/. "$R/work"/; rm -rf "$S"
  elif [ $mode = gold ]; then
    EX=(); for p in $HIDDEN; do EX+=(":(exclude)$p"); done
    git -C "$SRC" diff "$BASE" "$MERGE" -- . "${EX[@]}" | git -C "$R/work" apply --whitespace=nowarn
  fi
  echo "== $ID $mode"; "$BENCH/bin/grade.sh" "$R" "$T" | sed 's/^/   /'
done
