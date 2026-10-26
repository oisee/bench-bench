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
        self.assertIn('running', page.render(self.root))

    def test_html_escaping_and_refresh(self):
        self.put('result.json', {'status': '<script>alert(1)</script>'})
        (self.run / 'live.txt').write_text('<script>transcript</script>')
        headers, body = self.request('/')
        self.assertEqual(headers['status'], 200)
        self.assertIn(b'http-equiv="refresh"', body)
        self.assertNotIn(b'<script>', body)
        self.assertIn(b'&lt;script&gt;', body)
        self.assertIn(b'live.txt (tail)', body)
        headers, body = self.request('/live/demo-l0-task/exec-20261026-120000/live.txt')
        self.assertEqual(headers['Content-Type'], 'text/plain; charset=utf-8')
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
        self.assertIn('Waiting for tasks', page.render(self.root / 'missing'))
