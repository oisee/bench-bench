#!/usr/bin/env python3
"""Replay recorded agent steps against another model: same context, compare the next move.

    bin/replay.py TRAFFIC.jsonl --steps 10-12,17 --base-url http://host:8081 --model qwen3.8-27b-q4kxl
    bin/replay.py TRAFFIC.jsonl --steps all --dry-run          list steps, context sizes and the original moves

TRAFFIC.jsonl is a run's recording (BENCH_RECORD=1, bin/record-proxy.py) of Anthropic /v1/messages
requests. For step N (the `seq` of a recorded request) the same request body is sent to --base-url with
the model swapped, and the reply is set beside the move the original model made at that step.
Thinking blocks of earlier assistant turns are removed from the history by default (--keep-thinking to
keep them), so the replayed model sees the original's actions and tool results but not its reasoning.

Writes one JSON line per step to --out (default: replay-<model>.jsonl next to TRAFFIC.jsonl) and prints
a side-by-side summary. The output names both models; for blind judging pass only the `original` and `replay`
moves, shuffled, never the model fields or file names. Steps run one at a time. The API key, if any, is read from CUSTOM_API_KEY.
"""
import argparse, copy, json, os, sys, time, urllib.request


def parse_steps(spec, available):
    if spec == "all":
        return available
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return [s for s in out if s in set(available)]


def blocks_from_sse(text):
    """Rebuild content blocks and usage from Anthropic SSE text (or a non-streaming JSON body)."""
    if isinstance(text, dict):  # non-streaming reply
        return text.get("content") or [], text.get("usage") or {}, text.get("stop_reason")
    blocks, usage, stop = {}, {}, None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        t = ev.get("type")
        if t == "message_start":
            usage.update((ev.get("message") or {}).get("usage") or {})
        elif t == "content_block_start":
            b = dict(ev.get("content_block") or {})
            b.setdefault("thinking", "") if b.get("type") == "thinking" else None
            b["_json"] = ""
            blocks[ev.get("index", len(blocks))] = b
        elif t == "content_block_delta":
            b = blocks.setdefault(ev.get("index", 0), {"type": "text", "text": "", "_json": ""})
            d = ev.get("delta") or {}
            if d.get("type") == "thinking_delta":
                b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
            elif d.get("type") == "text_delta":
                b["text"] = b.get("text", "") + d.get("text", "")
            elif d.get("type") == "input_json_delta":
                b["_json"] += d.get("partial_json", "")
        elif t == "message_delta":
            usage.update(ev.get("usage") or {})
            stop = (ev.get("delta") or {}).get("stop_reason") or stop
    out = []
    for _, b in sorted(blocks.items()):
        if b.get("type") == "tool_use" and b.get("_json"):
            try:
                b["input"] = json.loads(b["_json"])
            except ValueError:
                b["input"] = b["_json"]
        b.pop("_json", None)
        out.append(b)
    return out, usage, stop


def move(blocks):
    """The parts of a reply that matter for comparison."""
    return dict(thinking_chars=sum(len(b.get("thinking", "")) for b in blocks if b.get("type") == "thinking"),
                thinking=" ".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking"),
                text=" ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip(),
                tools=[dict(name=b.get("name"), input=b.get("input")) for b in blocks if b.get("type") == "tool_use"])


def strip_thinking(req):
    for m in req.get("messages", []):
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            m["content"] = [c for c in m["content"] if c.get("type") not in ("thinking", "redacted_thinking")] \
                or [{"type": "text", "text": ""}]
    return req


def send(base, req, key, timeout):
    body = json.dumps(req).encode()
    hdr = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if key:
        hdr["x-api-key"] = key; hdr["authorization"] = f"Bearer {key}"
    r = urllib.request.Request(base.rstrip("/") + "/v1/messages", data=body, headers=hdr, method="POST")
    t0, ttfb, chunks = time.time(), None, []
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        while True:
            c = resp.read1(65536)
            if not c:
                break
            ttfb = ttfb if ttfb is not None else time.time() - t0
            chunks.append(c)
    raw = b"".join(chunks).decode("utf-8", "replace")
    return (raw if req.get("stream") else json.loads(raw)), round(time.time() - t0, 2), round(ttfb or 0, 2)


