# Bench Bench

A small, reproducible benchmark of **coding agents** (model + harness + effort) on tasks cut from
**real merged pull requests**. Each agent gets the repository at the PR's parent commit and a written
brief, works alone in a sandbox under a time limit, and is graded by the PR's own tests, which it never sees.

**Report:** [`reports/summary.html`](reports/summary.html) (download and open in a browser) ·
[live version](https://claude.ai/artifact/55YCFRr4r2C6hSLUE2sRXu) ·
**Method:** [METHODOLOGY.md](METHODOLOGY.md) · **Data:** [data/](data/README.md)

## TL;DR

- **64 baseline runs + 3 pilot runs**, 6 tasks, 15 model + harness combinations, 8 models, 3 harnesses
  (Claude Code, Codex, Pi). One run per cell, so treat one-task differences as noise.
- **Opus in Claude Code solved 6/6.** Sol 6.1 in Codex solved 5/6 in a quarter of the time, failing only the hardest task.
- **The harness matters as much as the model.** The same GLM-5.3 solved 6/6 in Claude Code (3 only at the time limit), 5/6 in Pi and 4/6 in Codex.
- **Cheap per token is not cheap per task.** GLM-5.3 in Claude Code burned 112M input tokens on the six tasks, which costs about $32 at list price. Opus used 39M and cost about $18.
- **Hidden tests are not the whole truth.** A blind critic (Sol) found real bugs in two Opus patches that pass the
  tests; both were confirmed by running the critic's repro against the upstream solution.
- **A two-critic build loop did not help** in a 3-run pilot. It did not solve more tasks, and runs took 2–10× longer.

## Results (baseline, round 1)

| Model | Harness | Solved | at limit | Tasks (**solved**, ~~failed~~) | Total time | Input tok | Output tok | API cost |
|---|---|---|---|---|---|---|---|---|
| Opus | Claude Code | 6/6 | 1 | **L1a** **L1b** **L2a** **L2b** **L3a** **L3b** | 117 min | 39.3M | 233k | $18.24 |
| GLM-5.3 | Claude Code | 6/6 | 3 | **L1a** **L1b** **L2a** **L2b** **L3a** **L3b** | 221 min | 112.3M | 428k | $32.21 |
| Sol 6.1 (medium) | Codex | 5/6 | 0 | **L1a** **L1b** **L2a** **L2b** **L3a** ~~L3b~~ | 30 min | 8.3M | 61k | — |
| GLM-5.3 | Pi | 5/6 | 3 | **L1a** **L1b** **L2a** **L2b** **L3a** ~~L3b~~ | 234 min | 76.6M | 340k | $22.35 |
| Luna (xhigh) | Codex | 4/4 | 0 | **L1a** **L1b** **L2a** **L2b** | 25 min | 16.8M | 119k | — |
| GLM-5.3 | Codex | 4/6 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ **L3a** ~~L3b~~ | 72 min | 36.7M | 157k | $10.93 |
| Luna (medium) | Codex | 3/4 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ | 6 min | 2.0M | 17k | — |
| Luna (high) | Codex | 3/4 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ | 17 min | 10.0M | 65k | — |
| Qwen 3.7 Plus | Pi | 3/4 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ | 109 min | 10.4M | 95k | — |
| GLM-5 | Pi | 2/4 | 1 | **L1a** ~~L1b~~ **L2a** ~~L2b~~ | 131 min | 12.8M | 77k | — |
| Qwen 3.7 Plus | Codex | 1/2 | 0 | **L2a** ~~L2b~~ | 22 min | 3.9M | 52k | — |
| MiniMax M2.5 | Codex | 1/2 | 0 | **L2a** ~~L2b~~ | 44 min | 15.0M | 102k | — |
| Kimi K2.5 | Codex | 1/2 | 0 | **L2a** ~~L2b~~ | 66 min | 29.2M | 129k | — |
| Kimi K2.5 | Pi | 1/4 | 0 | **L1a** ~~L1b~~ ~~L2a~~ ~~L2b~~ | 116 min | 9.6M | 74k | — |
| MiniMax M2.5 | Pi | 0/4 | 0 | ~~L1a~~ ~~L1b~~ ~~L2a~~ ~~L2b~~ | 118 min | 13.1M | 79k | — |

*Solved* means all hidden acceptance tests (fail→pass) and regression tests (pass→pass) pass. A run stopped at
the time limit is graded on whatever its working tree held at that moment; *at limit* counts those.
Input tokens include cached input. API cost is shown only where a list price is recorded (see [prices.json](prices.json)).

## The tasks

| Task | Upstream PR | What the agent must do | Limit |
|---|---|---|---|
| L1a | [open-steamgate #678](https://github.com/oisee/open-steamgate/pull/678) | `trimLiterals(sql)` must survive ~15M-char SQL literals without a stack overflow (JS) | 30 min |
| L1b | [open-steamgate #680](https://github.com/oisee/open-steamgate/pull/680) | ABAP `cl_http_utility=>decode_base64` in the ABAP→Go runtime, Node-exact UTF-8 decoding | 30 min |
| L2a | [open-steamgate #693](https://github.com/oisee/open-steamgate/pull/693) | ABAP `string → int8` conversion in the Go and JS code generators | 45 min |
| L2b | [abapiti #87](https://github.com/oisee/abapiti/pull/87) | Indent all generated ABAP the way abaplint expects; wrap at 255 bytes | 45 min |
| L3a | [minz #39](https://github.com/oisee/minz/pull/39) | Short-circuit `&&` / `\|\|` in Nanz (MinZ Z80 compiler) through parser, HIR, VM | 60 min |
| L3b | [abapiti #95](https://github.com/oisee/abapiti/pull/95) | Lower anonymous TypeScript object literals to HIR for the TS→ABAP/Go backends | 60 min |

The exact briefs are in [`tasks/*/brief.md`](tasks/), and the grading setup is in `tasks/*/task.env`. All three
source repositories are public, so the reference solutions are public too. Models trained after October 2026
may have seen them.

## Repository layout

```
tasks/<id>/         brief.md (what the agent got), brief-review.md (brief QA), task.env (BASE, MERGE, hidden tests, commands)
runs/<task>/<run>/  patch.diff, result.json, meta.json, grade/*.log per agent run (transcripts are not published)
reviews/jury/       L1 jury reviews (critics saw test results)
reviews/screen/     blind critic screening reviews (no test results)
data/               flat CSV/JSON exports: runs, reviews, findings, critic screening, adjudication
reports/            summary.html (self-contained report)
bin/                sandbox, run, grade, review, export and report scripts
matrix.yaml         executors and critics; prices.json list prices
gateway/            LiteLLM bridge used to run Alibaba models through Codex
```

## Reproducing and extending

Requirements: Linux with `bwrap` (bubblewrap) and `systemd-run`, Go and Node.js, plus the harness CLIs you
want to test: `claude`, `codex` and `pi`. Provider keys are read from the environment
(`ZAI_API_KEY`, `AZURE_OPENAI_API_KEY` with `AZURE_OPENAI_ENDPOINT`, `ALI_CP_API_KEY`, and so on).

```bash
git clone --bare https://github.com/oisee/abapiti .cache/repos/abapiti.git   # likewise open-steamgate, minz
bin/template.sh tasks/L2b-abapiti87                                         # prepare the task's base tree once
bin/goldcheck.sh tasks/L2b-abapiti87                                        # base must fail, upstream fix must pass
BENCH_INTERNAL_REVIEW_ROUNDS=0 bin/run.sh tasks/L2b-abapiti87 ant-opus-claude   # one baseline run
python3 bin/export.py && python3 bin/summary-report.py                      # refresh data/ and the report
```

**To add a model or harness,** add a line to `matrix.yaml`. For a new harness, also add its launch and
transcript parsing to `bin/sandbox.sh` and `bin/export.py`. **To add a task,** pick a merged PR whose tests
fail before it and pass after it, then write `task.env` and `brief.md`. The brief must describe behaviour
without copying test fixtures; see `brief-review.md` for the QA step.
