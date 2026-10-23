# Plan: domain pruning of a dense model (Qwen3.8-27B) for ABAP agent work

Status: proposal, not started; reviewed by cnm 2026-10-23 (notes folded in below). Owners: Alice; cnm (statistics tooling, i7 serving); bench-bench (calibration
data, replay, benchmark runs, report). Written 2026-10-23.

## Question

How much of a dense 27B model does an ABAP coding agent actually need? Can we remove the parts that stay quiet
on ABAP agent work, make the model faster on our own hardware (i7: 2x RTX 4060 Ti 16 GB, 128 GB RAM), and keep
its benchmark results?

This is the dense counterpart of cnm's expert pruning on GLM-5.3-Flash. In a MoE model the router makes the
paths explicit (which of 512 experts a token uses), so unused experts can be cut whole. A dense model has no
router: every weight runs on every token. The prunable units are smaller or larger instead: MLP neurons,
attention heads, whole layers. They are polysemantic (one neuron serves several unrelated features), so a clean
"cut the poetry, keep the ABAP" split is not expected; the benchmark has to show where the loss starts.

## Model facts (from `Qwen/Qwen3.8-27B` config.json)

- 64 layers, hidden size 5120; every 4th layer has full attention (16 layers), the other 48 linear attention.
- Full attention: 24 query heads, 4 KV heads, head dim 256 (KV cache about 64 KB per token in f16).
- MLP: SwiGLU, roughly two thirds of all weights. This is where width pruning pays.
- Baseline on i7: unsloth UD-Q4_K_XL, llama.cpp 970b51ce7, 192k context, KV q8_0: 14.55 tok/s decode,
  1242 tok/s prefill (cnm, 2026-10-23). Live agent runs decode at 12.1-12.4 tok/s.

## Phase 0: baselines (before any pruning)

1. Qwen3.8-27B UD-Q4_K_XL on all six tasks, time-scaled protocol (`BENCH_TIME_SCALE=10`), traffic recorded.
   L2b is running now (started 18:55).
2. The same at a lower quantization (UD-Q3_K_XL, 13.1 GB, or IQ3_S, 12.0 GB). Pruning has to beat this simple
   control: if Q3 keeps the results and is as fast, pruning is not worth it.

## Phase 1: activation statistics

