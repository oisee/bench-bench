#!/usr/bin/env bash
# Native ABAP tasks (an external, privately distributed package) as bench-bench tasks.
#   ABAP_TASKS=<package root> ABAP_TASKS_REPO=<descriptor checkout> [ABAP_ENV=<n>] bin/abap-tasks.sh [<tier>|ID|ID@n ...]
# The package root is <root>/{agent,grader-tool,private/{abap-tasks,src-repos,hidden}}, named after the descriptor
# repository's commit; tasks-abap/PACKAGE pins that commit. Descriptors are read from that commit's git objects, package
# files are checked against <root>/MANIFEST.sha256 when present, and sources come from the package's own clones
# (<root>/private/src-repos, matched by remote URL; the upstream stays in UPSTREAM_URL), so no task needs the network.
# Writes tasks-abap/<id>/{task.env,brief.md} (git-ignored)
# for the chosen tier (tiers/<tier>.txt at the pinned commit, else tasks-abap/tiers/<tier>.txt) or ids, then:
# ABAP_SET (corpus | agentic | synthetic, from the id) keeps the sets in separate result tables.
#   BENCH_TASKS_DIR=tasks-abap bin/setup.sh --check smoke-ids...      (or no ids: every materialized task)
#   BENCH_TASKS_DIR=tasks-abap bin/bench.sh <executor> [ids...]
# Each task.env is the package's descriptor plus the fields bench-bench needs (REPO, LEVEL, STRIP, P2P) and the
# absolute package paths: AGENT_TOOL (bound read-only at /agent-tool in the agent sandbox, `osg-abap` on PATH),
# GRADER_TOOL (read-only at /tool in grading only), HIDDEN_ROOT (HIDDEN_DIR is copied from there into the grade
# tree only), ABAP_TASKS (for PREPARE when the template is built). GOLD_PREPARE builds the reference solution for
# bin/goldcheck.sh. The private package is never mounted in the agent's sandbox.
set -euo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${BENCH_TASKS_DIR:-$BENCH/tasks-abap}"
mkdir -p "$OUT"
: "${ABAP_TASKS:?set ABAP_TASKS to the package root}" "${ABAP_TASKS_REPO:?set ABAP_TASKS_REPO to the descriptor checkout}"
ROOT="$(realpath "$ABAP_TASKS")"; REPO_DIR="$(realpath "$ABAP_TASKS_REPO")"
PIN="${ABAP_PACKAGE_SHA:-$(sed -n 's/^sha=//p' "$OUT/PACKAGE")}"
git -C "$REPO_DIR" cat-file -e "$PIN^{commit}" 2>/dev/null || { echo "descriptor repository has no commit $PIN (tasks-abap/PACKAGE)" >&2; exit 2; }
[ "$(basename "$ROOT")" = "$PIN" ] || [ "${ABAP_PACKAGE_SHA:-}" = "$PIN" ] || { echo "package root $ROOT is not named after the pinned commit $PIN" >&2; exit 2; }
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
    *)
      # a tier is any list at the pinned commit (tiers/<name>.txt) or in tasks-abap/tiers; anything else is a task id
      n0=${#IDS[@]}
      if git -C "$REPO_DIR" cat-file -e "$PIN:tiers/$a.txt" 2>/dev/null; then
        mapfile -t -O "$n0" IDS < <(git -C "$REPO_DIR" show "$PIN:tiers/$a.txt" | grep -v '^\s*$')
      elif [ -f "$BENCH/tasks-abap/tiers/$a.txt" ]; then mapfile -t -O "$n0" IDS < <(grep -v '^\s*$' "$BENCH/tasks-abap/tiers/$a.txt")
      else IDS+=("$a"); TIER[$n0]=ids; continue; fi
      for ((k = n0; k < ${#IDS[@]}; k++)); do TIER[$k]=$a; done ;;
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
  [[ "$id" =~ ^[a-z0-9-]+(@[0-9]+)?$ ]] || { echo "bad task id: $id" >&2; exit 2; }
  # a versioned uid (id@n, v2.5 packages) uses the descriptor for that uid under ABAP_ENV (bench-bench/<uid>.env<E>);
  # its local task dir is <id>-u<n>-e<E>, so it never collides with the latest descriptor of the same id
  ddir="$id"; tdir="$id"
  if [[ "$id" == *@* ]]; then
    : "${ABAP_ENV:?a versioned task id ($id) needs ABAP_ENV (the environment version, e.g. 3)}"
    ddir="$id.env$ABAP_ENV"; tdir="${id%@*}-u${id#*@}-e$ABAP_ENV"
  fi
  # descriptors are read from the pinned commit's objects, never from the working tree (which may differ)
  src="$STAGE/$tdir"; mkdir -p "$src"
  for f in task.env brief.md; do
    git -C "$REPO_DIR" show "$PIN:bench-bench/$ddir/$f" > "$src/$f" 2>/dev/null || { echo "no descriptor for $ddir at $PIN" >&2; exit 2; }
  done
  url="$(bash -c 'set -u; source "$1"; printf %s "$URL"' _ "$src/task.env")"
  prep="$(bash -c 'set -u; source "$1"; printf %s "$PREPARE"' _ "$src/task.env")"
  gold="${prep/\"\$task_stage\/w\";/\"\$task_stage\/w\" --solution;}"
  [ "$gold" != "$prep" ] || { echo "$id: cannot derive GOLD_PREPARE from PREPARE" >&2; exit 2; }
  case "$id" in *-l0-*) level=ABAP-L0 ;; *-l1-*) level=ABAP-L1 ;; *-l2-*) level=ABAP-L2 ;; *-l3-*) level=ABAP-L3 ;; *) level=ABAP ;; esac
  case "$id" in *-agentic-*) set_=agentic ;; *-synthetic-*) set_=synthetic ;; *) set_=corpus ;; esac   # separate result tables
  key="${url%.git}"; local_src="${LOCAL_SRC[${key,,}]:-}"
  name="$(basename "$url" .git)"
  rm -rf "$OUT/$tdir"; mkdir -p "$OUT/$tdir"
  cp "$src/brief.md" "$OUT/$tdir/brief.md"
  { cat "$src/task.env"; echo
    echo "# added by bin/abap-tasks.sh (package $PIN)"
    printf "REPO=%q\nLEVEL=%q\nSTRIP=''\nP2P=''\n" "abap-$name" "$level"
    if [ -n "$local_src" ]; then printf "UPSTREAM_URL=%q\nURL=%q\n" "$url" "$local_src"; fi
    printf "ABAP_TASKS=%q\nAGENT_TOOL=%q\nGRADER_TOOL=%q\nHIDDEN_ROOT=%q\n" "$ROOT" "$ROOT/agent" "$ROOT/grader-tool" "$ROOT/private"
    printf "GOLD_PREPARE=%q\n" "$gold"
    printf "ABAP_PACKAGE=%q\nABAP_TIER=%q\nABAP_SET=%q\n" "$PIN" "${TIER[$k]}" "$set_"
  } > "$OUT/$tdir/task.env"
  n=$((n + 1))
done
echo "[abap-tasks] $n tasks in $OUT (package $PIN; sources from the package: $( (set +u; echo ${#LOCAL_SRC[@]}) ) repositories)"
