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
git -C "$SRC" archive "$BASE" | tar -x -C "$OUT"
for p in $STRIP; do rm -rf "$OUT/$p"; done
cd "$OUT"
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
echo "[template] $ID ready: $OUT ($(du -sh . | cut -f1))"
