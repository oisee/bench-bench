#!/usr/bin/env bash
# Grade a finished run: overlay hidden tests from MERGE onto a copy of the agent's work
# and run FAIL_TO_PASS + PASS_TO_PASS commands in an offline sandbox.
#   bin/grade.sh RUN_DIR TASK_DIR
# Writes RUN_DIR/patch.diff, RUN_DIR/grade/*.log, RUN_DIR/result.json
# GRADE_WORK=DIR grades DIR instead of RUN_DIR/work and GRADE_OUT=DIR writes the three outputs there
# (bin/first-green.py uses both for snapshots).
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
. "$BENCH/bin/lib.sh"
RUN="$(realpath "$1")"; T="$(realpath "$2")"
source "$T/task.env"
SRC="$BENCH/.cache/repos/$REPO.git"
WORK="$(realpath "${GRADE_WORK:-$RUN/work}")"; OUT="${GRADE_OUT:-$RUN}"; mkdir -p "$OUT"; OUT="$(realpath "$OUT")"

# agent's patch relative to the baseline commit (untracked files included). Never run git inside the agent's repo:
# its .git/config could name an fsmonitor, filter or diff driver that would run outside the sandbox.
# If the diff fails, patch.diff is left empty and result.json says why (patch_error): the verdict still comes from
# the tree, but an empty patch must never pass for "no change".
PATCH_ERR=""
python3 -I "$BENCH/bin/snapshot.py" diff "$WORK" > "$OUT/patch.diff" 2> "$OUT/patch.err" || {
  PATCH_ERR="$(tail -1 "$OUT/patch.err" | cut -c1-300)"; [ -n "$PATCH_ERR" ] || PATCH_ERR="diff failed"
  echo "[grade] patch.diff unavailable: $PATCH_ERR" >&2; : > "$OUT/patch.diff"; }
rm -f "$OUT/patch.err"

G="$OUT/grade-tree"; LOGS="$OUT/grade"
rm -rf "$G" "$LOGS"; mkdir -p "$LOGS"
# a failed copy (e.g. disk full) is an infrastructure problem, not a failed patch
infra_exit() { echo "[grade] INCONCLUSIVE: $1" >&2; rm -rf "$G"; exit 2; }
cp -a --reflink=auto "$WORK" "$G" || infra_exit "cannot copy the work tree (disk full?)"
for p in $HIDDEN; do mkdir -p "$G/$(dirname "$p")" && git -C "$SRC" show "$MERGE:$p" > "$G/$p" || infra_exit "cannot write hidden test $p"; done

# L2a's upstream TestParseI8 name is a natural choice for an agent-written
# unit test. Give the held-out function a unique name so a valid patch cannot
# fail to compile merely because it added its own TestParseI8. The F2P
# -run TestParseI8 selector still matches this name; assertions are unchanged.
GRADE_FIX=""
if [[ "$(basename "$T")" == L2a-osg693 ]]; then
  python3 -I - "$G/tools/gogen/go/abap/parsei8_test.go" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = "func TestParseI8(t *testing.T) {"
assert s.count(old) == 1, "unexpected held-out TestParseI8 declaration"
p.write_text(s.replace(old, "func TestParseI8_BenchHeldOut693(t *testing.T) {"))
PY
  GRADE_FIX="l2a-hidden-test-name-v1"
fi
# Same collision on L3b: an agent may add its own TestAnonymousObjectLiterals in
# package tsfront. Rename the held-out function; F2P's -run selector still matches
# it, and P2P's -skip is widened so the held-out test stays out of the regression set.
if [[ "$(basename "$T")" == L3b-abapiti95 ]]; then
  python3 -I - "$G/tsfront/object_literal_test.go" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = "func TestAnonymousObjectLiterals(t *testing.T) {"
assert s.count(old) == 1, "unexpected held-out TestAnonymousObjectLiterals declaration"
p.write_text(s.replace(old, "func TestAnonymousObjectLiterals_BenchHeldOut95(t *testing.T) {"))
PY
  P2P="${P2P//'^TestAnonymousObjectLiterals$'/'^TestAnonymousObjectLiterals(_BenchHeldOut95)?$'}"
  GRADE_FIX="l3b-hidden-test-name-v1"
