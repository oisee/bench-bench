#!/usr/bin/env bash
# Grade + leak-audit + result.json for a run whose agent session has ended.
#   bin/finalize.sh RUN_DIR TASK_DIR EXECUTOR_ID AGENT_EXIT DURATION_S
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$(realpath "$1")"; T="$(realpath "$2")"; TID="$(basename "$T")"; EX="$3"; RC="$4"; DUR="$5"
source "$T/task.env"
# the endpoint key is for the secret scan only; nothing else in finalize (grading included) gets it
SCAN_SECRET="${BENCH_SECRET:-}"; unset BENCH_SECRET
read -r PROVIDER MODEL HARNESS EFFORT < <(python3 -I -c 'import json,sys;m=json.load(open(sys.argv[1]));print(m["provider"],m["model"],m["harness"],m["effort"])' "$RUN/meta.json")
STOP=$({ [ "$RC" = 124 ] || [ "$RC" = 137 ]; } && echo TIMEOUT || echo DONE)
"$BENCH/bin/grade.sh" "$RUN" "$T" | tail -1
HITS=$(grep -rhoE "github\.com/oisee/$REPO/(pull|pulls|commit|commits|compare|tree|blob|issues|branches)[^\"' )]*|api\.github\.com/repos/oisee/$REPO[^\"' )]*" \
  "$RUN/agent.out" "$RUN/agent.err" "$RUN/home" 2>/dev/null | \
  grep -vF "github.com/oisee/$REPO/blob/main-archive/docs/" | sort | uniq -c | sort -rn | head -20)
rm -f "$RUN/leak-audit.txt"; [ -n "$HITS" ] && printf '%s\n' "$HITS" > "$RUN/leak-audit.txt"
# native ABAP tasks: their answers are public code, so a run whose network access brought back the hidden body (or a
# provider-side search result) is `contaminated`: shown, counted as neither solved nor failed
CONTAM=""
if [ -n "${ABAP_PACKAGE:-}" ]; then
  python3 -I "$BENCH/bin/contamination.py" "$RUN" "$T" > "$RUN/contamination.json" 2> "$RUN/contamination.err" \
    && rm -f "$RUN/contamination.err" || { echo '[run] contamination check failed; see contamination.err' >&2; CONTAM=unknown; }
  [ -z "$CONTAM" ] && CONTAM="$(python3 -I -c 'import json,sys;print("yes" if json.load(open(sys.argv[1]))["contaminated"] else "no")' "$RUN/contamination.json")"
fi
# no secret may stay in the run's artifacts: the endpoint key (BENCH_SECRET, from bin/run.sh) and every provider key,
# token or OAuth login secret are masked in place (work/ and snap.git only reported); any hit keeps secret_leak set
SECRET_LEAK=""; rm -f "$RUN/secret-audit.txt"
BENCH_SECRET="$SCAN_SECRET" python3 -I "$BENCH/bin/secret-scan.py" "$RUN" --mask > "$RUN/secret-audit.txt"
case $? in
  0) rm -f "$RUN/secret-audit.txt" ;;
  1) SECRET_LEAK=yes; echo "[run] secrets found in the run directory and masked where possible, see $RUN/secret-audit.txt" >&2 ;;
  *) SECRET_LEAK=unknown; echo '[run] secret scan failed; treat the run directory as unsafe to publish' >&2 ;;
esac
python3 -I - "$RUN" "$TID" "$LEVEL" "$EX" "$HARNESS" "$PROVIDER" "$MODEL" "$EFFORT" "$RC" "$DUR" "$STOP" "${HITS:+yes}" "$SECRET_LEAK" "$CONTAM" <<'PY'
import json,sys,os
run,tid,lvl,ex,h,p,m,e,rc,dur,stop,leak,secret,contam=sys.argv[1:]
r=json.load(open(os.path.join(run,"result.json")))
meta=json.load(open(os.path.join(run,"meta.json")))
r.update(task=tid,level=lvl,executor=ex,harness=h,provider=p,model=m,effort=e,agent_exit=int(rc),
         duration_s=int(dur),stop=stop,leak_suspect=bool(leak),harness_version=meta.get("harness_version"))
if contam:
    r["contaminated"]=contam
if secret:
    r["secret_leak"]=secret
    audit=os.path.join(run,"secret-audit.txt")
    if os.path.exists(audit) and "masked\tpatch.diff" in open(audit).read().splitlines():
        r["patch_masked"]=True   # the published patch differs from the graded tree where a secret was masked
for k in ("net_policy", "abap_package", "abap_tier", "abap_set", "task_uid", "env_version", "content_digest"):
    if meta.get(k):
        r[k]=meta[k]
if meta.get("protocol"):
    r.update(protocol=meta["protocol"],internal_review_rounds=meta.get("internal_review_rounds",0))
json.dump(r,open(os.path.join(run,"result.json"),"w"),indent=2)
print(f"[run] {tid} {ex}: {r['status']} stop={stop} {dur}s patch={r['patch_lines']} lines leak={'YES' if leak else 'no'}")
PY
