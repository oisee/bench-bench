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
- **Reproduced on a simpler, slower home setup:** the same model as a 4-bit GGUF in llama.cpp on a PC with one
  consumer CPU and two 16 GB gaming GPUs (about 16 tokens/s, a tenth of the speed, so limits ×10) solved L1a in
  26 minutes; its L1b session was rated on track by two blind judges when it was stopped at 47 minutes.
- **A pruned build on the same home PC solved all four L1–L2 tasks:** ISTA-DASLab's 3.5-bit
  GSQ-RCO-Coder build of Qwen3.8-Flash-Next (256 of 512 experts), cut further to 224 experts on our own
  routing statistics, fits in 32 GB of VRAM and runs at 20–35 tokens/s. With limits ×5 it solved L1a, L2a, L1b
  and L2b (8, 38, 88 and 99 minutes). L3b was stopped after 94 minutes to free the machine; the session is kept.
- **Smaller builds and small models on the same home PC (October 24–25):** K192p-implant16, a 192-expert cut of
  DSLab's build with 16 experts per layer implanted from our session statistics, solved L1a, L2a, L1b and L3b (L2b was
  stopped by the operator after a reasoning loop). Four small models that fit entirely in VRAM (Ornith-1.5-35B-A3B,
  Flint, Nemotron 3.5 Lightning, Muse Glimmer 30B) each solved L1a in 11–13 minutes. On L2a the fix was in place
  after about 11 minutes in all three runs where we measured it; the rest of each session (12–38 min) went into
  the agent's own tests and checks.
- **A two-critic build loop did not help** in a 3-run pilot. It did not solve more tasks, and runs took 2–10× longer.

## Results (baseline, round 1, plus local models from round 4)

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
| *Local, round 4:* | | | | | | | | |
| Qwen3.8-Flash-Next NVFP4, one 96 GB GPU | Claude Code | 5/6 | 1 | **L1a** **L1b** **L2a** **L2b** **L3a** ~~L3b~~ | 153 min | 68.3M | 540k | $0 local (hosted ≈ $11.19) |
| Qwen3.8-Flash-Next DSLab K224 (pruned to 224/512 experts), i7 + 2× 16 GB GPUs, limits ×5 | Claude Code | 4/4 | 0 | **L1a** **L1b** **L2a** **L2b** (L3b stopped at 94 min) | 233 min | 33.8M | 268k | $0 local (hosted ≈ $5.53) |
| Qwen3.8-Flash-Next IQ4_XS, i7 + 2× 16 GB GPUs, limits ×10 | Claude Code | 1/1 | 0 | **L1a** (L1b stopped, on track) | 26 min | 0.7M | 17k | $0 local (hosted ≈ $0.11) |
| *Local, October 24–25, home PC (i7, 2× 16 GB GPUs), limits ×5:* | | | | | | | | |
| Qwen3.8-Flash-Next K192p-implant16 | Claude Code | 4/4 | 0 | **L1a** **L1b**¹ **L2a** **L3b** (L2b stopped by the operator at 49.5 min; L1a and L2a solved twice) | 221 min | 42.8M | 339k | $0 local (hosted ≈ $7.01) |
| Qwen3.8-27B (dense) Q4 + MTP | Claude Code | 3/3 | 0 | **L1a** **L2a** **L3a** | 226 min | 31.5M | 226k | $0 local |
| Qwen3.8-Flash-Next K224-implant16 | Claude Code | 2/2 | 0 | **L1a** **L2a** | 31 min | 4.4M | 36k | $0 local (hosted ≈ $0.73) |
| Qwen3.8-Flash-Next DSLab 192p | Claude Code | 2/2 | 0 | **L1a** **L2a** | 38 min | 12.0M | 53k | $0 local (hosted ≈ $1.94) |
| Qwen3.8-Flash-Next DSLab 192, Q4 MTP head | Claude Code | 2/2 | 0 | **L2a** **L3a**² | 133 min | 33.2M | 140k | $0 local (hosted ≈ $5.38) |
| Qwen3.8-Flash-Next 2-bit, 192 experts | Claude Code | 2/2 | 0 | **L1a** **L2a** | 38 min | 13.7M | 65k | $0 local (hosted ≈ $2.22) |
| Qwen3.8-Flash-Next 2-bit, 280 experts | Claude Code | 2/2 | 0 | **L1a** **L2a** | 84 min | 12.8M | 133k | $0 local (hosted ≈ $2.11) |
| Qwen3.8-Flash-Next DSLab 192, MTP + n-gram | Claude Code | 1/1 | 0 | **L1a** | 16 min | 0.9M | 43k | $0 local (hosted ≈ $0.17) |
| Qwen3.8-Flash-Next DSLab 192, V cache q5_1 | Claude Code | 1/1 | 0 | **L1a** | 8 min | 1.1M | 15k | $0 local (hosted ≈ $0.18) |
| Flint Q4_K_M | Claude Code | 1/1 | 0 | **L1a** | 11 min | 1.6M | 28k | $0 local |
| Muse Glimmer 30B Q4_K_XL + DFlash | Claude Code | 1/1 | 0 | **L1a** | 11 min | 2.0M | 18k | $0 local |
| Nemotron 3.5 Lightning 30B-A3B UD-Q4_K_XL | Claude Code | 1/1 | 0 | **L1a** | 12 min | 5.6M | 52k | $0 local |
| Ornith-1.5-35B-A3B Q4_K_M | Claude Code | 1/1 | 0 | **L1a** | 13 min | 1.7M | 46k | $0 local |