fi

toolchain_setup "$HOME" || exit 2
GO_MOUNTS=()  # module cache read-only (GOPROXY=off), build cache writable
[ -n "$GOMODCACHE_DIR" ] && GO_MOUNTS=(--ro-bind "$GOMODCACHE_DIR" "$GOMODCACHE_DIR" --bind "$GOCACHE_DIR" "$GOCACHE_DIR")
run_check() { # kind idx cmd
  local log="$LOGS/$1-$2.log" rc
  timeout 900 bwrap --die-with-parent --unshare-pid --unshare-ipc --unshare-net \
    --ro-bind /usr /usr --ro-bind /etc /etc \
    --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /mnt --tmpfs "$HOME" \
    "${TOOL_BINDS[@]+"${TOOL_BINDS[@]}"}" "${GO_MOUNTS[@]+"${GO_MOUNTS[@]}"}" \
    --bind "$G" /work --chdir /work \
    --clearenv --setenv HOME "$HOME" --setenv PATH "$TOOL_PATH:/usr/lib/go/bin:/usr/local/bin:/usr/bin:/bin" \
    "${TOOL_ENV[@]+"${TOOL_ENV[@]}"}" --setenv LANG C.UTF-8 --setenv GOFLAGS -mod=mod --setenv GOPROXY off --setenv GOTOOLCHAIN local \
    bash -c "$3" > "$log" 2>&1
  rc=$?
  echo "$rc"
}

json_items() { python3 -I -c 'import json,sys;print(json.dumps([{"cmd":c,"rc":int(r)} for c,r in zip(sys.argv[1::2],sys.argv[2::2])]))' "$@"; }
F=(); P=(); ok=1; infra=0; i=0
while IFS= read -r c; do [ -z "$c" ] && continue; i=$((i+1)); rc=$(run_check f2p $i "$c"); F+=("$c" "$rc"); [ "$rc" = 0 ] || ok=0; echo "[grade] f2p-$i rc=$rc  $c"; done <<< "$F2P"
i=0
while IFS= read -r c; do [ -z "$c" ] && continue; i=$((i+1)); rc=$(run_check p2p $i "$c"); P+=("$c" "$rc"); [ "$rc" = 0 ] || ok=0; echo "[grade] p2p-$i rc=$rc  $c"; done <<< "$P2P"

# A broken sandbox, test timeout, or OOM is not evidence that the patch failed.
for ((i=1; i<${#F[@]}; i+=2)); do
  case "${F[i]}" in 124|125|126|127|137) infra=1 ;; esac
done
for ((i=1; i<${#P[@]}; i+=2)); do
  case "${P[i]}" in 124|125|126|127|137) infra=1 ;; esac
done
if grep -rqiE '(^bwrap:|signal: killed|out of memory|cannot allocate memory)' "$LOGS"; then infra=1; fi
if ((infra)); then
  echo '[grade] INCONCLUSIVE: sandbox, resource limit, or test timeout; see grade/*.log' >&2
  rm -rf "$G"
  exit 2
fi

status=$([ $ok = 1 ] && echo RESOLVED || echo FAILED)
python3 -I - "$OUT/result.json" "$status" "$(json_items "${F[@]}")" "$(json_items "${P[@]+"${P[@]}"}")" \
  "$(wc -l < "$OUT/patch.diff")" "$GRADE_FIX" "$PATCH_ERR" <<'EOF'
import json,sys
p,status,f,pp,lines,grade_fix,patch_err=sys.argv[1:]
r={"status":status,"f2p":json.loads(f),"p2p":json.loads(pp) if pp else [],"patch_lines":int(lines)}
if grade_fix: r["grade_fix"]=grade_fix
if patch_err: r["patch_error"]=patch_err
json.dump(r,open(p,"w"),indent=2)
EOF
rm -rf "$G"
echo "[grade] $status  ($RUN)"
