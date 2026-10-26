#!/usr/bin/env python3
"""Small live benchmark dashboard. Uses only the Python standard library."""
import argparse
from datetime import datetime, timezone
import math
import re
import statistics
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


STATUSES = ('queued', 'running', 'RESOLVED', 'FAILED', 'TIMEOUT', 'INCONCLUSIVE', 'INVALID', 'not run')


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
        for key in ('model', 'protocol', 'abap_set', 'abap_package', 'abap_tier', 'env_version', 'net_policy', 'contaminated'):
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
        row['_order'] = (str(info.get('started_at') or ''), directory.name)
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
            candidates = [r for r in data if r['executor'] == executor]
            # Legacy queue files contain no group fields. Associate pending tasks with
            # that executor's newest invocation, never with every historical group.
            context = (plan if any(k in plan for k in GROUP_FIELDS) else
                       max(candidates, key=run_order) if candidates else plan)
            if not any(r['task'] == task and r['executor'] == executor and
                       group_key(r) == group_key(context) for r in data):
                pending = dict(task=task, run=executor, executor=executor, status='queued',
                               duration=None, requests=0, patch_lines=None, live=None, patch=None, result=None)
                pending.update({k: context.get(k) for k in GROUP_FIELDS})
                data.append(pending)
    return sorted(data, key=lambda r: (r['task'], r['run']))


def totals(entries):
    counts = {status: sum(r['status'] == status for r in entries) for status in STATUSES}
    return dict(total=len(entries), solved=counts['RESOLVED'],
                failed=sum(counts[s] for s in ('FAILED', 'TIMEOUT', 'INCONCLUSIVE')),
                running=counts['running'], queued=counts['queued'], invalid=counts['INVALID'],
                completed=sum(counts[s] for s in ('RESOLVED', 'FAILED', 'TIMEOUT', 'INCONCLUSIVE', 'INVALID')),
                agent_time=sum(r['duration'] or 0 for r in entries), statuses=counts)


GROUP_FIELDS = ('protocol', 'abap_set', 'abap_package', 'abap_tier', 'env_version')


def group_key(row):
    return tuple(str(row.get(k) or '') for k in GROUP_FIELDS)


def group_id(key):
    return json.dumps(key, separators=(',', ':'))


def run_order(row):
    stamp = re.search(r'([0-9]{8})[-T]([0-9]{6})', row['run'])
    # Run directory timestamps remain usable when started_at is absent.
    date = row.get('_order', ('', ''))[0]
    normalized = re.sub(r'[^0-9]', '', date)[:14] if date else ''
    return (normalized or (''.join(stamp.groups()) if stamp else ''), row['run'])


def build_state(root, compare=(), now=None, group=None, executor=None):
    local = rows(root, now)
    all_rows = list(local)
    for index, source in enumerate(compare):
        for row in rows(Path(source), now):
            row['source'] = str(index)
            for artifact in ('live', 'patch', 'result'):
                if row.get(artifact):
                    row[artifact] = '/reference/' + str(index) + row[artifact]
            all_rows.append(row)
    grouped = {}
    for row in all_rows:
        grouped.setdefault(group_key(row), []).append(row)
    newest = max(local or all_rows, key=run_order, default=None)
    default_key = group_key(newest) if newest else ('',) * len(GROUP_FIELDS)
    key = next((k for k in grouped if group_id(k) == group), default_key)
    entries = grouped.get(key, [])
    newest_in_group = max([r for r in local if group_key(r) == key] or entries,
                          key=run_order, default=None)
    executors = sorted({r['executor'] for r in entries})
    primary = executor if executor in executors else (newest_in_group['executor'] if newest_in_group else None)
    task_ids = sorted({r['task'] for r in entries})
    latest, valid = {}, {}
    for row in sorted(entries, key=run_order):
        pair = (row['executor'], row['task'])
        latest[pair] = row
        if row['status'] not in ('INVALID', 'queued', 'running'):
            valid[pair] = row
    tasks = []
    for task in task_ids:
        row = dict(latest.get((primary, task), dict(task=task, executor=primary, run='',
                   status='not run', duration=None, requests=0, patch_lines=None,
                   live=None, patch=None, result=None)))
        row.pop('_order', None)
        row['comparisons'] = {ex: valid[(ex, task)]['status'] for ex in executors
                              if ex != primary and (ex, task) in valid}
        tasks.append(row)
    summary = totals(tasks)
    summary['executor'] = primary
    for field in ('model', *GROUP_FIELDS):
        summary[field] = sorted({str(r[field]) for r in tasks if r.get(field) is not None})
    comparisons = []
    for ex in executors:
        # Comparison results stay stable when switching the selected executor.
        attempts = [valid[(ex, task)] for task in task_ids if (ex, task) in valid]
        metrics = totals(attempts)
        metrics['total'] = len(task_ids)
        durations = [r['duration'] for r in attempts if r['duration'] is not None
                     and r['status'] not in ('queued', 'running')]
        comparisons.append(dict(executor=ex, selected=ex == primary,
                                median_time=statistics.median(durations) if durations else None, **metrics))
    comparisons.sort(key=lambda r: (-r['solved'], r['executor']))
    groups = [dict(id=group_id(k), label=' · '.join(v or '—' for v in k)
                   if any(k) else 'Non-ABAP / legacy', **dict(zip(GROUP_FIELDS, k)))
              for k in sorted(grouped)]
    return dict(summary=summary, tasks=tasks, comparisons=comparisons,
                groups=groups, group=group_id(key), executors=executors)


