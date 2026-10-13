#!/usr/bin/env python3
"""Export every run, review and finding into flat files for analysis / visualisation.

    bin/export.py            -> data/runs.csv, data/reviews.csv, data/findings.csv, data/bench.json

Token / call metrics are parsed from each harness's own transcript:
  claude  agent.out (stream-json): usage summed over all `result` events, last total_cost_usd; tool_use blocks
  codex   agent.out (--json): sum of turn.completed usage; item types (command_execution, file_change, ...)
  pi      home/.pi/agent/sessions/**.jsonl: per-assistant-message usage and toolCall blocks
Unknown values stay empty; nothing is estimated.
"""
import csv, glob, json, os, re, collections

B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(B, "data")
TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search"}


def jlines(path):
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        yield json.loads(line)
                    except ValueError:
                        pass
    except FileNotFoundError:
        return


def metrics_claude(d, out_name):
    """A session can emit several `result` events (one per query, e.g. after background tasks);
    each carries that query's usage, while modelUsage / total_cost_usd are cumulative. A session
    killed at the time limit has no `result`: tokens are then summed per message id from the session
    log in home/.claude/projects (matches the `result` totals exactly where both exist); the stream's
    own per-message usage is only a fallback for input, as it carries partial or zero output counts."""
    m = dict(llm_calls=None, tool_calls=0, input_tokens=None, cached_input_tokens=None,
             cache_write_tokens=None, output_tokens=None, reasoning_tokens=None, notional_cost_usd=None,
             web_searches=None, web_fetches=None)
    ids, tools, per_msg, results = set(), collections.Counter(), {}, []
    for e in jlines(os.path.join(d, out_name)):
        if e.get("type") == "assistant":
            msg = e.get("message") or {}
            ids.add(msg.get("id")); per_msg[msg.get("id")] = msg.get("usage") or {}
            for b in msg.get("content") or []:
                if b.get("type") == "tool_use":
                    m["tool_calls"] += 1; tools[b.get("name")] += 1
        if e.get("type") == "result":
            results.append(e)
    if results:
        tot = collections.Counter()
        for e in results:
            u = e.get("usage") or {}
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
                tot[k] += u.get(k) or 0
            tot["thinking"] += (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
            st = u.get("server_tool_use") or {}
            tot["ws"] += st.get("web_search_requests") or 0; tot["wf"] += st.get("web_fetch_requests") or 0
        cr, cw = tot["cache_read_input_tokens"], tot["cache_creation_input_tokens"]
        m.update(input_tokens=tot["input_tokens"] + cr + cw, cached_input_tokens=cr, cache_write_tokens=cw,
                 output_tokens=tot["output_tokens"], reasoning_tokens=tot["thinking"] or None,
                 notional_cost_usd=results[-1].get("total_cost_usd"), web_searches=tot["ws"], web_fetches=tot["wf"])
    elif per_msg:
        # the session log (home/.claude/projects) holds each message's final usage; the stream may hold zeros
        sess = {}
        if out_name == "agent.out":
            for f in glob.glob(os.path.join(d, "home/.claude/projects/*/*.jsonl")):
                for e in jlines(f):
                    if e.get("type") == "assistant":
                        msg = e.get("message") or {}; sess[msg.get("id")] = msg.get("usage") or {}
        us = [u for k, u in (sess or per_msg).items() if k]
        if sess:
            m.update(output_tokens=sum(u.get("output_tokens") or 0 for u in us),
                     reasoning_tokens=sum((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0 for u in us) or None)
        cr = sum(u.get("cache_read_input_tokens") or 0 for u in us); cw = sum(u.get("cache_creation_input_tokens") or 0 for u in us)
        m.update(input_tokens=sum(u.get("input_tokens") or 0 for u in us) + cr + cw, cached_input_tokens=cr, cache_write_tokens=cw)
    m["llm_calls"] = len(ids - {None}) or None
    m["tools_by_name"] = dict(tools)
    return m


def metrics_codex(d, out_name):
    m = dict(llm_calls=None, tool_calls=0, input_tokens=0, cached_input_tokens=0, cache_write_tokens=0,
             output_tokens=0, reasoning_tokens=0, notional_cost_usd=None, web_searches=0, web_fetches=None)
    tools, seen = collections.Counter(), False
    for e in jlines(os.path.join(d, out_name)):
        if e.get("type") == "turn.completed":
            u = e.get("usage") or {}; seen = True
            m["input_tokens"] += u.get("input_tokens", 0); m["cached_input_tokens"] += u.get("cached_input_tokens", 0)
            m["cache_write_tokens"] += u.get("cache_write_input_tokens", 0); m["output_tokens"] += u.get("output_tokens", 0)
            m["reasoning_tokens"] += u.get("reasoning_output_tokens", 0)
        if e.get("type") == "item.completed":
            t = (e.get("item") or {}).get("type")
            if t in TOOL_ITEMS:
                m["tool_calls"] += 1; tools[t] += 1
                if t == "web_search": m["web_searches"] += 1
    if not seen:
        for k in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"): m[k] = None
    m["tools_by_name"] = dict(tools)
    return m


def metrics_pi(d):
    m = dict(llm_calls=0, tool_calls=0, input_tokens=0, cached_input_tokens=0, cache_write_tokens=0,
             output_tokens=0, reasoning_tokens=0, notional_cost_usd=0.0, web_searches=None, web_fetches=None)
    tools = collections.Counter()
    files = glob.glob(os.path.join(d, "home/.pi/agent/sessions/**/*.jsonl"), recursive=True)
    for f in files:
        for e in jlines(f):
            msg = e.get("message") or {}
            if msg.get("role") != "assistant": continue
            m["llm_calls"] += 1
            u = msg.get("usage") or {}
            m["input_tokens"] += u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0)
            m["cached_input_tokens"] += u.get("cacheRead", 0); m["cache_write_tokens"] += u.get("cacheWrite", 0)
            m["output_tokens"] += u.get("output", 0); m["reasoning_tokens"] += u.get("reasoning", 0) or 0
            m["notional_cost_usd"] += (u.get("cost") or {}).get("total", 0) or 0
            for b in msg.get("content") or []:
                if b.get("type") == "toolCall":
                    m["tool_calls"] += 1; tools[b.get("name")] += 1
    if not files:
        for k in m: m[k] = None
    elif not m["notional_cost_usd"]:
        m["notional_cost_usd"] = None
    m["tools_by_name"] = dict(tools)
    return m


def metrics(d, harness, out_name):
    if harness == "claude": return metrics_claude(d, out_name)
    if harness == "codex": return metrics_codex(d, out_name)
    if harness == "pi": return metrics_pi(d)
    return {}


def reviewed_metrics(d, harness):
    """Sum independent workflow stages; keep unknown metrics unknown."""
    stage_file = os.path.join(d, "internal-reviews", "stages.jsonl")
    stages = [row["stage"] for row in jlines(stage_file)]
    if harness == "pi":
        parts = [metrics_pi(d)]
        parts += [metrics_pi(os.path.join(d, "internal-reviews", name))
                  for name in stages if name.startswith("critic-")]
    else:
        parts = [metrics(d, harness, f"internal-reviews/{name}.out") for name in stages]
    if not parts:
        return metrics(d, harness, "agent.out")
    out = {}
    for key in parts[0]:
        if key == "tools_by_name":
            counts = collections.Counter()
            for part in parts:
                counts.update(part.get(key) or {})
            out[key] = dict(counts)
        else:
            values = [part.get(key) for part in parts]
            out[key] = sum(values) if all(isinstance(value, (int, float)) for value in values) else None
    return out


def matrix():
    ex, cr = {}, {}
    for line in open(os.path.join(B, "matrix.yaml")):
        if line.lstrip().startswith("#"): continue
        mm = re.search(r'\{id:\s*([^,]+),\s*provider:\s*([^,]+),\s*model:\s*"?([^,"]+)"?,\s*harness:\s*([^,]+),\s*effort:\s*([^,}]+)', line)
        if mm:
            row = dict(provider=mm.group(2).strip(), model=mm.group(3).strip(), harness=mm.group(4).strip(), effort=mm.group(5).strip())
            (cr if mm.group(1).startswith(("K", "S-")) else ex)[mm.group(1).strip()] = row
    return ex, cr


def main():
    os.makedirs(OUT, exist_ok=True)
    executors, critics = matrix()
    amap = {}
    mp = os.path.join(B, "runs/_review/map.json")
    if os.path.exists(mp): amap = json.load(open(mp))
    inv = {v: k for k, v in amap.items()}

    runs, reviews, findings = [], [], []
    for rj in sorted(glob.glob(os.path.join(B, "runs/L*/*/result.json"))):
        d = os.path.dirname(rj); rel = os.path.relpath(d, os.path.join(B, "runs"))
        r = json.load(open(rj))
        meta = json.load(open(os.path.join(d, "meta.json"))) if os.path.exists(os.path.join(d, "meta.json")) else {}
        met = (reviewed_metrics(d, r.get("harness")) if meta.get("protocol")
               else metrics(d, r.get("harness"), "agent.out"))
        run_id = rel
        anon = inv.get(rel)
        jury_override = os.path.exists(os.path.join(d, "jury-override.json"))
        row = dict(run_id=run_id, anon_id=anon, task=r["task"], level=r["level"],
                   round=int(meta.get("round", 1)), executor=r["executor"],
                   protocol=meta.get("protocol"), internal_review_rounds=meta.get("internal_review_rounds", 0),
                   grade_fix=r.get("grade_fix"),
                   provider=r["provider"], model=r["model"], harness=r["harness"], harness_version=r.get("harness_version"),
                   effort=r["effort"], status=r["status"], stop=r["stop"], agent_exit=r["agent_exit"],
                   duration_s=r["duration_s"], patch_lines=r["patch_lines"],
                   f2p_total=len(r["f2p"]), f2p_pass=sum(c["rc"] == 0 for c in r["f2p"]),
                   p2p_total=len(r["p2p"]), p2p_pass=sum(c["rc"] == 0 for c in r["p2p"]),
                   leak_suspect=r.get("leak_suspect", False), started_at=meta.get("started_at"), finished_at=meta.get("finished_at"))
        if jury_override:
            row.update(json.load(open(os.path.join(d, "jury-override.json"))))
        row.update({k: v for k, v in met.items() if k != "tools_by_name"})
        row["tools_by_name"] = json.dumps(met.get("tools_by_name", {}), ensure_ascii=False)
        runs.append(row)

        if not anon: continue
        for rv in sorted(glob.glob(os.path.join(B, f"runs/_review/{anon}/*/review.json"))):
            v = json.load(open(rv)); rd = os.path.dirname(rv); c = v.get("critic")
            cinfo = critics.get(c, {})
            cm = metrics(rd, cinfo.get("harness"), "critic.out")
            review_id = f"{anon}/{c}"
            reviews.append(dict(review_id=review_id, run_id=run_id, anon_id=anon, critic=c,
                                critic_model=v.get("critic_model") or f"{cinfo.get('provider')}/{cinfo.get('model')}",
                                critic_harness=cinfo.get("harness"), task=r["task"], executor=r["executor"],
                                run_status=r["status"], verdict=v.get("verdict"), quality=v.get("quality"),
                                self_review=v.get("self_review", False), duration_s=v.get("duration_s"),
                                error=v.get("error"), summary=v.get("summary"),
                                n_findings=len(v.get("findings", [])),
                                **{f"n_{s}": sum(1 for x in v.get("findings", []) if x.get("severity") == s) for s in ("P0", "P1", "P2", "P3")},
                                **{k: val for k, val in cm.items() if k != "tools_by_name"}))
            for i, x in enumerate(v.get("findings", [])):
                findings.append(dict(finding_id=f"{review_id}#{i}", review_id=review_id, run_id=run_id, critic=c,
                                     task=r["task"], executor=r["executor"], run_status=r["status"],
                                     severity=x.get("severity"), file=x.get("file"), line=x.get("line"),
                                     claim=x.get("claim"), repro=x.get("repro"),
                                     confirmed=None, note=None))
    # manual confirmations (bench/confirmations.json): {"<finding_id or review_id>": {"confirmed": true/false, "note": "..."}}
    cp = os.path.join(B, "confirmations.json")
    if os.path.exists(cp):
        conf = json.load(open(cp))
        for f in findings:
            c = conf.get(f["finding_id"])
            if c: f.update(confirmed=c.get("confirmed"), note=c.get("note"))

    # ---- cost ----------------------------------------------------------------------
    prices = json.load(open(os.path.join(B, "prices.json"))) if os.path.exists(os.path.join(B, "prices.json")) else {}
    api, plans = prices.get("api_per_mtok", {}), prices.get("plans", {})

    def price_for(model):
        p = api.get(model)
        while isinstance(p, dict) and p.get("same_as"): p = api.get(p["same_as"])
        return p

    def cost_fields(row):
        p = price_for(row["model"]); api_cost = None
        if isinstance(p, dict) and p.get("use_harness_cost"):
            api_cost = row.get("notional_cost_usd")
        elif isinstance(p, dict) and row.get("input_tokens") is not None and row.get("output_tokens") is not None:
            cached = row.get("cached_input_tokens") or 0
            api_cost = ((row["input_tokens"] - cached) * p["input"] + cached * p.get("cache_read", p["input"]) + row["output_tokens"] * p["output"]) / 1e6
        plan = plans.get(row["provider"]) or {}
        plan_cost = None
        if plan.get("usd_per_month") and plan.get("units_per_month") and plan.get("unit") == "llm_call" and row.get("llm_calls"):
            plan_cost = row["llm_calls"] * plan["usd_per_month"] / plan["units_per_month"]
        return dict(cost_api_usd=round(api_cost, 4) if api_cost is not None else None,
                    cost_plan_usd=round(plan_cost, 4) if plan_cost is not None else None,
                    total_tokens=(row.get("input_tokens") or 0) + (row.get("output_tokens") or 0) or None)
    for r in runs: r.update(cost_fields(r))

    # ---- comparative scores (see data/README.md) ---------------------------------
    VERDICT = {"correct": 1.0, "uncertain": 0.5, "incorrect": 0.0}
    by_run = collections.defaultdict(list)
    for v in reviews:
        if v.get("verdict") and not v.get("self_review"): by_run[v["run_id"]].append(v)
    for r in runs:
        js = by_run.get(r["run_id"], [])
        r["jury_n"] = len(js)
        r["jury_correct_share"] = round(sum(VERDICT.get(v["verdict"], 0) for v in js) / len(js), 3) if js else None
        qs = [v["quality"] for v in js if isinstance(v.get("quality"), (int, float))]
        r["jury_quality_mean"] = round(sum(qs) / len(qs), 2) if qs else None
        r["jury_P0"] = sum(v["n_P0"] for v in js); r["jury_P1"] = sum(v["n_P1"] for v in js)
        r["jury_penalty"] = round(sum(v["n_P0"] + 0.5 * v["n_P1"] + 0.1 * v["n_P2"] for v in js) / len(js), 3) if js else None
        r["effective_resolved"] = r["status"] == "RESOLVED" and not r.get("jury_override", False)
        q = ((r["jury_quality_mean"] or 1) - 1) / 4
        sc = 100 * (0.6 * r["effective_resolved"] + 0.25 * q + 0.15 * (r["jury_correct_share"] or 0)) - 10 * (r["jury_penalty"] or 0)
        r["score"] = round(max(0.0, min(100.0, sc)), 1)
    # Rank and calculate frontiers only within the same task and protocol
    # cohort. Round-2 reviewed runs must not alter historical round-1 ranks.
    for task, round_no, protocol in {(r["task"], r["round"], r["protocol"]) for r in runs}:
        rs = [r for r in runs if (r["task"], r["round"], r["protocol"]) ==
              (task, round_no, protocol)]
        for i, r in enumerate(sorted(rs, key=lambda r: (-r["score"], r["duration_s"]))): r["rank_in_task"] = i + 1
        durs = sorted(r["duration_s"] for r in rs); med = durs[len(durs) // 2]
        for r in rs:
            r["duration_vs_median"] = round(r["duration_s"] / med, 2) if med else None
            r["n_runs_in_task"] = len(rs)
        ok = [r for r in rs if r.get("output_tokens")]
        for i, r in enumerate(sorted(ok, key=lambda r: r["output_tokens"])): r["output_tokens_rank"] = i + 1
        # Pareto fronts: not dominated on (score higher, X lower)
        def front(key, flag):
            pts = [r for r in rs if r.get(key) is not None]
            for r in rs: r[flag] = None if r.get(key) is None else False
            for r in pts:
                r[flag] = not any(o is not r and o["score"] >= r["score"] and o[key] <= r[key] and
                                  (o["score"] > r["score"] or o[key] < r[key]) for o in pts)
        front("duration_s", "pareto_quality_speed")
        front("total_tokens", "pareto_quality_tokens")
        front("cost_api_usd", "pareto_quality_cost")
        for key, name in (("duration_s", "fastest_solved"), ("total_tokens", "fewest_tokens_solved"), ("cost_api_usd", "cheapest_solved")):
            solved = [r for r in rs if r["effective_resolved"] and r.get(key) is not None]
            best = min(solved, key=lambda r: r[key]) if solved else None
            for r in rs: r[name] = r is best
        best_q = max(rs, key=lambda r: (r["score"], -r["duration_s"]))
        for r in rs: r["best_quality"] = r is best_q
    # The existing leaderboard is the original single-session cohort.
    board = []
    baseline = [r for r in runs if r["round"] == 1 and not r["protocol"]]
    for ex in sorted({r["executor"] for r in baseline}):
        rs = [r for r in baseline if r["executor"] == ex]
        board.append(dict(executor=ex, provider=rs[0]["provider"], model=rs[0]["model"], harness=rs[0]["harness"], effort=rs[0]["effort"],
                          runs=len(rs), solved=sum(r["effective_resolved"] for r in rs),
                          mean_score=round(sum(r["score"] for r in rs) / len(rs), 1),
                          mean_rank=round(sum(r["rank_in_task"] for r in rs) / len(rs), 2),
                          total_duration_s=sum(r["duration_s"] for r in rs),
                          total_output_tokens=sum(r.get("output_tokens") or 0 for r in rs) or None,
                          total_input_tokens=sum(r.get("input_tokens") or 0 for r in rs) or None,
                          total_tool_calls=sum(r.get("tool_calls") or 0 for r in rs),
                          total_cost_api_usd=round(sum(r["cost_api_usd"] for r in rs), 4) if all(r.get("cost_api_usd") is not None for r in rs) else None,
                          total_cost_plan_usd=round(sum(r["cost_plan_usd"] for r in rs), 4) if all(r.get("cost_plan_usd") is not None for r in rs) else None,
                          pareto_speed_hits=sum(bool(r.get("pareto_quality_speed")) for r in rs),
                          pareto_tokens_hits=sum(bool(r.get("pareto_quality_tokens")) for r in rs)))
    board.sort(key=lambda b: (-b["solved"], -b["mean_score"], b["total_duration_s"]))
    for i, b in enumerate(board): b["overall_rank"] = i + 1
    # critic accuracy vs ground truth (effective_resolved)
    truth = {r["run_id"]: r["effective_resolved"] for r in runs}
    critic_stats = []
    for c in sorted({v["critic"] for v in reviews}):
        vs = [v for v in reviews if v["critic"] == c and v.get("verdict")]
        agree = sum((v["verdict"] == "correct") == truth[v["run_id"]] for v in vs)
        critic_stats.append(dict(critic=c, critic_model=vs[0]["critic_model"] if vs else None, reviews=len(vs),
                                 errors=sum(1 for v in reviews if v["critic"] == c and not v.get("verdict")),
                                 agreement=round(agree / len(vs), 3) if vs else None,
                                 said_correct_on_solved=sum(v["verdict"] == "correct" for v in vs if truth[v["run_id"]]),
                                 solved_reviewed=sum(1 for v in vs if truth[v["run_id"]]),
                                 said_incorrect_on_unsolved=sum(v["verdict"] == "incorrect" for v in vs if not truth[v["run_id"]]),
                                 unsolved_reviewed=sum(1 for v in vs if not truth[v["run_id"]]),
                                 p0_p1_reported=sum(v["n_P0"] + v["n_P1"] for v in vs),
                                 confirmed_true=sum(1 for f in findings if f["critic"] == c and f["confirmed"] is True),
                                 confirmed_false=sum(1 for f in findings if f["critic"] == c and f["confirmed"] is False),
                                 mean_duration_s=round(sum(v["duration_s"] or 0 for v in vs) / len(vs)) if vs else None,
                                 output_tokens=sum(v.get("output_tokens") or 0 for v in vs) or None))

    def write(name, rows):
        if not rows: return
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(os.path.join(OUT, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)

    write("runs.csv", runs); write("reviews.csv", reviews); write("findings.csv", findings)
    write("leaderboard.csv", board); write("critics.csv", critic_stats)
    tasks = {}
    for t in glob.glob(os.path.join(B, "tasks/*/task.env")):
        env = {}
        for line in open(t):
            mm = re.match(r'^([A-Z_0-9]+)=(.*)$', line.strip())
            if mm: env[mm.group(1)] = mm.group(2).strip("'\"")
        tasks[os.path.basename(os.path.dirname(t))] = {k: env.get(k) for k in ("REPO", "BASE", "MERGE", "LEVEL", "TIME_MIN")}
    json.dump(dict(generated_from=B, tasks=tasks, executors=executors, critics=critics,
                   runs=runs, reviews=reviews, findings=findings,
                   leaderboard=board, critic_stats=critic_stats),
              open(os.path.join(OUT, "bench.json"), "w"), indent=1, ensure_ascii=False, default=str)
    print(f"runs={len(runs)} reviews={len(reviews)} findings={len(findings)} -> {OUT}")


if __name__ == "__main__":
    main()
