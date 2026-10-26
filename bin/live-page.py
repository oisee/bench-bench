#!/usr/bin/env python3
"""Small live benchmark dashboard. Uses only the Python standard library."""
import argparse
from datetime import datetime, timezone
import html
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
            return max(0, float(result['duration_s']))
        start = datetime.fromisoformat(meta['started_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(meta['finished_at'].replace('Z', '+00:00')) if meta.get('finished_at') else now
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, (end - start).total_seconds())
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def rows(root, now=None):
    now = now or datetime.now(timezone.utc)
    data = []
    for directory in sorted(root.glob('*/*')):
        if not directory.is_dir() or safe_file(root, directory) is None:
            continue
        paths = {name: safe_file(root, directory / name) for name in ('meta.json', 'result.json', 'traffic.jsonl', 'live.txt')}
        meta = read_json(paths['meta.json']) if paths['meta.json'] else {}
        result = read_json(paths['result.json']) if paths['result.json'] else {}
        live = paths['live.txt'] and paths['live.txt'].is_file()
        traffic = paths['traffic.jsonl'] and paths['traffic.jsonl'].is_file()
        data.append(dict(task=directory.parent.name, run=directory.name,
                         status=result.get('status') or ('running' if meta or live or traffic else 'queued'),
                         duration=elapsed(meta, result, now),
                         requests=request_count(paths['traffic.jsonl']) if traffic else 0,
                         live='/live/' + '/'.join(urllib.parse.quote(p, safe='') for p in
                                                 (directory.parent.name, directory.name, 'live.txt')) if live else None))
    for queue in sorted(root.glob('queue-*.json')):
        path = safe_file(root, queue)
        plan = read_json(path) if path else {}
        executor = plan.get('executor')
        tasks = plan.get('tasks', [])
        if not isinstance(executor, str) or not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, str) or not task or '/' in task or task in ('.', '..'):
                continue
            if not any(r['task'] == task and r['run'].startswith(executor + '-') for r in data):
                data.append(dict(task=task, run=executor, status='queued', duration=None, requests=0, live=None))
    return sorted(data, key=lambda r: (r['task'], r['run']))


def render(root, now=None):
    esc = lambda value: html.escape(str(value), quote=True)
    out = ['<!doctype html><html lang="en"><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta http-equiv="refresh" content="3"><title>ABAP bench progress</title>',
           '<style>body{font:16px system-ui;margin:2rem;max-width:1100px;background:#f6f8fa;color:#17212b}'
           'table{border-collapse:collapse;width:100%;background:white}th,td{padding:.7rem;text-align:left;'
           'border-bottom:1px solid #ddd}th{background:#e6edf3}a{color:#1559a1}</style>',
           '<h1>ABAP bench progress</h1><p>Refreshes every 3 seconds. Transcript links show the latest 200 lines.</p>',
           '<table><thead><tr><th>Task</th><th>Run</th><th>Status</th><th>Duration</th>'
           '<th>Requests</th><th>Transcript</th></tr></thead><tbody>']
    entries = rows(root, now)
    if not entries:
        out.append('<tr><td colspan="6">Waiting for tasks…</td></tr>')
    for row in entries:
        duration = '—' if row['duration'] is None else f"{row['duration']:.0f}s"
        link = f'<a href="{esc(row["live"])}">live.txt (tail)</a>' if row['live'] else '—'
        out.append('<tr>' + ''.join(f'<td>{esc(row[k])}</td>' for k in ('task', 'run', 'status')) +
                   f'<td>{esc(duration)}</td><td>{row["requests"]}</td><td>{link}</td></tr>')
    out.append('</tbody></table></html>')
    return '\n'.join(out)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        root = self.server.runs
        if path == '/':
            body, kind = render(root).encode(), 'text/html; charset=utf-8'
        else:
            parts = path.split('/')
            if (len(parts) != 5 or parts[1] != 'live' or parts[4] != 'live.txt'
                    or any(p in ('', '.', '..') or '\x00' in p for p in parts[2:])):
                self.send_error(404)
                return
            target = safe_file(root, root.joinpath(*parts[2:]))
            if target is None or not target.is_file():
                self.send_error(404)
                return
            body, kind = tail(target).encode(), 'text/plain; charset=utf-8'
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
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--bind', default='0.0.0.0')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be 1..65535')
    with http.server.ThreadingHTTPServer((args.bind, args.port), Handler) as server:
        server.daemon_threads = True
        server.runs = args.runs.resolve()
        print(f'ready: http://{args.bind}:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
