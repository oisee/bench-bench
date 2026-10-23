#!/usr/bin/env python3
"""Recording reverse proxy for the custom provider: forwards every request to UPSTREAM and
appends the full exchange (request body, response body or raw SSE stream, timing) to a JSONL file.

    bin/record-proxy.py UPSTREAM_ORIGIN OUT.jsonl     e.g. https://llm.example.com runs/.../traffic.jsonl

Listens on a free 127.0.0.1 port and prints it on stdout. API keys are redacted from the log.
Started by bin/run.sh when BENCH_RECORD=1.
"""
import http.client, http.server, json, sys, threading, time, urllib.parse

UP = urllib.parse.urlsplit(sys.argv[1])
OUT = open(sys.argv[2], "a")
LOCK = threading.Lock()
SEQ = [0]
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
        with LOCK:
            SEQ[0] += 1
            seq = SEQ[0]
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
            while True:
                data = resp.read1(65536)
                if not data:
                    break
                chunks.append(data)
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


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
srv.daemon_threads = True
print(srv.server_address[1], flush=True)
srv.serve_forever()
