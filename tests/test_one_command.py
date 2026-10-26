"""One-command launcher plans and orchestration, without containers or model calls."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'


class OneCommand(unittest.TestCase):
    def args(self, agent='claude'):
        return ['--agent', agent, '--model', 'demo-model', '--api-base', 'https://example.org/v1',
                '--api-key-env', 'TEST_ENDPOINT_KEY']

    def test_dry_run_all_agents(self):
        for agent, api in [('claude', 'Anthropic-compatible'), ('codex', 'Responses'), ('pi', 'chat completions')]:
            env = dict(os.environ, TEST_ENDPOINT_KEY='test-value-never-print')
            r = subprocess.run([str(BIN / 'abap-bench'), *self.args(agent), '--dry-run', '--tier', 'ember',
                                '--no-internet', '--port', '8080'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('tier=smoke4 ABAP_ENV=3', r.stdout)
            self.assertIn('BENCH_EGRESS_DOCS=0', r.stdout)
            self.assertIn(api, r.stdout)
            self.assertIn('setup.sh --check', r.stdout)
            self.assertIn('live-page.py --port 8080', r.stdout)
            self.assertNotIn(env['TEST_ENDPOINT_KEY'], r.stdout + r.stderr)

    def test_bad_arguments(self):
        for extra in [['--port', '0'], ['--port', '65536'], ['--port', '-1'], ['--env', '0'],
                      ['--tier', 'missing'], ['--api-key-env', 'a;echo'], ['--agent', 'other'],
                      ['--model', 'x,y'], ['--model', 'x\ny'], ['--api-base', 'https://user:pass@example.org'],
                      ['--api-base', 'https://example.org/?key=x'], ['--port']]:
            r = subprocess.run([str(BIN / 'abap-bench'), *self.args(), '--dry-run', *extra],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, extra)

    def test_setup_gates_benchmark_and_isolated_matrix(self):
        for setup_rc in (0, 7):
            with self.subTest(setup_rc=setup_rc), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bench = root / 'bench'
                (bench / 'bin').mkdir(parents=True)
                shutil.copy(BIN / 'abap-bench', bench / 'bin')
                # Descriptor checkout stands in for a package mounted under a different name (/pkg).
                pkg = root / 'pkg'
                repo = pkg / 'repo'
                repo.mkdir(parents=True)
                subprocess.run(['git', 'init', '-q', str(repo)], check=True)
                subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                                'commit', '--allow-empty', '-qm', 'fixture'], check=True)
                sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
                (bench / 'matrix.yaml').write_text('original matrix\n')
                for name, script in {
                    'abap-tasks.sh': 'echo "materialize $* $ABAP_PACKAGE_SHA env=$ABAP_ENV" >> "$CALLS"\n'
                                     'mkdir -p "$BENCH_TASKS_DIR/demo-l0-task"\n'
                                     'touch "$BENCH_TASKS_DIR/demo-l0-task/task.env"\n',
                    'setup.sh': 'echo "setup $*" >> "$CALLS"\n'
                                'cat "$BENCH_MATRIX" >> "$CALLS"\n'
                                'echo "docs=$BENCH_EGRESS_DOCS tasks=$BENCH_TASKS_DIR" >> "$CALLS"\n'
                                'exit ' + str(setup_rc) + '\n',
                    'bench.sh': 'echo "bench $*" >> "$CALLS"\n'
                }.items():
                    path = bench / 'bin' / name
                    path.write_text('#!/bin/sh\n' + script)
                    path.chmod(0o755)
                # Fake server announces readiness, then waits for launcher cleanup.
                (bench / 'bin/live-page.py').write_text(
                    'import os, signal, time\nfrom pathlib import Path\n'
                    'mark = Path(os.environ["PAGE_MARK"])\nmark.write_text("started")\n'
                    'def stop(*args):\n    mark.write_text("stopped")\n    raise SystemExit(0)\n'
                    'signal.signal(signal.SIGTERM, stop)\nprint("ready: fixture", flush=True)\n'
                    'while True: time.sleep(0.1)\n')
                calls = root / 'calls'
                out = root / 'out'
                env = dict(os.environ, TEST_ENDPOINT_KEY='fake-test-key', CALLS=str(calls), PAGE_MARK=str(root / 'page-mark'))
                r = subprocess.run([str(bench / 'bin/abap-bench'), *self.args(), '--pkg', str(pkg),
                                    '--out', str(out), '--no-internet', '--port', '8080'], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode, setup_rc, r.stderr)
                log = calls.read_text()
                self.assertIn('materialize smoke4 ' + sha + ' env=3', log)
                self.assertIn('setup --check', log)
                self.assertEqual('bench one-command-claude-' in log, setup_rc == 0)
                self.assertIn('provider: custom', log)
                self.assertIn('docs=0', log)
                self.assertNotIn('fake-test-key', log)
                self.assertEqual((bench / 'matrix.yaml').read_text(), 'original matrix\n')
                self.assertEqual((bench / 'runs').resolve(), out)
                self.assertFalse(list((bench / '.cache').iterdir()))
                self.assertTrue((out / 'versions.txt').exists())
                self.assertEqual((root / 'page-mark').read_text(), 'stopped')
                import json
                queue = json.loads(next(out.glob('queue-*.json')).read_text())
                self.assertEqual(queue['tasks'], ['demo-l0-task'])

    def test_existing_runs_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            shutil.copy(BIN / 'abap-bench', root / 'bin')
            (root / 'runs').mkdir()
            (root / 'runs/keep').write_text('keep')
            repo = root / 'pkg/repo'
            repo.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t',
                            'commit', '--allow-empty', '-qm', 'fixture'], check=True)
            r = subprocess.run([str(root / 'bin/abap-bench'), *self.args(), '--pkg', str(repo.parent),
                                '--out', str(root / 'out')], env=dict(os.environ, TEST_ENDPOINT_KEY='fake'),
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((root / 'runs/keep').read_text(), 'keep')
