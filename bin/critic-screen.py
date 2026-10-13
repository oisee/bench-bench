#!/usr/bin/env python3
"""Score blind screen critics against hidden-test ground truth.

Reads runs/_screen/<anon>/<critic>/review.json and runs/_screen/map.json.
A critic "catches" a FAILED patch by saying incorrect and raises a false alarm
by saying incorrect on a RESOLVED one; "uncertain" counts as half credit.
Writes data/critic-screen.csv (one row per review) and data/critic-screen.json.
"""
import collections, csv, glob, json, os

B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(B, "runs/_screen")
OUT = os.path.join(B, "data")
CREDIT = {"correct": 0.0, "uncertain": 0.5, "incorrect": 1.0}  # P(says broken)

amap = json.load(open(os.path.join(S, "map.json"))) if os.path.exists(os.path.join(S, "map.json")) else {}
rows = []
for f in sorted(glob.glob(os.path.join(S, "*/*/review.json"))):
    anon, critic = f.split(os.sep)[-3:-1]
    run = os.path.join(B, "runs", amap.get(anon, "").removeprefix("runs/")) if anon in amap else None
    res = json.load(open(os.path.join(run, "result.json"))) if run and os.path.exists(os.path.join(run, "result.json")) else {}
    rv = json.load(open(f))
    sev = collections.Counter(x.get("severity") for x in rv.get("findings", []) if isinstance(x, dict))
    rows.append(dict(
        critic=critic, run=amap.get(anon, ""), task=amap.get(anon, "")[:3],
        executor=res.get("executor", ""), truth=res.get("status", ""), verdict=rv.get("verdict", "ERROR"),
        quality=rv.get("quality"), self_review=rv.get("self_review", False), duration_s=rv.get("duration_s"),
        n_P0=sev["P0"], n_P1=sev["P1"], n_P2=sev["P2"], n_P3=sev["P3"],
        error=rv.get("error", ""), summary=rv.get("summary", "")))

summary = {}
for critic in sorted({r["critic"] for r in rows}):
    rs = [r for r in rows if r["critic"] == critic]
    ok = [r for r in rs if r["verdict"] in CREDIT]
    fail = [r for r in ok if r["truth"] == "FAILED"]
    good = [r for r in ok if r["truth"] == "RESOLVED"]
    tpr = sum(CREDIT[r["verdict"]] for r in fail) / len(fail) if fail else None
    fpr = sum(CREDIT[r["verdict"]] for r in good) / len(good) if good else None
    durs = [r["duration_s"] for r in rs if r["duration_s"]]
    summary[critic] = dict(
        reviews=len(rs), errors=len(rs) - len(ok), failed_seen=len(fail), resolved_seen=len(good),
        caught=sum(r["verdict"] == "incorrect" for r in fail), missed=sum(r["verdict"] == "correct" for r in fail),
        false_alarms=sum(r["verdict"] == "incorrect" for r in good), cleared=sum(r["verdict"] == "correct" for r in good),
        uncertain=sum(r["verdict"] == "uncertain" for r in ok),
        catch_rate=round(tpr, 3) if tpr is not None else None,
        false_alarm_rate=round(fpr, 3) if fpr is not None else None,
        balanced_accuracy=round((tpr + 1 - fpr) / 2, 3) if tpr is not None and fpr is not None else None,
        self_reviews=sum(bool(r["self_review"]) for r in rs),
        median_duration_s=sorted(durs)[len(durs) // 2] if durs else None)

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "critic-screen.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["critic"]); w.writeheader(); w.writerows(rows)
json.dump(dict(credit=CREDIT, critics=summary), open(os.path.join(OUT, "critic-screen.json"), "w"), indent=2)

print(f"{'critic':8} {'done':>4} {'err':>3} {'caught':>9} {'false+':>9} {'unc':>3} {'bal.acc':>7} {'med s':>6}")
for c, s in summary.items():
    print(f"{c:8} {s['reviews']:4} {s['errors']:3} {s['caught']:>4}/{s['failed_seen']:<4} "
          f"{s['false_alarms']:>4}/{s['resolved_seen']:<4} {s['uncertain']:3} "
          f"{s['balanced_accuracy'] if s['balanced_accuracy'] is not None else '-':>7} {s['median_duration_s'] or '-':>6}")
