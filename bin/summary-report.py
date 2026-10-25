#!/usr/bin/env python3
"""Build reports/summary.html: a one-page summary with charts.

Reads data/runs.csv (bin/export.py), data/critic-screen.csv (bin/critic-screen.py),
data/critic-adjudication.json (reference checks of rejected passing patches),
data/first-green.csv and data/decode-speed.csv (local runs)
and embeds them as JSON; the page draws everything client-side. Re-run after either
source changes:  python3 -I bin/summary-report.py
"""
import csv, datetime, json, os

B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = [("summary-report.template.en.html", "summary.html"), ("summary-report.template.ru.html", "summary.ru.html")]

NAMES = {
    "ant-opus-claude": ("Opus", "claude"), "az-sol61-codex": ("Sol 6.1", "codex"),
    "zai-glm53-claude": ("GLM-5.3", "claude"), "zai-glm53-pi": ("GLM-5.3", "pi"),
    "zai-glm53-codex": ("GLM-5.3", "codex"), "oai-luna-codex-x": ("Luna xhigh", "codex"),
    "oai-luna-codex-h": ("Luna high", "codex"), "oai-luna-codex-m": ("Luna medium", "codex"),
    "ali-qwen37-pi": ("Qwen 3.7+", "pi"), "ali-qwen37-codex": ("Qwen 3.7+", "codex"),
    "ali-glm5-pi": ("GLM-5", "pi"), "ali-kimi25-pi": ("Kimi K2.5", "pi"),
    "ali-kimi25-codex": ("Kimi K2.5", "codex"), "ali-minimax25-pi": ("MiniMax M2.5", "pi"),
    "ali-minimax25-codex": ("MiniMax M2.5", "codex"),
    "az-sol61-codex-hi": ("Sol 6.1 high", "codex"), "az-sol61-codex-gate": ("Sol 6.1 + final gate", "codex"),
    "oai-luna-codex-m-vis": ("Luna medium + visible checks", "codex"), "oai-luna-codex-h-vis": ("Luna high + visible checks", "codex"),
    "custom-qwen38fn-claude": ("Qwen3.8-Flash-Next NVFP4", "claude"), "custom-qwen38fn-pi": ("Qwen3.8-Flash-Next NVFP4", "pi"),
    "custom-qwen38fn-iq4-i7-claude": ("Qwen3.8-Flash-Next IQ4_XS (i7)", "claude"),
    "custom-qwen38fn-k224-i7-claude": ("Qwen3.8-Flash-Next DSLab K224 (i7)", "claude"),
    "custom-qwen38fn-k224-mtp-ng-i7-claude": ("K224 variant: MTP + n-gram (i7)", "claude"),
    "custom-qwen38fn-k224-mtp-ng-t0-i7-claude": ("K224 variant: MTP + n-gram, temperature 0 (i7)", "claude"),
    "custom-qwen38fn-k224-ng-t06-i7-claude": ("K224 variant: n-gram, temperature 0.6 (i7)", "claude"),
    "custom-qwen38fn-implant16-i7-claude": ("Qwen3.8-Flash-Next K224-implant16 (i7)", "claude"),
    "custom-qwen38fn-k192pimp16-i7-claude": ("Qwen3.8-Flash-Next K192p-implant16 (i7)", "claude"),
    "custom-qwen38fn-dslab192-mtp-i7-claude": ("Qwen3.8-Flash-Next DSLab 192, MTP + n-gram (i7)", "claude"),
    "custom-qwen38fn-dslab192-q51-i7-claude": ("Qwen3.8-Flash-Next DSLab 192, V cache q5_1 (i7)", "claude"),
    "custom-qwen38fn-dslab192-q4h-i7-claude": ("Qwen3.8-Flash-Next DSLab 192, Q4 MTP head (i7)", "claude"),
    "custom-qwen38fn-dslab192p-i7-claude": ("Qwen3.8-Flash-Next DSLab 192p (i7)", "claude"),
    "custom-qwen38fn-iq2-dslab192-mtp-i7-claude": ("Qwen3.8-Flash-Next 2-bit, 192 experts (i7)", "claude"),
    "custom-qwen38fn-iq2-dslab280-mtp-i7-claude": ("Qwen3.8-Flash-Next 2-bit, 280 experts (i7)", "claude"),
    "custom-qwen38-27b-mtp-i7-claude": ("Qwen3.8-27B Q4 + MTP (i7)", "claude"),
    "custom-cand-ornith-i7-claude": ("Ornith-1.5-35B-A3B Q4_K_M (i7)", "claude"),
    "custom-cand-flint-i7-claude": ("Flint Q4_K_M (i7)", "claude"),
    "custom-cand-nemotron-i7-claude": ("Nemotron 3.5 Lightning 30B-A3B (i7)", "claude"),
    "custom-cand-muse-i7-claude": ("Muse Glimmer 30B + DFlash (i7)", "claude"),
}
CRITICS = {"S-sol": "Sol · Codex", "S-opus": "Opus · Claude Code", "S-fable": "Fable · Claude Code",
           "S-glm": "GLM-5.3 · Claude Code", "S-glm-pi": "GLM-5.3 · Pi", "S-qwen-pi": "Qwen 3.7+ · Pi"}


