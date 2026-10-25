#!/usr/bin/env python3
"""Did the network hand the agent the answer? Content check for native ABAP runs.

    bin/contamination.py RUN_DIR TASK_DIR     -> JSON on stdout; exit 0

The reference is the hidden body of the hole: the pristine source in the task package at the hole's byte span
(<ABAP_TASKS>/private/abap-tasks/tasks.jsonl + private/hidden/<id>/.grader/pristine). Every network-derived tool result
found by bin/net-audit.py (web tool results, output of curl/wget/git and other commands naming a URL) is compared with it
after normalization (lower case, ABAP comments and whitespace dropped, word and symbol tokens):
- a hole body of 80+ characters: share of its token 8-grams found in one result; 0.5 or more is a match;
- a shorter body (a single generic line can occur in docs): the body's token sequence together with a neighbouring
  line of the original file (the line before or after the hole), both in one result. This is the rule of the task
  package's own checker (contamination.mjs).
A match makes the run `contaminated`. So does a provider-side search that returned results (Codex web_search): only its
result URLs are logged, so its content cannot be checked. Attempts to reach hosts outside ALLOWED are listed either way.
"""
import importlib.util
import json
import os
import re
import sys

ALLOWED = ('help.sap.com', 'rules.abaplint.org', 'abaplint.app')   # documentation the native ABAP protocol allows
N = 8

_spec = importlib.util.spec_from_file_location('net_audit', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'net-audit.py'))
net_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net_audit)


def tokens(text):
    lines = []
    for line in text.lower().splitlines():
        if line.lstrip().startswith('*'):
            continue
        line = re.sub(r'"[^`\']*$', '', line)            # ABAP end-of-line comment (outside string literals, roughly)
        lines.append(line)
    return re.findall(r'[a-z0-9_]+|[^\sa-z0-9_]', '\n'.join(lines))


def grams(toks, n=N):
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def contains_seq(hay, needle):
    k = len(needle)
    return any(hay[i:i + k] == needle for i in range(len(hay) - k + 1))


def reference(task_dir):
    env = {}
    for line in open(os.path.join(task_dir, 'task.env')):
        m = re.match(r'^(ABAP_TASKS)=(.*)$', line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip("'\"")
    root, tid = env.get('ABAP_TASKS'), os.path.basename(os.path.normpath(task_dir))
    if not root:
        raise RuntimeError('task.env has no ABAP_TASKS')
    for line in open(os.path.join(root, 'private', 'abap-tasks', 'tasks.jsonl')):
        t = json.loads(line)
        if t['id'] == tid:
            h = t['hole_span']
            data = open(os.path.join(root, 'private', 'hidden', tid, '.grader', 'pristine', t['file']), 'rb').read()
            body = data[h['start']:h['end']].decode('utf-8', 'replace')
            before = data[:h['start']].decode('utf-8', 'replace').rstrip('\n').split('\n')
            after = data[h['end']:].decode('utf-8', 'replace').lstrip('\n').split('\n')
            near = [l for l in (before[-1] if before else '', after[0] if after else '') if tokens(l)]
            return body, near
    raise RuntimeError('task %s not in the package' % tid)


def host_of(what):
    m = re.search(r'https?://([A-Za-z0-9.-]+)', what or '')
    return m.group(1).lower() if m else None


def check(run, ref_text, near=()):
    calls = net_audit.audit(run, full=True)['attempts']
    ref = tokens(ref_text)
    long_body = len(re.sub(r'\s+', '', ref_text)) >= 80 and len(ref) >= N
    ref_grams = grams(ref)
    best = dict(score=0.0, tool=None, what=None)
    contaminated, reasons = False, []
    for c in calls:
        if c['tool'] == 'web_search' and c['fetched']:
            contaminated = True
            reasons.append('provider-side web search returned results (content not logged): ' + c['what'][:120])
            continue
        hay = tokens(c.get('text') or '')
        if not hay:
            continue
        if long_body:
            score = len(ref_grams & grams(hay)) / max(1, len(ref_grams))
        else:
            score = 1.0 if ref and contains_seq(hay, ref) and any(contains_seq(hay, tokens(l)) for l in near) else 0.0
        if score > best['score']:
            best = dict(score=round(score, 3), tool=c['tool'], what=c['what'][:160])
        if score >= 0.5:
            contaminated = True
            reasons.append('%s result matches the hidden reference (%.0f%%): %s' % (c['tool'], 100 * score, c['what'][:120]))
    outside = sorted({h for h in (host_of(c['what']) for c in calls) if h and not any(h == a or h.endswith('.' + a) for a in ALLOWED)})
    return dict(contaminated=contaminated, reasons=reasons, best_match=best, reference_tokens=len(ref),
                network_attempts=len(calls), hosts_outside_allowlist=outside)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    body, near = reference(sys.argv[2])
    print(json.dumps(check(sys.argv[1], body, near), indent=1))
