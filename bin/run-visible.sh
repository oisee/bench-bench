#!/usr/bin/env bash
# Round-3 harness experiments with executable acceptance examples (tasks/<id>/visible/:
# the odd-numbered cases of the held-out tests, renamed Test Visible*).
#   bin/run-visible.sh visible tasks/<id> <executor>   examples are in the tree from the start
#   bin/run-visible.sh gate    tasks/<id> <executor>   one normal session; then the examples run
#                                                      once, and on failure one repair session
# Grading is unchanged: the full held-out tests from MERGE.
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
MODE="$1"; T="$(realpath "$2")"; TID="$(basename "$T")"; EX="$3"
source "$T/task.env"
VIS="$T/visible"; [ -d "$VIS" ] || { echo "$VIS missing" >&2; exit 2; }
REPAIR_MIN="${REPAIR_MIN:-20}"
export BENCH_ROUND="${BENCH_ROUND:-3}"
read -r PROVIDER MODEL HARNESS EFFORT < <(python3 -I - "$BENCH/matrix.yaml" "$EX" <<'PY'
import re,sys
for line in open(sys.argv[1]):
    m=re.search(r'\{id:\s*([^,]+),\s*provider:\s*([^,]+),\s*model:\s*("?[^,"]+"?),\s*harness:\s*([^,]+),\s*effort:\s*([^}]+)\}',line)
    if m and not line.lstrip().startswith('#') and m.group(1).strip()==sys.argv[2]:
        print(m.group(2).strip(),m.group(3).strip().strip('"'),m.group(4).strip(),m.group(5).strip());break
else: sys.exit(1)
PY
) || { echo "unknown executor $EX" >&2; exit 2; }
[ "$HARNESS" = codex ] || { echo "run-visible.sh supports codex only" >&2; exit 2; }

RUN="$BENCH/runs/$TID/$EX-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
cp -a --reflink=auto "$BENCH/.cache/templates/$TID" "$RUN/work"
VFILES="$(cd "$VIS" && find . -type f | sed 's|^\./||' | sort)"
RUNCMD="$(for f in $VFILES; do echo "go test -count=1 ./$(dirname "$f") -run Visible"; done | sort -u | paste -sd';')"
BRIEF="$(cat "$T/brief.md")"
if [ "$MODE" = visible ]; then
  cp -r "$VIS/." "$RUN/work/"
  # part of the baseline, so the examples never count as the agent's patch
  git -C "$RUN/work" add -A && git -C "$RUN/work" -c user.name=bench -c user.email=bench@localhost commit -qm "visible acceptance examples" --no-gpg-sign && git -C "$RUN/work" tag -f baseline >/dev/null
  BRIEF="$BRIEF

## Executable acceptance examples

The repository contains executable acceptance examples: $(echo $VFILES).
Run them with: $RUNCMD
They cover part of the acceptance criteria; the final acceptance check is broader
and includes cases you cannot see. Do not modify or delete these files."
fi
printf '%s\n' "$BRIEF" > "$RUN/brief.md"
echo "[visible] $MODE · $TID / $EX ($MODEL · $EFFORT) -> $RUN"

session() { # minutes prompt outfile
  timeout --kill-after=30 "$(($1*60))" "$BENCH/bin/sandbox.sh" --run "$RUN" --harness codex \
    --provider "$PROVIDER" --model "$MODEL" --effort "$EFFORT" -- \
    exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json "$2" < /dev/null >> "$RUN/agent.out" 2>> "$RUN/agent.err"
}
T0=$(date +%s)
session "$TIME_MIN" "$BRIEF"; RC=$?
GATE=none
if [ "$MODE" = gate ]; then
  G="$RUN/gate-tree"; rm -rf "$G"; cp -a --reflink=auto "$RUN/work" "$G"; cp -r "$VIS/." "$G/"
  NODE_BIN="$(dirname "$(readlink -f "$(command -v node)")")"
  timeout 900 bwrap --die-with-parent --unshare-pid --unshare-ipc --unshare-net \
    --ro-bind /usr /usr --ro-bind /etc /etc --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /mnt --tmpfs "$HOME" \
    --ro-bind "$HOME/go/pkg/mod" "$HOME/go/pkg/mod" --bind "$HOME/.cache/go-build" "$HOME/.cache/go-build" \
    --bind "$G" /work --chdir /work --clearenv --setenv HOME "$HOME" --setenv PATH "$NODE_BIN:/usr/lib/go/bin:/usr/local/bin:/usr/bin:/bin" \
    --setenv LANG C.UTF-8 --setenv GOFLAGS -mod=mod --setenv GOPROXY off --setenv GOTOOLCHAIN local \
    bash -c "$RUNCMD" > "$RUN/gate.log" 2>&1
  GRC=$?; rm -rf "$G"
  if [ "$GRC" = 0 ]; then GATE=pass; else
    GATE=fail
    TESTSRC="$(for f in $VFILES; do echo "--- $f"; cat "$VIS/$f"; done)"
    session "$REPAIR_MIN" "$BRIEF

## Final acceptance gate

Your change was checked once by an independent acceptance gate. It failed. These
examples cover part of the acceptance criteria; the final check is broader. Fix
the implementation (do not just special-case these inputs) and stop when done.

Gate command: $RUNCMD
Gate output (truncated):
$(tail -c 6000 "$RUN/gate.log")

Gate test source:
$TESTSRC"; RC=$?
  fi
fi
DUR=$(( $(date +%s) - T0 ))
python3 -I - "$RUN/meta.json" "$MODE" "$GATE" <<'PY'
import json,sys
p,mode,gate=sys.argv[1:]; m=json.load(open(p))
m.update(protocol={"visible":"visible-checks-v1","gate":"final-gate-v1"}[mode], gate=gate)
json.dump(m,open(p,"w"),indent=2)
PY
echo "[visible] agent exit=$RC gate=$GATE after ${DUR}s"
"$BENCH/bin/finalize.sh" "$RUN" "$T" "$EX" "$RC" "$DUR"
