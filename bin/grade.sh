#!/usr/bin/env bash
# Grade a finished run: overlay hidden tests from MERGE onto a copy of the agent's work
# and run FAIL_TO_PASS + PASS_TO_PASS commands in an offline sandbox.
#   bin/grade.sh RUN_DIR TASK_DIR
# Writes RUN_DIR/patch.diff, RUN_DIR/grade/*.log, RUN_DIR/result.json
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$(realpath "$1")"; T="$(realpath "$2")"
source "$T/task.env"
SRC="$BENCH/.cache/repos/$REPO.git"

# agent's patch relative to the baseline commit (untracked files included)
git -C "$RUN/work" add -A -N 2>/dev/null
git -C "$RUN/work" diff baseline > "$RUN/patch.diff"
git -C "$RUN/work" reset -q 2>/dev/null

G="$RUN/grade-tree"; LOGS="$RUN/grade"
rm -rf "$G" "$LOGS"; mkdir -p "$LOGS"
cp -a --reflink=auto "$RUN/work" "$G"
for p in $HIDDEN; do mkdir -p "$G/$(dirname "$p")"; git -C "$SRC" show "$MERGE:$p" > "$G/$p"; done

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

NODE_PATH="$(command -v node || true)"
if [ -z "$NODE_PATH" ]; then
  NODE_PATH="$(find "$HOME/.nvm/versions/node" -type f -path '*/bin/node' 2>/dev/null | sort -V | tail -1)"
fi
[ -n "$NODE_PATH" ] && [ -x "$NODE_PATH" ] || { echo 'node executable not found' >&2; exit 2; }
NODE_BIN="$(dirname "$(readlink -f "$NODE_PATH")")"
run_check() { # kind idx cmd
  local log="$LOGS/$1-$2.log" rc
  timeout 900 bwrap --die-with-parent --unshare-pid --unshare-ipc --unshare-net \
    --ro-bind /usr /usr --ro-bind /etc /etc \
    --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /mnt --tmpfs "$HOME" \
    --ro-bind "$HOME/.nvm" "$HOME/.nvm" --ro-bind "$HOME/go/pkg/mod" "$HOME/go/pkg/mod" \
    --bind "$HOME/.cache/go-build" "$HOME/.cache/go-build" \
    --bind "$G" /work --chdir /work \
    --clearenv --setenv HOME "$HOME" --setenv PATH "$NODE_BIN:/usr/lib/go/bin:/usr/local/bin:/usr/bin:/bin" \
    --setenv LANG C.UTF-8 --setenv GOFLAGS -mod=mod --setenv GOPROXY off --setenv GOTOOLCHAIN local \
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
if rg --no-ignore -qi '(^bwrap:|signal: killed|out of memory|cannot allocate memory)' "$LOGS"; then infra=1; fi
if ((infra)); then
  echo '[grade] INCONCLUSIVE: sandbox, resource limit, or test timeout; see grade/*.log' >&2
  rm -rf "$G"
  exit 2
fi

status=$([ $ok = 1 ] && echo RESOLVED || echo FAILED)
python3 -I - "$RUN/result.json" "$status" "$(json_items "${F[@]}")" "$(json_items "${P[@]+"${P[@]}"}")" \
  "$(wc -l < "$RUN/patch.diff")" "$GRADE_FIX" <<'EOF'
import json,sys
p,status,f,pp,lines,grade_fix=sys.argv[1:]
r={"status":status,"f2p":json.loads(f),"p2p":json.loads(pp) if pp else [],"patch_lines":int(lines)}
if grade_fix: r["grade_fix"]=grade_fix
json.dump(r,open(p,"w"),indent=2)
EOF
rm -rf "$G"
echo "[grade] $status  ($RUN)"
