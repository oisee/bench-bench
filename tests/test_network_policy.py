#!/usr/bin/env python3
"""Network policy of the native ABAP protocol (BENCH_NO_WEB=1) and the contamination check.
Run: python3 tests/test_network_policy.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / 'bin'

PRISTINE = '''CLASS zcl_demo IMPLEMENTATION.
  METHOD prove_path_exists.
    SPLIT iv_path AT '/' INTO TABLE lt_path.
    DELETE lt_path WHERE table_line IS INITIAL.
    LOOP AT lt_path INTO lv_seg.
      READ TABLE mt_json_tree INTO ls_node WITH KEY path = lv_cur name = lv_seg.
      IF sy-subrc <> 0.
        ls_node-path = lv_cur. ls_node-name = lv_seg. ls_node-type = zif_ajson_types=>node_type-object.
        INSERT ls_node INTO TABLE mt_json_tree.
      ENDIF.
      lv_cur = lv_cur && lv_seg && '/'.
    ENDLOOP.
  ENDMETHOD.
ENDCLASS.
'''
SHORT = "    DELETE lt_path WHERE table_line IS INITIAL."
LONG_START = PRISTINE.index("    LOOP AT lt_path")
LONG_END = PRISTINE.index("    ENDLOOP.") + len("    ENDLOOP.")


def claude_session(run, results):
    """A Claude Code session log with WebFetch/Bash calls and their results: [(tool, input, text, is_error)]."""
    d = os.path.join(run, 'home', '.claude', 'projects', 'x')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 's.jsonl'), 'w') as f:
        for i, (tool, inp, text, err) in enumerate(results):
            f.write(json.dumps({'message': {'content': [{'type': 'tool_use', 'id': 't%d' % i, 'name': tool, 'input': inp}]}}) + '\n')
            f.write(json.dumps({'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't%d' % i, 'content': text,
                                                         'is_error': err}]}}) + '\n')


class Contamination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ct-')
        self.root = os.path.join(self.tmp, 'pkg')
        pr = os.path.join(self.root, 'private', 'hidden', 'demo-l0-line-001', '.grader', 'pristine', 'src')
        os.makedirs(pr)
        open(os.path.join(pr, 'zcl_demo.clas.abap'), 'w').write(PRISTINE)
        os.makedirs(os.path.join(self.root, 'private', 'abap-tasks'))
        b = PRISTINE.encode()
        tasks = [dict(id='demo-l0-line-001', file='src/zcl_demo.clas.abap',
                      hole_span=dict(start=b.index(SHORT.strip().encode()), end=b.index(SHORT.strip().encode()) + len(SHORT.strip()))),
                 dict(id='demo-l1-method-002', file='src/zcl_demo.clas.abap',
                      hole_span=dict(start=len(PRISTINE[:LONG_START].encode()), end=len(PRISTINE[:LONG_END].encode())))]
        shutil.copytree(os.path.join(self.root, 'private', 'hidden', 'demo-l0-line-001'),
                        os.path.join(self.root, 'private', 'hidden', 'demo-l1-method-002'))
        with open(os.path.join(self.root, 'private', 'abap-tasks', 'tasks.jsonl'), 'w') as f:
            for t in tasks:
                f.write(json.dumps(t) + '\n')
        for tid in ('demo-l0-line-001', 'demo-l1-method-002'):
            os.makedirs(os.path.join(self.tmp, 'tasks', tid))
            open(os.path.join(self.tmp, 'tasks', tid, 'task.env'), 'w').write('ABAP_TASKS=%s\n' % self.root)
        self.run_dir = os.path.join(self.tmp, 'run')
        os.makedirs(self.run_dir)
        open(os.path.join(self.run_dir, 'hosts'), 'w').write('0.0.0.0 raw.githubusercontent.com\n')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def check(self, tid):
        r = subprocess.run([sys.executable, '-I', str(BIN / 'contamination.py'), self.run_dir, os.path.join(self.tmp, 'tasks', tid)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_fetched_upstream_file_is_contaminated(self):
        claude_session(self.run_dir, [('WebFetch', {'url': 'https://example.org/zcl_demo.clas.abap'}, PRISTINE, False)])
        self.assertTrue(self.check('demo-l0-line-001')['contaminated'])
        self.assertTrue(self.check('demo-l1-method-002')['contaminated'])

    def test_long_body_reformatted_still_matches(self):
        mangled = PRISTINE[LONG_START:LONG_END].upper().replace('  ', ' ').replace('\n', '\n\n')
        claude_session(self.run_dir, [('Bash', {'command': 'curl -s https://mirror.example.org/x.abap'}, mangled, False)])
        self.assertTrue(self.check('demo-l1-method-002')['contaminated'])

    def test_docs_with_the_same_short_line_is_clean(self):
        docs = 'Example from the keyword docs:\n  DELETE lt_path WHERE table_line IS INITIAL.\n  " removes empty rows'
        claude_session(self.run_dir, [('WebFetch', {'url': 'https://help.sap.com/doc/abapdocu'}, docs, False)])
        c = self.check('demo-l0-line-001')
        self.assertFalse(c['contaminated'])
        self.assertEqual(c['hosts_outside_allowlist'], [])

    def test_unrelated_fetch_is_clean_but_listed(self):
        claude_session(self.run_dir, [('WebFetch', {'url': 'https://example.org/page'}, 'nothing related here', False)])
        c = self.check('demo-l1-method-002')
        self.assertFalse(c['contaminated'])
        self.assertEqual(c['hosts_outside_allowlist'], ['example.org'])

    def test_local_file_reads_do_not_count(self):
        claude_session(self.run_dir, [('Read', {'file_path': '/work/src/zcl_demo.clas.abap'}, PRISTINE, False)])
        self.assertFalse(self.check('demo-l1-method-002')['contaminated'])

    def test_provider_side_search_with_results_is_contaminated(self):
        ev = {'type': 'item.completed', 'item': {'type': 'web_search', 'query': 'q', 'action': {'query': 'q'},
                                                 'results': [{'url': 'https://github.com/x/y'}]}}
        open(os.path.join(self.run_dir, 'agent.out'), 'w').write(json.dumps(ev) + '\n')
        self.assertTrue(self.check('demo-l0-line-001')['contaminated'])
        ev['item']['results'] = []
        open(os.path.join(self.run_dir, 'agent.out'), 'w').write(json.dumps(ev) + '\n')
        self.assertFalse(self.check('demo-l0-line-001')['contaminated'])


class SandboxNoWeb(unittest.TestCase):
    """bin/sandbox.sh with BENCH_NO_WEB=1, driven by fake harnesses that report what they were given."""

    def setUp(self):
        if not shutil.which('bwrap'):
            self.skipTest('bwrap not installed')
        self.tmp = tempfile.mkdtemp(prefix='nw-')
        self.fake = os.path.join(self.tmp, 'fakebin')
        os.makedirs(self.fake)
        self.run_dir = os.path.join(self.tmp, 'run')
        os.makedirs(os.path.join(self.run_dir, 'work'))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake_harness(self, name, script):
        p = os.path.join(self.fake, name)
        open(p, 'w').write('#!/bin/sh\n' + script)
        os.chmod(p, 0o755)

    def sandbox(self, harness, provider, model='m'):
        env = dict(os.environ, BENCH_NO_WEB='1', CUSTOM_BASE_URL='http://127.0.0.1:9/v1')
        env['PATH'] = self.fake + ':' + env['PATH']
        r = subprocess.run([str(BIN / 'sandbox.sh'), '--run', self.run_dir, '--harness', harness, '--provider', provider,
                            '--model', model, '--effort', 'high', '--', '-p', 'hi'], env=env, capture_output=True, text=True, timeout=120)
        out = os.path.join(self.run_dir, 'work', 'probe.txt')
        if not os.path.exists(out):
            self.skipTest('sandbox did not start here: ' + r.stderr[-300:])
        return open(out).read()

    def test_claude_web_tools_off(self):
        self.fake_harness('claude', 'echo "$@" > /work/probe.txt; cat "$HOME/.claude/settings.json" >> /work/probe.txt\n')
        out = self.sandbox('claude', 'custom')
        self.assertIn('--disallowedTools WebFetch,WebSearch', out)
        self.assertIn('"deny": ["WebFetch", "WebSearch"]', out)

    def test_codex_web_search_disabled_at_top_level(self):
        self.fake_harness('codex', 'cat "$HOME/.codex/config.toml" > /work/probe.txt\n')
        out = self.sandbox('codex', 'custom')
        self.assertEqual(out.splitlines()[0], 'web_search = "disabled"')

    def test_upstream_fetch_is_blocked(self):
        self.fake_harness('pi', 'for h in raw.githubusercontent.com github.com gitlab.com sourcegraph.com; do '
                                'getent hosts $h | head -1; done > /work/probe.txt\n')
        out = self.sandbox('pi', 'custom')
        for line in out.splitlines():
            self.assertTrue(line.startswith('0.0.0.0') or line.startswith('::'), line)
        self.assertEqual(len(out.splitlines()), 4)


if __name__ == '__main__':
    unittest.main()
