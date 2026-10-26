#!/usr/bin/env python3
"""Small live benchmark dashboard. Uses only the Python standard library."""
import argparse
from datetime import datetime, timezone
import math
import re
import http.server
import json
from pathlib import Path
import urllib.parse


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def safe_file(root, path):
    """Never follow an agent-created link outside the output tree."""
    try:
        resolved = path.resolve()
        return resolved if resolved.is_relative_to(root.resolve()) else None
    except (OSError, ValueError, RuntimeError):
        return None


def tail(path, limit=65536):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            data = stream.read(limit)
        if size > limit:
            data = data.partition(b'\n')[2]
        return b'\n'.join(data.splitlines()[-200:]).decode('utf-8', 'replace')
    except OSError:
        return ''


def request_count(path):
    try:
        with path.open('rb') as stream:
            return sum(chunk.count(b'\n') for chunk in iter(lambda: stream.read(65536), b''))
    except OSError:
        return 0


def elapsed(meta, result, now):
    try:
        if 'duration_s' in result:
            value = float(result['duration_s'])
            return max(0, value) if math.isfinite(value) else None
        start = datetime.fromisoformat(meta['started_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(meta['finished_at'].replace('Z', '+00:00')) if meta.get('finished_at') else now
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, (end - start).total_seconds())
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


STATUSES = ('queued', 'running', 'RESOLVED', 'FAILED', 'TIMEOUT', 'INCONCLUSIVE', 'INVALID')


def rows(root, now=None):
    """Read only dashboard fields; never expose arbitrary metadata."""
    now = now or datetime.now(timezone.utc)
    data = []
    for directory in sorted(root.glob('*/*')):
        if not directory.is_dir() or safe_file(root, directory) is None:
            continue
        paths = {name: safe_file(root, directory / name) for name in
                 ('meta.json', 'result.json', 'traffic.jsonl', 'live.txt', 'patch.diff')}
        meta = read_json(paths['meta.json']) if paths['meta.json'] else {}
        result = read_json(paths['result.json']) if paths['result.json'] else {}
        info = dict(meta, **result)
        status = result.get('status') or ('running' if meta or any(
            paths[n] and paths[n].is_file() for n in ('live.txt', 'traffic.jsonl')) else 'queued')
        if status not in STATUSES:
            status = 'INCONCLUSIVE'
        executor = info.get('executor') or re.sub(r'-[0-9]{8}[-T].*$', '', directory.name)
        row = dict(task=directory.parent.name, run=directory.name, executor=str(executor),
                   status=status, duration=elapsed(meta, result, now),
                   requests=request_count(paths['traffic.jsonl']) if paths['traffic.jsonl'] else 0)
        for key in ('model', 'abap_package', 'abap_tier', 'env_version', 'net_policy', 'contaminated'):
            row[key] = info.get(key)
        patch = paths['patch.diff']
        row['patch_lines'] = result.get('patch_lines')
        if row['patch_lines'] is None and patch and patch.is_file():
            try:
                with patch.open(errors='replace') as stream:
                    row['patch_lines'] = sum(line.startswith(('+', '-')) and not line.startswith(
                        ('+++', '---')) for line in stream)
            except OSError:
                pass
        prefix = '/'.join(urllib.parse.quote(p, safe='') for p in (row['task'], row['run']))
        for key, name in (('live', 'live.txt'), ('patch', 'patch.diff'), ('result', 'result.json')):
            row[key] = '/live/' + prefix + '/' + name if paths[name] and paths[name].is_file() else None
        data.append(row)
    for queue in sorted(root.glob('queue-*.json')):
        path = safe_file(root, queue)
        plan = read_json(path) if path else {}
        executor, tasks = plan.get('executor'), plan.get('tasks', [])
        if not isinstance(executor, str) or not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, str) or not task or '/' in task or task in ('.', '..'):
                continue
            if not any(r['task'] == task and r['executor'] == executor for r in data):
                data.append(dict(task=task, run=executor, executor=executor, status='queued',
                                 duration=None, requests=0, patch_lines=None, live=None, patch=None, result=None))
    return sorted(data, key=lambda r: (r['task'], r['run']))


