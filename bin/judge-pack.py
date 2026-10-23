#!/usr/bin/env python3
"""Build blind judge input from bin/replay.py results.

    bin/judge-pack.py --replay iq4=replay-a.jsonl --replay k256=replay-b.jsonl --out-dir judge/L2b [--seed 1]

Each --replay is LABEL=FILE (the label is for the key only; judges never see it). For every step present in all
files the pack holds: the task brief, the context the model saw (the last messages, trimmed), the reference move
(what the recorded model actually did), and one candidate move per replay file, shuffled into letters A, B, ...
Model names, file names and model fields are left out. A move whose reply hit max_tokens is marked
`"capped": true`, so a truncated thinking pass is not scored as a wrong action.

Writes judge-input.jsonl (one step per line) and judge-key.json (step -> letter -> label) to --out-dir.
"""
import argparse, json, os, random

CTX_CHARS = 6000  # tail of the visible context handed to the judge


def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        t = c.get("type")
        if t == "text":
            out.append(c.get("text", ""))
        elif t == "tool_use":
            out.append(f"[tool call {c.get('name')}] {json.dumps(c.get('input'), ensure_ascii=False)}")
        elif t == "tool_result":
            out.append(f"[tool result] {text_of(c.get('content'))}")
    return "\n".join(out)


def context_tail(req):
    msgs = req.get("messages") or []
    brief = text_of(msgs[0]["content"]) if msgs else ""
    tail, size = [], 0
    for m in reversed(msgs[1:]):
        s = f"--- {m['role']}\n{text_of(m['content'])}"
        if size + len(s) > CTX_CHARS and tail:
            break
        tail.append(s[-CTX_CHARS:]); size += len(s)
    return brief, "\n".join(reversed(tail))


def clean(move, capped):
    if move is None:
        return {"error": "no reply"}
    return {"thinking": move.get("thinking", ""), "text": move.get("text", ""),
            "tool_calls": move.get("tools", []), "capped": capped}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="append", required=True, help="LABEL=replay.jsonl")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    runs = {}
    for spec in a.replay:
        label, _, path = spec.partition("=")
        rows = {}
        for line in open(path):
            r = json.loads(line)
            rows[r["step"]] = r  # a later line for the same step wins (re-runs)
        runs[label] = rows
    steps = sorted(set.intersection(*(set(r) for r in runs.values())))
    if not steps:
        raise SystemExit("no step is present in every replay file")

    traffic = {}
    src = next(iter(runs.values()))[steps[0]]["source"]
    for line in open(src):
        r = json.loads(line)
        traffic[r["seq"]] = r["req"]

    os.makedirs(a.out_dir, exist_ok=True)
    key = {}
    with open(os.path.join(a.out_dir, "judge-input.jsonl"), "w") as out:
        for s in steps:
            first = next(iter(runs.values()))[s]
            brief, ctx = context_tail(traffic[s])
            labels = list(runs)
            rng.shuffle(labels)
            letters = {chr(65 + i): lab for i, lab in enumerate(labels)}
            key[s] = letters
            cands = {L: clean(runs[lab][s]["replay"], runs[lab][s].get("replay_stop") == "max_tokens")
                     for L, lab in letters.items()}
            out.write(json.dumps(dict(step=s, task_brief=brief, context_tail=ctx,
                                      reference=clean(first["original"], first.get("original_stop") == "max_tokens"),
                                      candidates=cands), ensure_ascii=False) + "\n")
    json.dump(key, open(os.path.join(a.out_dir, "judge-key.json"), "w"), indent=1)
    print(f"{len(steps)} steps, {len(runs)} candidate(s) each -> {a.out_dir}/judge-input.jsonl (+ judge-key.json, keep it from judges)")


if __name__ == "__main__":
    main()
