#!/usr/bin/env python3
"""Summarize recorded model traffic (traffic.jsonl from BENCH_RECORD=1).

    bin/traffic-summary.py runs/<task>/<run>/traffic.jsonl [...]      totals per file
    bin/traffic-summary.py --requests FILE                             one line per request
    bin/traffic-summary.py --csv OUT.csv FILE [...]                    per-request rows for analysis

Reads Anthropic /v1/messages (JSON or SSE) and OpenAI chat/responses usage. Generation speed is
output tokens / (duration - time to first byte); ttfb is recorded by bin/record-proxy.py.
"""
import collections, csv, json, re, sys

INT = lambda pat, s: [int(x) for x in re.findall(pat, s)]


def parse(rec):
    req = rec["req"] if isinstance(rec.get("req"), dict) else {}
    resp = rec.get("resp")
    text = resp if isinstance(resp, str) else json.dumps(resp or {})
    inp = INT(r'"(?:input_tokens|prompt_tokens)":\s*(\d+)', text)
    out = INT(r'"(?:output_tokens|completion_tokens)":\s*(\d+)', text)
    tools = re.findall(r'"type":\s*"tool_use",\s*"id":\s*"[^"]*",\s*"name":\s*"([^"]+)"', text)
    tools += re.findall(r'"function":\s*\{\s*"name":\s*"([^"]+)"', text)
    stop = re.findall(r'"(?:stop_reason|finish_reason)":\s*"([^"]+)"', text)
    thinking = sum(len(x) for x in re.findall(r'"thinking(?:_delta)?":\s*"((?:[^"\\]|\\.)*)"', text))
    dur, ttfb = rec.get("dur_s") or 0.0, rec.get("ttfb_s")
    o = max(out or [0])
    gen = dur - ttfb if ttfb is not None else None
    return dict(seq=rec.get("seq"), ts=rec.get("ts"), status=rec.get("status"), error=rec.get("error"),
                path=rec.get("path"), model=req.get("model"), effort=(req.get("output_config") or {}).get("effort")
                or req.get("reasoning_effort"), max_tokens=req.get("max_tokens"),
                messages=len(req.get("messages") or []), ctx=max(inp or [0]), out=o,
                dur_s=round(dur, 3), ttfb_s=ttfb,
                tok_s=round(o / gen, 1) if gen and gen > 0.05 and o else (round(o / dur, 1) if dur and o else None),
                tools=",".join(tools), stop=stop[-1] if stop else None, thinking_chars=thinking)


def load(path):
    rows = []
    for line in open(path, errors="replace"):
        line = line.strip()
        if line.startswith("{"):
            rows.append(parse(json.loads(line)))
    return rows


def summary(path, rows):
    ok = [r for r in rows if r["status"] == 200]
    out = sum(r["out"] for r in ok); dur = sum(r["dur_s"] for r in rows)
    ttfb = [r["ttfb_s"] for r in ok if r["ttfb_s"] is not None]
    long_ = [r["tok_s"] for r in ok if r["tok_s"] and r["out"] >= 1000]
    tools = collections.Counter(t for r in ok for t in r["tools"].split(",") if t)
    stops = collections.Counter(r["stop"] for r in ok)
    print(f"\n{path}")
    print(f"  requests       {len(rows)}  (errors: {len(rows) - len(ok)})")
    print(f"  model time     {dur / 60:.1f} min   (span {rows[0]['ts'][11:19]} - {rows[-1]['ts'][11:19]})" if rows else "")
    print(f"  output tokens  {out:,}   context: first {ok[0]['ctx']:,}, max {max(r['ctx'] for r in ok):,}" if ok else "")
    print(f"  input tokens   {sum(r['ctx'] for r in ok):,} summed over requests (every request re-sends the context)")
    if ttfb:
        s = sorted(ttfb)
        print(f"  first byte     median {s[len(s) // 2]:.2f}s, p90 {s[int(len(s) * .9)]:.2f}s, max {s[-1]:.2f}s")
    if long_:
        s = sorted(long_)
        print(f"  speed          {s[len(s) // 2]:.0f} tok/s median on answers of 1k+ tokens ({len(s)} of them)")
    print(f"  overall        {out / dur:.0f} tok/s (output tokens / total request time)" if dur else "")
    print(f"  tool calls     {sum(tools.values())}: " + ", ".join(f"{k} {v}" for k, v in tools.most_common(8)))
    print(f"  stop reasons   " + ", ".join(f"{k} {v}" for k, v in stops.most_common()))
    errs = [r for r in rows if r["status"] != 200]
    for r in errs[:5]:
        print(f"  ! #{r['seq']} {r['status']} {r['error'] or ''}")


def main(argv):
    mode, out_csv, files = "summary", None, []
    it = iter(argv)
    for a in it:
        if a == "--requests": mode = "requests"
        elif a == "--csv": mode, out_csv = "csv", next(it)
        elif a in ("-h", "--help"): print(__doc__); return 0
        else: files.append(a)
    if not files:
        print(__doc__); return 2
    all_rows = []
    for f in files:
        rows = load(f)
        if mode == "summary": summary(f, rows)
        elif mode == "requests":
            print(f"\n{f}\n  {'#':>4} {'time':8} {'ctx':>7} {'out':>6} {'dur':>6} {'ttfb':>5} {'tok/s':>6}  tools / stop")
            for r in rows:
                tt = f"{r['ttfb_s']:.2f}" if r["ttfb_s"] is not None else "-"
                print(f"  {r['seq']:>4} {r['ts'][11:19]} {r['ctx']:>7} {r['out']:>6} {r['dur_s']:>6.1f} {tt:>5} "
                      f"{r['tok_s'] or '-':>6}  {r['tools'] or ''} {r['stop'] or r['status']}")
        all_rows += [dict(file=f, **r) for r in rows]
    if mode == "csv":
        with open(out_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(all_rows[0])); w.writeheader(); w.writerows(all_rows)
        print(f"{len(all_rows)} requests -> {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