def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None


runs = []
for r in csv.DictReader(open(os.path.join(B, "data/runs.csv"))):
    model, harness = NAMES.get(r["executor"], (r["executor"], r["harness"]))
    runs.append(dict(
        task=r["task"].split("-")[0], executor=r["executor"], model=model, harness=harness,
        round=int(r["round"]), status=r["status"], stop=r["stop"], dur=num(r["duration_s"]),
        f2p=f'{r["f2p_pass"]}/{r["f2p_total"]}', p2p=f'{r["p2p_pass"]}/{r["p2p_total"]}',
        lines=num(r["patch_lines"]), effective=r["effective_resolved"] == "True",
        override=r.get("jury_override") == "True",
        tin=num(r["input_tokens"]), tcache=num(r["cached_input_tokens"]), tout=num(r["output_tokens"]),
        calls=num(r["llm_calls"]), tools=num(r["tool_calls"]), cost=num(r["cost_api_usd"]), model_id=r["model"],
        protocol=r.get("protocol") or None, run=r["run_id"]))

# time to first green (data/first-green.csv) and decode speed of local servers (data/decode-speed.csv)
first_green = []
p = os.path.join(B, "data/first-green.csv")
if os.path.exists(p):
    for r in csv.DictReader(open(p)):
        first_green.append(dict(run=r["run_id"], fg=num(r["first_green_s"]), req=num(r["first_green_request"]),
                                reqs=num(r["requests"]), dur=num(r["duration_s"]), stayed=r["stayed_green"] == "True",
                                method=r["method"]))
decode = {}
p = os.path.join(B, "data/decode-speed.csv")
if os.path.exists(p):
    for r in csv.DictReader(open(p)):
        decode[r["executor"]] = num(r["tg_median"])

screen = []
p = os.path.join(B, "data/critic-screen.csv")
if os.path.exists(p):
    for r in csv.DictReader(open(p)):
        if r["critic"] not in CRITICS: continue
        ex = r["executor"]; model, harness = NAMES.get(ex, (ex, ""))
        screen.append(dict(critic=r["critic"], critic_name=CRITICS[r["critic"]], run=r["run"],
                           task=r["task"], patch=f"{model} · {harness}", truth=r["truth"],
                           verdict=r["verdict"], self=r["self_review"] == "True",
                           dur=num(r["duration_s"]), p0=int(r["n_P0"] or 0), p1=int(r["n_P1"] or 0),
                           summary=r["summary"]))

adj = {}
p = os.path.join(B, "data/critic-adjudication.json")
if os.path.exists(p):
    for a in json.load(open(p))["reviews"]:
        adj[(a["critic"], a["run"])] = a
for r in screen:
    a = adj.get((r["critic"], r["run"]))
    r["confirmed"] = None if a is None else a["confirmed"]
    r["check"] = a and dict(check=a["check"], patch=a["patch"], reference=a["reference"])

prices = {}
pj = json.load(open(os.path.join(B, "prices.json")))["api_per_mtok"]
for k, v in pj.items():
    while isinstance(v, dict) and v.get("same_as"): v = pj[v["same_as"]]
    if isinstance(v, dict) and "input" in v:
        prices[k] = dict(i=v["input"], c=v.get("cache_read"), o=v["output"], est="third-party" in v.get("source", ""))
prices["opus"] = dict(i=None, c=None, o=None, est=False, harness=True)

data = dict(prices=prices, generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            runs=runs, screen=screen, critics=CRITICS, first_green=first_green, decode=decode)
for tpl, out in PAGES:
    out = os.path.join(B, "reports", out)
    html = open(os.path.join(B, "bin", tpl)).read().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    open(out, "w").write(html)
    print(f"[summary] {out}: {len(runs)} runs, {len(screen)} screen reviews")
