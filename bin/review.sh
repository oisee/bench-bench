#!/usr/bin/env bash
# Blind review of one finished run by one critic, inside the sandbox.
#   bin/review.sh RUN_DIR CRITIC_ID
# Output: runs/_review/<anon-id>/<critic>/review.json (+ transcript in home/)
# REVIEW_BLIND=1 hides the hidden-test results from the critic and writes to
# runs/_screen instead, so critic screening never mixes with the jury data.
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
SRC_RUN="$(realpath "$1")"; CR="$2"

read -r PROVIDER MODEL HARNESS EFFORT SELF < <(python3 -I - "$BENCH/matrix.yaml" "$CR" <<'EOF'
import re,sys
for line in open(sys.argv[1]):
    m=re.search(r'\{id:\s*([^,]+),\s*provider:\s*([^,]+),\s*model:\s*([^,]+),\s*harness:\s*([^,]+),\s*effort:\s*([^,}]+)(?:,\s*self_family:\s*\[([^\]]*)\])?',line)
    if m and m.group(1).strip()==sys.argv[2]:
        print(*(g.strip().strip('"') for g in m.groups()[1:5]), (m.group(6) or "-").replace(" ","")); break
else: sys.exit(1)
EOF
) || { echo "unknown critic $CR" >&2; exit 2; }

EXEC_ID="$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["executor"])' "$SRC_RUN/result.json")"
ANON="$(printf '%s' "$SRC_RUN" | sha256sum | cut -c1-10)"
SELFREV=no
for f in ${SELF//,/ }; do [ "$f" != "-" ] && [[ "$EXEC_ID" == $f* ]] && SELFREV=yes; done

ROOT="$BENCH/runs/$([ "${REVIEW_BLIND:-0}" = 1 ] && echo _screen || echo _review)"
R="$ROOT/$ANON/$CR"
rm -rf "$R"; mkdir -p "$R"
flock "$ROOT/.map.lock" python3 -I - "$ROOT/map.json" "$ANON" "$SRC_RUN" <<'EOF'
import json,sys,os
p,a,run=sys.argv[1:]; d=json.load(open(p)) if os.path.exists(p) else {}
d[a]=os.path.relpath(run,os.path.dirname(os.path.dirname(p))); json.dump(d,open(p,"w"),indent=2,sort_keys=True)
EOF

# critic workspace: the agent's final tree + review materials; agent transcripts are NOT included
cp -a --reflink=auto "$SRC_RUN/work" "$R/work"
M="$R/work/.bench-review"; mkdir -p "$M"
cp "$SRC_RUN/brief.md" "$M/task.md"
cp "$SRC_RUN/patch.diff" "$M/patch.diff"
[ "${REVIEW_BLIND:-0}" = 1 ] || python3 -I - "$SRC_RUN/result.json" > "$M/tests.md" <<'EOF'
import json,sys
r=json.load(open(sys.argv[1]))
print("# Automated test results (hidden acceptance tests + regression tests)\n")
print(f"Overall: {r['status']}\n")
for kind in ("f2p","p2p"):
    for c in r.get(kind,[]):
        print(f"- [{'PASS' if c['rc']==0 else 'FAIL'}] {'acceptance' if kind=='f2p' else 'regression'}: `{c['cmd'].splitlines()[0][:160]}`")
EOF

PROMPT='You are a senior code reviewer. In this repository, .bench-review/task.md is the task a developer was given,
.bench-review/patch.diff is their change (also applied in the working tree; `git diff baseline` shows it),
.bench-review/tests.md lists automated test results. You do not know who wrote the change.'
[ "${REVIEW_BLIND:-0}" = 1 ] && PROMPT='You are a senior code reviewer. In this repository, .bench-review/task.md is the task a developer was given,
and .bench-review/patch.diff is their change (also applied in the working tree; `git diff baseline` shows it).
No acceptance-test results are provided: decide yourself whether the change fully solves the task.
You do not know who wrote the change.'
PROMPT+='

Review the change for correctness against the task, edge cases, regressions, robustness and maintainability.
You may read code and run commands/tests. Do not modify tracked files; scratch work goes in /tmp.
Only report defects you can justify concretely; prefer a reproducing command or input for each.

Write your result as JSON to .bench-review/review.json with exactly this shape:
{"verdict": "correct" | "incorrect" | "uncertain",
 "quality": 1-5,
 "summary": "<2-3 sentences>",
 "findings": [{"severity": "P0|P1|P2|P3", "file": "...", "line": 0, "claim": "...", "repro": "<command or input showing it, or empty>"}]}
P0 = wrong result on the task itself, P1 = regression or serious edge-case bug, P2 = minor bug / risky code, P3 = style/maintainability.
When the file is written, reply with just: done.'

case "$HARNESS" in
  pi)     ARGS=(-p "$PROMPT") ;;
  claude) ARGS=(-p "$PROMPT" --dangerously-skip-permissions --output-format stream-json --verbose) ;;
  codex)  ARGS=(exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json "$PROMPT") ;;
esac
T0=$(date +%s)
timeout --kill-after=30 1800 "$BENCH/bin/sandbox.sh" --run "$R" --harness "$HARNESS" --provider "$PROVIDER" \
  --model "$MODEL" --effort "$EFFORT" -- "${ARGS[@]}" < /dev/null > "$R/critic.out" 2> "$R/critic.err"
RC=$?
CR_MODEL="$PROVIDER/$MODEL" python3 -I - "$R" "$M/review.json" "$CR" "$ANON" "$SELFREV" "$RC" "$(( $(date +%s) - T0 ))" <<'EOF'
import json,sys,os
R,src,cr,anon,selfrev,rc,dur=sys.argv[1:]
try: rv=json.load(open(src)); ok=True
except Exception as e: rv={"error":f"no valid review.json: {e}"}; ok=False
rv.update(critic=cr,critic_model=os.environ.get("CR_MODEL",""),anon=anon,self_review=selfrev=="yes",exit=int(rc),duration_s=int(dur))
json.dump(rv,open(os.path.join(R,"review.json"),"w"),indent=2,ensure_ascii=False)
print(f"[review] {anon} {cr}: {rv.get('verdict','ERROR')} q={rv.get('quality','-')} findings={len(rv.get('findings',[]))} self={selfrev} {dur}s")
EOF
rm -rf "$R/work"   # keep only review.json + transcripts
