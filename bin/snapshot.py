#!/usr/bin/env python3
"""Snapshots of a run's work tree, kept outside the agent's sandbox in RUN/snap.git.

The record proxy (bin/record-proxy.py) takes one snapshot when it starts (seq 0, the untouched task tree) and one
when each model request arrives, before forwarding it (seq N: the tree after every tool call that came before
request N). bin/first-green.py later grades the snapshots to find when the solution first passed.
A snapshot is a git tree object written with a separate GIT_DIR and index, so the agent's own .git is never touched;
files the task repo ignores (node_modules/, build output) are not captured. Listing: RUN/snap.git/snapshots.tsv
(seq, unix time, tree id). Library and CLI:

    bin/snapshot.py take RUN SEQ [UNIX_TIME]   add one snapshot of RUN/work (time: now)
    bin/snapshot.py list RUN            print snapshots.tsv
"""
import os
import subprocess
import sys
import time

LIST = 'snapshots.tsv'
REF = 'refs/heads/snapshots'


def _git(snap, work, index, *args, stdin=None):
    env = dict(os.environ, GIT_DIR=snap, GIT_INDEX_FILE=index, GIT_AUTHOR_NAME='bench', GIT_AUTHOR_EMAIL='bench@localhost',
               GIT_COMMITTER_NAME='bench', GIT_COMMITTER_EMAIL='bench@localhost')
    if work:
        env['GIT_WORK_TREE'] = work
    for k in ('GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CEILING_DIRECTORIES'):
        env.pop(k, None)
    r = subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=/dev/null', *args],
                       env=env, input=stdin, capture_output=True, text=True, cwd=work or snap)
    if r.returncode:
        raise RuntimeError('git %s: %s' % (' '.join(args), r.stderr.strip()))
    return r.stdout.strip()


def snap_dir(run):
    return os.path.join(run, 'snap.git')


def init(run):
    snap = snap_dir(run)
    if not os.path.isdir(snap):
        subprocess.run(['git', 'init', '-q', '--bare', snap], check=True)
        _git(snap, None, os.path.join(snap, 'index'), 'config', 'core.bare', 'false')
    return snap


def take(run, seq, now=None):
    """Snapshot RUN/work as request SEQ; returns the tree id."""
    now = time.time() if now is None else now  # when the request arrived, not when the scan finished
    snap = init(run)
    index = os.path.join(snap, 'index')
    _git(snap, os.path.join(run, 'work'), index, 'add', '-A')
    tree = _git(snap, None, index, 'write-tree')
    try:
        parent = ['-p', _git(snap, None, index, 'rev-parse', '-q', '--verify', REF)]
    except RuntimeError:
        parent = []
    commit = _git(snap, None, index, 'commit-tree', tree, *parent, '-m', 'seq=%s t=%.3f' % (seq, now))
    _git(snap, None, index, 'update-ref', REF, commit)  # keeps every tree reachable
    with open(os.path.join(snap, LIST), 'a') as f:
        f.write('%s\t%.3f\t%s\n' % (seq, now, tree))
    return tree


def current_tree(run):
    """Tree id of RUN/work as it is now, without recording a snapshot."""
    snap = snap_dir(run)
    index = os.path.join(snap, 'check.index')
    try:
        _git(snap, None, index, 'read-tree', load(run)[-1][2])
        _git(snap, os.path.join(run, 'work'), index, 'add', '-A')
        return _git(snap, None, index, 'write-tree')
    finally:
        if os.path.exists(index):
            os.remove(index)


def t0(run):
    """Agent start (bin/run.sh writes it), else the seq 0 snapshot time."""
    p = os.path.join(snap_dir(run), 't0')
    return float(open(p).read()) if os.path.isfile(p) else load(run)[0][1]


def changed_paths(run, a, b):
    """Paths that differ between trees A and B."""
    out = _git(snap_dir(run), None, os.path.join(snap_dir(run), 'index'), 'diff-tree', '-r', '--name-only', '--no-renames', a, b)
    return [p for p in out.split('\n') if p]


def load(run):
    """[(seq, unix_time, tree)] in the order taken; seq is an int, or 'final'."""
    p = os.path.join(snap_dir(run), LIST)
    out = []
    if os.path.isfile(p):
        for line in open(p):
            seq, t, tree = line.rstrip('\n').split('\t')
            out.append((int(seq) if seq.isdigit() else seq, float(t), tree))
    return out


def materialize(run, tree, dst, base=None):
    """Copy RUN/work to DST (reflink when possible, so ignored files such as node_modules come along) and turn it
    from snapshot BASE (default: the latest one, i.e. RUN/work as last snapshotted) into snapshot TREE: files in BASE
    but not in TREE are removed, TREE's files are written. Files in neither (ignored ones) are left alone."""
    snap = snap_dir(run)
    base = base or load(run)[-1][2]
    subprocess.run(['cp', '-a', '--reflink=auto', os.path.join(run, 'work'), dst], check=True)
    index = dst.rstrip('/') + '.index'
    try:
        _git(snap, dst, index, 'read-tree', base)
        _git(snap, dst, index, 'read-tree', '--reset', '-u', tree)
    finally:
        if os.path.exists(index):
            os.remove(index)


if __name__ == '__main__':
    if len(sys.argv) in (4, 5) and sys.argv[1] == 'take':
        print(take(sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) == 5 else None))
    elif len(sys.argv) == 3 and sys.argv[1] == 'list':
        for s in load(sys.argv[2]):
            print('%s\t%.3f\t%s' % s)
    else:
        sys.exit(__doc__)
