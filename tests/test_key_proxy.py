#!/usr/bin/env python3
"""Tests: the endpoint key stays outside the sandbox (bin/record-proxy.py swaps the dummy key for the real one) and
bin/secret-scan.py finds the key in a run directory in every form we know of. Run: python3 tests/test_key_proxy.py
"""
import base64
import http.server
import io
from email.message import Message
import json
import os
import random
import runpy
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / 'bin'
KEY = 'sk-live-' + ''.join(random.Random(7).choices(string.ascii_letters + string.digits, k=32))
DUMMY = 'bench-sandbox-dummy-key'


def run_proxy(tmp, headers_list, record=True, key=KEY, auth_header="", echo=False):
    """Start an upstream that records the headers it sees and the proxy in front of it; send requests."""
    seen = []

    class Up(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['content-length']))
            seen.append({k.lower(): v for k, v in self.headers.items()})
            body = json.dumps({'echo': key if echo else 'ok'}).encode()
            self.send_response(200)
            self.send_header('content-type', 'application/json')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    up = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Up)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    env = dict(os.environ, BENCH_UPSTREAM_KEY=key, BENCH_PROXY_RECORD='1' if record else '0',
               BENCH_UPSTREAM_AUTH_HEADER=auth_header)
    env.pop('BENCH_SNAPSHOT_RUN', None)
    p = subprocess.Popen([sys.executable, '-I', str(BIN / 'record-proxy.py'), 'http://127.0.0.1:%d' % up.server_address[1],
                          os.path.join(tmp, 'traffic.jsonl')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        port = int(p.stdout.readline())
        transcript = []
        for h in headers_list:
            transcript.append(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/v1/messages' % port, b'{"m": 1}',
                                                          dict({'content-type': 'application/json'}, **h))).read().decode())
        Path(tmp, 'transcript.jsonl').write_text('\n'.join(transcript))
        log = os.path.join(tmp, 'traffic.jsonl')
        for _ in range(100):                                # the record is written after the response is sent
            if not record or (os.path.exists(log) and len(open(log).readlines()) >= len(headers_list)):
                break
            time.sleep(0.02)
    finally:
        p.terminate()
        err = p.communicate()[1]
        up.shutdown()
    return seen, err


class TestProxyKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='kp-')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_dummy_swapped_for_real_key(self):
        seen, err = run_proxy(self.tmp, [{'Authorization': 'Bearer ' + DUMMY}, {'x-api-key': DUMMY}])
        self.assertEqual(err, '')
        self.assertEqual(seen[0]['authorization'], 'Bearer ' + KEY)
        self.assertEqual(seen[1]['x-api-key'], KEY)
        self.assertNotIn('authorization', seen[1])

    def test_provider_headers_and_echoes(self):
        for provider, header, forced in [('anthropic', 'x-api-key', 'x-api-key'),
                                         ('azure', 'api-key', 'api-key'),
                                         ('zai', 'Authorization', ''),
                                         ('alibaba', 'x-api-key', '')]:
            for record in (False, True):
                with self.subTest(provider=provider, record=record), tempfile.TemporaryDirectory() as tmp:
                    value = 'Bearer ' + DUMMY if header == 'Authorization' else DUMMY
                    seen, err = run_proxy(tmp, [{header: value}], record=record,
                                          auth_header=forced, echo=True)
                    self.assertEqual(err, '')
                    expected = 'Bearer ' + KEY if header == 'Authorization' else KEY
                    self.assertEqual(seen[0][header.lower()], expected)
                    if header != 'Authorization':
                        self.assertNotIn('authorization', seen[0])
                    self.assertIn(DUMMY, Path(tmp, 'transcript.jsonl').read_text())
                    for path in Path(tmp).iterdir():
                        self.assertNotIn(KEY, path.read_text(), path.name)
                    self.assertEqual(Path(tmp, 'traffic.jsonl').exists(), record)
                    self.assertEqual(Path(tmp, 'live.txt').exists(), record)

    def test_no_auth_header_gets_bearer(self):
        seen, _ = run_proxy(self.tmp, [{}])
        self.assertEqual(seen[0]['authorization'], 'Bearer ' + KEY)

    def test_other_credentials_are_not_forwarded(self):
        seen, _ = run_proxy(self.tmp, [{'Authorization': 'Bearer something-else'}])
        self.assertEqual(seen[0]['authorization'], 'Bearer ' + KEY)

    def test_traffic_has_no_key(self):
        run_proxy(self.tmp, [{'Authorization': 'Bearer ' + DUMMY}])
        log = open(os.path.join(self.tmp, 'traffic.jsonl')).read()
        self.assertNotIn(KEY, log)
        self.assertIn('<redacted>', log)

    def test_no_record_mode_writes_nothing(self):
        seen, _ = run_proxy(self.tmp, [{'Authorization': 'Bearer ' + DUMMY}], record=False)
        self.assertEqual(seen[0]['authorization'], 'Bearer ' + KEY)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'traffic.jsonl')))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'live.txt')))

    def test_echoed_key_scrubbed_from_response(self):
        class Echo(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['content-length']))
                body = ('{"error": "bad key ' + self.headers['authorization'] + '"}' + ' pad' * 20000).encode()
                self.send_response(401)
                self.send_header('content-type', 'application/json')
                self.send_header('x-debug-auth', self.headers['authorization'])
                self.end_headers()
                for i in range(0, len(body), 7):                  # dribble, so the key spans reads
                    self.wfile.write(body[i:i + 7])

            def log_message(self, *a):
                pass
        up = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        env = dict(os.environ, BENCH_UPSTREAM_KEY=KEY, BENCH_PROXY_RECORD='1')
        env.pop('BENCH_SNAPSHOT_RUN', None)
        p = subprocess.Popen([sys.executable, '-I', str(BIN / 'record-proxy.py'), 'http://127.0.0.1:%d' % up.server_address[1],
                              os.path.join(self.tmp, 'traffic.jsonl')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = int(p.stdout.readline())
            try:
                urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/v1/x' % port, b'{}',
                                                              {'Authorization': 'Bearer ' + DUMMY}))
                self.fail('expected 401')
            except urllib.error.HTTPError as e:
                body, hdrs = e.read().decode(), dict(e.headers)
            for _ in range(100):
                if os.path.exists(os.path.join(self.tmp, 'traffic.jsonl')) and open(os.path.join(self.tmp, 'traffic.jsonl')).read():
                    break
                time.sleep(0.02)
        finally:
            p.terminate()
            p.communicate()
            up.shutdown()
        self.assertNotIn(KEY, body)
        self.assertIn('bad key Bearer ' + DUMMY, body)
        self.assertTrue(body.endswith(' pad'))                   # nothing lost at the end of the stream
        self.assertNotIn(KEY, json.dumps(hdrs))
        self.assertNotIn(KEY, open(os.path.join(self.tmp, 'traffic.jsonl')).read())

    def test_gzip_echo_scrubbed(self):
        import gzip

        class Gz(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['content-length']))
                body = gzip.compress(('{"echo": "' + self.headers['authorization'] + '"}').encode())
                self.send_response(200)
                self.send_header('content-encoding', 'gzip')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except Exception:
                    pass
        body = self._via(Gz)
        self.assertNotIn(KEY, body)
        self.assertIn('Bearer ' + DUMMY, body)

    def test_unknown_encoding_refused(self):
        class Br(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['content-length']))
                self.send_response(200)
                self.send_header('content-encoding', 'br')
                self.send_header('content-length', '3')
                self.end_headers()
                self.wfile.write(b'xyz')

            def log_message(self, *a):
                pass
        with self.assertRaises(urllib.error.HTTPError) as e:
            self._via(Br, raise_http=True)
        self.assertEqual(e.exception.code, 502)

    def _via(self, handler, raise_http=False):
        up = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        env = dict(os.environ, BENCH_UPSTREAM_KEY=KEY, BENCH_PROXY_RECORD='0')
        env.pop('BENCH_SNAPSHOT_RUN', None)
        p = subprocess.Popen([sys.executable, '-I', str(BIN / 'record-proxy.py'), 'http://127.0.0.1:%d' % up.server_address[1],
                              os.path.join(self.tmp, 'traffic.jsonl')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = int(p.stdout.readline())
            req = urllib.request.Request('http://127.0.0.1:%d/v1/x' % port, b'{}', {'Authorization': 'Bearer ' + DUMMY})
            try:
                return urllib.request.urlopen(req).read().decode()
            except urllib.error.HTTPError as e:
                if raise_http:
                    raise
                return e.read().decode()
        finally:
            p.terminate()
            p.communicate()
            up.shutdown()

    def test_without_key_headers_pass_through(self):
        seen, _ = run_proxy(self.tmp, [{'Authorization': 'Bearer local'}], key='')
        self.assertEqual(seen[0]['authorization'], 'Bearer local')


class TestProxyOffline(unittest.TestCase):
    """Credential injection and response scrubbing also run where TCP is forbidden."""

    def test_provider_auth_and_stream_scrubbing(self):
        for header in ('x-api-key', 'api-key'):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as tmp:
                server = mock.Mock(server_address=('127.0.0.1', 32123))
                with mock.patch.dict(os.environ, {'BENCH_UPSTREAM_KEY': KEY,
                                     'BENCH_UPSTREAM_AUTH_HEADER': header, 'BENCH_PROXY_RECORD': '0'}, clear=True), \
                     mock.patch.object(sys, 'argv', ['record-proxy.py', 'https://example.org', tmp + '/traffic.jsonl']), \
                     mock.patch('http.server.ThreadingHTTPServer', return_value=server), \
                     mock.patch('builtins.print'):
                    proxy = runpy.run_path(str(BIN / 'record-proxy.py'))
                headers = proxy['with_real_key']({'Authorization': 'Bearer ' + DUMMY,
                                                  'x-api-key': 'untrusted', 'api-key': DUMMY,
                                                  'anthropic-version': '2023-06-01'})
                self.assertEqual(headers, {header: KEY, 'anthropic-version': '2023-06-01'})
                # An echoed credential split across arbitrary reads never reaches the transcript.
                scrub = proxy['Scrub']()
                raw = ('prefix ' + KEY + ' suffix ' + urllib.parse.quote(KEY, safe='')).encode()
                output = b''.join(scrub.feed(bytes([b])) for b in raw) + scrub.feed(b'', final=True)
                self.assertNotIn(KEY.encode(), output)
                self.assertEqual(output.count(DUMMY.encode()), 2)
                self.assertEqual(list(Path(tmp).iterdir()), [])


    def test_provider_relay_without_sockets(self):
        for provider, header, forced in [('anthropic', 'x-api-key', 'x-api-key'),
                                         ('azure', 'api-key', 'api-key'),
                                         ('zai', 'Authorization', ''),
                                         ('alibaba', 'Authorization', '')]:
            for record in (False, True):
                with self.subTest(provider=provider, record=record), tempfile.TemporaryDirectory() as tmp:
                    server = mock.Mock(server_address=('127.0.0.1', 32123))
                    with mock.patch.dict(os.environ, {'BENCH_UPSTREAM_KEY': KEY,
                                         'BENCH_UPSTREAM_AUTH_HEADER': forced,
                                         'BENCH_PROXY_RECORD': '1' if record else '0'}, clear=True), \
                         mock.patch.object(sys, 'argv', ['record-proxy.py', 'https://example.org', tmp + '/traffic.jsonl']), \
                         mock.patch('http.server.ThreadingHTTPServer', return_value=server), \
                         mock.patch('builtins.print'):
                        proxy = runpy.run_path(str(BIN / 'record-proxy.py'))
                    body = ('data: ' + json.dumps({'type': 'content_block_delta',
                            'delta': {'text': 'echo ' + KEY}}) + '\n\n').encode()
                    resp = mock.Mock(status=200)
                    resp.getheader.side_effect = lambda name: {'content-type': 'text/event-stream'}.get(name)
                    resp.getheaders.return_value = [('content-type', 'text/event-stream'), ('x-echo', KEY)]
                    resp.read1.side_effect = [body[:30], body[30:], b'']
                    handler = proxy['Proxy'].__new__(proxy['Proxy'])
                    handler.headers = Message()
                    handler.headers['content-length'] = '2'
                    handler.headers[header] = 'Bearer ' + DUMMY if header == 'Authorization' else DUMMY
                    handler.rfile = io.BytesIO(b'{}')
                    handler.wfile = io.BytesIO()
                    handler.command, handler.path = 'POST', '/v1/responses'
                    handler.send_response = mock.Mock()
                    handler.send_header = mock.Mock()
                    handler.end_headers = mock.Mock()
                    with mock.patch('http.client.HTTPSConnection') as connection:
                        connection.return_value.getresponse.return_value = resp
                        handler.relay()
                    sent = connection.return_value.request.call_args.kwargs['headers']
                    self.assertEqual(sent[header], 'Bearer ' + KEY if header == 'Authorization' else KEY)
                    if forced:
                        self.assertNotIn('Authorization', sent)
                    transcript = handler.wfile.getvalue().decode()
                    self.assertIn(DUMMY, transcript)
                    self.assertNotIn(KEY, transcript)
                    self.assertNotIn(KEY, str(handler.send_header.call_args_list))
                    for name in ('OUT', 'LIVE'):
                        if proxy[name]:
                            proxy[name].close()
                    for path in Path(tmp).iterdir():
                        self.assertNotIn(KEY, path.read_text(), path.name)
                    self.assertEqual(Path(tmp, 'traffic.jsonl').exists(), record)
                    self.assertEqual(Path(tmp, 'live.txt').exists(), record)
                    if record:
                        self.assertIn(DUMMY, Path(tmp, 'live.txt').read_text())


def scan(run, key=KEY, mask=False, extra_env=None, home=None):
    env = {'PATH': os.environ['PATH'], 'HOME': home or tempfile.gettempdir() + '/no-such-home', 'BENCH_SECRET': key}
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, '-I', str(BIN / 'secret-scan.py'), run] + (['--mask'] if mask else []),
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class TestSecretScan(unittest.TestCase):
    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix='ss-')
        os.makedirs(os.path.join(self.run_dir, 'work', 'src'))
        open(os.path.join(self.run_dir, 'result.json'), 'w').write('{"status": "RESOLVED"}')
        open(os.path.join(self.run_dir, 'work', 'src', 'a.go'), 'w').write('package a\n')

    def tearDown(self):
        shutil.rmtree(self.run_dir)

    def put(self, rel, data):
        p = os.path.join(self.run_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(data if isinstance(data, bytes) else data.encode())

    def read(self, rel):
        return open(os.path.join(self.run_dir, rel), 'rb').read()

    def assertHit(self, rel, key=KEY, **kw):
        rc, out = scan(self.run_dir, key, **kw)
        self.assertEqual(rc, 1, out)
        self.assertIn(rel, out)
        if key:
            self.assertNotIn(key, out)
        return out

    def test_clean(self):
        self.assertEqual(scan(self.run_dir)[0], 0)

    def test_no_secrets_known_is_clean(self):
        self.assertEqual(scan(self.run_dir, key='')[0], 0)

    def test_raw_in_patch(self):
        self.put('patch.diff', '+ token := "%s"\n' % KEY)
        self.assertHit('patch.diff')

    def test_url_encoded(self):
        key = KEY + '/+='
        self.put('agent.out', 'GET /?k=' + urllib.parse.quote(key, safe=''))
        self.assertHit('agent.out', key)

    def test_base64_at_every_alignment(self):
        rnd = random.Random(1)
        for off in range(3):
            for trail in range(3):
                blob = bytes(rnd.randrange(256) for _ in range(off)) + KEY.encode() + bytes(rnd.randrange(256) for _ in range(trail))
                self.put('home/s.jsonl', 'x' + base64.b64encode(blob).decode() + 'y')
                self.assertHit('home/s.jsonl')

    def test_large_file(self):
        self.put('traffic.jsonl', b'a' * ((1 << 20) - 5) + KEY.encode())
        self.assertHit('traffic.jsonl')

    def test_inside_snapshot_objects(self):
        snap = os.path.join(self.run_dir, 'snap.git')
        subprocess.run(['git', 'init', '-q', '--bare', snap], check=True)
        subprocess.run(['git', '--git-dir', snap, 'hash-object', '-w', '--stdin'], input=('leak ' + KEY).encode(), check=True,
                       capture_output=True)
        self.assertIn('git objects', self.assertHit('snap.git/objects'))

    def test_short_values_ignored(self):
        self.put('agent.out', 'the word abc is everywhere')
        self.assertEqual(scan(self.run_dir, key='abc')[0], 0)

    def test_unreadable_is_not_clean(self):
        self.put('home/hidden.txt', KEY)
        os.chmod(os.path.join(self.run_dir, 'home', 'hidden.txt'), 0)
        try:
            if os.access(os.path.join(self.run_dir, 'home', 'hidden.txt'), os.R_OK):
                self.skipTest('running as root')
            self.assertEqual(scan(self.run_dir)[0], 2)
            os.chmod(os.path.join(self.run_dir, 'home', 'hidden.txt'), 0o600)
            os.chmod(os.path.join(self.run_dir, 'home'), 0)
            self.assertEqual(scan(self.run_dir)[0], 2)          # an unreadable directory too
        finally:
            os.chmod(os.path.join(self.run_dir, 'home'), 0o700)

    def test_key_in_agent_git_objects(self):                    # stored compressed, plaintext removed
        work = os.path.join(self.run_dir, 'work')
        subprocess.run(['git', 'init', '-q', work], check=True)
        subprocess.run(['git', '-C', work, 'hash-object', '-w', '--stdin'], input=('k=' + KEY).encode(), check=True,
                       capture_output=True)
        self.assertIn('git objects', self.assertHit('work/.git/objects'))

    def test_object_store_without_head(self):                    # .git/HEAD deleted: still an object store
        work = os.path.join(self.run_dir, 'work')
        subprocess.run(['git', 'init', '-q', work], check=True)
        subprocess.run(['git', '-C', work, 'hash-object', '-w', '--stdin'], input=('k=' + KEY).encode(), check=True,
                       capture_output=True)
        os.remove(os.path.join(work, '.git', 'HEAD'))
        self.assertHit('work/.git')

    def test_url_encoding_variants(self):
        key = 'sk-review/+secret-123'
        for enc in ('sk-review%2f%2bsecret-123', 'sk-review%2F%2Bsecret-123', '%73k-review/+secret-123'):
            self.put('agent.out', 'x=' + enc)
            self.assertHit('agent.out', key)

    # masking

    def test_mask_traffic_all_forms(self):
        b64 = base64.b64encode(b'{"k":"' + KEY.encode() + b'"}').decode()
        self.put('traffic.jsonl', json.dumps({'raw': KEY, 'url': urllib.parse.quote(KEY), 'b64': b64}) + '\n')
        out = self.assertHit('traffic.jsonl', mask=True)
        self.assertIn('masked\ttraffic.jsonl', out)
        data = self.read('traffic.jsonl')
        self.assertNotIn(KEY.encode(), data)
        self.assertNotIn(b64.encode()[8:20], data)                # the base64 form is gone too
        self.assertIn(b's***** [CUSTOM_API_KEY]', data)
        self.assertEqual(scan(self.run_dir)[0], 0)                  # nothing left after masking

    def test_mask_env_secret_with_its_name(self):
        zai = 'Ezai-' + 'q' * 30
        self.put('home/.claude/projects/x/s.jsonl', 'export ZAI_API_KEY=' + zai)
        self.assertHit('s.jsonl', key='', mask=True, extra_env={'ZAI_API_KEY': zai})
        self.assertIn(b'E***** [ZAI_API_KEY]', self.read('home/.claude/projects/x/s.jsonl'))

    def test_mask_rc_and_oauth_secrets(self):
        home = os.path.join(self.run_dir, '..', os.path.basename(self.run_dir) + '-home')
        os.makedirs(os.path.join(home, '.claude'))
        rc_key, oauth = 'rc-key-' + 'z' * 20, 'oat-' + 'y' * 40
        open(os.path.join(home, '.bashrc'), 'w').write('export ALI_CP_API_KEY="%s"\n' % rc_key)
        json.dump({'claudeAiOauth': {'accessToken': oauth, 'subscriptionType': 'max'}},
                  open(os.path.join(home, '.claude', '.credentials.json'), 'w'))
        try:
            self.put('agent.out', rc_key + ' ' + oauth + ' max')
            self.assertHit('agent.out', key='', mask=True, home=home)
            data = self.read('agent.out')
            self.assertIn(b'r***** [ALI_CP_API_KEY]', data)
            self.assertIn(b'o***** [CLAUDE_OAUTH]', data)
            self.assertIn(b' max', data)                         # non-secret fields untouched
        finally:
            shutil.rmtree(home)

    def test_mask_leaves_work_and_reports_it(self):
        self.put('work/src/k.go', KEY)
        self.put('patch.diff', '+' + KEY)
        out = self.assertHit('work/src/k.go', mask=True)
        self.assertIn('found\twork/src/k.go', out)
        self.assertIn('masked\tpatch.diff', out)
        self.assertIn(KEY.encode(), self.read('work/src/k.go'))
        self.assertNotIn(KEY.encode(), self.read('patch.diff'))

    def test_dummy_and_none_not_secrets(self):
        self.put('agent.out', DUMMY + ' none')
        self.assertEqual(scan(self.run_dir, key='', extra_env={'CUSTOM_API_KEY': DUMMY, 'X_TOKEN': 'none'})[0], 0)

    def test_mode_kept(self):
        self.put('agent.out', KEY)
        os.chmod(os.path.join(self.run_dir, 'agent.out'), 0o640)
        scan(self.run_dir, mask=True)
        self.assertEqual(os.stat(os.path.join(self.run_dir, 'agent.out')).st_mode & 0o777, 0o640)


class TestSandboxKey(unittest.TestCase):
    """bin/sandbox.sh itself: whoever calls it with a real key, the sandbox env gets the dummy and the harness talks
    to a local key proxy. Runs sandbox.sh with a fake harness that dumps its environment."""

    def test_sandbox_env_has_dummy(self):
        if not shutil.which('bwrap'):
            self.skipTest('bwrap not installed')
        tmp = tempfile.mkdtemp(prefix='sb-')
        try:
            real_home = os.path.join(tmp, 'home')
            os.makedirs(os.path.join(real_home, '.local', 'bin'))
            fake = os.path.join(real_home, '.local', 'bin', 'pi')
            open(fake, 'w').write('#!/bin/sh\nenv > /work/env.txt\ncat "$HOME/.pi/agent/models.json" > /work/models.txt 2>/dev/null\n')
            os.chmod(fake, 0o755)
            run = os.path.join(tmp, 'run')
            os.makedirs(os.path.join(run, 'work'))
            env = dict(os.environ, HOME=real_home, CUSTOM_API_KEY=KEY, CUSTOM_BASE_URL='http://127.0.0.1:9/v1')
            env['PATH'] = os.path.join(real_home, '.local', 'bin') + ':' + env['PATH']
            r = subprocess.run([str(BIN / 'sandbox.sh'), '--run', run, '--harness', 'pi', '--provider', 'custom',
                                '--model', 'm', '--effort', 'high', '--', '-p', 'hi'], env=env, capture_output=True, text=True,
                               timeout=120)
            dumped = open(os.path.join(run, 'work', 'env.txt')).read() if os.path.exists(os.path.join(run, 'work', 'env.txt')) else ''
            if not dumped:
                self.skipTest('sandbox did not start here: ' + (r.stderr[-300:] or str(r.returncode)))
            self.assertIn('CUSTOM_API_KEY=' + DUMMY, dumped)
            self.assertNotIn(KEY, dumped)
            for root, _, files in os.walk(run):
                for n in files:
                    self.assertNotIn(KEY, open(os.path.join(root, n), errors='replace').read(), n)
            models = open(os.path.join(run, 'work', 'models.txt')).read()
            self.assertIn('127.0.0.1:', models)
            self.assertNotIn('127.0.0.1:9/', models)          # pointed at the key proxy, not straight upstream
        finally:
            shutil.rmtree(tmp)


class TestSafeDiff(unittest.TestCase):
    """bin/grade.sh builds patch.diff without running git in the agent's repository (its config is agent-controlled)."""

    def test_agent_git_config_never_runs(self):
        tmp = tempfile.mkdtemp(prefix='sd-')
        try:
            work = os.path.join(tmp, 'w')
            os.makedirs(work)
            open(os.path.join(work, 'a.txt'), 'w').write('a\n')
            g = lambda *a: subprocess.run(['git', '-C', work, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True,
                                          capture_output=True)
            g('init', '-q'); g('add', '-A'); g('commit', '-qm', 'b'); g('tag', 'baseline')
            mark = os.path.join(tmp, 'PWNED')
            hook = os.path.join(tmp, 'hook.sh')
            open(hook, 'w').write('#!/bin/sh\ntouch %s\n' % mark)
            os.chmod(hook, 0o755)
            g('config', 'core.fsmonitor', hook)
            g('config', 'filter.evil.clean', 'sh -c "touch %s; cat"' % mark)
            g('config', 'diff.evil.textconv', 'sh -c "touch %s; cat"' % mark)
            open(os.path.join(work, '.gitattributes'), 'w').write('*.txt filter=evil diff=evil\n')
            open(os.path.join(work, 'a.txt'), 'w').write('a\nb\n')
            open(os.path.join(work, 'new.txt'), 'w').write('n\n')
            r = subprocess.run([sys.executable, '-I', str(BIN / 'snapshot.py'), 'diff', work], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(mark))
            self.assertIn('+b', r.stdout)
            self.assertIn('new.txt', r.stdout)
        finally:
            shutil.rmtree(tmp)


class TestSafeDiffEquivalence(unittest.TestCase):
    """bin/snapshot.py diff gives byte for byte what `git add -A -N; git diff baseline` gave in the agent's repo."""

    def test_same_as_git_diff(self):
        tmp = tempfile.mkdtemp(prefix='sde-')
        try:
            w = os.path.join(tmp, 'w')
            os.makedirs(w)
            g = lambda *a: subprocess.run(['git', '-C', w, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True,
                                          capture_output=True).stdout
            for n, data in (('a.txt', b'a\n'), ('latin.txt', b'\xff\xfe x \x80\n'), ('gone.txt', b'g\n'), ('bin.dat', b'\x00\x01')):
                open(os.path.join(w, n), 'wb').write(data)
            g('init', '-q'); g('add', '-A'); g('commit', '-qm', 'b'); g('tag', 'baseline')
            open(os.path.join(w, 'a.txt'), 'ab').write(b'a2\n')
            open(os.path.join(w, '.gitignore'), 'w').write('a.txt\n')          # tracked and now ignored
            open(os.path.join(w, 'latin.txt'), 'ab').write(b'\xff more \x81\n')  # not UTF-8
            open(os.path.join(w, 'bin.dat'), 'wb').write(b'\x00\x02\x03')
            os.remove(os.path.join(w, 'gone.txt'))
            os.makedirs(os.path.join(w, 'sub'))
            open(os.path.join(w, 'sub', 'new.go'), 'w').write('package sub\n')
            g('add', '-A', '-N')
            old = g('diff', 'baseline')
            g('reset', '-q')
            new = subprocess.run([sys.executable, '-I', str(BIN / 'snapshot.py'), 'diff', w], capture_output=True, check=True).stdout
            self.assertEqual(new, old)
            self.assertIn(b'+a2', new)
        finally:
            shutil.rmtree(tmp)


class TestRunShWiring(unittest.TestCase):
    """bin/run.sh: the real key goes only to the proxy and finalize; the sandbox gets the dummy."""

    def test_dummy_matches(self):
        run_sh = (BIN / 'run.sh').read_text()
        proxy = (BIN / 'record-proxy.py').read_text()
        lib = (BIN / 'lib.sh').read_text()
        self.assertIn('export CUSTOM_API_KEY=%s' % DUMMY, lib)
        self.assertIn('DUMMY_KEY = "%s"' % DUMMY, proxy)
        i = run_sh.index('key_proxy_setup')
        self.assertLess(i, run_sh.index('bin/sandbox.sh'))



class TestProviderWiring(unittest.TestCase):
    """Exercise sandbox configuration with fake launchers, without TCP or kernel namespaces."""

    def test_all_key_providers_and_harnesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / 'bin'
            fake.mkdir()
            # The proxy only needs to announce a port; no upstream or benchmark is run.
            (fake / 'python3').write_text('#!/bin/bash\nif [[ "$*" == *record-proxy.py* ]]; then\n'
                                        '  echo "${BENCH_PROXY_RECORD}" > "$PROXY_MODE"\n'
                                        '  echo "${BENCH_UPSTREAM_AUTH_HEADER}" > "$PROXY_MODE.header"\n'
                                        '  echo 32123; exec sleep 60\nfi\nexec ' + sys.executable + ' "$@"\n')
            (fake / 'bwrap').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$PROBE"\n')
            for name in ('pi', 'claude', 'codex'):
                (fake / name).write_text('#!/bin/sh\necho fake-version\n')
            for path in fake.iterdir():
                path.chmod(0o755)
            for provider, key_var in [('custom', 'CUSTOM_API_KEY'), ('zai', 'ZAI_API_KEY'),
                                      ('alibaba', 'ALI_CP_API_KEY'), ('azure', 'AZURE_OPENAI_API_KEY'),
                                      ('anthropic', 'ANTHROPIC_API_KEY')]:
                for harness in ('pi', 'claude', 'codex'):
                    if (provider == 'azure' and harness != 'codex') or (provider == 'anthropic' and harness != 'claude'):
                        continue
                    with self.subTest(provider=provider, harness=harness):
                        home = root / (provider + harness)
                        (home / '.pi/agent').mkdir(parents=True)
                        (home / '.pi/agent/models.json').write_text(json.dumps({'providers': {
                            provider: {'baseUrl': 'https://example.org/v1', 'apiKey': '$' + key_var,
                                       'api': 'anthropic-messages', 'models': []}}}))
                        run = home / 'run'
                        (run / 'work').mkdir(parents=True)
                        probe = home / 'probe'
                        mode = home / 'mode'
                        env = dict(os.environ, HOME=str(home), PATH=str(fake) + ':' + os.environ['PATH'],
                                   BENCH_NO_WEB='0', PROBE=str(probe), PROXY_MODE=str(mode),
                                   CUSTOM_BASE_URL='https://example.org/v1', ZAI_ORIGIN='https://example.org',
                                   ALI_ANTHROPIC_BASE_URL='https://example.org/anthropic',
                                   ALI_CODEX_BASE_URL='http://127.0.0.1:9/v1', ALI_CODEX_MASTER_KEY=KEY,
                                   AZURE_OPENAI_ENDPOINT='https://example.org')
                        env[key_var] = KEY
                        r = subprocess.run([str(BIN / 'sandbox.sh'), '--run', str(run), '--harness', harness,
                                            '--provider', provider, '--model', 'qwen3.7-plus'],
                                           env=env, capture_output=True, text=True, timeout=20)
                        self.assertEqual(r.returncode, 0, r.stderr)
                        dumped = probe.read_text()
                        self.assertIn(DUMMY, dumped)
                        self.assertNotIn(KEY, dumped)
                        self.assertEqual(mode.read_text().strip(), '0')
                        expected_header = {'anthropic': 'x-api-key', 'azure': 'api-key'}.get(provider, '')
                        self.assertEqual(Path(str(mode) + '.header').read_text().strip(), expected_header)
                        for path in run.rglob('*'):
                            if path.is_file() and not path.is_symlink():
                                self.assertNotIn(KEY, path.read_text(errors='replace'), str(path))
                        config = (run / 'home/.pi/agent/models.json' if harness == 'pi' else
                                  run / 'home/.codex/config.toml' if harness == 'codex' else probe)
                        self.assertIn('127.0.0.1:32123', config.read_text())
                        if provider == 'azure':
                            self.assertIn('env_http_headers = { api-key = "AZURE_OPENAI_API_KEY" }', config.read_text())
                            self.assertNotIn('env_key =', config.read_text())
                            self.assertIn('requires_openai_auth = false', config.read_text())
                        if harness == 'pi' and provider in ('zai', 'alibaba'):
                            models = json.loads(config.read_text())
                            self.assertEqual(models['providers'][provider]['baseUrl'], 'http://127.0.0.1:32123/v1')

    def test_login_paths_keep_existing_credentials(self):
        for provider, harness, credential in [('anthropic', 'claude', '.claude/.credentials.json'),
                                               ('openai', 'codex', '.codex/auth.json')]:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake = root / 'bin'
                fake.mkdir()
                (fake / harness).write_text('#!/bin/sh\necho fake-version\n')
                (fake / 'bwrap').write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                    'from pathlib import Path\n'
                    'p = Path(os.environ["PROBE"])\n'
                    'p.write_text(json.dumps(sys.argv[1:]))\n'
                    'credential = p.parent / "run/home" / os.environ["CREDENTIAL"]\n'
                    'if credential.stat().st_size:\n'
                    '    (p.parent / "copied-auth").write_bytes(credential.read_bytes())\n')
                for path in fake.iterdir():
                    path.chmod(0o755)
                home = root / 'real'
                source = home / credential
                source.parent.mkdir(parents=True)
                source.write_text('{"test-login":"synthetic-login-token"}')
                run = root / 'run'
                (run / 'work').mkdir(parents=True)
                env = dict(os.environ, HOME=str(home), PATH=str(fake) + ':' + os.environ['PATH'],
                           PROBE=str(root / 'probe'), CREDENTIAL=credential, BENCH_NO_WEB='0',
                           ANTHROPIC_API_KEY=KEY, OPENAI_API_KEY=KEY)
                r = subprocess.run([str(BIN / 'sandbox.sh'), '--run', str(run), '--harness', harness,
                                    '--provider', provider, '--model', 'm'], env=env,
                                   capture_output=True, text=True, timeout=20)
                self.assertEqual(r.returncode, 0, r.stderr)
                args = json.loads((root / 'probe').read_text())
                self.assertNotIn(KEY, args)
                if provider == 'anthropic':
                    self.assertIn(str(source), args)  # writable OAuth bind, preserving refresh
                    self.assertNotIn('ANTHROPIC_API_KEY', args)
                else:
                    self.assertEqual((root / 'copied-auth').read_bytes(), source.read_bytes())
                    self.assertFalse((run / 'home' / credential).exists())
                self.assertEqual(source.read_text(), '{"test-login":"synthetic-login-token"}')


if __name__ == '__main__':
    unittest.main()
