#!/usr/bin/env bash
# One-time preparation, safe to re-run: check the machine, clone the task repositories,
# build every task's base tree, and (with --check) verify each task's grading.
#   bin/setup.sh [--check] [--rebuild] [task ...]     tasks: ids or prefixes (L1, L2b); default all
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
. "$BENCH/bin/lib.sh"
bench_load_env
CHECK=0 REBUILD=0 SEL=()
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --rebuild) REBUILD=1 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) SEL+=("$a") ;;
  esac
done

"$BENCH/bin/doctor.sh" --base || { echo; echo "Fix the items marked ✗ above, then run bin/setup.sh again."; exit 1; }

TASKS=()
for t in "$BENCH"/tasks/*/; do
  id="$(basename "$t")"
  if [ ${#SEL[@]} -eq 0 ]; then TASKS+=("$id"); continue; fi
  for s in "${SEL[@]}"; do [[ "$id" == "$s"* ]] && { TASKS+=("$id"); break; }; done
done
[ ${#TASKS[@]} -gt 0 ] || { echo "no task matches: ${SEL[*]}" >&2; exit 2; }

echo; echo "== Task repositories"
mkdir -p "$BENCH/.cache/repos"
for id in "${TASKS[@]}"; do
  ( source "$BENCH/tasks/$id/task.env"; R="$BENCH/.cache/repos/$REPO.git"
    if [ ! -d "$R" ]; then
      echo "  cloning oisee/$REPO ..."
      git clone -q --bare "https://github.com/oisee/$REPO" "$R" || exit 1
    fi
    for c in $BASE $MERGE; do
      git -C "$R" cat-file -e "$c^{commit}" 2>/dev/null && continue
      echo "  fetching oisee/$REPO (missing $c) ..."
      git -C "$R" fetch -q origin '+refs/heads/*:refs/heads/*' && git -C "$R" cat-file -e "$c^{commit}" || exit 1
    done
    echo "  ✓ $id: $REPO" ) || { echo "  ✗ $id: cannot get oisee/$(. "$BENCH/tasks/$id/task.env"; echo "$REPO")" >&2; exit 1; }
done

echo; echo "== Task base trees (.cache/templates)"
FAILED=()
for id in "${TASKS[@]}"; do
  if [ $REBUILD = 0 ] && git -C "$BENCH/.cache/templates/$id" rev-parse -q --verify baseline >/dev/null 2>&1; then
    echo "  ✓ $id (already built)"; continue
  fi
  echo "  building $id (installs dependencies, may take a few minutes) ..."
  if "$BENCH/bin/template.sh" "$BENCH/tasks/$id" > "$BENCH/.cache/template-$id.log" 2>&1; then
    echo "  ✓ $id"
  else
    echo "  ✗ $id failed, see .cache/template-$id.log"; FAILED+=("$id")
  fi
done

if [ $CHECK = 1 ]; then
  echo; echo "== Grading check (base must fail, upstream fix must pass)"
  for id in "${TASKS[@]}"; do
    out="$("$BENCH/bin/goldcheck.sh" "$BENCH/tasks/$id" 2>&1)"
    if [ "$(grep -oE '\[grade\] (FAILED|RESOLVED)' <<<"$out" | cut -d' ' -f2 | paste -sd' ')" = "FAILED RESOLVED" ]; then
      echo "  ✓ $id"
    else
      echo "  ✗ $id"; sed 's/^/      /' <<<"$out"; FAILED+=("$id")
    fi
  done
fi

echo
if [ ${#FAILED[@]} -gt 0 ]; then echo "Setup finished with problems: ${FAILED[*]}"; exit 1; fi
echo "Ready. Next: put your endpoint in .env (cp .env.example .env), then"
echo "  bin/doctor.sh --smoke <executor-id>     # 1-minute check that the model works in the sandbox"
echo "  bin/bench.sh <executor-id>              # run all tasks"