Calibration data:
- ABAP agent traffic: the recorded requests and responses of every Qwen run (`runs/*/custom-qwen38*/traffic.jsonl`,
  a copy of Flash-Next's is on the share in `bench-bench/traffic-qwen38fn/`). They hold system prompts, briefs,
  repository code, tool output and model answers, which is what the model sees when it works.
- Contrast corpus: general text with no code (prose, poetry, dialogue, other natural languages), same token count.

Statistics per layer:
- Layer redundancy: cosine similarity between the residual stream entering and leaving each layer.
  High similarity = the layer changes little = candidate for removal.
- MLP neurons: mean |activation| and the share of tokens where the neuron is active, for both corpora.
  Domain score = activity on ABAP traffic relative to activity on the contrast corpus.
- Attention heads: output norm per head (low priority: only 24 heads, 4 KV groups).

Tooling options:
- `llama-imatrix` (llama.cpp) accumulates, per input column of every matmul, the sum of squared activations and a
  call count. For `ffn_down` the input columns are the SwiGLU neurons, so it gives the mean activation energy per
  neuron (Σ a_j² over tokens), on the GPU with no new code. Energy ratio ABAP/contrast is the first domain score.
  It does not give mean |a| or the share of tokens where a neuron is active; those need a hook. Run both corpora
  with the same binary, `-ngl 999`, similar token counts and the same chat-template rendering (thinking included).
  Check that `in_sum2` for `blk.N.ffn_down` has n_ff entries. Cost: about 1-2M unique tokens at ~1200 tok/s, so
  20-30 minutes per corpus on i7.
- Convergence: run imatrix on growing corpus slices and compare the per-layer neuron rankings (Spearman, Jaccard of
  the bottom set), using cnm's convergence logic with a small adapter in `stats-convergence.py`.
- For layer cosine similarity, head norms and activity shares: transformers with forward hooks on the BF16 weights
  (about 54 GB) on CPU, on i5 or i7 RAM overnight.

## Phase 2: pruned variants, found by bisection from the aggressive end

Start with a brutal cut, not a small one. If the model is still good after it, the experiment is over with the
biggest possible win. If it is not, bisect back toward the full model. Growing the cut in small steps would spend
many evaluations walking up to the same boundary, and every benchmark evaluation on i7 costs hours. This is the
same bisect protocol cnm uses for experts (full / K216 / K72), applied to a cut fraction.

Pruning edits the BF16 safetensors, then converts to GGUF and quantizes to the same Q4_K_XL recipe as the baseline.
Quantized blocks cannot be sliced cleanly along the neuron axis of `ffn_down`. Cut the same number of neurons in
every layer (W10 = −10% in each layer, chosen by that layer's scores), so the scalar n_ff stays valid and llama.cpp's
qwen35 architecture loads the model unchanged.

Two axes, bisected separately, then combined:

| Axis | Unit | First cut | Bisection |
|---|---|---|---|
| Width (W) | share of MLP neurons removed per layer, lowest domain score first | W50 | pass → try W75; fail → W25, then W37 or W12, ... |
| Depth (D) | linear-attention layers removed, the most identity-like first by residual in/out cosine, not by position (all 16 full-attention layers kept) | D16 (of 48) | pass → D32; fail → D8, then D12 or D4, ... |

Each step is judged by the cheap gates of Phase 4 only (perplexity on held-out ABAP traffic, then replay of key
decision points). About four steps per axis reach a resolution of about 6% of neurons or 2-3 layers. Then combine the
two boundaries (for example W25 + D8), check the combination with the gates, and run only the final candidate on the
benchmark. If the benchmark disagrees with the gates, step back one bisection point and benchmark that.

Gate thresholds are fixed before the first cut, from the full model and the Q3 control: a step passes when its
perplexity is no worse than the Q3 control and its replay moves are judged equivalent to the full model's at least as
often as the Q3 control's are. Bisection assumes quality falls monotonically with the cut; if a deeper cut passes
where a shallower one failed, record it and re-check the shallower one once (calibration noise).

Expectation (cnm, from the literature, not measured): dense neuron pruning without healing degrades much faster
than MoE expert pruning. W10 may hold, W20 is likely visibly worse without QLoRA; dropping 2-4 near-identity layers
usually survives. So the brutal first cuts will probably fail and the boundary is likely low, but bisection reaches
it in a few cheap steps either way. The payoff is small if the boundary is near W20: −20% MLP ≈ −13% weights ≈
+13-15% decode, about what Q4 → Q3.5 gives, so compare variants against quantizations at equal file size, not at
equal quantization type.

## Phase 3: healing (optional)

Short LoRA fine-tune on code and agent traces to let the model compensate. QLoRA on 2x 16 GB is possible but slow;
do it only for a variant that is close to the baseline without it.

## Phase 4: evaluation, cheapest first

1. Perplexity on held-out ABAP traffic (minutes). Drop variants that degrade sharply.
2. Replay of key decision points from a solved run (`bin/replay.py`, history thinking stripped), judged blind by
   two judges with cnm's replay and judge pipeline: does the variant make the same move as the full model at the
   same context? Replay with max_tokens of at least 4096 (cnm: a 1024 cap cut long edits and scored 0 for every
   variant on 2 of 25 steps), and strip model and file names from the replies before judging (cnm's blinding
   leaked through the response `model` field). For L2b: steps 10-17 of Flash-Next's run (finding and
   reading the bundled abaplint sources), the golden-file regeneration, the `GET` statement handling.
3. Benchmark: L2b, then all six tasks, same time-scaled protocol as the baseline.
4. Report per variant: tasks solved, decode tok/s, prefill tok/s, file size, VRAM at 192k context.

Success: the bisection boundary gives a variant that solves as many tasks as the Q4 baseline and is clearly faster
than both the baseline and the Q3 control at equal file size. Stop: if the boundary on both axes is below W10 / D4
before healing, and healing does not move it.

## Alternative track: hot neurons on GPU, cold on CPU (low priority)

PowerInfer-style offloading (hot neurons in VRAM, the rest in RAM) relies on natural activation sparsity, which
ReLU models have and SwiGLU models such as Qwen mostly do not; it needs a ReLU-fied model. Strata is specific to
Flash-Next. The imatrix energy distribution from Phase 1 shows the sparsity cheaply; do not plan around this track.

## Priority on i7

After the 27B L2b window, Alice's current plan is Track B: Flash-Next statistics and a cut to 256 of 512 experts,
which would fit VRAM and could give several times the speed, a bigger payoff than this plan. 27B Phase 1 imatrix
runs are short and can fill gaps; the BF16 hook runs can go overnight on i5 or i7. Alice sets the order.

## Risks

- Polysemantic neurons: removing "non-ABAP" neurons can remove features that code also uses.
- Calibration bias: the ABAP traffic comes from six tasks in one ecosystem (Go/TS tooling around ABAP). Neurons
  needed for other ABAP work may look unused. Keep a held-out task for evaluation.
- Time: every benchmark task on i7 takes hours at about 12 tok/s.
