#!/usr/bin/env bash
# Build the per-task work template: BASE tree without history + preinstalled deps.
#   bin/template.sh tasks/<id>      -> .cache/templates/<id>/
# Runs OUTSIDE the sandbox (needs GitHub for dependency sync).
set -euo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
T="$(realpath "$1")"; ID="$(basename "$T")"
source "$T/task.env"
SRC="$BENCH/.cache/repos/$REPO.git"
OUT="$BENCH/.cache/templates/$ID"
rm -rf "$OUT"; mkdir -p "$OUT"
# with PREPARE the package builds the whole tree (it empties the dir first), so the BASE tree is not needed; tasks
# from packages without sources.json still start from it
if [ -z "${PREPARE:-}" ] || [ ! -f "${ABAP_TASKS:-}/private/src-repos/sources.json" ]; then
  git -C "$SRC" archive "$BASE" | tar -x -C "$OUT"
fi
for p in $STRIP; do rm -rf "$OUT/$p"; done
cd "$OUT"
# PREPARE (native ABAP tasks): trusted package code that turns the tree into the task's workspace (cuts the hole,
# removes held-out tests); it runs before the baseline commit, so the prepared tree is the baseline
if [ -n "${PREPARE:-}" ]; then
  # PREPARE empties its working directory first: run it only inside this task's template dir
  [ "$(pwd -P)" = "$(realpath "$BENCH/.cache/templates")/$ID" ] || { echo "[template] refusing PREPARE outside the template dir: $(pwd -P)" >&2; exit 2; }
  echo "[template] $ID: PREPARE"
  ABAP_TASKS="${ABAP_TASKS:-}" bash -c "$PREPARE"
  rm -rf .git
fi
git init -q
git add -A
git -c user.name=bench -c user.email=bench@localhost commit -qm "baseline" --no-gpg-sign
git tag baseline
echo "[template] $ID: $(git ls-files | wc -l) files; running setup"
bash -c "$SETUP"
# setup tools may leave absolute symlinks into this template dir; make them relative
find . -path ./node_modules -prune -o -type l -print | while read -r l; do
  t="$(readlink "$l")"; case "$t" in "$OUT"/*) ln -sfn "$(realpath -s --relative-to="$(dirname "$l")" "$t")" "$l" ;; esac
done
git status --porcelain | head -5 | sed 's/^/[template] dirty after setup: /' || true
sha256sum < "$T/task.env" | cut -d' ' -f1 > "$OUT.stamp"   # bin/setup.sh rebuilds when the descriptor changes
echo "[template] $ID ready: $OUT ($(du -sh . | cut -f1))"
