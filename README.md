# Bench Bench

A small, reproducible benchmark of **coding agents** (model + harness + effort) on tasks cut from
**real merged pull requests**. Each agent gets the repository at the PR's parent commit and a written
brief, works alone in a sandbox under a time limit, and is graded by the PR's own tests, which it never sees.

**Report:** [`reports/summary.html`](reports/summary.html) (download and open in a browser) ·
[live version](https://claude.ai/artifact/55YCFRr4r2C6hSLUE2sRXu) ([на русском](https://claude.ai/artifact/RJ9CyqqUJtiEj8Jy9zkmPm)) ·
**Method:** [METHODOLOGY.md](METHODOLOGY.md) · **Data:** [data/](data/README.md)

## TL;DR

- **66 baseline runs + 3 pilot runs**, 6 tasks, 15 model + harness combinations, 8 models, 3 harnesses
  (Claude Code, Codex, Pi). One run per cell, so treat one-task differences as noise.
- **Opus in Claude Code solved 6/6.** Sol 6.1 in Codex solved 5/6 in a quarter of the time, failing only the hardest task (L3b).
  Luna xhigh in Codex also solved 5/6, failing the same task, for about $0.41 in total at list price versus about $2.30 for Sol.
- **The harness matters as much as the model.** The same GLM-5.3 solved 6/6 in Claude Code (3 only at the time limit), 5/6 in Pi and 4/6 in Codex.
- **Cheap per token is not cheap per task.** GLM-5.3 in Claude Code burned 112M input tokens on the six tasks, about $32 at list price; Opus used 39M, about $18. Sol solved 5 tasks for about $2.30, and Luna in Codex costs cents per task.
- **Hidden tests are not the whole truth.** A blind critic (Sol) found real bugs in three patches that pass the
  tests (two by Opus, one by Luna xhigh); each was confirmed by running the critic's repro against the upstream solution.
- **Round 3 (effort vs executable checks, one run each):** Sol 6.1 at high effort solved L3b, which it failed at medium.
  Luna high solved L2b when half of the held-out cases were in the repo as runnable examples; Luna medium gave up even with them.
- **Round 4, the first local model:** Qwen3.8-Flash-Next, 4-bit, on a single 96 GB GPU in a home machine, solved
  5 of 6 tasks in Claude Code (L3a at the time limit), as many as Sol 6.1 and Luna xhigh. Like them it failed L3b,
  also on a second attempt with Sol as critic. One run per task.
- **A two-critic build loop did not help** in a 3-run pilot. It did not solve more tasks, and runs took 2–10× longer.

## Results (baseline, round 1)

| Model | Harness | Solved | at limit | Tasks (**solved**, ~~failed~~) | Total time | Input tok | Output tok | API cost |
|---|---|---|---|---|---|---|---|---|
| Opus | Claude Code | 6/6 | 1 | **L1a** **L1b** **L2a** **L2b** **L3a** **L3b** | 117 min | 39.3M | 233k | $18.24 |
| GLM-5.3 | Claude Code | 6/6 | 3 | **L1a** **L1b** **L2a** **L2b** **L3a** **L3b** | 221 min | 112.3M | 428k | $32.21 |
| Sol 6.1 (medium) | Codex | 5/6 | 0 | **L1a** **L1b** **L2a** **L2b** **L3a** ~~L3b~~ | 30 min | 8.3M | 61k | $2.32 |
| Luna (xhigh) | Codex | 5/6 | 0 | **L1a** **L1b** **L2a** **L2b** **L3a** ~~L3b~~ | 41 min | 25.8M | 176k | $0.42 |
| GLM-5.3 | Pi | 5/6 | 3 | **L1a** **L1b** **L2a** **L2b** **L3a** ~~L3b~~ | 234 min | 76.6M | 340k | $22.35 |
| GLM-5.3 | Codex | 4/6 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ **L3a** ~~L3b~~ | 72 min | 36.7M | 157k | $10.93 |
| Luna (medium) | Codex | 3/4 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ | 6 min | 2.0M | 17k | $0.04 |
| Luna (high) | Codex | 3/4 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ | 17 min | 10.0M | 65k | $0.16 |
| Qwen 3.7 Plus | Pi | 3/4 | 0 | **L1a** **L1b** **L2a** ~~L2b~~ | 109 min | 10.4M | 95k | $0.87 |
| GLM-5 | Pi | 2/4 | 1 | **L1a** ~~L1b~~ **L2a** ~~L2b~~ | 131 min | 12.8M | 77k | $3.46 |
| Qwen 3.7 Plus | Codex | 1/2 | 0 | **L2a** ~~L2b~~ | 22 min | 3.9M | 52k | $0.34 |
| MiniMax M2.5 | Codex | 1/2 | 0 | **L2a** ~~L2b~~ | 44 min | 15.0M | 102k | $0.86 |
| Kimi K2.5 | Codex | 1/2 | 0 | **L2a** ~~L2b~~ | 66 min | 29.2M | 129k | $3.64 |
| Kimi K2.5 | Pi | 1/4 | 0 | **L1a** ~~L1b~~ ~~L2a~~ ~~L2b~~ | 116 min | 9.6M | 74k | $1.47 |
| MiniMax M2.5 | Pi | 0/4 | 0 | ~~L1a~~ ~~L1b~~ ~~L2a~~ ~~L2b~~ | 118 min | 13.1M | 79k | $0.74 |

*Solved* means all hidden acceptance tests (fail→pass) and regression tests (pass→pass) pass. A run stopped at
the time limit is graded on whatever its working tree held at that moment; *at limit* counts those.
Input tokens include cached input. API cost is what the same tokens would cost at public list prices ([prices.json](prices.json)):
Opus from Claude Code's own cost report, GLM-5.3 from the Z.ai price list, the other models from third-party price trackers.
It is an estimate for comparison, not what was paid; most runs used subscription plans.

**How the runs were actually paid.** Almost everything ran on flat-rate coding subscriptions, where a run costs
nothing extra until the plan's rolling limit is reached:

| Provider | Plan | Price | Used for |
|---|---|---|---|
| Anthropic | Claude Max 20x | $200 / month | Opus (agent, critic), Fable (critic) |
| OpenAI | ChatGPT Pro, signed in to Codex | $200 / month | Luna, Sol 6 and Astra (critics, brief drafts) |
| Z.ai | GLM Coding Max | ~$130 / month | GLM-5.3 in all harnesses |
| Alibaba | Coding Plan Pro (~90,000 requests/month) | ~$50 / month | Qwen 3.7+, Kimi K2.5, MiniMax M2.5, GLM-5 |
| Azure OpenAI | pay per token (cloud credits) | metered | Sol 6.1 as agent |

The whole matrix used part of about $580/month of subscriptions plus a few dollars of Azure credits. The list-price
column shows what the same work would cost through the API, which matters for CI or large-scale use.

## Round 4: local models

The first model in the bench that ran on someone's own hardware instead of a cloud API. It solved **5 of 6 tasks**,
as many as Sol 6.1 and Luna xhigh in Codex, and like them it failed L3b. **This is a limited result:** one run per task,
one quantization, one harness, one machine; a one-task difference may be noise.

| | |
|---|---|
| Model | [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next): open weights, MoE with 125B parameters and 6B active per token (512 experts, 10 active), plus a 51B n-gram embedding table; released August 2026 |
| Checkpoint | [`local-inference-lab/Qwen3.8-Flash-Next-NVFP4-MXFP8-CSF-QAD`](https://huggingface.co/local-inference-lab/Qwen3.8-Flash-Next-NVFP4-MXFP8-CSF-QAD): community 4-bit quantization, NVFP4 experts and MXFP8 attention, 102 GB |
| Hardware | Minisforum MS-S1 Max (AMD Ryzen AI Max+ 395, 128 GB unified memory) + NVIDIA RTX PRO 6000 Blackwell Max-Q (96 GB) in an external GPU dock |
| Serving | vLLM, Anthropic-compatible `/v1/messages`, 262k context |
| Harness | Claude Code 2.1.309, effort `xhigh` (the server accepts only `low`, `medium`, `xhigh`) |

| Task | Qwen3.8-Flash-Next (local) | Requests | Input / output tokens | Hosted price | Opus, round 1 | GLM-5.3 in Claude Code, round 1 |
|---|---|---|---|---|---|---|
| L1a | **solved**, 4 min | 22 | 1.0M / 25k | $0.17 | solved, 1 min | solved, 6 min |
| L1b | **solved**, 18 min | 86 | 8.5M / 65k | $1.40 | solved, 4 min | solved, 22 min |
| L2a | **solved**, 17 min | 80 | 8.1M / 46k | $1.32 | solved, 4 min | solved, 21 min |
| L2b | **solved**, 30 min | 111 | 13.1M / 132k | $2.16 | solved, 20 min | solved at the limit |
| L3a | **solved at the limit**, 60 min | 240 | 28.6M / 192k | $4.67 | solved at the limit | solved at the limit |
| L3b | failed, 24 min | 93 | 8.9M / 78k | $1.47 | solved, 28 min | solved at the limit |
| L3b, 2nd attempt with Sol as critic | failed, 6 + 30 min (repair hit its limit) | 200 | 26.9M / 143k | $4.37 | | |

- **L3a at the limit.** It was graded on the tree it left at 60 minutes. A snapshot taken at minute 47 already passed
  every hidden test; the rest of the session went into fixing Z80 code-generator bugs that its own end-to-end tests
  had found beyond the task, without breaking anything.
- **L3b with a critic.** A second attempt started from the failed patch (`bin/run-repair.sh`, protocol
  `critic-repair-v1`). Sol (Codex, xhigh) reviewed it without the hidden tests and reported five gaps, each with a
  reproducing input it ran (not yet checked against the upstream solution; [review](runs/L3b-abapiti95/custom-qwen38fn-claude-critic-S-sol-20261023-172550/critic-review.md)),
  but not the case the hidden test checks: a record `||` an anonymous object. Qwen then had 30 minutes to repair, was
  still refactoring when the time ran out, and the tree it left failed both the held-out test and its own new tests.
  There was no control run without the critic, so this says nothing general about critics.
- **Speed.** No server errors in any run. With time to first byte recorded separately (the L3b repair run), generation
  was 176 tokens/s median on answers of 1k+ tokens, and the first byte arrived after 3.6 s median with contexts up to
  230k tokens. Earlier runs, measured without that split, show 92–125 tokens/s including the wait.
- **How it worked.** On L2b it found the abaplint sources archived in the repository and implemented the indentation
  rules from them, then checked its formatter on 51 real ABAP files; on L2a it ran the project's full semantics
  harness and ABAP Unit fixtures, not only the tests the brief mentions.
- **The same model on a small home machine.** On i7 (Core i7-10700K, 128 GB DDR4, 2× RTX 4060 Ti 16 GB),
  llama.cpp (`--fit`) keeps the most used experts in VRAM and the rest in RAM; the 4-bit
  [UD-IQ4_XS](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF) build (94 GB) runs at 361 tokens/s prefill and
  16 tokens/s decode, about a tenth of the machine above. Time limits were scaled ×10 to match
  (`BENCH_TIME_SCALE=10`, protocol `time-scaled-v1`). It solved **L1a in 26 minutes**, inside even the standard
  30, with 18 requests against 22 on the fast machine. In an open-loop replay (`bin/replay.py`) of 23 steps from
  the fast machine's solved L2b session, with the same context at every step, it made the same move on 9 steps,
  mostly while implementing, and different moves mostly while exploring; blind judging of those steps is pending.
  L1b is running. A dense Qwen3.8-27B (Q4) run on the same machine was stopped after 31 minutes when the work moved
  to this model, so it has no result.
- **Hosted price** is what the same tokens would cost on QwenCloud's hosted `qwen3.8-flash` at list price
  ([prices.json](prices.json)); the local runs paid nothing per token. The repair row counts Qwen's tokens only.
- The full request/response stream of every run was recorded (`BENCH_RECORD=1`, `bin/traffic-summary.py`); like other
  transcripts, it is not published.

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

## Quick start: run your own model

No SAP system is needed: the tasks are Go and JavaScript changes in ABAP tooling repositories, checked by
the upstream PR's own tests. What you need is a Linux machine (WSL2 works) and an LLM endpoint.

### 1. Prerequisites

- **Go 1.26+**, **Node.js 20+**, git, Python 3
- **bubblewrap**: `sudo apt install bubblewrap` (on Ubuntu 24.04+ also
  `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`)
- **one agent harness**: [Claude Code](https://claude.com/claude-code) (`claude`), Codex (`codex`) or Pi (`pi`).
  For your own model you only need the CLI installed, no account.

### 2. Set up (once, ~5 minutes, ~3 GB)

```bash
git clone https://github.com/oisee/bench-bench && cd bench-bench
bin/setup.sh --check     # checks the machine, clones the task repos, builds and verifies the 6 tasks
```

`bin/setup.sh` is safe to re-run. If something is missing it says what and how to fix it.

### 3. Point it at your model

```bash
cp .env.example .env     # set CUSTOM_BASE_URL (and CUSTOM_API_KEY if the endpoint needs one)
```

Then add a line to `matrix.yaml`; `provider: custom` means "the endpoint from `.env`":

```yaml
  - {id: my-model-claude, provider: custom, model: <model id from /v1/models>, harness: claude, effort: high}
```

Any OpenAI- or Anthropic-compatible server works: vLLM, SGLang, Ollama, LM Studio, OpenRouter, a cloud API.
Which harness to choose:

| harness | endpoint API it uses | notes |
|---|---|---|
| `claude` | Anthropic `/v1/messages` | GLM-5.3 did best in this harness; vLLM and SGLang serve this API |
| `pi` | OpenAI `/v1/chat/completions` | works with almost anything (`CUSTOM_PI_API` switches API) |
| `codex` | OpenAI `/v1/responses` | needs a Responses-API endpoint |

`effort` is passed to the model as its reasoning effort; use a level your server accepts
(vLLM rejects levels a model does not support, e.g. some accept only `low`, `medium`, `xhigh`).

### 4. Run

```bash
bin/doctor.sh --smoke my-model-claude   # 1-minute live check: the model runs a tool inside the sandbox
bin/bench.sh my-model-claude            # all 6 tasks, one after another (up to ~4.5 h); prints a table
bin/bench.sh my-model-claude L2b        # or just some tasks (ids or prefixes)
bin/bench.sh my-model-claude --status   # results so far
```

`bin/bench.sh` is safe to interrupt and re-run: tasks that already have a result are skipped (`--again` repeats them).
Each run is kept in `runs/<task>/<executor>-<time>/`: `patch.diff`, `result.json`, `grade/*.log`, and the agent
transcript in `agent.out`. With `BENCH_RECORD=1` (in `.env` or the environment) every request to and response
from your endpoint is also logged to `traffic.jsonl` in the run directory; `bin/traffic-summary.py` turns it into
requests, tokens, context growth, time to first byte, generation speed and tool calls (`--csv` for analysis).

To compare with the published results, put your `result.json` next to `data/runs.csv`. A full re-export
(`bin/export.py`) needs the jury reviews and agent transcripts, which are not in git, so on a fresh clone it would
rebuild `data/` without them; run it only where those exist.

### Hosted providers and the published matrix

The executors used for the published results (Opus, Sol, Luna, GLM, Qwen, Kimi, MiniMax) are in `matrix.yaml`.
Claude Code and Codex use your own login (`claude` /login, `codex login`) or `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY`; Z.ai, Alibaba and Azure read their keys from `.env` (see `.env.example`).
Pi with a hosted provider also needs that provider in `~/.pi/agent/models.json`.
`gateway/` has the LiteLLM bridge used to run Alibaba models through Codex.

**To add a harness,** add its launch to `bin/sandbox.sh` and its transcript parsing to `bin/export.py`.
**To add a task,** pick a merged PR whose tests fail before it and pass after it, then write `task.env` and
`brief.md` and check it with `bin/setup.sh --check <task>`. The brief must describe behaviour without copying
test fixtures; see `brief-review.md` for the QA step.

## License

[MIT](LICENSE) for the benchmark code, data and report. Task content (briefs aside) comes from the linked public
repositories under their own licenses.
