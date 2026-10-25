#!/usr/bin/env python3
"""The only way out of an isolated sandbox: an allowlisting HTTP proxy on a unix socket, outside the sandbox.

    bin/egress-proxy.py SOCKET LOG HOST[,HOST...] [LOCAL_PORT=SOCKET ...]

- SOCKET: a unix socket the sandbox's forwarder (bin/egress-fwd.py) connects to. It speaks HTTP proxy: CONNECT host:port
  for TLS, or a plain request with an absolute http:// URL. A host is allowed when it equals an allowlist entry or ends
  with "." + entry. Everything else gets 403.
- LOCAL_PORT=SOCKET: also relay raw TCP from that unix socket to 127.0.0.1:LOCAL_PORT on the host, e.g. the recording
  proxy of a custom endpoint.
- LOG: one JSON line per attempt, {"t", "host", "port", "allowed"}. bin/net-audit.py reads it.
Prints "ready" when listening.
"""
import asyncio
import json
import os
import sys
import time


def allowed(host, allow):
    host = host.lower().rstrip('.')
    return any(host == a or host.endswith('.' + a) for a in allow)


async def pipe(r, w):
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def splice(r1, w1, r2, w2):
    await asyncio.gather(pipe(r1, w2), pipe(r2, w1))


class Proxy:
    def __init__(self, log, allow):
        self.log = open(log, 'a')
        self.allow = allow

    def note(self, host, port, ok):
        self.log.write(json.dumps(dict(t=round(time.time(), 3), host=host, port=port, allowed=ok)) + '\n')
        self.log.flush()

    async def handle(self, reader, writer):
        try:
            head = await reader.readuntil(b'\r\n\r\n')
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        line = head.split(b'\r\n', 1)[0].decode('latin-1')
        parts = line.split()
        if len(parts) < 3:
            writer.close()
            return
        method, target = parts[0], parts[1]
        if method.upper() == 'CONNECT':
            host, _, port = target.rpartition(':')
            host, port = host.strip('[]'), int(port or 443)
            rest = b''
        else:
            if not target.startswith('http://'):
                writer.write(b'HTTP/1.1 400 Bad Request\r\n\r\n')
                writer.close()
                return
            hostport = target[7:].split('/', 1)[0]
            host, _, port = hostport.partition(':')
            port = int(port or 80)
            path = '/' + target[7:].split('/', 1)[1] if '/' in target[7:] else '/'
            rest = head.replace(target.encode('latin-1'), path.encode('latin-1'), 1)
        ok = allowed(host, self.allow)
        self.note(host, port, ok)
        if not ok:
            writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            await writer.drain()
            writer.close()
            return
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), 30)
        except Exception:
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            await writer.drain()
            writer.close()
            return
        if rest:
            uw.write(rest)
            await uw.drain()
        else:
            writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
            await writer.drain()
        await splice(reader, writer, ur, uw)


async def relay(port, reader, writer):
    try:
        ur, uw = await asyncio.open_connection('127.0.0.1', port)
    except OSError:
        writer.close()
        return
    await splice(reader, writer, ur, uw)


async def main(sock, log, allow, locals_):
    p = Proxy(log, allow)
    servers = [await asyncio.start_unix_server(p.handle, path=sock)]
    for port, path in locals_:
        servers.append(await asyncio.start_unix_server(lambda r, w, port=port: relay(port, r, w), path=path))
    for path in [sock] + [s for _, s in locals_]:
        os.chmod(path, 0o600)
    print('ready', flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    locs = [(int(a.split('=', 1)[0]), a.split('=', 1)[1]) for a in sys.argv[4:]]
    allow = [h.strip().lower() for h in sys.argv[3].split(',') if h.strip()]
    try:
        asyncio.run(main(sys.argv[1], sys.argv[2], allow, locs))
    except KeyboardInterrupt:
        pass
