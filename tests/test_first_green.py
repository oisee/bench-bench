#!/usr/bin/env python3
"""Tests for bin/snapshot.py, bin/first-green.py and the record proxy's per-request snapshots.

A fake run: RUN/work is a git repo with a 'baseline' commit, and a file 'state' whose content the fake grader
reads ('green' passes, anything else fails). Snapshots are taken with chosen times, so the expected
first_green_s / polish_s are exact. Run: python3 tests/test_first_green.py
"""
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import snapshot  # noqa: E402

spec = importlib.util.spec_from_file_location('first_green', BIN / 'first-green.py')
fg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fg)

FAKE_GRADER = r'''#!/usr/bin/env bash
# fake bin/grade.sh: RESOLVED iff $GRADE_WORK/state is "green"; counts calls; fails if hidden-test overlay leaked
set -u
echo x >> "$(dirname "$0")/calls"
mkdir -p "$GRADE_OUT"
[ -e "$GRADE_WORK/node_modules/dep" ] || { echo "ignored files not carried over" >&2; exit 2; }
s=FAILED; [ "$(cat "$GRADE_WORK/state" 2>/dev/null)" = green ] && s=RESOLVED
echo "{\"status\": \"$s\"}" > "$GRADE_OUT/result.json"
'''


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fg-')
        self.run_dir = os.path.join(self.tmp, 'run')
        self.work = os.path.join(self.run_dir, 'work')
        os.makedirs(self.work)
        self.write('state', 'red')
        self.write('.gitignore', 'node_modules/\n')
        self.write('node_modules/dep', 'x')
        sh(self.work, 'git', 'init', '-q')
        sh(self.work, 'git', 'add', '-A')
        sh(self.work, 'git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base')
        sh(self.work, 'git', 'tag', 'baseline')
        self.grader = os.path.join(self.tmp, 'grader.sh')
        open(self.grader, 'w').write(FAKE_GRADER)
        os.chmod(self.grader, 0o755)
        os.environ['GRADER'] = self.grader
        self.t = 1000.0
        snapshot.take(self.run_dir, 0, now=self.t - 1)              # the proxy starts just before the agent
        open(os.path.join(snapshot.snap_dir(self.run_dir), 't0'), 'w').write('1000')
        self.seq = 0

    def tearDown(self):
        os.environ.pop('GRADER', None)
        shutil.rmtree(self.tmp)

    def write(self, name, text):
        p = os.path.join(self.work, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write(text)

    def step(self, state, dt=60, extra=None):
        """the agent changes the tree, then sends its next request after dt seconds"""
        self.write('state', state)
        if extra:
            self.write(*extra)
        self.seq += 1
        self.t += dt
        snapshot.take(self.run_dir, self.seq, now=self.t)

    def finish(self, status, duration, final=True):
        """what bin/run.sh and finalize.sh do when the session ends"""
        if final:
            snapshot.take(self.run_dir, 'final', now=1000 + duration)
        json.dump({'status': status, 'duration_s': duration}, open(os.path.join(self.run_dir, 'result.json'), 'w'))

    def calls(self):
        p = os.path.join(self.tmp, 'calls')
        return len(open(p).read().split()) if os.path.exists(p) else 0

    def first_green(self, **kw):
        return fg.first_green(self.run_dir, self.tmp, **kw)


class TestFirstGreen(Base):
    def test_green_then_polish(self):
        for s in ['red', 'red', 'red', 'green', 'green', 'green', 'green']:
            self.step(s, extra=('README.md', s + str(self.seq)))          # polishing changes the tree too
        self.finish('RESOLVED', 600)
        r = self.first_green()
        self.assertEqual(r['first_green_request'], 4)
        self.assertEqual(r['first_green_s'], 240)
        self.assertEqual(r['polish_s'], 360)
        self.assertIs(r['stayed_green'], True)
        res = json.load(open(os.path.join(self.run_dir, 'result.json')))
        self.assertEqual(res['first_green_s'], 240)
        self.assertEqual(res['status'], 'RESOLVED')

    def test_green_then_broken_ends_red(self):                       # the case that must flip stayed_green
        for s in ['red', 'green', 'green', 'broken', 'broken']:
            self.step(s, extra=('README.md', str(self.seq)))
        self.finish('FAILED', 400)
        for bisect in (False, True):
            r = self.first_green(bisect=bisect)
            self.assertEqual(r['first_green_request'], 2)
            self.assertIs(r['stayed_green'], False)

    def test_early_green_spell_is_found(self):                      # red, green, broken x3, green x2
        for s in ['green', 'broken', 'broken2', 'broken3', 'green', 'green2']:
            self.step(s)
        self.finish('RESOLVED', 400)
        r = self.first_green()
        self.assertEqual((r['first_green_request'], r['first_green_s'], r['stayed_green']), (1, 60, False))
        self.assertEqual(r['first_green_method'], 'all')
        r = self.first_green(bisect=True)                           # documented limit: bisection finds a later boundary
        self.assertEqual(r['first_green_method'], 'bisect')
        self.assertEqual(r['first_green_request'], 5)

    def test_green_broken_green_again(self):
        for s in ['red', 'green', 'broken', 'green', 'green']:
            self.step(s, extra=('README.md', str(self.seq)))
        self.finish('RESOLVED', 400)
        r = self.first_green()
        self.assertIs(r['stayed_green'], False)
        self.assertIsNotNone(r['first_green_request'])

    def test_bisect_still_checks_later_trees(self):
        for s in ['red', 'green', 'broken', 'green']:
            self.step(s, extra=('README.md', str(self.seq)))
        self.finish('RESOLVED', 300)
        r = self.first_green(bisect=True)
        self.assertIs(r['stayed_green'], False)

    def test_never_green(self):
        for s in ['red', 'red2', 'red3']:
            self.step(s)
        self.finish('FAILED', 200)
        r = self.first_green()
        self.assertIsNone(r['first_green_s'])
        self.assertIsNone(r['stayed_green'])

    def test_identical_trees_graded_once_and_bisection_is_short(self):
        states = ['red'] * 3 + ['red%d' % i for i in range(30)] + ['green'] * 3
        for i, s in enumerate(states):
            self.step(s, extra=('README.md', 'same') if i % 2 else None)
        self.finish('RESOLVED', 3000)
        r = self.first_green(bisect=True)
        self.assertEqual(r['first_green_request'], 34)
        self.assertLessEqual(self.calls(), 9)                         # log2(~33 distinct trees) + the 2 trees after
        self.assertEqual(r['first_green_grades'], self.calls())

    def test_green_only_at_the_end(self):                             # last edit came after the last request
        self.step('red')
        self.write('state', 'green')
        self.finish('RESOLVED', 100)
        r = self.first_green()
        self.assertIsNone(r['first_green_request'])
        self.assertEqual(r['first_green_s'], 100)
        self.assertEqual(r['polish_s'], 0)

    def test_inconclusive_grade_is_an_error(self):
        self.step('red2')
        self.step('red3')
        self.step('green')
        self.finish('RESOLVED', 100)
        shutil.rmtree(os.path.join(self.work, 'node_modules'))         # fake grader refuses without it
        with self.assertRaises(fg.Inconclusive):
            self.first_green()

    def test_jobs(self):
        for s in ['red2', 'red3', 'green', 'green2']:
            self.step(s)
        self.finish('RESOLVED', 300)
        r = fg.first_green(self.run_dir, self.tmp, jobs=4)
        self.assertEqual((r['first_green_request'], r['first_green_grades']), (3, 3))

    def test_no_final_snapshot(self):
        self.step('green')
        self.finish('RESOLVED', 100, final=False)
        with self.assertRaisesRegex(fg.Inconclusive, 'final'):
            self.first_green()

    def test_work_changed_after_run(self):
        self.step('green')
        self.finish('RESOLVED', 100)
        self.write('state', 'red-again')
        with self.assertRaisesRegex(fg.Inconclusive, 'changed after'):
            self.first_green()

    def test_caveat_dependency_change(self):
        self.step('red2')
        self.step('green', extra=('package-lock.json', '{}'))
        self.finish('RESOLVED', 200)
        r = self.first_green()
        self.assertIn('package-lock.json', r['first_green_caveat'])

    def test_caveat_reverted_dependency_change(self):                # changed, installed, then restored
        self.step('red2', extra=('package.json', '{"dependencies": {"x": "1"}}'))
        self.step('red3', extra=('package.json', '{}'))
        os.remove(os.path.join(self.work, 'package.json'))
        self.step('green')
        self.finish('RESOLVED', 200)
        self.assertIn('package.json', self.first_green()['first_green_caveat'])

    def test_caveat_gitignore_change(self):
        self.step('red2')
        self.step('green', extra=('.gitignore', 'node_modules/\nbuild/\n'))
        self.finish('RESOLVED', 200)
        self.assertIn('.gitignore', self.first_green()['first_green_caveat'])

    def test_no_caveat_normally(self):
        self.step('red2')
        self.step('green')
        self.finish('RESOLVED', 200)
        self.assertNotIn('first_green_caveat', self.first_green())

    def test_fix_only_in_ignored_files(self):
        self.write('node_modules/dep2', 'y')
        self.finish('RESOLVED', 50)
        r = self.first_green()
        self.assertIsNone(r['first_green_s'])
        self.assertIn('ignored', r['first_green_caveat'])

    def test_no_snapshots(self):
        shutil.rmtree(snapshot.snap_dir(self.run_dir))
        self.finish('RESOLVED', 10)
        with self.assertRaises(fg.Inconclusive):
            self.first_green()


class TestSnapshot(Base):
    def test_agent_tree_untouched(self):
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.work, capture_output=True, text=True).stdout
        self.step('green', extra=('new.txt', 'n'))
        self.assertEqual(subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.work, capture_output=True, text=True).stdout, head)
        self.assertEqual(subprocess.run(['git', 'stash', 'list'], cwd=self.work, capture_output=True, text=True).stdout, '')
        refs = subprocess.run(['git', 'for-each-ref'], cwd=self.work, capture_output=True, text=True).stdout
        self.assertNotIn('snapshots', refs)
        self.assertFalse(os.path.relpath(snapshot.snap_dir(self.run_dir), self.work).startswith('work'))

    def test_materialize_removes_and_restores(self):
        first = snapshot.load(self.run_dir)[0][2]
        self.step('green', extra=('added.txt', 'a'))
        os.remove(os.path.join(self.work, '.gitignore'))
        dst = os.path.join(self.tmp, 'm')
        snapshot.materialize(self.run_dir, first, dst)
        self.assertEqual(open(os.path.join(dst, 'state')).read(), 'red')
        self.assertFalse(os.path.exists(os.path.join(dst, 'added.txt')))
        self.assertTrue(os.path.exists(os.path.join(dst, '.gitignore')))
        self.assertTrue(os.path.exists(os.path.join(dst, 'node_modules', 'dep')))
        self.assertEqual(open(os.path.join(self.work, 'state')).read(), 'green')   # source tree unchanged


