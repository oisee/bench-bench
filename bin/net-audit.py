#!/usr/bin/env python3
"""What did the agent try to fetch from the network, and did anything come back?

    bin/net-audit.py RUN_DIR            -> JSON on stdout; exit 0

Reads the harness's own records: Claude Code session logs (home/.claude/projects/**.jsonl), Codex events
(agent.out), Pi sessions (home/.pi/agent/sessions/**.jsonl). It looks for:
- web tools: Claude Code WebFetch / WebSearch, Codex web_search (run on the provider's side, outside the sandbox);
- shell commands that reach the network: curl, wget, git clone/fetch/pull/ls-remote, or any http(s) URL.
A call counts as `fetched` when it returned something:
- a web tool without an error, or a Codex web_search with results;
- a network command whose output has no connection error and is not empty.
Output: {"web_tool_calls": n, "net_commands": n, "fetched": [...], "attempts": [...]}. For the native ABAP protocol
bin/finalize.sh marks the run `contaminated` when anything was fetched: its answer is public code, so such a run counts
as neither solved nor failed.
"""
import glob
import json
import os
import re
import sys

NET = re.compile(r'\b(curl|wget|git\s+(clone|fetch|pull|ls-remote)|https?://)', re.I)
FAILED = re.compile(r"(Could not resolve|Couldn't connect|Failed to connect|Connection refused|Name or service not known|"
                    r"Temporary failure in name resolution|Network is unreachable|exit=[1-9]|curl: \(\d+\)|unable to access|"
                    r"fatal: |ERROR|No such file)", re.I)


URL = re.compile(r'https?://([A-Za-z0-9.-]+)')


def blocked_hosts(run):
    """Hosts the run's sandbox could not resolve (RUN/hosts maps them to 0.0.0.0)."""
    try:
        return {l.split()[1] for l in open(os.path.join(run, 'hosts')) if l.startswith('0.0.0.0 ')}
    except OSError:
        return set()


def cmd_fetched(cmd, ok, body, blocked):
    """A shell command fetched something only if it named a reachable host and printed real output."""
    hosts = set(URL.findall(cmd))
    if hosts and hosts <= blocked:
        return False
    body = re.sub(r'\(Bash completed with no output\)', '', body).strip()
    return ok and bool(body) and not FAILED.search(body) and body not in ('0', '000')


def short(x, n=200):
    return (x if isinstance(x, str) else json.dumps(x))[:n].replace('\n', ' ')


def claude(run, out, blocked):
    for f in glob.glob(os.path.join(run, 'home/.claude/projects/*/*.jsonl')):
        uses, results = {}, {}
        for line in open(f, errors='replace'):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c = (e.get('message') or {}).get('content')
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get('type') == 'tool_use':
                    inp = b.get('input') or {}
                    if b.get('name') in ('WebFetch', 'WebSearch'):
                        uses[b['id']] = ('web', b['name'], short(inp))
                    elif b.get('name') == 'Bash' and NET.search(inp.get('command', '')):
                        uses[b['id']] = ('cmd', 'Bash', short(inp.get('command', '')))
                elif b.get('type') == 'tool_result' and b.get('tool_use_id') in uses:
                    cc = b.get('content')
                    results[b['tool_use_id']] = (bool(b.get('is_error')), cc if isinstance(cc, str) else json.dumps(cc))
        for k, (kind, name, what) in uses.items():
            err, text = results.get(k, (True, ''))
            if kind == 'web':
                got = not err and bool(text.strip())
            else:
                got = cmd_fetched(what, not err, text, blocked)
            out.append(dict(kind=kind, tool=name, what=what, fetched=got, result=short(text, 160), text=text))


def codex(run, out, blocked):
    p = os.path.join(run, 'agent.out')
    if not os.path.exists(p):
        return
    for line in open(p, errors='replace'):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        it = e.get('item') or {}
        if e.get('type') != 'item.completed':
            continue
        if it.get('type') == 'web_search':
            res = it.get('results') or []
            out.append(dict(kind='web', tool='web_search', what=short(it.get('action') or it.get('query')),
                            fetched=bool(res), result=short([r.get('url') for r in res], 160), text=json.dumps(res)))
        elif it.get('type') == 'command_execution' and NET.search(it.get('command', '')):
            body = (it.get('aggregated_output') or '').strip()
            got = cmd_fetched(it.get('command', ''), it.get('exit_code') == 0, body, blocked)
            out.append(dict(kind='cmd', tool='shell', what=short(it.get('command', '')), fetched=got, result=short(body, 160),
                            text=body))


def pi(run, out):
    for f in glob.glob(os.path.join(run, 'home/.pi/agent/sessions/**/*.jsonl'), recursive=True):
        for line in open(f, errors='replace'):
            if NET.search(line) and '"toolCall"' in line:
                out.append(dict(kind='cmd', tool='pi', what=short(line), fetched=None, result='', text=line))


def audit(run, full=False):
    calls = []
    blocked = blocked_hosts(run)
    claude(run, calls, blocked)
    codex(run, calls, blocked)
    pi(run, calls)
    if not full:
        for c in calls:
            c.pop('text', None)
    return dict(web_tool_calls=sum(c['kind'] == 'web' for c in calls), net_commands=sum(c['kind'] == 'cmd' for c in calls),
                fetched=[c for c in calls if c['fetched']], attempts=calls)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(audit(sys.argv[1]), indent=1))
