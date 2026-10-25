#!/usr/bin/env python3
"""Inside an isolated sandbox (no network but its own loopback): forward local TCP ports to unix sockets that lead
out through bin/egress-proxy.py.

    egress-fwd.py PORT=SOCKET [PORT=SOCKET ...]

Prints "ready" when listening, then runs until killed.
"""
import asyncio
import sys


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


async def handle(path, reader, writer):
    try:
        ur, uw = await asyncio.open_unix_connection(path)
    except OSError:
        writer.close()
        return
    await asyncio.gather(pipe(reader, uw), pipe(ur, writer))


async def main(maps):
    servers = [await asyncio.start_server(lambda r, w, p=path: handle(p, r, w), '127.0.0.1', port) for port, path in maps]
    print('ready', flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == '__main__':
    maps = [(int(a.split('=', 1)[0]), a.split('=', 1)[1]) for a in sys.argv[1:]]
    if not maps:
        sys.exit(__doc__)
    asyncio.run(main(maps))