*Solved* means all hidden acceptance tests (fail→pass) and regression tests (pass→pass) pass. A run stopped at
the time limit is graded on whatever its working tree held at that moment; *at limit* counts those.
Input tokens include cached input. API cost is what the same tokens would cost at public list prices ([prices.json](prices.json)):
Opus from Claude Code's own cost report, GLM-5.3 from the Z.ai price list, the other models from third-party price trackers.
It is an estimate for comparison, not what was paid; most runs used subscription plans.
The local rows ran on October 23 with the same briefs, sandbox and hidden tests, on the owners' own hardware, so they
cost nothing per token; *hosted* is what the same tokens would cost on QwenCloud's hosted API ([Round 4](#round-4-local-models)).
NVFP4, IQ4_XS and DSLab K224 are different compressed builds of the same model ([what the names mean](#local-builds-what-the-names-mean)).
The October 24–25 rows are described in [Smaller builds and small models](#smaller-builds-and-small-models-on-the-home-pc).
For a row with a repeated task, its first run counts. ¹ In-sample: K192p-implant16's experts were selected on an L1b
session. ² Stopped by the operator at 105 min after the agent had finished; graded on the tree at that moment.

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

### Local builds: what the names mean

Every local row is the same model, Qwen3.8-Flash-Next. The rows differ in how the weights were compressed so that they
fit the machine.

| Name in the tables | What it is | Size, speed | Machine |
|---|---|---|---|
| **NVFP4** | [Community 4-bit quantization](https://huggingface.co/local-inference-lab/Qwen3.8-Flash-Next-NVFP4-MXFP8-CSF-QAD) of all 512 experts (NVFP4 experts, MXFP8 attention), served by vLLM | 102 GB; ~176 tokens/s | one 96 GB GPU |
| **IQ4_XS** | unsloth's [UD-IQ4_XS GGUF](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF), all 512 experts, ~4 bits; llama.cpp keeps the most used experts in VRAM and the rest in RAM | 94 GB; 16 tokens/s | i7 + 2× 16 GB GPUs |
| **DSLab K224** | ISTA-DASLab's [GSQ-RCO-Coder](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF) keeps 256 of the 512 experts. They were chosen to keep the pruned model's output closest to the full model's (KL divergence) on a calibration set of code and agentic data ([RCO](https://github.com/IST-DASLab/RCO), [arXiv 2605.00649](https://arxiv.org/abs/2605.00649)), then quantized to ~3.5 bits ([GSQ](https://github.com/IST-DASLab/GSQ), [arXiv 2604.18556](https://arxiv.org/abs/2604.18556)). We removed 32 more experts, the ones our own ABAP agent session routed to least, leaving **224**. DSLab's weights were copied unchanged. Still 10 experts active per token, as in the original model | fits in 32 GB of VRAM; 1,200 tokens/s prefill, 35 tokens/s decode | i7 + 2× 16 GB GPUs |

- **Limits ×5 / ×10.** The home PC is slower, so each task's time limit is multiplied by 5 (K224) or 10 (IQ4_XS)
  (`BENCH_TIME_SCALE`, protocol `time-scaled-v1`). Times in the tables are real wall-clock minutes.
- **Why DSLab and not our own cut.** Our own cuts of unsloth's GGUF either did not fit in VRAM (256 experts) or stopped acting. At 192 experts, and at
  224 experts in IQ3_XXS, the model answered the first L2b turn with a plan sentence and ended the turn without
  calling a tool; that IQ3 224 attempt failed in 17 s and is kept on record. Before going live, every candidate now
  replays the first L2b step 20 times and must call a tool each time; DSLab K224 did 20/20.

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
  L1b was stopped at 47 minutes (35 requests) to free the machine for pruning experiments; two blind judges
  rated the session on track (the same plan as the fast machine's solved run, 1–4 steps behind), and the session
  is kept so it can be resumed. A dense Qwen3.8-27B (Q4) run on the same machine was stopped after 31 minutes when the work moved
  to this model, so it has no result.
- **Hosted price** is what the same tokens would cost on QwenCloud's hosted `qwen3.8-flash` at list price
  ([prices.json](prices.json)); the local runs paid nothing per token. The repair row counts Qwen's tokens only.
- The full request/response stream of every run was recorded (`BENCH_RECORD=1`, `bin/traffic-summary.py`); like other
  transcripts, it is not published.

### Smaller builds and small models on the home PC

October 24–25, on the same i7 PC (2× RTX 4060 Ti 16 GB), all with limits ×5, Claude Code at xhigh, llama.cpp. Two
lines of work: Qwen3.8-Flash-Next cut further so that it fits the GPUs, and small open models that fit entirely in
VRAM. Per-run numbers are in the [main table](#results-baseline-round-1-plus-local-models-from-round-4) and in section
05d of the [report](reports/summary.html).

| Build | What it is |
|---|---|
| DSLab 192 | 192 of DSLab's 224 experts. Three server settings: MTP head + n-gram drafts; full MTP head with V cache q5_1; Q4 MTP head with nothing spilled to RAM |
| DSLab 192p | 192 experts chosen inside the 224 with protection for decision tokens, Q4 MTP head |
| K224-implant16 | DSLab 224 with 16 experts per layer replaced by session-ranked ones ([data/expert-sets/qwen38fn-224-implant16.json](data/expert-sets/qwen38fn-224-implant16.json)); 3.5-bit GGUF, implants requantized |
| K192p-implant16 | DSLab 192p with 16 experts per layer implanted the same way; Q4 MTP head + n-gram drafts, 190K context. On Hugging Face: [oisee/Qwen3.8-Flash-Next-GSQ-RCO-Coder-192E-implant16-GGUF](https://huggingface.co/oisee/Qwen3.8-Flash-Next-GSQ-RCO-Coder-192E-implant16-GGUF) |
| 2-bit, 192 / 280 experts | cuts from DSLab's IQ2_XS 512-expert build, MTP + n-gram |
| K224 variants | the K224 build above with other server settings: MTP + n-gram drafts; the same at temperature 0; n-gram only at temperature 0.6 |
| Qwen3.8-27B | the dense 27B model, unsloth UD-Q4_K_XL, with an MTP head |
| Small models | Q4, 131K context, vendor sampling, all in VRAM: [Ornith-1.5-35B-A3B](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF) Q4_K_M (MTP), [Flint](https://huggingface.co/ggml-org/Flint-GGUF) Q4_K_M (no speculation), [NVIDIA Nemotron 3.5 Lightning 30B-A3B](https://huggingface.co/unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF) UD-Q4_K_XL (MTP), [Muse Glimmer 30B](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF) KQuant-Dynamic-Q4_K_XL (DFlash). Per-model server flags will be published separately |

- **K192p-implant16** solved L1a (5.7 and 5.2 min), L2a (49 and 35 min), L1b (57 min) and L3b (60 min). L3b was
  not solved by any local build before: K224's run was stopped at 94 min, and the NVFP4 build failed it. **L1b is in-sample**: its experts were selected on an L1b session.
  **L2b was stopped by the operator** at 49.5 min after the model looped in its reasoning with no edits. It is shown as
  stopped, not as a failure. L3a was not run. Its second L2a run is flagged `leak_suspect` by the URL audit. That is a
  false positive: the URLs come from the repository's own `ANORMALIES.md`, and the agent made no web calls.
- **Time to first green.** On L2a the fix was in place after 11.9 min (K224-implant16), 10.8 min (DSLab 192p) and
  10.9 min (K192p-implant16). The rest of each run, 12, 21 and 38 min, went into the agent's own tests, docs and
  checks, and every tree stayed green to the end. On L1a the first green came at 4.1–4.4 min.
  [data/first-green.csv](data/first-green.csv) has the details. These were reconstructed from the recorded traffic:
  every edit was replayed and every checkpoint graded with the bench's grader. New runs get it from per-request
  snapshots (`bin/first-green.py`).
- **Fast decoding is not fast solving.** Median decode speeds and L1a times:
  - Nemotron: 78.7 tokens/s, 12.3 min, 86 requests;
  - Ornith: 68.7 tokens/s, 12.9 min;
  - Flint: 50.9 tokens/s, 11.4 min;
  - Muse: 26.9 tokens/s, 11.4 min;
  - K192p-implant16: about 30 tokens/s, 5.7 and 5.2 min.

  [data/decode-speed.csv](data/decode-speed.csv) has the numbers. They come from the llama.cpp server's own timing
  lines.
- **Claude Code sends system messages in the middle of the conversation.** Ornith and gpt-oss drop them with their
  vendor chat templates; Devstral and Muse were served with patched templates. Devstral Small 2's first L1a run is
  not in the table: its tool calls came out as plain text the server did not parse. That is a template problem, a
  rerun is pending, and the run says nothing about the model.
- **At temperature 0** the K224 build failed L2a and L2b, which it solved with default sampling (0.6).

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

**Endpoint key and other secrets.** A custom endpoint's key (`CUSTOM_API_KEY`) never enters the sandbox. The
agent gets a dummy key, and a local proxy outside the sandbox swaps in the real one on the way to your endpoint. The
proxy also removes the key from anything the endpoint sends back. After each run, `bin/secret-scan.py --mask` looks
for that key and for every provider key, token and OAuth login secret it can find (environment, `~/.bashrc`,
`~/.profile`, Claude/Codex logins). It checks each one as is, URL-encoded and base64-encoded, across the whole run
directory, git objects included:
- **Masking.** Matches in logs, traffic, transcripts, `patch.diff` and `result.json` become `E***** [ZAI_API_KEY]`.
  Matches in `work/` and the snapshots are only reported.
- **Flags.** Any match sets `secret_leak` in `result.json` and lists the files in `secret-audit.txt`, never the
  secret itself. `patch_masked` says the published patch differs from the graded tree.

The scan catches accidental leaks, not a deliberately obfuscated key. Z.ai, Alibaba and Azure use the same
host-side key proxy. Anthropic and OpenAI login credentials remain available inside their sandboxes.

**Grading never runs git inside the agent's repository.** Its `.git/config` could name hooks or filters that would
run outside the sandbox. `patch.diff` is built in a throwaway repository instead (`bin/snapshot.py diff`).

**Time to first green.** A run that passes often has the fix in place long before it ends, and spends the rest on
polish (README, extra tests, CI). With `BENCH_RECORD=1` the recording proxy snapshots the work tree into `snap.git`
in the run directory when the run starts, as each request arrives, and once more when the session ends. That
directory is outside the sandbox, so the agent never sees it. Afterwards,
`bin/first-green.py runs/<task>/<run> tasks/<task> [--jobs N]` grades every distinct snapshot with the hidden tests
and adds `first_green_s`, `first_green_request`, `polish_s` (= `duration_s − first_green_s`) and `stayed_green` to
`result.json`.
- **Bisection.** `--bisect` grades only about log2 of the snapshots, plus the ones after the boundary. It assumes
  the run never went green and then broke before that boundary, so it can miss an earlier green spell.
- **Ignored files.** Files the repo ignores (`node_modules/`, build output) are not snapshotted; every snapshot is
  graded with the ones the run ended with. If a `.gitignore` or a dependency file changed during the run,
  `first_green_caveat` says so.

Grading takes CPU, so run it when no timed run shares the machine.

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

## Native ABAP tasks

A second, larger task set, the native ABAP tasks: holes cut into real ABAP code and graded by its own ABAP Unit
tests, with tiers smoke (12 tasks), medium (69) and full (337). The tasks come from a separately distributed package;
bench-bench has only the adapter ([tasks-abap/README.md](tasks-abap/README.md)). These results are kept apart from
the six-task tables.

## Cutting Qwen3.8-Flash-Next to fewer experts

`bin/expert-cut.py` cuts a Hugging Face safetensors checkpoint of Qwen3.8-Flash-Next (e.g. NVIDIA NVFP4) to the 224- or
192-expert sets (or 224 with implanted experts) in `data/expert-sets/`, streaming, standard-library Python only, with a content `--verify` and a
`--compare-router` check. How-to: [docs/expert-cut.md](docs/expert-cut.md).


## API keys and network isolation

Custom, Z.ai, Alibaba, Anthropic and Azure API keys stay in a proxy outside the agent sandbox.
The sandbox receives `bench-sandbox-dummy-key`; the proxy replaces it on upstream
requests and scrubs echoed keys from responses. This applies to direct sandbox
calls as well as benchmark runs. `BENCH_RECORD=1` enables traffic recording;
otherwise the proxy uses `BENCH_PROXY_RECORD=0` and writes no traffic or live log.
Alibaba Codex still requires its existing local Responses API bridge.
Anthropic API-key mode uses `ANTHROPIC_API_KEY` with a loopback
`ANTHROPIC_BASE_URL`; the proxy injects `x-api-key` for `api.anthropic.com`.
As before, an existing `~/.claude/.credentials.json` takes precedence over the
API key and is bound writable into the sandbox so OAuth refreshes persist.

OpenAI (ChatGPT) and Anthropic subscription providers place a login in the sandbox:
Codex gets a private copy removed after the run; Claude binds its credential file
so token refreshes persist. These login providers do not provide API-key isolation.

Native ABAP tasks default to documentation-only web access plus the model endpoint.
Set `BENCH_EGRESS_DOCS=0` for endpoint-only access (`net_policy=egress-endpoint-only`);
this also ignores `BENCH_EGRESS_EXTRA`. The egress filter must remain enabled.


## One-command native ABAP runs

Build from the repository root:

```bash
docker build -f docker/Dockerfile -t abap-bench .
```

The defaults pin Node 22.14.0, Claude Code 2.1.309, Codex 0.162.1 and pi 1.0.2.
Override harness pins with build arguments `CLAUDE_VERSION`, `CODEX_VERSION`,
`PI_VERSION`. The image records those pins in `/opt/agent-versions.txt`; every
invocation writes observed tool versions to `/out/versions.txt`. Managed builds
can pass `--secret id=proxy_ca,src="$CODEX_PROXY_CERT"` to preserve proxy CA trust.

Mount a complete private package root (including its bundled `repo/` descriptor
checkout) and an output directory writable by UID 1000:

```bash
export API_KEY  # set this in your shell or secret manager
mkdir -p output
docker run --rm --security-opt seccomp=unconfined \
  -v "$PACKAGE_ROOT:/pkg:ro" -v "$PWD/output:/out" -e API_KEY \
  abap-bench --agent claude --model demo-model --api-base https://example.org \
  --api-key-env API_KEY --tier smoke4 --env 3 --no-internet
```

Use `--agent codex` with a Responses API base (usually ending in `/v1`), or
`--agent pi` with an OpenAI-compatible chat completions base. Claude uses an
Anthropic-compatible base; a trailing `/v1` is removed before Claude appends
`/v1/messages`. The key is read by variable name and never written to the matrix.
Recording is enabled for these runs. Models/endpoints get distinct executor IDs,
so an existing result from another configuration does not suppress the run.

Tiers are `smoke4`, `smoke`, `medium`, `full`; `ember` aliases `smoke4`. A package's
committed tier wins; older packages fall back to the lists in `tasks-abap/tiers/`.
The fallback `smoke4` contains the first four tasks of this checkout's smoke tier.
The package SHA comes from its root name, or from bundled `repo/HEAD` when the
bind mount renames it to `/pkg`. Descriptors always come from that commit's git
objects. `--env` selects the environment for versioned task IDs. Only the selected
tier is materialized into temporary descriptors; `setup.sh --check` must succeed
before any agent session starts. Native ABAP uses packaged toolchains and does
not require Go.

`--no-internet` sets `BENCH_EGRESS_DOCS=0`: the sandbox can reach only the model
endpoint through the key proxy. Without it, approved documentation hosts are also
available. The container host must allow bubblewrap namespaces; the command above
relaxes Docker's default seccomp restrictions.

Without Docker, inspect the complete plan without credentials or filesystem changes:

```bash
bin/abap-bench --dry-run --agent codex --model demo-model \
  --api-base https://example.org/v1 --api-key-env API_KEY --tier ember --no-internet
```

A local execution needs an isolated checkout without an existing `runs/` directory;
`--pkg`, `--repo`, and `--out` override the container paths. No image build or model
run is performed by the unit tests.


### Live progress

Add `--port 8080` and Docker's `-p 8080:8080`, then open
`http://localhost:8080`. The stdlib server starts before setup checks, refreshes
every three seconds, and shows each task's queued/running/verdict state, duration,
request count, and a link to the latest 200 lines of `live.txt`. Queues are saved
in `/out/queue-<executor>.json`; results and transcripts stay under
`/out/<task>/<run>/`. Partial JSON files are tolerated while a run is writing them.
The server stops when the launcher exits, including on setup failure. To view
saved output afterwards:

```bash
python3 bin/live-page.py --runs output --port 8080
```

The server exposes the dashboard and transcript tails only. It does not serve
package files or arbitrary paths under the output directory.
