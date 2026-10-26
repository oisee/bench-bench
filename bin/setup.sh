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

TASKS_DIR="$(realpath "${BENCH_TASKS_DIR:-$BENCH/tasks}")"   # BENCH_TASKS_DIR=tasks-abap: the native ABAP tasks (bin/abap-tasks.sh)
TASKS=()
for t in "$TASKS_DIR"/*/; do
  [ -f "$t/task.env" ] || continue
  id="$(basename "$t")"
  if [ ${#SEL[@]} -eq 0 ]; then TASKS+=("$id"); continue; fi
  for s in "${SEL[@]}"; do [[ "$id" == "$s"* ]] && { TASKS+=("$id"); break; }; done
done
[ ${#TASKS[@]} -gt 0 ] || { echo "no task matches: ${SEL[*]}" >&2; exit 2; }

echo; echo "== Task repositories"
mkdir -p "$BENCH/.cache/repos"
for id in "${TASKS[@]}"; do
  ( source "$TASKS_DIR/$id/task.env"; R="$BENCH/.cache/repos/$REPO.git"; U="${URL:-https://github.com/oisee/$REPO}"
    # native ABAP tasks: PREPARE builds the whole workspace from the package, so no base clone and no network. Instead
    # the task's BASE is checked against the package: an agentic task (built from a snapshot of the change's parent)
    # needs BASE == task.json sha == parent; any other task needs BASE == the sources.json revision of its repository.
    if [ -n "${PREPARE:-}" ] && [ -f "${ABAP_TASKS:-}/private/src-repos/sources.json" ]; then
      python3 -I - "$ABAP_TASKS" "$id" "$BASE" "${UPSTREAM_URL:-$URL}" <<'PY' || exit 1
import json, os, sys
root, tid, base, url = sys.argv[1:5]
pa = os.path.join(root, 'private', 'abap-tasks')
t = None
tj = os.path.join(pa, 'tasks', tid, 'task.json')
if os.path.exists(tj):
    t = json.load(open(tj))
elif os.path.exists(os.path.join(pa, 'tasks.jsonl')):
    t = next((json.loads(l) for l in open(os.path.join(pa, 'tasks.jsonl')) if json.loads(l).get('id') == tid), None)
if t is None:
    sys.exit(f'the package has no descriptor for {tid}')
if t.get('kind') == 'agentic':
    if not (t.get('sha') == t.get('parent') == base):
        sys.exit(f'{tid}: BASE {base[:12]} != task sha {str(t.get("sha"))[:12]} / parent {str(t.get("parent"))[:12]}')
else:
    src = json.load(open(os.path.join(root, 'private', 'src-repos', 'sources.json')))
    e = src.get(t.get('repo') or '') or next((v for v in src.values()
        if v.get('url', '').lower().removesuffix('.git') == url.lower().removesuffix('.git')), None)
    if not e:
        sys.exit(f'{tid}: sources.json has no repository {t.get("repo") or url}')
    if e.get('revision') != base:
        sys.exit(f'{tid}: BASE {base[:12]} != sources.json revision {str(e.get("revision"))[:12]}')
PY
      echo "  ✓ $id: $REPO (from the package, BASE ${BASE:0:12})"; exit 0
    fi
    if [ ! -d "$R" ]; then
      echo "  cloning $U ..."
      git clone -q --bare "$U" "$R" || exit 1
    fi
    for c in $BASE $MERGE; do
      git -C "$R" cat-file -e "$c^{commit}" 2>/dev/null && continue
      echo "  fetching $U (missing $c) ..."
      git -C "$R" fetch -q origin '+refs/heads/*:refs/heads/*' && git -C "$R" cat-file -e "$c^{commit}" || exit 1
    done
    echo "  ✓ $id: $REPO" ) || { echo "  ✗ $id: cannot get $(. "$TASKS_DIR/$id/task.env"; echo "${URL:-oisee/$REPO}")" >&2; exit 1; }
done

echo; echo "== Task base trees (.cache/templates)"
FAILED=()
for id in "${TASKS[@]}"; do
  stamp="$BENCH/.cache/templates/$id.stamp"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" != "$(sha256sum < "$TASKS_DIR/$id/task.env" | cut -d' ' -f1)" ]; then
    echo "  $id: task.env changed since the template was built; rebuilding"
  elif [ $REBUILD = 0 ] && git -C "$BENCH/.cache/templates/$id" rev-parse -q --verify baseline >/dev/null 2>&1; then
    echo "  ✓ $id (already built)"; continue
  fi
  echo "  building $id (installs dependencies, may take a few minutes) ..."
  if "$BENCH/bin/template.sh" "$TASKS_DIR/$id" > "$BENCH/.cache/template-$id.log" 2>&1; then
    echo "  ✓ $id"
  else
    echo "  ✗ $id failed, see .cache/template-$id.log"; FAILED+=("$id")
  fi
done

if [ $CHECK = 1 ]; then
  echo; echo "== Grading check (base must fail, upstream fix must pass)"
  for id in "${TASKS[@]}"; do
    out="$("$BENCH/bin/goldcheck.sh" "$TASKS_DIR/$id" 2>&1)"
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