PAGE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ABAP bench progress</title>
<style>
:root{color-scheme:light dark;--bg:#f5f7fb;--panel:#fff;--text:#182536;--line:#cbd5e1;--link:#1358a0}
@media(prefers-color-scheme:dark){:root{--bg:#111923;--panel:#1b2635;--text:#edf2fa;--line:#45556a;--link:#9bcaff}}
*{box-sizing:border-box}body{margin:0;padding:clamp(12px,3vw,32px);background:var(--bg);color:var(--text);font:16px system-ui}main{max-width:1250px;margin:auto}h1{font-size:1.6rem}h2{font-size:1.15rem}a{color:var(--link)}button,input,select{font:inherit;max-width:100%;padding:.5rem;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text)}button{cursor:pointer}header,aside{background:var(--panel);padding:1rem;border:1px solid var(--line);border-radius:12px}p{overflow-wrap:anywhere}.metrics,.controls{display:flex;flex-wrap:wrap;gap:12px;margin:1rem 0}.metrics span{padding:.5rem;border:1px solid var(--line);border-radius:8px}progress{width:100%;height:16px}table{width:100%;border-collapse:collapse;table-layout:fixed;margin-top:1rem}th,td{text-align:left;padding:.65rem .4rem;border-bottom:1px solid var(--line);overflow-wrap:anywhere}th:first-child{width:24%}.task{border:0;padding:0;text-align:left;color:var(--link);overflow-wrap:anywhere}.chip{display:inline-block;font-size:.8rem;padding:.25rem .4rem;border:1px solid currentColor;border-radius:6px}.RESOLVED{color:#187746}.FAILED,.TIMEOUT,.INVALID{color:#b63e39}.running{color:#225fc4}.queued,.INCONCLUSIVE{color:#796012}@media(prefers-color-scheme:dark){.RESOLVED{color:#76e3ac}.FAILED,.TIMEOUT,.INVALID{color:#ffa39f}.running{color:#9bcaff}.queued,.INCONCLUSIVE{color:#e7ce81}}aside{position:fixed;right:0;top:0;bottom:0;width:min(620px,100%);z-index:2;box-shadow:0 0 30px #0005;display:flex;flex-direction:column}aside[hidden]{display:none}pre{white-space:pre-wrap;overflow-wrap:anywhere;overflow:auto;min-height:0;flex:1;font-size:.85rem}#close{align-self:flex-end}#error{color:var(--link)}
@media(max-width:700px){thead{display:none}table,tbody,tr,td{display:block;width:100%}tr{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:12px 0;padding:.5rem}td{border:0;padding:.3rem}td:before{content:attr(data-label) ' ';font-weight:600}th:first-child{width:auto}}
.selectors{display:grid;grid-template-columns:2fr 1fr;gap:12px}.selectors label{min-width:0}.selectors select{display:block;width:100%;text-overflow:ellipsis}.truncate{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}#identity{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.comparison th:first-child{width:46%}.comparison .selected{background:var(--bg);font-weight:700}.comparison td{padding:.4rem}details summary{cursor:pointer;white-space:nowrap}details .reference-list{font-size:.8rem;overflow-wrap:anywhere}details p{margin:.3rem 0}.policy{font-size:.75rem}.controls input{min-width:0}.controls>*{flex:1;min-width:0}td .chip{max-width:100%;overflow-wrap:normal;font-size:.7rem}#show-all{margin-top:.6rem}#logtitle{overflow-wrap:anywhere}
@media(min-width:701px) and (max-width:1150px){.task-table .optional{display:none}.task-table th:first-child{width:28%}}
@media(max-width:700px){.selectors{grid-template-columns:1fr}.comparison{display:table;width:100%}.comparison tbody{display:table-row-group}.comparison thead{display:table-header-group}.comparison tr{display:table-row}.comparison th,.comparison td{display:table-cell;width:auto;font-size:.8rem}.comparison td:before{content:none}.task-table td.optional{display:none}.controls{gap:6px}.controls input{flex-basis:100%}}
</style><main><h1>ABAP bench progress</h1><header><div class="selectors"><label>Run group<select id="group" aria-label="Run group"></select></label><label>Executor<select id="executor" aria-label="Executor"></select></label></div><h2 id="identity">Waiting for tasks…</h2><p id="context"></p><div class="metrics" id="metrics"></div><progress id="progress" value="0" max="1" aria-label="Completed tasks"></progress><h2>Executor comparison</h2><table class="comparison" aria-label="Executor comparison"><thead><tr><th>Executor</th><th>Solved / total</th><th>Failed</th><th>Median</th></tr></thead><tbody id="refs"></tbody></table><button id="show-all" hidden>Show all</button></header>
<p id="error" role="status"></p><div class="controls"><input id="search" type="search" placeholder="Search task id" aria-label="Search task id"><select id="filter" aria-label="Filter status"><option value="">All statuses</option></select><select id="sort" aria-label="Sort tasks"><option value="task">Sort: task id</option><option value="status">Sort: status</option><option value="duration">Sort: duration (longest)</option><option value="requests">Sort: requests (most)</option></select></div>
<table class="task-table"><thead><tr><th>Task</th><th>Status</th><th>Duration</th><th>Requests</th><th>Patch lines</th><th class="optional">Net</th><th class="optional">Contam.</th><th>References</th></tr></thead><tbody id="tasks"></tbody></table></main>
<aside id="panel" hidden aria-label="Task log"><button id="close">Close log</button><h2 id="logtitle"></h2><p id="files"></p><pre id="log" tabindex="0"></pre></aside>
<script>
const $=id=>document.getElementById(id), statuses=['queued','running','RESOLVED','FAILED','TIMEOUT','INCONCLUSIVE','INVALID','not run'];
const icons=['○','▶','✓','✗','◷','?','!','—'];let state={tasks:[]}, selected=null, logVersion=0, activeGroup='', activeExecutor='', showAll=false, refreshVersion=0, refreshTimer;
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}
function chip(status){return node('span',icons[statuses.indexOf(status)]+' '+status,'chip '+status)}
function duration(s){if(s==null)return '—';const n=Math.round(s);return Math.floor(n/60)+':'+String(n%60).padStart(2,'0')}
function selector(id,entries,value){$(id).replaceChildren(...entries.map(([v,label])=>{const o=node('option',label);o.value=v;o.title=label;return o}));$(id).value=value||'';$(id).title=$(id).selectedOptions[0]?.textContent||''}
for(const s of statuses){const o=node('option',s);o.value=s;$('filter').append(o)}
function draw(){const s=state.summary;selector('group',state.groups.map(g=>[g.id,g.label]),state.group);selector('executor',state.executors.map(ex=>[ex,ex]),s.executor);$('identity').textContent=(s.executor||'Waiting for tasks…')+' / '+(s.model.join(', ')||'model pending');$('identity').title=$('identity').textContent;$('context').textContent='Package: '+(s.abap_package.join(', ')||'—')+' · Tier: '+(s.abap_tier.join(', ')||'—')+' · Env: '+(s.env_version.join(', ')||'—');$('metrics').replaceChildren(...[`${s.solved} solved of ${s.total}`,`${s.failed} failed`,`${s.running} running`,`${s.queued} queued`,`${s.invalid} invalid`,`${duration(s.agent_time)} total agent time`].map(t=>node('span',t)));$('progress').max=s.total||1;$('progress').value=s.completed;drawComparison();drawTasks()}
function drawComparison(){$('refs').replaceChildren();for(const r of state.comparisons.slice(0,showAll?undefined:8)){const tr=node('tr','',r.selected?'selected':'');if(r.selected)tr.setAttribute('aria-current','true');const name=node('span',(r.selected?'▶ ':'')+r.executor,'truncate');name.title=r.executor;for(const v of [name,`${r.solved} / ${r.total}`,r.failed,duration(r.median_time)]){const td=node('td','');if(v instanceof Node)td.append(v);else td.textContent=v;tr.append(td)}$('refs').append(tr)}$('show-all').hidden=state.comparisons.length<=8;$('show-all').textContent=showAll?'Show fewer':'Show all ('+state.comparisons.length+')'}
function references(comparisons){const entries=Object.entries(comparisons);if(!entries.length)return '—';const details=node('details','');const summary=node('summary',entries.filter(([,status])=>status==='RESOLVED').length+'/'+entries.length+' ✓');summary.title=entries.map(([ex,status])=>ex+': '+status).join('\n');details.append(summary);const list=node('div','','reference-list');for(const [ex,status] of entries){const line=node('p','');const name=node('span',ex,'truncate');name.title=ex;line.append(name,chip(status));list.append(line)}details.append(list);return details}
function policy(value){const n=node('span',({'egress-endpoint-only':'endpoint','offline':'offline','none':'none','unrestricted':'open'})[value]||value||'—','policy');n.title=value||'Unknown network policy';return n}
function drawTasks(){const key=$('sort').value;const rows=state.tasks.filter(r=>(!$('filter').value||r.status===$('filter').value)&&r.task.toLowerCase().includes($('search').value.toLowerCase())).sort((a,b)=>key==='duration'||key==='requests'?(b[key]||0)-(a[key]||0):String(a[key]).localeCompare(String(b[key])));$('tasks').replaceChildren();for(const r of rows){const tr=node('tr','');const task=node('button',r.task,'task');task.onclick=()=>openLog(r);const contamination=node('span',r.contaminated==null?'?':r.contaminated?'✗':'✓');contamination.title=r.contaminated==null?'Contamination unknown':r.contaminated?'Contaminated':'Clean';const values=[task,chip(r.status),duration(r.duration),r.requests,r.patch_lines??'—',policy(r.net_policy),contamination,references(r.comparisons)];values.forEach((v,i)=>{const td=node('td','');td.dataset.label=['Task','Status','Duration','Requests','Patch lines','Net','Contam.','References'][i];if(i===5||i===6)td.className='optional';if(v instanceof Node)td.append(v);else td.textContent=v;tr.append(td)});$('tasks').append(tr)}if(!rows.length){const tr=node('tr','');const td=node('td','No matching tasks');td.colSpan=8;tr.append(td);$('tasks').append(tr)}}
function showFiles(r){$('files').replaceChildren();for(const [key,label] of [['patch','patch.diff'],['result','result.json']])if(r[key]){const a=node('a',label);a.href=r[key];$('files').append(a,document.createTextNode(' '))}}
function openLog(r){selected=r;logVersion++;$('panel').hidden=false;$('logtitle').textContent=r.task;showFiles(r);$('log').textContent='Loading live.txt (tail)…';pollLog()}
async function pollLog(){if(!selected)return;const r=selected,version=logVersion;try{const response=await fetch('/api/log?task='+encodeURIComponent(r.task)+'&run='+encodeURIComponent(r.run)+(r.source!=null?'&source='+encodeURIComponent(r.source):''));if(!response.ok)throw Error('Log unavailable');const data=await response.json();if(version!==logVersion)return;$('log').textContent=data.text||'Waiting for live.txt…';if(r.status==='running')$('log').scrollTop=$('log').scrollHeight}catch(e){if(version===logVersion)$('log').textContent=e.message}}
async function refresh(){clearTimeout(refreshTimer);const version=++refreshVersion;try{const query=new URLSearchParams();if(activeGroup)query.set('group',activeGroup);if(activeExecutor)query.set('executor',activeExecutor);const response=await fetch('/api/state'+(query.size?'?'+query:''));if(!response.ok)throw Error('State unavailable');const data=await response.json();if(version!==refreshVersion)return;state=data;draw();$('error').textContent='';if(selected){const current=state.tasks.find(r=>r.task===selected.task&&r.run===selected.run);if(current){selected=current;showFiles(current)}await pollLog()}}catch(e){$('error').textContent='Updates paused; retrying: '+e.message}finally{if(version===refreshVersion)refreshTimer=setTimeout(refresh,2000)}}
$('show-all').onclick=()=>{showAll=!showAll;drawComparison()};for(const id of ['group','executor'])$(id).onchange=()=>{if(id==='group'){activeGroup=$('group').value;activeExecutor='';showAll=false}else activeExecutor=$('executor').value;$('close').click();refresh()};
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
        query = urllib.parse.parse_qs(url.query)
        source = query.get('source', [None])[0] if path == '/api/log' else None
        if path.startswith('/reference/'):
            prefix = path.split('/', 3)
            if len(prefix) == 4:
                source, path = prefix[2], '/' + prefix[3]
        if source is not None:
            references = getattr(self.server, 'compare', ())
            if not re.fullmatch(r'[0-9]{1,6}', source) or int(source) >= len(references):
                self.send_error(404)
                return
            root = Path(references[int(source)])
        if path == '/':
            body, kind = render().encode(), 'text/html; charset=utf-8'
        elif path == '/api/state':
            query = urllib.parse.parse_qs(url.query)
            body = json.dumps(build_state(root, getattr(self.server, 'compare', ()),
                              group=query.get('group', [None])[0],
                              executor=query.get('executor', [None])[0]), allow_nan=False).encode()
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
