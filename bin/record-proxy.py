#!/usr/bin/env python3
"""Recording reverse proxy for the custom provider: forwards every request to UPSTREAM and
appends the full exchange (request body, response body or raw SSE stream, timing) to a JSONL file.

    bin/record-proxy.py UPSTREAM_ORIGIN OUT.jsonl     e.g. https://llm.example.com runs/.../traffic.jsonl

Listens on a free 127.0.0.1 port and prints it on stdout. API keys are redacted from the log.
While a streamed answer is still being generated, its thinking, text and tool calls are also appended as plain
text to live.txt next to OUT.jsonl (`tail -f runs/.../live.txt` to watch). Started by bin/run.sh when BENCH_RECORD=1.
With BENCH_SNAPSHOT_RUN=RUN_DIR it also snapshots RUN_DIR/work at start (seq 0) and as each request arrives, before
forwarding it (bin/snapshot.py; used by bin/first-green.py). A failed snapshot is logged to stderr, never fatal.
"""
import http.client, http.server, json, os, sys, threading, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snapshot

UP = urllib.parse.urlsplit(sys.argv[1])
OUT = open(sys.argv[2], "a")
LIVE = open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])), "live.txt"), "a")
LOCK = threading.Lock()
SEQ = [0]
LIVE_LAST = [None]  # seq of the request live.txt is currently showing
SNAP_RUN = os.environ.get("BENCH_SNAPSHOT_RUN")
SNAP_LOCK = threading.Lock()


def take_snapshot(seq):
    if not SNAP_RUN:
        return
    try:
        snapshot.take(SNAP_RUN, seq)
    except Exception as e:
        print(f"snapshot {seq} failed: {e}", file=sys.stderr, flush=True)


def live_write(seq, text):
    """Append text of request `seq` to live.txt, with a header whenever the request changes."""
    if not text:
        return
    with LOCK:
        if LIVE_LAST[0] != seq:
            LIVE.write(f"\n\n=== #{seq} {time.strftime('%H:%M:%S')} ===\n")
            LIVE_LAST[0] = seq
        LIVE.write(text)
        LIVE.flush()


class SSEText:
    """Turns Anthropic SSE bytes into readable text as they arrive: thinking, answer text, tool calls."""

    def __init__(self, seq):
        self.seq, self.buf = seq, b""

    def feed(self, data):
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            t, out = ev.get("type"), ""
            if t == "content_block_start":
                b = ev.get("content_block") or {}
                out = {"thinking": "\n[thinking] ", "text": "\n[answer] "}.get(b.get("type"), "")
                if b.get("type") == "tool_use":
                    out = f"\n[tool] {b.get('name')} "
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                out = d.get("thinking") or d.get("text") or d.get("partial_json") or ""
            elif t == "message_delta":
                u = ev.get("usage") or {}
                out = f"\n[end: {(ev.get('delta') or {}).get('stop_reason')}, {u.get('output_tokens')} tokens out]"
            live_write(self.seq, out)
HOP = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "accept-encoding",
       "proxy-connection", "te", "upgrade"}
SECRET = {"authorization", "x-api-key", "api-key", "proxy-authorization"}


def body_value(raw, ctype):
    text = raw.decode("utf-8", "replace")
    if "json" in (ctype or ""):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text


class Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def relay(self):
        n = int(self.headers.get("content-length") or 0)
        req = self.rfile.read(n) if n else b""
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        hdrs["Host"] = UP.netloc
        if req:
            hdrs["Content-Length"] = str(len(req))
        with SNAP_LOCK:  # numbering and snapshot together, so request N's snapshot is taken before request N+1's
            with LOCK:
                SEQ[0] += 1
                seq = SEQ[0]
            take_snapshot(seq)
        rec = {"seq": seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "method": self.command, "path": self.path,
               "req_headers": {k: ("<redacted>" if k.lower() in SECRET else v) for k, v in hdrs.items()},
               "req": body_value(req, self.headers.get("content-type", "json"))}
        t0 = time.time()
        chunks = []
        try:
            conn = (http.client.HTTPSConnection if UP.scheme == "https" else http.client.HTTPConnection)(
                UP.netloc, timeout=900)
            conn.request(self.command, UP.path.rstrip("/") + self.path, body=req or None, headers=hdrs)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            live = SSEText(seq) if "event-stream" in (resp.getheader("content-type") or "") else None
            if live and isinstance(rec["req"], dict):
                live_write(seq, f"[request: {len(rec['req'].get('messages') or [])} messages]")
            while True:
                data = resp.read1(65536)
                if not data:
                    break
                if not chunks:
                    rec["ttfb_s"] = round(time.time() - t0, 3)
                chunks.append(data)
                if live:
                    live.feed(data)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            rec.update(status=resp.status, resp_headers=dict(resp.getheaders()),
                       resp=body_value(b"".join(chunks), resp.getheader("content-type")))
            conn.close()
        except Exception as e:  # upstream or client failure: keep what we have
            rec.update(error=f"{type(e).__name__}: {e}", resp=b"".join(chunks).decode("utf-8", "replace"))
            if not chunks:
                try:
                    self.send_error(502, str(e))
                except Exception:
                    pass
        rec["dur_s"] = round(time.time() - t0, 3)
        with LOCK:
            OUT.write(json.dumps(rec, ensure_ascii=False) + "\n")
            OUT.flush()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = relay


take_snapshot(0)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
srv.daemon_threads = True
print(srv.server_address[1], flush=True)
srv.serve_forever()
