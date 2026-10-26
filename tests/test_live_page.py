"""Dashboard rendering and HTTP routing with filesystem fixtures and no model calls."""
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bin/live-page.py'
spec = importlib.util.spec_from_file_location('live_page', SCRIPT)
page = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page)


class LivePage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = self.root / 'demo-l0-task/exec-20261026-120000'
        self.run.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, value):
        (self.run / name).write_text(json.dumps(value))

    def request(self, path):
        handler = page.Handler.__new__(page.Handler)
        handler.server = SimpleNamespace(runs=self.root)
        handler.path = path
        handler.wfile = io.BytesIO()
        headers = {}
        handler.send_response = lambda status: headers.update(status=status)
        handler.send_header = lambda key, value: headers.update({key: value})
        handler.end_headers = lambda: None
        handler.send_error = lambda status: headers.update(status=status)
        handler.do_GET()
        return headers, handler.wfile.getvalue()

    def test_queued_running_verdict_and_duration(self):
        self.assertEqual(page.rows(self.root)[0]['status'], 'queued')
        self.put('meta.json', {'started_at': '2026-10-26T12:00:00Z'})
        row = page.rows(self.root, datetime(2026, 10, 26, 12, 1, tzinfo=timezone.utc))[0]
        self.assertEqual((row['status'], row['duration']), ('running', 60))
        self.put('result.json', {'status': 'RESOLVED', 'duration_s': 75})
        row = page.rows(self.root)[0]
        self.assertEqual((row['status'], row['duration']), ('RESOLVED', 75))
        self.put('meta.json', {'started_at': 'broken date'})
        self.assertEqual(page.rows(self.root)[0]['duration'], 75)

    def test_queue_before_any_run(self):
        (self.root / 'queue-exec.json').write_text(json.dumps({'executor': 'exec', 'tasks': ['demo-l0-task', 'later']}))
        rows = page.rows(self.root)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['task'], 'later')
        self.assertEqual(rows[1]['status'], 'queued')

    def test_partial_files_and_requests(self):
        (self.run / 'meta.json').write_text('{')
        (self.run / 'result.json').write_text('[]')
        (self.run / 'traffic.jsonl').write_text('{"seq":1}\n{"seq":2}\n{"seq":')
        self.assertEqual(page.rows(self.root)[0]['requests'], 2)
        self.assertEqual(page.build_state(self.root)['summary']['running'], 1)

    def test_html_and_json_served_without_injecting_run_data(self):
        self.put('result.json', {'status': '<script>alert(1)</script>', 'secret': 'private-value'})
        (self.run / 'live.txt').write_text('<script>transcript</script>')
        headers, body = self.request('/')
        self.assertEqual(headers['status'], 200)
        self.assertIn(b"fetch('/api/state')", body)
        self.assertIn(b'setTimeout(refresh,2000)', body)
        self.assertIn(b'prefers-color-scheme:dark', body)
        self.assertIn(b'@media(max-width:700px)', body)
        self.assertIn(b'textContent', body)
        self.assertNotIn(b'innerHTML', body)
        self.assertNotIn(b'alert(1)', body)
        headers, body = self.request('/api/state')
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertNotIn(b'private-value', body)
        self.assertEqual(json.loads(body)['tasks'][0]['status'], 'INCONCLUSIVE')
        headers, body = self.request('/api/log?task=demo-l0-task&run=exec-20261026-120000')
        self.assertEqual(json.loads(body)['text'], '<script>transcript</script>')
        headers, body = self.request('/live/demo-l0-task/exec-20261026-120000/live.txt')
        self.assertEqual(body, b'<script>transcript</script>')

    def test_tail_is_bounded(self):
        path = self.run / 'live.txt'
        path.write_text('\n'.join(str(i) + ' x' * 300 for i in range(1000)))
        text = page.tail(path)
        self.assertLessEqual(len(text.encode()), 65536)
        self.assertLessEqual(len(text.splitlines()), 200)
        self.assertTrue(text.splitlines()[-1].startswith('999 '))

    def test_traversal_and_private_files_are_not_served(self):
        self.put('meta.json', {'secret': 'unavailable'})
        for path in ['/live/../x/live.txt', '/live/%2e%2e/x/live.txt', '/live/a/b/meta.json',
                     '/meta.json', '/live/a/b/live.txt/extra', '/live/%00/b/live.txt']:
            self.assertEqual(self.request(path)[0]['status'], 404, path)
        (self.run / 'live.txt').symlink_to('/etc/hostname')
        self.assertEqual(self.request('/live/demo-l0-task/exec-20261026-120000/live.txt')[0]['status'], 404)
        self.assertIsNone(page.rows(self.root)[0]['live'])

    def test_empty_and_missing_root(self):
        self.assertEqual(page.build_state(self.root / 'missing')['summary']['total'], 0)

    def fixture(self, root, task, run, result, meta=None):
        directory = root / task / run
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'result.json').write_text(json.dumps(result))
        if meta:
            (directory / 'meta.json').write_text(json.dumps(meta))
        return directory

    def test_summary_metadata_and_latest_attempt(self):
        self.put('result.json', dict(status='FAILED', executor='exec', duration_s=10))
        current = self.fixture(self.root, 'demo-l0-task', 'exec-20261026-130000',
                               dict(status='RESOLVED', executor='exec', duration_s=25, contaminated=False),
                               dict(model='demo', abap_package='abc', abap_tier='smoke4',
                                    env_version='3', net_policy='egress-endpoint-only'))
        (current / 'patch.diff').write_text('--- a/test\n+++ b/test\n-old\n+new\n context\n')
        self.fixture(self.root, 'timeout', 'exec-20261026-130000',
                     dict(status='TIMEOUT', duration_s=50, executor='exec'))
        (self.root / 'queue-exec.json').write_text(json.dumps(dict(executor='exec', tasks=['later'])))
        state = page.build_state(self.root)
        s = state['summary']
        self.assertEqual((s['total'], s['solved'], s['failed'], s['queued'], s['agent_time']), (3, 1, 1, 1, 75))
        self.assertEqual((s['model'], s['abap_package'], s['abap_tier'], s['env_version']),
                         (['demo'], ['abc'], ['smoke4'], ['3']))
        row = state['tasks'][0]
        self.assertEqual((row['patch_lines'], row['contaminated'], row['net_policy']),
                         (2, False, 'egress-endpoint-only'))
        for filename in ('patch.diff', 'result.json'):
            path = '/live/demo-l0-task/exec-20261026-130000/' + filename
            headers, body = self.request(path)
            self.assertEqual(headers['status'], 200)
            self.assertEqual(body, (current / filename).read_bytes())

    def test_comparison_latest_valid_and_same_task_totals(self):
        self.put('result.json', dict(status='RESOLVED', executor='exec'))
        (self.root / 'queue-exec.json').write_text(json.dumps(dict(executor='exec', tasks=['demo-l0-task'])))
        self.fixture(self.root, 'demo-l0-task', 'ref-20261026-120000', dict(executor='ref', status='FAILED'))
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp)
            self.fixture(other, 'demo-l0-task', 'ref-20261026-130000', dict(executor='ref', status='RESOLVED'))
            self.fixture(other, 'demo-l0-task', 'ref-20261026-140000', dict(executor='ref', status='INVALID'))
            self.fixture(other, 'unrelated', 'ref-20261026-150000', dict(executor='ref', status='RESOLVED'))
            self.fixture(other, 'demo-l0-task', 'invalid-20261026-140000', dict(executor='invalid', status='INVALID'))
            state = page.build_state(self.root, [other])
            self.assertEqual(state['tasks'][0]['comparisons'], {'ref': 'RESOLVED'})
            self.assertEqual(len(state['comparisons']), 1)
            self.assertEqual((state['comparisons'][0]['solved'], state['comparisons'][0]['total']), (1, 1))
        state = page.build_state(self.root)
        self.assertEqual(state['tasks'][0]['comparisons'], {'ref': 'FAILED'})

    def test_api_log_traversal_and_symlink_artifacts(self):
        for query in ('task=..&run=x', 'task=a%2Fb&run=x', 'task=a&run=%00'):
            self.assertEqual(self.request('/api/log?' + query)[0]['status'], 404)
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / 'private'
            outside.write_text('private-value')
            for name in ('meta.json', 'result.json', 'patch.diff', 'live.txt', 'traffic.jsonl'):
                (self.run / name).symlink_to(outside)
            self.assertNotIn('private-value', json.dumps(page.build_state(self.root)))
            for name in ('patch.diff', 'result.json', 'live.txt'):
                self.assertEqual(self.request('/live/demo-l0-task/exec-20261026-120000/' + name)[0]['status'], 404)

    def test_nonfinite_duration_and_missing_log(self):
        for value in ('NaN', 'Infinity', 'broken'):
            self.put('result.json', dict(status='FAILED', duration_s=value))
            self.assertIsNone(page.rows(self.root)[0]['duration'])
            self.assertEqual(self.request('/api/state')[0]['status'], 200)
        headers, body = self.request('/api/log?task=demo-l0-task&run=exec-20261026-120000')
        self.assertEqual(json.loads(body), {'text': ''})

    def test_saved_output_selects_newest_executor_by_timestamp(self):
        self.put('result.json', dict(executor='exec', status='FAILED'))
        self.fixture(self.root, 'demo-l0-task', 'aaa-20261027-120000',
                     dict(executor='aaa', status='RESOLVED'))
        state = page.build_state(self.root)
        self.assertEqual(state['summary']['executor'], 'aaa')
        self.assertEqual(state['tasks'][0]['comparisons'], {'exec': 'FAILED'})

    def test_all_status_totals_and_finished_duration(self):
        for index, status in enumerate(page.STATUSES):
            self.fixture(self.root, 'task-' + str(index), 'exec-20261026-120000',
                         dict(status=status, executor='exec'),
                         dict(started_at='2026-10-26T12:00:00Z', finished_at='2026-10-26T12:01:00Z'))
        state = page.build_state(self.root)
        self.assertEqual(state['summary']['statuses'], dict.fromkeys(page.STATUSES, 1) | {'queued': 2})
        self.assertEqual(state['summary']['agent_time'], 420)