def short_tool(t):
    i = t.get("input")
    s = (i.get("command") or i.get("file_path") or i.get("pattern") or json.dumps(i)) if isinstance(i, dict) else str(i)
    return f"{t.get('name')}: {s[:110]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traffic")
    ap.add_argument("--steps", default="all", help="seq numbers: 3,10-12,17 or all")
    ap.add_argument("--base-url", help="model server origin, e.g. http://192.168.8.107:8081 (no /v1)")
    ap.add_argument("--model")
    ap.add_argument("--max-tokens", type=int, help="cap on the reply (default: the recorded request's max_tokens; "
                    "at least 4096, since lower caps cut long edits)")
    ap.add_argument("--effort", help="override output_config.effort")
    ap.add_argument("--keep-thinking", action="store_true", help="keep the original model's earlier thinking in history")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    recs = {}
    for line in open(a.traffic, errors="replace"):
        r = json.loads(line)
        # agent turns only: skip Claude Code's /v1/messages/count_tokens side requests
        if (isinstance(r.get("req"), dict) and r["req"].get("messages") and r.get("status") == 200
                and "count_tokens" not in (r.get("path") or "")):
            recs[r["seq"]] = r
    steps = parse_steps(a.steps, sorted(recs))
    if not steps:
        sys.exit("no matching steps with a recorded /v1/messages request")
    if not a.dry_run and not (a.base_url and a.model):
        sys.exit("--base-url and --model are required unless --dry-run")
    if a.max_tokens is not None and a.max_tokens < 4096:
        sys.exit("--max-tokens below 4096 truncates long edits and makes every variant score 0 on those steps")
    out_path = a.out or os.path.join(os.path.dirname(os.path.abspath(a.traffic)), f"replay-{a.model or 'dry'}.jsonl")
    out = None if a.dry_run else open(out_path, "a")

    for s in steps:
        rec = recs[s]
        orig_blocks, orig_usage, orig_stop = blocks_from_sse(rec["resp"])
        orig = move(orig_blocks)
        ctx = orig_usage.get("input_tokens", 0) + orig_usage.get("cache_read_input_tokens", 0)
        print(f"\n#{s}  ctx {ctx:,}  msgs {len(rec['req']['messages'])}")
        print(f"  original ({rec['req'].get('model')}): think {orig['thinking_chars']:,} ch · "
              + ("; ".join(short_tool(t) for t in orig["tools"]) or (orig["text"][:120] or "(empty)")))
        if a.dry_run:
            continue
        req = copy.deepcopy(rec["req"])
        req["model"] = a.model
        if a.max_tokens:
            req["max_tokens"] = a.max_tokens
        if a.effort:
            req.setdefault("output_config", {})["effort"] = a.effort
        if not a.keep_thinking:
            strip_thinking(req)
        try:
            resp, dur, ttfb = send(a.base_url, req, os.environ.get("CUSTOM_API_KEY"), a.timeout)
            blocks, usage, stop = blocks_from_sse(resp)
            rep, err = move(blocks), None
        except Exception as e:  # keep going; record the failure
            rep, usage, stop, dur, ttfb, err = None, {}, None, None, None, f"{type(e).__name__}: {e}"
        if rep:
            print(f"  replay   ({a.model}): think {rep['thinking_chars']:,} ch · "
                  + ("; ".join(short_tool(t) for t in rep["tools"]) or (rep["text"][:120] or "(empty)"))
                  + f"  [{dur}s, ttfb {ttfb}s, out {usage.get('output_tokens', '?')}, {stop}]")
        else:
            print(f"  replay   ({a.model}): ERROR {err}")
        out.write(json.dumps(dict(step=s, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), source=os.path.abspath(a.traffic),
                                  original_model=rec["req"].get("model"), replay_model=a.model, context_tokens=ctx,
                                  thinking_stripped=not a.keep_thinking, original=orig, original_stop=orig_stop,
                                  replay=rep, replay_stop=stop, replay_usage=usage, dur_s=dur, ttfb_s=ttfb, error=err),
                             ensure_ascii=False) + "\n")
        out.flush()
    if out:
        print(f"\n-> {out_path}")


if __name__ == "__main__":
    main()
