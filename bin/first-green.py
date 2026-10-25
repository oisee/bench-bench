#!/usr/bin/env python3
"""Time to first green: when did the run's tree first pass every hidden test (F2P + P2P)?

    bin/first-green.py RUN_DIR TASK_DIR [--jobs N] [--bisect]

Needs the snapshots a BENCH_RECORD=1 run takes (bin/snapshot.py: seq 0 at start, one per request, 'final' when
the session ends) and the run's own grade (result.json). Consecutive identical trees count as one, timed by the
first request that saw it. Seq 0 is the untouched task tree, which bin/setup.sh --check proves red; the final tree
has the run's own grade. Every other tree is graded at most once with bin/grade.sh (GRADE_WORK/GRADE_OUT) in a
scratch copy; the agent's tree and the run's grade are not touched.

Default: every distinct tree is graded (N at a time with --jobs). first_green is the earliest green tree, the one
before it is red by construction, and stayed_green says whether every later tree passed. A run can go green, break
and go green again; only grading every tree sees that.
--bisect: assume the trees go red...red green...green and bisect (about log2 of the distinct trees, plus the trees
after the boundary to decide stayed_green). Quicker, but a green spell before the boundary it finds goes unseen;
first_green_method says which was used. On a failed run --bisect has no green end to bisect towards, so it grades
in order until one passes.

Adds to result.json: first_green_s (seconds from agent start), first_green_request (that tree was in place when
request N was sent; null if only the final tree is green), polish_s (duration_s - first_green_s), stayed_green,
first_green_grades, first_green_method and, when the replay can be wrong, first_green_caveat. Per-tree verdicts go to
RUN_DIR/first-green.json.

Replay limit: files the task repo ignores (node_modules/, build output) are not in the snapshots; every tree is
graded with the ignored files the run ended with. The caveat is set when a .gitignore or a dependency manifest
changed during the run, since then an early tree may be graded with dependencies or files it never had.
Grading uses the CPU: run this when no timed run shares the machine. GRADER=cmd replaces bin/grade.sh (tests).
"""
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snapshot  # noqa: E402

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEP_FILES = ('package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml',
             'go.mod', 'go.sum', 'go.work', 'requirements.txt', 'pyproject.toml', 'Cargo.lock')


class Inconclusive(Exception):
    pass


def distinct(snaps):
    """Collapse consecutive identical trees: [(tree, seq, t)] with the first seq/time of each run of equal trees."""
    out = []
    for seq, t, tree in snaps:
        if not out or out[-1][0] != tree:
            out.append((tree, seq, t))
    return out


class Grader:
    def __init__(self, run, task, scratch, final_tree):
        self.run, self.task, self.scratch, self.final_tree = run, task, scratch, final_tree
        self.cmd = os.environ.get('GRADER') or os.path.join(BENCH, 'bin', 'grade.sh')
        self.cache = {}
        self.graded = 0

    def _grade(self, tree):
        d = os.path.join(self.scratch, tree[:12])
        work, out = d + '-work', d + '-out'
        try:
            snapshot.materialize(self.run, tree, work, base=self.final_tree)
            env = dict(os.environ, GRADE_WORK=work, GRADE_OUT=out)
            r = subprocess.run([self.cmd, self.run, self.task], env=env, capture_output=True, text=True)
            try:
                status = json.load(open(os.path.join(out, 'result.json')))['status'] if r.returncode == 0 else None
            except (OSError, ValueError, KeyError):
                status = None
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(out, ignore_errors=True)
        if status not in ('RESOLVED', 'FAILED'):
            raise Inconclusive('tree %s: grader exit %d\n%s' % (tree[:12], r.returncode, (r.stderr or r.stdout)[-2000:]))
        return status == 'RESOLVED'

    def many(self, trees, jobs=1):
        todo = [t for t in dict.fromkeys(trees) if t not in self.cache]
        with concurrent.futures.ThreadPoolExecutor(max(1, jobs)) as ex:
            for tree, green in zip(todo, ex.map(self._grade, todo)):
                self.cache[tree] = green
                self.graded += 1
        return [self.cache[t] for t in trees]

    def __call__(self, tree):
        return self.many([tree])[0]