def totals(entries):
    counts = {status: sum(r['status'] == status for r in entries) for status in STATUSES}
    return dict(total=len(entries), solved=counts['RESOLVED'],
                failed=sum(counts[s] for s in ('FAILED', 'TIMEOUT', 'INCONCLUSIVE')),
                running=counts['running'], queued=counts['queued'], invalid=counts['INVALID'],
                completed=sum(counts[s] for s in STATUSES[2:]),
                agent_time=sum(r['duration'] or 0 for r in entries), statuses=counts)


def build_state(root, compare=(), now=None):
    local = rows(root, now)
    # The newest queue identifies the active invocation; saved output falls back to newest run.
    queues = []
    for q in root.glob('queue-*.json'):
        target = safe_file(root, q)
        if target:
            plan = read_json(target)
            if isinstance(plan.get('executor'), str):
                queues.append((q.stat().st_mtime_ns, q.name, plan['executor']))
    def run_order(row):
        stamp = re.search(r'([0-9]{8}-[0-9]{6})$', row['run'])
        return (stamp.group(1) if stamp else '', row['run'])

    primary = max(queues)[2] if queues else (max(local, key=run_order)['executor'] if local else None)
    latest = {}
    for row in local:
        if row['executor'] == primary:
            latest[row['task']] = row
    tasks = sorted(latest.values(), key=lambda r: r['task'])
    references = {}
    # INVALID attempts never hide an older valid reference verdict.
    for source in (root, *compare):
        for row in rows(Path(source), now):
            if row['executor'] == primary or row['status'] in ('INVALID', 'queued', 'running'):
                continue
            key = (row['executor'], row['task'])
            if key not in references or row['run'] > references[key]['run']:
                references[key] = row
    executors = sorted({ex for ex, task in references if task in latest})
    for task in tasks:
        task['comparisons'] = {ex: references[(ex, task['task'])]['status']
                               for ex in executors if (ex, task['task']) in references}
    summary = totals(tasks)
    summary['executor'] = primary
    for key in ('model', 'abap_package', 'abap_tier', 'env_version'):
        summary[key] = sorted({str(r[key]) for r in tasks if r.get(key) is not None})
    return dict(summary=summary, tasks=tasks, comparisons=[dict(executor=ex, **totals(
        [references[(ex, task)] for task in latest if (ex, task) in references])) for ex in executors])


