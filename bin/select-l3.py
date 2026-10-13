#!/usr/bin/env python3
"""Select preregistered L3 finalists after the full L2 matrix is graded."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "data" / "bench.json").read_text())
L1 = ("L1a-osg678", "L1b-osg680")
L2 = ("L2a-osg693", "L2b-abapiti87")
TASKS = L1 + L2
L2_ONLY = {"ali-qwen37-codex", "ali-kimi25-codex", "ali-minimax25-codex"}

runs = {}
for row in DATA["runs"]:
    if row["task"] in TASKS and row["round"] == 1:
        key = (row["executor"], row["task"])
        if key not in runs or row["run_id"] > runs[key]["run_id"]:
            runs[key] = row

executors = DATA["executors"]
missing = [(e, t) for e in executors for t in L2 if (e, t) not in runs]
if missing:
    raise SystemExit(f"L2 is not complete: {len(missing)} cells lack result.json: {missing}")
missing_l1 = [(e, t) for e in executors if e not in L2_ONLY for t in L1 if (e, t) not in runs]
if missing_l1:
    raise SystemExit(f"L1 baseline is incomplete: {missing_l1}")

best_time = {task: min(row["duration_s"] for (executor, t), row in runs.items()
                       if t == task and row["effective_resolved"] and row["duration_s"] > 0)
             for task in TASKS}

def speed_ratio(executor, tasks):
    return math.exp(sum(math.log(runs[executor, task]["duration_s"] / best_time[task])
                        for task in tasks) / len(tasks))

full = [e for e in executors if e not in L2_ONLY and
        all(runs[e, t]["effective_resolved"] for t in TASKS)]
full.sort(key=lambda e: (speed_ratio(e, TASKS), e))
selected = [{"executor": e, "route": "4/4 L1+L2", "speed_ratio": round(speed_ratio(e, TASKS), 3)}
            for e in full[:4]]

wildcards = [e for e in L2_ONLY if e in executors and
             all(runs[e, t]["effective_resolved"] for t in L2)]
wildcards.sort(key=lambda e: (speed_ratio(e, L2), e))
if wildcards:
    e = wildcards[0]
    selected.append({"executor": e, "route": "2/2 L2-only Codex wildcard",
                     "speed_ratio": round(speed_ratio(e, L2), 3)})
if not selected:
    raise SystemExit("No L3 finalist met the preregistered pass criteria")

for entry in selected:
    e = entry["executor"]
    entry["evidence"] = {task: {
        "run_id": runs[e, task]["run_id"],
        "passed": runs[e, task]["effective_resolved"],
        "duration_s": runs[e, task]["duration_s"],
        "total_tokens": runs[e, task]["total_tokens"],
        "jury_n": runs[e, task]["jury_n"],
        "score": runs[e, task]["score"] if runs[e, task]["jury_n"] >= 3 else None,
    } for task in (L2 if e in L2_ONLY else TASKS)}

selection = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "rule": "All L1+L2 passes; at most four baseline finalists by geometric mean of task-normalized time. At most one Alibaba Codex wildcard, requiring both L2 passes, by normalized L2 time. L2 jury scores are unavailable and are not used.",
    "l2_graded_cells": len(executors) * len(L2),
    "full_pass_candidates": full,
    "l2_only_pass_candidates": wildcards,
    "selected": selected,
}
path = ROOT / "data" / "l3-finalists.json"
if path.exists():
    locked = json.loads(path.read_text())
    if locked.get("selected") != selection["selected"]:
        raise SystemExit(f"L3 selection is already locked and differs from current data: {path}")
    print("L3 selection already locked:", ", ".join(entry["executor"] for entry in locked["selected"]))
    raise SystemExit(0)
path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n")
print("L3 finalists:", ", ".join(entry["executor"] for entry in selected))
print("selection evidence:", path)
