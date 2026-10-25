#!/usr/bin/env python3
"""Tests for bin/abap-tasks.sh (materializing the native ABAP task package as bench-bench tasks) on a fake package.
The end-to-end check (PREPARE, hidden overlay, /tool, /agent-tool, gold PASS / hole FAIL) is
`BENCH_TASKS_DIR=tasks-abap bin/setup.sh --check` on the smoke tier. Run: python3 tests/test_abap_adapter.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / 'bin'
PREP = ('set -e; task_stage=$(mktemp -d); "$ABAP_TASKS/private/abap-tasks/bin/node" '
        '"$ABAP_TASKS/private/abap-tasks/bin/make-workspace.mjs" {id} "$task_stage/w"; '
        'find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +; cp -a "$task_stage/w"/. .; rm -rf -- "$task_stage"')


def git(cwd, *a):
    return subprocess.run(['git', '-C', cwd, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True,
                          capture_output=True, text=True).stdout.strip()


class Adapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='abap-')
        self.bench = os.path.join(self.tmp, 'bench')
        os.makedirs(os.path.join(self.bench, 'bin'))
        shutil.copy(BIN / 'abap-tasks.sh', os.path.join(self.bench, 'bin'))
        self.repo = os.path.join(self.tmp, 'repo')
        for i in ('abapgit-l0-block-001', 'zvdb-l1-method-002'):
            d = os.path.join(self.repo, 'bench-bench', i)
            os.makedirs(d)
            open(os.path.join(d, 'task.env'), 'w').write(
                "URL='https://github.com/abapGit/abapGit.git'\nBASE='abc'\nMERGE='abc'\nPREPARE=%s\nHIDDEN=''\n"
                "HIDDEN_DIR='hidden/%s/.grader'\nSETUP=''\nF2P='/tool/bin/node /tool/bin/grade-tree.mjs /work'\n"
                "TIME_MIN='15'\n" % ("'" + PREP.format(id=i) + "'", i))
            open(os.path.join(d, 'brief.md'), 'w').write('Restore the block.\n')
        git(self.repo, 'init', '-q'); git(self.repo, 'add', '-A'); git(self.repo, 'commit', '-qm', 'd')
        self.sha = git(self.repo, 'rev-parse', 'HEAD')
        self.root = os.path.join(self.tmp, 'pkg', self.sha)
        for d in ('agent', 'grader-tool', 'private/abap-tasks', 'private/hidden'):
            os.makedirs(os.path.join(self.root, d))
        ta = os.path.join(self.bench, 'tasks-abap', 'tiers')
        os.makedirs(ta)
        open(os.path.join(ta, 'smoke.txt'), 'w').write('abapgit-l0-block-001\nzvdb-l1-method-002\n')
        self.pin(self.sha)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pin(self, sha):
        open(os.path.join(self.bench, 'tasks-abap', 'PACKAGE'), 'w').write('repo=x\nsha=%s\n' % sha)

    def run_it(self, *args, root=None):
        env = dict(os.environ, ABAP_TASKS=root or self.root, ABAP_TASKS_REPO=self.repo)
        return subprocess.run(['bash', os.path.join(self.bench, 'bin', 'abap-tasks.sh'), *args], env=env,
                              capture_output=True, text=True)

    def env_of(self, i):
        p = os.path.join(self.bench, 'tasks-abap', i, 'task.env')
        out = subprocess.run(['bash', '-c', 'set -u; source "$1"; printf "%s\\n" "$REPO" "$LEVEL" "[$P2P]" "[$STRIP]" '
                              '"$AGENT_TOOL" "$GRADER_TOOL" "$HIDDEN_ROOT" "$HIDDEN_DIR" "$GOLD_PREPARE" "$PREPARE"', '_', p],
                             capture_output=True, text=True, check=True).stdout.split('\n')
        return out

    def test_smoke_tier(self):
        r = self.run_it('smoke')
        self.assertEqual(r.returncode, 0, r.stderr)
        v = self.env_of('abapgit-l0-block-001')
        self.assertEqual(v[:4], ['abap-abapGit', 'ABAP-L0', '[]', '[]'])
        self.assertEqual(v[4:7], [self.root + '/agent', self.root + '/grader-tool', self.root + '/private'])
        self.assertIn('"$task_stage/w" --solution;', v[8])
        self.assertNotIn('--solution', v[9])
        self.assertEqual(self.env_of('zvdb-l1-method-002')[1], 'ABAP-L1')
        self.assertTrue(os.path.exists(os.path.join(self.bench, 'tasks-abap', 'zvdb-l1-method-002', 'brief.md')))

    def test_refuses_unknown_pin(self):
        self.pin('0' * 40)
        r = self.run_it('smoke')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no commit', r.stderr)

    def test_working_tree_edits_are_ignored(self):              # descriptors come from the pinned commit's objects
        p = os.path.join(self.repo, 'bench-bench', 'abapgit-l0-block-001', 'task.env')
        open(p, 'a').write("touch %s/PWNED\n" % self.tmp)
        r = self.run_it('smoke')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'PWNED')))
        self.assertNotIn('PWNED', open(os.path.join(self.bench, 'tasks-abap', 'abapgit-l0-block-001', 'task.env')).read())

    def test_manifest_checked(self):
        open(os.path.join(self.root, 'agent', 'f'), 'w').write('x')
        subprocess.run('sha256sum agent/f > MANIFEST.sha256', shell=True, cwd=self.root, check=True)
        self.assertEqual(self.run_it('smoke').returncode, 0)
        open(os.path.join(self.root, 'agent', 'f'), 'w').write('changed')
        r = self.run_it('smoke')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('MANIFEST', r.stderr)

    def test_tier_and_package_recorded(self):
        self.assertEqual(self.run_it('smoke').returncode, 0)
        out = subprocess.run(['bash', '-c', 'source "$1"; echo "$ABAP_PACKAGE $ABAP_TIER"', '_',
                              os.path.join(self.bench, 'tasks-abap', 'zvdb-l1-method-002', 'task.env')],
                             capture_output=True, text=True).stdout.split()
        self.assertEqual(out, [self.sha, 'smoke'])
        self.assertEqual(self.run_it('zvdb-l1-method-002').returncode, 0)
        out = subprocess.run(['bash', '-c', 'source "$1"; echo "$ABAP_TIER"', '_',
                              os.path.join(self.bench, 'tasks-abap', 'zvdb-l1-method-002', 'task.env')],
                             capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ['ids'])


    def test_refuses_package_of_another_commit(self):
        other = os.path.join(self.tmp, 'pkg', 'f' * 40)
        shutil.copytree(self.root, other)
        r = self.run_it('smoke', root=other)
        self.assertNotEqual(r.returncode, 0)

    def test_refuses_bad_or_unknown_id(self):
        self.assertNotEqual(self.run_it('../etc').returncode, 0)
        self.assertNotEqual(self.run_it('nosuch-l0-x-001').returncode, 0)


class GradeHiddenDir(unittest.TestCase):
    """bin/grade.sh with HIDDEN_DIR: the hidden part reaches the grade copy only, replaces what the agent left there,
    and a candidate tree with a symlink is rejected without running the tests."""

    def setUp(self):
        if not shutil.which('bwrap'):
            self.skipTest('bwrap not installed')
        self.tmp = tempfile.mkdtemp(prefix='gh-')
        self.task = os.path.join(self.tmp, 'tasks', 'zvdb-l0-line-001')
        os.makedirs(self.task)
        self.hidden = os.path.join(self.tmp, 'private', 'hidden', 'zvdb-l0-line-001', '.grader')
        os.makedirs(self.hidden)
        open(os.path.join(self.hidden, 'oracle'), 'w').write('42\n')
        open(os.path.join(self.task, 'task.env'), 'w').write(
            "REPO=none\nBASE=x\nMERGE=x\nHIDDEN=''\nHIDDEN_DIR='hidden/zvdb-l0-line-001/.grader'\nHIDDEN_ROOT=%s\n"
            "F2P='test \"$(cat .grader/oracle)\" = \"$(cat answer)\" && test ! -e .grader/planted'\nP2P=''\nTIME_MIN=15\nLEVEL=ABAP-L0\n"
            % os.path.join(self.tmp, 'private'))
        self.run_dir = os.path.join(self.tmp, 'run')
        self.work = os.path.join(self.run_dir, 'work')
        os.makedirs(self.work)
        git(self.work, 'init', '-q'); open(os.path.join(self.work, 'answer'), 'w').write('0\n')
        git(self.work, 'add', '-A'); git(self.work, 'commit', '-qm', 'b'); git(self.work, 'tag', 'baseline')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def grade(self):
        r = subprocess.run([str(BIN / 'grade.sh'), self.run_dir, self.task], capture_output=True, text=True)
        import json
        return json.load(open(os.path.join(self.run_dir, 'result.json'))), r

    def test_pass_and_fail(self):
        res, r = self.grade()
        self.assertEqual(res['status'], 'FAILED', r.stderr)
        open(os.path.join(self.work, 'answer'), 'w').write('42\n')
        res, r = self.grade()
        self.assertEqual(res['status'], 'RESOLVED', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.work, '.grader')))     # never in the agent's tree

    def test_agent_planted_grader_dir_is_replaced(self):
        os.makedirs(os.path.join(self.work, '.grader'))
        open(os.path.join(self.work, '.grader', 'planted'), 'w').write('x')       # the grader fails if this survives
        open(os.path.join(self.work, 'answer'), 'w').write('42\n')
        res, r = self.grade()
        self.assertEqual(res['status'], 'RESOLVED', r.stderr)                     # the agent's .grader was replaced whole

    def test_symlink_rejected(self):
        os.remove(os.path.join(self.work, 'answer'))
        os.symlink('.grader/oracle', os.path.join(self.work, 'answer'))           # would read the hidden answer
        res, r = self.grade()
        self.assertEqual(res['status'], 'FAILED')
        self.assertIn('symlink', res.get('rejected', ''))
        self.assertEqual(res['f2p'], [])

if __name__ == '__main__':
    unittest.main()
