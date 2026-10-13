#!/usr/bin/env bash
# One-time, sequential regrade after fixing the held-out TestAnonymousObjectLiterals name
# collision. Preserve prior results and write a compact before/after audit.
set -euo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
TASK="$BENCH/tasks/L3b-abapiti95"
AUDIT="$BENCH/data/l3b-hidden-name-fix-audit.json"
mapfile -t RESULTS < <(find "$BENCH/runs/L3b-abapiti95" -mindepth 2 -maxdepth 2 -name result.json -print | sort)
python3 -I - "$AUDIT" "${RESULTS[@]}" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
rows = []
for name in sys.argv[2:]:
    p = pathlib.Path(name)
    backup = p.with_name('result.before-l3b-name-fix.json')
    if not backup.exists():
        backup.write_text(p.read_text())
    old = json.loads(backup.read_text())
    rows.append(dict(run=str(p.parent.relative_to(p.parents[2])),
                     before=dict(status=old['status'], f2p=old['f2p'], p2p=old['p2p']),
                     after=None))
out.write_text(json.dumps(dict(fix='l3b-hidden-test-name-v1', cases=rows), indent=2) + '\n')
print(f"[regrade] archived {len(rows)} prior results")
PY
for result in "${RESULTS[@]}"; do
  run="$(dirname "$result")"
  read -r ex rc dur < <(python3 -I - "$run/result.before-l3b-name-fix.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
print(r['executor'],r['agent_exit'],r['duration_s'])
PY
)
  echo "[regrade] $(basename "$run")"
  "$BENCH/bin/finalize.sh" "$run" "$TASK" "$ex" "$rc" "$dur"
  python3 -I - "$run/result.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert r.get('grade_fix')=='l3b-hidden-test-name-v1', 'corrected grade was not recorded'
PY
done
python3 -I - "$AUDIT" "$BENCH" <<'PY'
import json, pathlib, sys
p, bench = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
audit = json.loads(p.read_text())
for row in audit['cases']:
    r = json.loads((bench / 'runs' / row['run'] / 'result.json').read_text())
    row['after'] = dict(status=r['status'], f2p=r['f2p'], p2p=r['p2p'])
    row['changed'] = row['before'] != row['after']
p.write_text(json.dumps(audit, indent=2) + '\n')
print('[regrade] changed:', ', '.join(row['run'] for row in audit['cases'] if row['changed']) or 'none')
PY
