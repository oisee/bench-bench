#!/usr/bin/env bash
# Native ABAP tasks (an external, privately distributed package) as bench-bench tasks.
#   ABAP_TASKS=<package root> ABAP_TASKS_REPO=<descriptor checkout> bin/abap-tasks.sh [smoke|medium|full|ID ...]
# The package root is <root>/{agent,grader-tool,private/{abap-tasks,src-repos,hidden}}, named after the descriptor
# repository's commit; tasks-abap/PACKAGE pins that commit. Descriptors are read from that commit's git objects, package
# files are checked against <root>/MANIFEST.sha256 when present, and sources come from the package's own clones
# (<root>/private/src-repos, matched by remote URL; the upstream stays in UPSTREAM_URL), so no task needs the network.
# Writes tasks-abap/<id>/{task.env,brief.md} (git-ignored)
# for the chosen tier (tasks-abap/tiers/<tier>.txt) or ids, then:
#   BENCH_TASKS_DIR=tasks-abap bin/setup.sh --check smoke-ids...      (or no ids: every materialized task)
#   BENCH_TASKS_DIR=tasks-abap bin/bench.sh <executor> [ids...]
# Each task.env is the package's descriptor plus the fields bench-bench needs (REPO, LEVEL, STRIP, P2P) and the
# absolute package paths: AGENT_TOOL (bound read-only at /agent-tool in the agent sandbox, `osg-abap` on PATH),
# GRADER_TOOL (read-only at /tool in grading only), HIDDEN_ROOT (HIDDEN_DIR is copied from there into the grade
# tree only), ABAP_TASKS (for PREPARE when the template is built). GOLD_PREPARE builds the reference solution for
# bin/goldcheck.sh. The private package is never mounted in the agent's sandbox.
set -euo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$BENCH/tasks-abap"
: "${ABAP_TASKS:?set ABAP_TASKS to the package root}" "${ABAP_TASKS_REPO:?set ABAP_TASKS_REPO to the descriptor checkout}"
ROOT="$(realpath "$ABAP_TASKS")"; REPO_DIR="$(realpath "$ABAP_TASKS_REPO")"
PIN="$(sed -n 's/^sha=//p' "$OUT/PACKAGE")"
git -C "$REPO_DIR" cat-file -e "$PIN^{commit}" 2>/dev/null || { echo "descriptor repository has no commit $PIN (tasks-abap/PACKAGE)" >&2; exit 2; }
[ "$(basename "$ROOT")" = "$PIN" ] || { echo "package root $ROOT is not named after the pinned commit $PIN" >&2; exit 2; }
for d in agent grader-tool private/abap-tasks private/hidden; do [ -d "$ROOT/$d" ] || { echo "missing $ROOT/$d" >&2; exit 2; }; done

# the package's own manifest, when it ships one: every file under agent/, grader-tool/ and private/ as listed
if [ -f "$ROOT/MANIFEST.sha256" ]; then
  (cd "$ROOT" && sha256sum --quiet -c MANIFEST.sha256) || { echo "package files differ from $ROOT/MANIFEST.sha256" >&2; exit 2; }
else
  echo "[abap-tasks] warning: $ROOT has no MANIFEST.sha256; package files are not verified" >&2
fi

IDS=(); TIER=()
for a in "${@:-smoke}"; do
  case "$a" in
    smoke|medium|full) n0=${#IDS[@]}; mapfile -t -O "$n0" IDS < <(grep -v '^\s*$' "$OUT/tiers/$a.txt")
                       for ((k = n0; k < ${#IDS[@]}; k++)); do TIER[$k]=$a; done ;;
    *) IDS+=("$a"); TIER[$((${#IDS[@]} - 1))]=ids ;;
  esac
done

# the package's own clones of the source repositories (private/src-repos/*), keyed by their upstream URL: tasks then
# need no network at all (setup.sh clones from the local path)
declare -A LOCAL_SRC=()
for d in "$ROOT"/private/src-repos/*/; do
  u="$(git -C "$d" config --get remote.origin.url 2>/dev/null)" || continue
  u="${u%.git}"; LOCAL_SRC["${u,,}"]="${d%/}"
done

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
n=0
for k in "${!IDS[@]}"; do
  id="${IDS[$k]}"
  [[ "$id" =~ ^[a-z0-9-]+$ ]] || { echo "bad task id: $id" >&2; exit 2; }
  # descriptors are read from the pinned commit's objects, never from the working tree (which may differ)
  src="$STAGE/$id"; mkdir -p "$src"
  for f in task.env brief.md; do
    git -C "$REPO_DIR" show "$PIN:bench-bench/$id/$f" > "$src/$f" 2>/dev/null || { echo "no descriptor for $id at $PIN" >&2; exit 2; }
  done
  url="$(bash -c 'set -u; source "$1"; printf %s "$URL"' _ "$src/task.env")"
  prep="$(bash -c 'set -u; source "$1"; printf %s "$PREPARE"' _ "$src/task.env")"
  gold="${prep/\"\$task_stage\/w\";/\"\$task_stage\/w\" --solution;}"
  [ "$gold" != "$prep" ] || { echo "$id: cannot derive GOLD_PREPARE from PREPARE" >&2; exit 2; }
  case "$id" in *-l0-*) level=ABAP-L0 ;; *-l1-*) level=ABAP-L1 ;; *) level=ABAP ;; esac
  key="${url%.git}"; local_src="${LOCAL_SRC[${key,,}]:-}"
  name="$(basename "$url" .git)"
  rm -rf "$OUT/$id"; mkdir -p "$OUT/$id"
  cp "$src/brief.md" "$OUT/$id/brief.md"
  { cat "$src/task.env"; echo
    echo "# added by bin/abap-tasks.sh (package $PIN)"
    printf "REPO=%q\nLEVEL=%q\nSTRIP=''\nP2P=''\n" "abap-$name" "$level"
    if [ -n "$local_src" ]; then printf "UPSTREAM_URL=%q\nURL=%q\n" "$url" "$local_src"; fi
    printf "ABAP_TASKS=%q\nAGENT_TOOL=%q\nGRADER_TOOL=%q\nHIDDEN_ROOT=%q\n" "$ROOT" "$ROOT/agent" "$ROOT/grader-tool" "$ROOT/private"
    printf "GOLD_PREPARE=%q\n" "$gold"
    printf "ABAP_PACKAGE=%q\nABAP_TIER=%q\n" "$PIN" "${TIER[$k]}"
  } > "$OUT/$id/task.env"
  n=$((n + 1))
done
echo "[abap-tasks] $n tasks in $OUT (package $PIN; sources from the package: $( (set +u; echo ${#LOCAL_SRC[@]}) ) repositories)"
