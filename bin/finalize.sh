#!/usr/bin/env bash
# Grade + leak-audit + result.json for a run whose agent session has ended.
#   bin/finalize.sh RUN_DIR TASK_DIR EXECUTOR_ID AGENT_EXIT DURATION_S
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$(realpath "$1")"; T="$(realpath "$2")"; TID="$(basename "$T")"; EX="$3"; RC="$4"; DUR="$5"
source "$T/task.env"
read -r PROVIDER MODEL HARNESS EFFORT < <(python3 -I -c 'import json,sys;m=json.load(open(sys.argv[1]));print(m["provider"],m["model"],m["harness"],m["effort"])' "$RUN/meta.json")
STOP=$({ [ "$RC" = 124 ] || [ "$RC" = 137 ]; } && echo TIMEOUT || echo DONE)
"$BENCH/bin/grade.sh" "$RUN" "$T" | tail -1
HITS=$(grep -rhoE "github\.com/oisee/$REPO/(pull|pulls|commit|commits|compare|tree|blob|issues|branches)[^\"' )]*|api\.github\.com/repos/oisee/$REPO[^\"' )]*" \
  "$RUN/agent.out" "$RUN/agent.err" "$RUN/home" 2>/dev/null | \
  grep -vF "github.com/oisee/$REPO/blob/main-archive/docs/" | sort | uniq -c | sort -rn | head -20)
rm -f "$RUN/leak-audit.txt"; [ -n "$HITS" ] && printf '%s\n' "$HITS" > "$RUN/leak-audit.txt"
python3 -I - "$RUN" "$TID" "$LEVEL" "$EX" "$HARNESS" "$PROVIDER" "$MODEL" "$EFFORT" "$RC" "$DUR" "$STOP" "${HITS:+yes}" <<'PY'
import json,sys,os
run,tid,lvl,ex,h,p,m,e,rc,dur,stop,leak=sys.argv[1:]
r=json.load(open(os.path.join(run,"result.json")))
meta=json.load(open(os.path.join(run,"meta.json")))
r.update(task=tid,level=lvl,executor=ex,harness=h,provider=p,model=m,effort=e,agent_exit=int(rc),
         duration_s=int(dur),stop=stop,leak_suspect=bool(leak),harness_version=meta.get("harness_version"))
if meta.get("protocol"):
    r.update(protocol=meta["protocol"],internal_review_rounds=meta.get("internal_review_rounds",0))
json.dump(r,open(os.path.join(run,"result.json"),"w"),indent=2)
print(f"[run] {tid} {ex}: {r['status']} stop={stop} {dur}s patch={r['patch_lines']} lines leak={'YES' if leak else 'no'}")
PY