class TestProxySnapshots(Base):
    def test_snapshot_per_request(self):
        class Up(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['content-length']))
                self.send_response(200)
                self.send_header('content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *a):
                pass
        up = http.server.HTTPServer(('127.0.0.1', 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        shutil.rmtree(snapshot.snap_dir(self.run_dir))
        env = dict(os.environ, BENCH_SNAPSHOT_RUN=self.run_dir)
        p = subprocess.Popen([sys.executable, '-I', str(BIN / 'record-proxy.py'), 'http://127.0.0.1:%d' % up.server_address[1],
                              os.path.join(self.run_dir, 'traffic.jsonl')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = int(p.stdout.readline())
            for state in ('a', 'b'):
                self.write('state', state)
                urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/v1/messages' % port, b'{}', {'content-type': 'application/json'})).read()
        finally:
            p.terminate()
            err = p.communicate()[1]
            up.shutdown()
        self.assertEqual(err, '')
        snaps = snapshot.load(self.run_dir)
        self.assertEqual([s[0] for s in snaps], [0, 1, 2])
        self.assertEqual(len({s[2] for s in snaps}), 3)

    def test_concurrent_requests_keep_order(self):
        class Up(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['content-length']))
                time.sleep(0.2)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *a):
                pass
        up = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        shutil.rmtree(snapshot.snap_dir(self.run_dir))
        env = dict(os.environ, BENCH_SNAPSHOT_RUN=self.run_dir)
        p = subprocess.Popen([sys.executable, '-I', str(BIN / 'record-proxy.py'), 'http://127.0.0.1:%d' % up.server_address[1],
                              os.path.join(self.run_dir, 'traffic.jsonl')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = int(p.stdout.readline())
            def req(i):
                self.write('f%d' % i, str(i))
                urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/v1/messages' % port, b'{}', {'content-type': 'application/json'})).read()
            ts = [threading.Thread(target=req, args=(i,)) for i in range(8)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        finally:
            p.terminate()
            err = p.communicate()[1]
            up.shutdown()
        self.assertEqual(err, '')
        seqs = [s[0] for s in snapshot.load(self.run_dir)]
        self.assertEqual(seqs, list(range(9)))                       # snapshot order = request numbering
        recs = [json.loads(l) for l in open(os.path.join(self.run_dir, 'traffic.jsonl'))]
        self.assertEqual(sorted(r['seq'] for r in recs), list(range(1, 9)))


if __name__ == '__main__':
    unittest.main()