def first_green(run, task, jobs=1, bisect=False):
    res_path = os.path.join(run, 'result.json')
    res = json.load(open(res_path))
    if res.get('status') not in ('RESOLVED', 'FAILED'):
        raise Inconclusive('run has no RESOLVED/FAILED grade')
    snaps = snapshot.load(run)
    if not snaps or snaps[0][0] != 0:
        raise Inconclusive('no snapshots (the run was not recorded with BENCH_RECORD=1)')
    if snaps[-1][0] != 'final':
        raise Inconclusive('no final snapshot (bin/run.sh takes it when the session ends)')
    final_tree = snaps[-1][2]
    if snapshot.current_tree(run) != final_tree:
        raise Inconclusive('RUN/work changed after the run; its grade no longer matches the final snapshot')
    t0 = snapshot.t0(run)
    trees = distinct(snaps)
    m = len(trees) - 1
    final_green = res['status'] == 'RESOLVED'
    scratch = tempfile.mkdtemp(prefix='first-green-', dir=run)
    grade = Grader(run, task, scratch, final_tree)
    grade.cache[trees[m][0]] = final_green           # the run's own grade
    grade.cache.setdefault(trees[0][0], False)       # seq 0: the task tree, red by bin/setup.sh --check
    caveats = []
    try:
        g = None
        if m == 0:
            method = 'none'
            if final_green:
                caveats.append('green, but no tracked file changed: the fix is only in ignored files')
        elif not bisect:
            method = 'all'
            verdicts = grade.many([t for t, _, _ in trees], jobs)
            g = next((i for i, v in enumerate(verdicts) if v), None)
        elif final_green:
            method = 'bisect'
            lo, hi = 0, m
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if grade(trees[mid][0]):
                    hi = mid
                else:
                    lo = mid
            g = hi
        else:
            method = 'bisect'
            g = next((i for i in range(1, m) if grade(trees[i][0])), None)
        stayed = None
        if g is not None:
            if g == 0 or grade(trees[g - 1][0]):
                raise Inconclusive('snapshot before the first green one is not red')
            stayed = all(grade.many([t for t, _, _ in trees[g + 1:]], jobs))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    changed = sorted({p for a, b in zip(trees, trees[1:]) for p in snapshot.changed_paths(run, a[0], b[0])})
    if any(os.path.basename(p) == '.gitignore' for p in changed):
        caveats.append('.gitignore changed during the run: early trees are replayed with ignored files from the end')
    deps = [p for p in changed if os.path.basename(p) in DEP_FILES]
    if deps:
        caveats.append('dependency files changed during the run (%s): early trees are graded with the final '
                       'dependencies' % ', '.join(deps[:5]))
    out = {'first_green_s': None, 'first_green_request': None, 'polish_s': None, 'stayed_green': stayed,
           'first_green_grades': grade.graded, 'first_green_method': method}
    if g is not None:
        tree, seq, t = trees[g]
        out['first_green_s'] = max(0, round(t - t0))
        out['first_green_request'] = seq if isinstance(seq, int) else None
        if res.get('duration_s') is not None:
            out['polish_s'] = max(0, res['duration_s'] - out['first_green_s'])
    res.pop('first_green_caveat', None)
    if caveats:
        out['first_green_caveat'] = '; '.join(caveats)
    detail = {'snapshots': len(snaps), 'distinct_trees': len(trees),
              'trees': [{'seq': seq, 't_s': round(t - t0), 'tree': tree,
                         'green': grade.cache.get(tree)} for tree, seq, t in trees]}
    json.dump(dict(out, **detail), open(os.path.join(run, 'first-green.json'), 'w'), indent=2)
    res.update(out)
    json.dump(res, open(res_path, 'w'), indent=2)
    return out


if __name__ == '__main__':
    args, jobs, bisect = [], 1, False
    it = iter(sys.argv[1:])
    for a in it:
        if a == '--bisect':
            bisect = True
        elif a == '--jobs':
            jobs = int(next(it))
        else:
            args.append(a)
    if len(args) != 2:
        sys.exit(__doc__)
    try:
        print(json.dumps(first_green(os.path.realpath(args[0]), os.path.realpath(args[1]), jobs, bisect)))
    except Inconclusive as e:
        sys.exit('[first-green] INCONCLUSIVE: %s' % e)
