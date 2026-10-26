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

    def request(self, path, compare=()):
        handler = page.Handler.__new__(page.Handler)
        handler.server = SimpleNamespace(runs=self.root, compare=compare)
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
        self.assertIn(b"fetch('/api/state'+", body)
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
                     dict(status='TIMEOUT', duration_s=50, executor='exec'),
                     dict(model='demo', abap_package='abc', abap_tier='smoke4', env_version='3'))
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
            state = page.build_state(self.root, [other], executor='exec')
            self.assertEqual(state['tasks'][0]['comparisons'], {'ref': 'RESOLVED'})
            self.assertEqual(len(state['comparisons']), 3)
            self.assertEqual((next(r for r in state['comparisons'] if r['executor'] == 'ref')['solved'],
                              state['comparisons'][0]['total']), (2, 2))
        state = page.build_state(self.root, executor='exec')
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
        self.assertEqual(state['summary']['agent_time'], 480)

    def grouped_fixture(self):
        abap = dict(protocol='abap-native-v1', abap_set='SMOKE',
                    abap_package='demo', abap_tier='l0', env_version='23')
        # Every executor ran both groups. Shared task IDs must still stay separate.
        for ex in ('alpha', 'beta', 'gamma'):
            for task in ('one', 'two'):
                self.fixture(self.root, task, ex + '-20261025-120000',
                             dict(executor=ex, status='FAILED', duration_s=999))
                status = 'RESOLVED' if ex == 'alpha' or (ex == 'beta' and task == 'one') else 'FAILED'
                self.fixture(self.root, task, ex + '-20261026-120000',
                             dict(executor=ex, status=status, duration_s=30 if task == 'one' else 90), abap)
        # Latest INVALID attempt must not erase beta's solved reference.
        self.fixture(self.root, 'one', 'beta-20261026-130000',
                     dict(executor='beta', status='INVALID', duration_s=1), abap)
        return abap

    def test_two_groups_three_executors_and_api_selection(self):
        self.run.rmdir()
        self.grouped_fixture()
        state = page.build_state(self.root)
        self.assertEqual(len(state['groups']), 2)
        self.assertEqual(state['executors'], ['alpha', 'beta', 'gamma'])
        self.assertEqual(state['summary']['executor'], 'beta')
        self.assertEqual((state['summary']['total'], state['summary']['invalid']), (2, 1))
        self.assertEqual([r['executor'] for r in state['comparisons']], ['alpha', 'beta', 'gamma'])
        self.assertEqual([r['solved'] for r in state['comparisons']], [2, 1, 0])
        self.assertEqual([r['median_time'] for r in state['comparisons']], [60, 60, 60])
        self.assertEqual([r['total'] for r in state['comparisons']], [2, 2, 2])
        selected = page.build_state(self.root, group=state['group'], executor='alpha')
        self.assertEqual(selected['summary']['solved'], 2)
        self.assertEqual(selected['tasks'][0]['comparisons'], {'beta': 'RESOLVED', 'gamma': 'FAILED'})
        self.assertTrue(selected['comparisons'][0]['selected'])
        legacy = next(g['id'] for g in state['groups'] if not g['protocol'])
        query = page.urllib.parse.urlencode(dict(group=legacy, executor='gamma'))
        headers, body = self.request('/api/state?' + query)
        old = json.loads(body)
        self.assertEqual(headers['status'], 200)
        self.assertEqual((old['summary']['executor'], old['summary']['total'], old['summary']['solved']),
                         ('gamma', 2, 0))
        self.assertEqual(old['summary']['agent_time'], 1998)
        self.assertEqual(page.build_state(self.root, group='bad', executor='bad')['group'], state['group'])

    def test_each_group_field_separates_runs_and_missing_attempts(self):
        self.run.rmdir()
        abap = self.grouped_fixture()
        for index, field in enumerate(page.GROUP_FIELDS):
            self.fixture(self.root, 'extra', 'alpha-20261027-12000' + str(index),
                         dict(executor='alpha', status='RESOLVED'), dict(abap, **{field: 'other'}))
        self.assertEqual(len(page.build_state(self.root)['groups']), 7)
        key = page.group_id(page.group_key(abap))
        self.fixture(self.root, 'only-alpha', 'alpha-20261026-140000',
                     dict(executor='alpha', status='RESOLVED'), abap)
        state = page.build_state(self.root, group=key, executor='gamma')
        self.assertEqual(state['summary']['total'], 3)
        self.assertEqual(state['summary']['statuses']['not run'], 1)
        self.assertEqual(state['tasks'][1]['status'], 'not run')
        self.assertEqual({r['total'] for r in state['comparisons']}, {3})

    def test_legacy_queue_is_scoped_to_newest_executor_group(self):
        self.run.rmdir()
        self.grouped_fixture()
        (self.root / 'queue-alpha.json').write_text(json.dumps(dict(executor='alpha', tasks=['pending'])))
        state = page.build_state(self.root, executor='alpha')
        self.assertEqual(state['summary']['queued'], 1)
        legacy = next(g['id'] for g in state['groups'] if not g['protocol'])
        self.assertNotIn('pending', [r['task'] for r in page.build_state(self.root, group=legacy)['tasks']])

    def test_reference_artifacts_are_scoped_to_their_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp)
            directory = self.fixture(other, 'demo-l0-task', 'ref-20261027-120000',
                                     dict(executor='ref', status='RESOLVED'))
            (directory / 'live.txt').write_text('reference log')
            state = page.build_state(self.root, [other], executor='ref')
            row = state['tasks'][0]
            self.assertEqual(row['source'], '0')
            self.assertTrue(row['result'].startswith('/reference/0/live/'))
            self.assertEqual(self.request(row['result'], [other])[0]['status'], 200)
            headers, body = self.request('/api/log?source=0&task=demo-l0-task&run=ref-20261027-120000', [other])
            self.assertEqual(headers['status'], 200)
            self.assertEqual(json.loads(body)['text'], 'reference log')
            self.assertEqual(self.request('/api/log?source=9&task=a&run=b')[0]['status'], 404)

    def test_compact_dashboard_component_at_both_sizes_and_themes(self):
        """Render only the inline component, with fixture state and no server/network."""
        import shutil
        import subprocess
        browser = shutil.which('chrome-headless-shell') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not browser:
            browser = next(iter(sorted((Path.home() / '.cache/ms-playwright').glob(
                'chromium-*/chrome-linux*/chrome'))), None)
        if not browser:
            self.skipTest('Chromium unavailable for component layout unit test')
        self.run.rmdir()
        self.grouped_fixture()
        state = page.build_state(self.root, executor='alpha')
        long_name = 'executor-' + 'very-long-name-' * 40
        state['comparisons'] += [dict(state['comparisons'][-1], executor=long_name + str(i), selected=False)
                                 for i in range(8)]
        state['executors'].append(long_name)
        state['tasks'][0]['task'] = 'long-task-' * 50
        state['tasks'][0]['comparisons'][long_name] = 'INCONCLUSIVE'
        checks = r'''
function check(ok,message){if(!ok)throw Error(message)}
try {
 check(innerWidth===WIDTH,'requested viewport width '+innerWidth);
 state=FIXTURE;draw();
 check(document.querySelectorAll('#refs tr').length===8,'comparison must collapse at eight');
 check(document.querySelectorAll('#refs .selected').length===1,'selected executor highlight');
 check(!$('show-all').hidden,'show all visible');
 check(document.querySelectorAll('#tasks details').length===2,'reference disclosures');
 check(!document.querySelector('#tasks details').open,'references initially collapsed');
 check(duration(75)==='1:15'&&duration(0)==='0:00'&&duration(null)==='—','m:ss formatting');
 const fits=()=>check(document.documentElement.scrollWidth<=innerWidth,'horizontal overflow '+document.documentElement.scrollWidth+'/'+innerWidth);
 fits();$('show-all').click();
 check(document.querySelectorAll('#refs tr').length===11,'show all expands');fits();
 const name=document.querySelector('#refs tr:last-child .truncate');
 check(name.scrollWidth>name.clientWidth&&name.title.length>400,'executor truncation and tooltip');
 document.querySelector('#tasks details').open=true;fits();
 $('search').value='nothing-matches';drawTasks();check($('tasks').textContent==='No matching tasks','search');
 check(matchMedia('(prefers-color-scheme:dark)').matches===DARK,'theme');
 document.body.dataset.unitResult='PASS';
} catch(error) {document.body.dataset.unitResult='FAIL: '+error.message}
'''
        for width in (1100, 420):
            for dark in (False, True):
                with self.subTest(width=width, dark=dark), tempfile.TemporaryDirectory() as tmp:
                    html = page.PAGE.replace(';refresh();', ';')
                    script = checks.replace('FIXTURE', json.dumps(state)).replace('DARK', str(dark).lower()).replace('WIDTH', str(width))
                    html = html.replace('</script></html>', '</script><script>' + script + '</script></html>')
                    document = Path(tmp) / 'component.html'
                    document.write_text(html)
                    command = [str(browser), '--headless', '--no-sandbox', '--disable-gpu',
                               '--disable-dev-shm-usage', '--disable-background-networking',
                               '--disable-extensions', '--no-first-run', '--disable-component-update',
                               '--disable-sync', '--no-default-browser-check', '--no-proxy-server',
                               '--user-data-dir=' + str(Path(tmp) / 'profile'),
                               '--window-size=' + str(width) + ',900', '--force-device-scale-factor=1']
                    if dark:
                        command.append('--force-dark-mode')
                    result = subprocess.run(command + ['--dump-dom', document.as_uri()],
                                            capture_output=True, text=True, timeout=30)
                    if result.returncode and 'Operation not permitted' in result.stderr:
                        self.skipTest('Sandbox blocks Chromium startup: Operation not permitted')
                    self.assertEqual(result.returncode, 0, result.stderr[-1000:])
                    import re
                    marker = re.search(r'data-unit-result="([^"]+)"', result.stdout)
                    self.assertIsNotNone(marker, result.stderr[-1000:])
                    self.assertEqual(marker.group(1), 'PASS')

    def test_dashboard_component_behavior_without_browser(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('Node unavailable for JavaScript component unit test')
        self.run.rmdir()
        self.grouped_fixture()
        state = page.build_state(self.root, executor='alpha')
        state['comparisons'] += [dict(state['comparisons'][-1], executor='extra-' + str(i), selected=False)
                                 for i in range(8)]
        script = page.PAGE.split('<script>', 1)[1].split('</script>', 1)[0].replace(';refresh();', ';')
        result = subprocess.run([node, str(Path(__file__).with_name('live_page_component.js'))],
                                input=json.dumps(dict(script=script, state=state)),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