PAGE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ABAP bench progress</title>
<style>
:root{color-scheme:light dark;--bg:#f5f7fb;--panel:#fff;--text:#182536;--line:#cbd5e1;--link:#1358a0}
@media(prefers-color-scheme:dark){:root{--bg:#111923;--panel:#1b2635;--text:#edf2fa;--line:#45556a;--link:#9bcaff}}
*{box-sizing:border-box}body{margin:0;padding:clamp(12px,3vw,32px);background:var(--bg);color:var(--text);font:16px system-ui}main{max-width:1250px;margin:auto}h1{font-size:1.6rem}h2{font-size:1.15rem}a{color:var(--link)}button,input,select{font:inherit;max-width:100%;padding:.5rem;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text)}button{cursor:pointer}header,aside{background:var(--panel);padding:1rem;border:1px solid var(--line);border-radius:12px}p{overflow-wrap:anywhere}.metrics,.controls{display:flex;flex-wrap:wrap;gap:12px;margin:1rem 0}.metrics span{padding:.5rem;border:1px solid var(--line);border-radius:8px}progress{width:100%;height:16px}table{width:100%;border-collapse:collapse;table-layout:fixed;margin-top:1rem}th,td{text-align:left;padding:.65rem .4rem;border-bottom:1px solid var(--line);overflow-wrap:anywhere}th:first-child{width:24%}.task{border:0;padding:0;text-align:left;color:var(--link);overflow-wrap:anywhere}.chip{display:inline-block;font-size:.8rem;padding:.25rem .4rem;border:1px solid currentColor;border-radius:6px}.RESOLVED{color:#187746}.FAILED,.TIMEOUT,.INVALID{color:#b63e39}.running{color:#225fc4}.queued,.INCONCLUSIVE{color:#796012}@media(prefers-color-scheme:dark){.RESOLVED{color:#76e3ac}.FAILED,.TIMEOUT,.INVALID{color:#ffa39f}.running{color:#9bcaff}.queued,.INCONCLUSIVE{color:#e7ce81}}aside{position:fixed;right:0;top:0;bottom:0;width:min(620px,100%);z-index:2;box-shadow:0 0 30px #0005;display:flex;flex-direction:column}aside[hidden]{display:none}pre{white-space:pre-wrap;overflow-wrap:anywhere;overflow:auto;min-height:0;flex:1;font-size:.85rem}#close{align-self:flex-end}#error{color:var(--link)}
@media(max-width:700px){thead{display:none}table,tbody,tr,td{display:block;width:100%}tr{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:12px 0;padding:.5rem}td{border:0;padding:.3rem}td:before{content:attr(data-label) ' ';font-weight:600}th:first-child{width:auto}}
</style><main><h1>ABAP bench progress</h1><header><h2 id="identity">Waiting for tasks…</h2><p id="context"></p><div class="metrics" id="metrics"></div><progress id="progress" value="0" max="1" aria-label="Completed tasks"></progress><p id="refs"></p></header>
<p id="error" role="status"></p><div class="controls"><input id="search" type="search" placeholder="Search task id" aria-label="Search task id"><select id="filter" aria-label="Filter status"><option value="">All statuses</option></select><select id="sort" aria-label="Sort tasks"><option value="task">Sort: task id</option><option value="status">Sort: status</option><option value="duration">Sort: duration (longest)</option><option value="requests">Sort: requests (most)</option></select></div>
<table><thead><tr><th>Task</th><th>Status</th><th>Duration</th><th>Requests</th><th>Patch lines</th><th>Policy / contamination</th><th>References</th></tr></thead><tbody id="tasks"></tbody></table></main>
<aside id="panel" hidden aria-label="Task log"><button id="close">Close log</button><h2 id="logtitle"></h2><p id="files"></p><pre id="log" tabindex="0"></pre></aside>
<script>
const $=id=>document.getElementById(id), statuses=['queued','running','RESOLVED','FAILED','TIMEOUT','INCONCLUSIVE','INVALID'];
const icons=['○','▶','✓','✗','◷','?','!'];let state={tasks:[]}, selected=null, logVersion=0;
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}
function chip(status){return node('span',icons[statuses.indexOf(status)]+' '+status,'chip '+status)}
function duration(s){return s==null?'—':Math.round(s)+'s'}
for(const s of statuses){const o=node('option',s);o.value=s;$('filter').append(o)}
function draw(){const s=state.summary;$('identity').textContent=(s.executor||'Waiting for tasks…')+' / '+(s.model.join(', ')||'model pending');$('context').textContent='Package: '+(s.abap_package.join(', ')||'—')+' · Tier: '+(s.abap_tier.join(', ')||'—')+' · Env: '+(s.env_version.join(', ')||'—');$('metrics').replaceChildren(...[`${s.solved} solved of ${s.total}`,`${s.failed} failed`,`${s.running} running`,`${s.queued} queued`,`${s.invalid} invalid`,`${duration(s.agent_time)} total agent time`].map(t=>node('span',t)));$('progress').max=s.total||1;$('progress').value=s.completed;$('refs').textContent=state.comparisons.map(r=>`${r.executor}: ${r.solved} solved of ${s.total} (${r.total} verdicts), ${r.failed} failed`).join(' · ');drawTasks()}
function drawTasks(){const key=$('sort').value;const rows=state.tasks.filter(r=>(!$('filter').value||r.status===$('filter').value)&&r.task.toLowerCase().includes($('search').value.toLowerCase())).sort((a,b)=>key==='duration'||key==='requests'?(b[key]||0)-(a[key]||0):String(a[key]).localeCompare(String(b[key])));$('tasks').replaceChildren();for(const r of rows){const tr=node('tr','');const task=node('button',r.task,'task');task.onclick=()=>openLog(r);const refs=node('div','');for(const [ex,status] of Object.entries(r.comparisons)){const line=node('p',ex+' ');line.append(chip(status));refs.append(line)}const values=[task,chip(r.status),duration(r.duration),r.requests,r.patch_lines??'—',(r.net_policy||'—')+' / contaminated: '+(r.contaminated==null?'unknown':String(r.contaminated)),refs];values.forEach((v,i)=>{const td=node('td','');td.dataset.label=['Task','Status','Duration','Requests','Patch lines','Policy / contamination','References'][i];if(v instanceof Node)td.append(v);else td.textContent=v;tr.append(td)});$('tasks').append(tr)}if(!rows.length)$('tasks').append(node('tr','No matching tasks'))}
function showFiles(r){$('files').replaceChildren();for(const [key,label] of [['patch','patch.diff'],['result','result.json']])if(r[key]){const a=node('a',label);a.href=r[key];$('files').append(a,document.createTextNode(' '))}}
function openLog(r){selected=r;logVersion++;$('panel').hidden=false;$('logtitle').textContent=r.task;showFiles(r);$('log').textContent='Loading live.txt (tail)…';pollLog()}
async function pollLog(){if(!selected)return;const r=selected,version=logVersion;try{const response=await fetch('/api/log?task='+encodeURIComponent(r.task)+'&run='+encodeURIComponent(r.run));if(!response.ok)throw Error('Log unavailable');const data=await response.json();if(version!==logVersion)return;$('log').textContent=data.text||'Waiting for live.txt…';if(r.status==='running')$('log').scrollTop=$('log').scrollHeight}catch(e){if(version===logVersion)$('log').textContent=e.message}}
async function refresh(){try{const response=await fetch('/api/state');if(!response.ok)throw Error('State unavailable');state=await response.json();draw();$('error').textContent='';if(selected){const current=state.tasks.find(r=>r.task===selected.task&&r.run===selected.run);if(current){selected=current;showFiles(current)}await pollLog()}}catch(e){$('error').textContent='Updates paused; retrying: '+e.message}finally{setTimeout(refresh,2000)}}
$('close').onclick=()=>{selected=null;logVersion++;$('panel').hidden=true};document.addEventListener('keydown',e=>{if(e.key==='Escape')$('close').click()});for(const id of ['search','filter','sort'])$(id).addEventListener('input',drawTasks);refresh();
</script></html>'''


def render(root=None, now=None):
    return PAGE


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        root = self.server.runs
        if path == '/':
            body, kind = render().encode(), 'text/html; charset=utf-8'
        elif path == '/api/state':
            body = json.dumps(build_state(root, getattr(self.server, 'compare', ())), allow_nan=False).encode()
            kind = 'application/json; charset=utf-8'
        else:
            if path == '/api/log':
                query = urllib.parse.parse_qs(url.query)
                parts = ['', 'live', query.get('task', [''])[0], query.get('run', [''])[0], 'live.txt']
            else:
                parts = path.split('/')
            if (len(parts) != 5 or parts[1] != 'live' or parts[4] not in ('live.txt', 'patch.diff', 'result.json')
                    or any(p in ('', '.', '..') or '/' in p or '\\' in p or '\x00' in p for p in parts[2:])):
                self.send_error(404)
                return
            target = safe_file(root, root.joinpath(*parts[2:]))
            if target is None or (not target.is_file() and path != '/api/log'):
                self.send_error(404)
                return
            if path == '/api/log':
                body, kind = json.dumps(dict(text=tail(target))).encode(), 'application/json; charset=utf-8'
            elif parts[4] == 'live.txt':
                body, kind = tail(target).encode(), 'text/plain; charset=utf-8'
            else:
                try:
                    body = target.read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                kind = 'text/plain; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, default=Path(__file__).resolve().parents[1] / 'runs')
    parser.add_argument('--compare', default='', help='Comma-separated reference runs directories')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--bind', default='0.0.0.0')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be 1..65535')
    with http.server.ThreadingHTTPServer((args.bind, args.port), Handler) as server:
        server.daemon_threads = True
        server.runs = args.runs.resolve()
        server.compare = [Path(p).resolve() for p in args.compare.split(',') if p]
        print(f'ready: http://{args.bind}:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
