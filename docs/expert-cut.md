# Cutting your own HF checkpoint of Qwen3.8-Flash-Next to 224 or 192 experts

For a vLLM user with a safetensors build (e.g. `nvidia/Qwen3.8-Flash-Next-NVFP4`). You get a keep list
and a script, not weights: you cut your own files on your own disk.

- `data/expert-sets/qwen38fn-192.json` — the 192-expert keep list (a subset of the 224 set; the build we run ourselves).
- `data/expert-sets/qwen38fn-224.json` — the keep list: `{"0": [...], ..., "47": [...]}`, 224 **original** expert ids
  (0..511) per routed MoE layer, ascending.
- `data/expert-sets/qwen38fn-224-implant16.json` — 224 with 16 per layer replaced by session-ranked experts ([below](#224-with-implanted-experts-qwen38fn-224-implant16json)).
- `bin/expert-cut.py` — the script: Python 3.8+, **standard library only** (no torch, numpy or
  `safetensors` package). It never loads the model; it streams shard by shard and stays under 1 GB of RAM
  (430 MB peak just to index the 299,545 tensors of the NVIDIA build, plus 64 MB copy buffers).

## What it does

For every routed MoE layer (`model.language_model.layers.N.mlp`) it keeps the listed experts in ascending
original-id order and renumbers them 0..223. The tensors
`experts.E.{gate,up,down}_proj.{weight,weight_scale,weight_scale_2,input_scale}` are copied byte for byte,
same dtype. It slices the rows of the router `mlp.gate.weight` in the same order and copies every other
tensor unchanged (shared expert, `shared_expert_gate`, attention, PLE, vision, embeddings, lm_head).

It writes the shards under the same file names (as `*.partial`, renamed only when all are complete), a new
`model.safetensors.index.json`, and `config.json` with `text_config.num_experts = 224` (nothing else
changed). It copies the tokenizer, chat template, generation config and quant configs as-is, and writes
`expert-cut-manifest.json`: the sha256 of every source file, plus the kept ids and the new->original mapping
for each layer. Contiguous ids are required: vLLM sizes the router and FusedMoE with `num_experts` slots and
loads checkpoint experts by index.

It refuses rather than guesses on:

- fused 3D expert tensors (`experts.gate_up_proj` with no expert index, as in some GPTQ / compressed-tensors builds);
- unknown per-expert tensor names;
- a quantised or unknown router;
- quantization configs that name single experts;
- layers with missing experts or missing scale tensors;
- a keep list that does not match the model's layers;
- `top-k >= K`;
- a non-empty output directory;
- too little disk space.

If a cut is interrupted, delete the output directory and run the cut again. `--verify` reports leftover
`*.partial` files.

## MTP block: drop it (`--mtp drop`)

The NVIDIA build carries an MTP head (`mtp.layers.0.mlp.experts.0..511`, FP8 with `weight_scale_inv`) in
`model-fp8-mtp-ple.safetensors`. Its config has **no expert count of its own**. vLLM's qwen4_exp MTP draft
(`vllm/models/qwen4_exp/nvidia/mtp.py`) builds the same decoder layer and `Qwen3NextSparseMoeBlock` from the
draft's text config, which as far as we can tell is loaded from the same `config.json`. Its router and
FusedMoE would then get 224 slots after the cut, and a 512-expert MTP block would not load.

We have no routing statistics for the MTP head, so we cannot give it a justified keep list. `--mtp drop`
therefore removes the whole `mtp.*` module. The main model never loads `mtp.*` anyway: vLLM maps `mtp.` to
nothing for the main model. After the drop, **do not pass `--speculative-config` with `"method": "mtp"`**;
n-gram speculation is unaffected. Without `--mtp drop` (and without an `"mtp.0"` list in the keep file) the
script refuses.

## Steps

```bash
# 1) plan only: reads headers, validates, prints sizes; writes nothing
python3 bin/expert-cut.py /models/Qwen3.8-Flash-Next-NVFP4 data/expert-sets/qwen38fn-224.json /models/qwen38fn-224 --dry-run --mtp drop

# 2) is your router in the original expert order? Reads only router rows. REF may be a local
#    dir or the original repo over HTTP range requests (~160 MB).
python3 bin/expert-cut.py --compare-router /models/Qwen3.8-Flash-Next-NVFP4 hf:nvidia/Qwen3.8-Flash-Next-NVFP4

# 3) cut
python3 bin/expert-cut.py /models/Qwen3.8-Flash-Next-NVFP4 data/expert-sets/qwen38fn-224.json /models/qwen38fn-224 --mtp drop

# 4) verify by content: every output tensor against the source, routers row by row, config, index.
#    --hashes also re-hashes source and output files against the manifest.
python3 bin/expert-cut.py --verify /models/Qwen3.8-Flash-Next-NVFP4 /models/qwen38fn-224 --keep data/expert-sets/qwen38fn-224.json --hashes

# 5) serve as before, only the path changes (no MTP speculative config)
vllm serve /models/qwen38fn-224 <your usual flags>
```

Exit codes:

- 0: ok;
- 1: `--verify` found a problem, or `--compare-router` found permuted rows;
- 2: refused (unsupported layout, bad input, missing file or network error; the message says which).

**Disk:** the output is the non-expert bytes plus 224/512 of the routed-expert bytes. For the NVIDIA NVFP4
build:

| part | before | after |
|---|---|---|
| routed experts | 67.95 GB | 29.73 GB |
| PLE | 51.27 GB | 51.27 GB |
| other non-expert tensors | 9.83 GB | 9.83 GB |
| vision | 0.90 GB | 0.90 GB |
| **total** | 132.6 GB | **≈ 91.7 GB (85.4 GiB)** |

The total is with `--mtp drop`, and the source stays on disk. On a 4-core CPU box the tool read about
220 MB/s of source, so a full cut takes about 10 minutes plus your disk time. `--verify --hashes` takes
about as long again.

## Router order: why `--compare-router` matters

The ids in the keep list are row positions in the original router (`mlp.gate.weight`). For layers 0 and 1
we checked that the NVIDIA NVFP4 router rows equal, row for row at the same index, those of an independent
conversion (unsloth's Q8_0 GGUF, whose F32 router values are exactly BF16-representable). The other layers
are not checked.

If your build is a fine-tune or a re-export that permuted experts, the same ids name different experts. The
cut is then wrong even though `--verify` passes, because verify checks the cut against *your* source, not
against the original. `--compare-router` gives one verdict for the whole model:

- `IDENTICAL`: rows are byte-equal.
- `FINETUNED`: rows changed, but every row is still closest to its own index (cos >= 0.98). The ids still
  map, but the routing statistics behind this set may not carry over to your weights.
- `PERMUTED`: some row best matches another index. Do not use this keep list.

It refuses (exit 2, no verdict) if any router tensor is missing from REF under the same name. That happens,
for example, when REF uses a different name prefix.

## Evidence and what is not measured

This 224-expert set held up on our agentic ABAP/Go tasks with a ~3.5-bpw GGUF build in llama.cpp. It is
not measured on NVFP4 or in vLLM.

Not tested or verified:

- loading in vLLM, with any TP/EP setting, with 224 experts;
- serving speed without MTP;
- whether vLLM's ModelOpt NVFP4 MoE path shares or reduces per-expert `input_scale` across experts (for
  example takes the max). If it does, dropping experts could change activation scales slightly. Compare
  outputs against the full model on your own tasks.

## Checks of the script

`python3 tests/test_expert_cut.py` runs CPU tests on a synthetic model (4 layers x 16 experts, NVFP4-style scales, an MTP
block, layers split across shards). They check router rows, experts under the mapping, non-expert tensors,
config and index.

Each of these mutants makes `--verify` fail:

- wrong order;
- off-by-one mapping;
- swapped experts;
- permuted router rows;
- dropped scale tensor;
- extra expert;
- config not updated;
- wrong index shard;
- one flipped byte;
- leftover partial shard.

The cut is refused on: fused 3D experts, unknown leaves, missing source scales, expert-indexed quant
configs and low disk space. A router comparison with a prefix mismatch gives no verdict.

On the real NVIDIA files: the full-model dry run passes, a real cut of shard 1 passes `--verify --hashes`,
and the official `safetensors` library opens the output.


## 192 instead of 224

Same commands with `data/expert-sets/qwen38fn-192.json`. The 192 set is the 224 set minus 32 experts per layer with the lowest
router weight on the same agentic session. In our GGUF builds it passed the same checks as 224 and solved our first live
tasks; it was not measured on NVFP4/vLLM either. For the NVIDIA NVFP4 checkpoint a dry run gives 81.4 GiB (192) vs 85.4 GiB
(224) with `--mtp drop`.

## 224 with implanted experts (`qwen38fn-224-implant16.json`)

DSLab 224 with 16 experts per layer replaced by experts ranked on an agentic ABAP/Go session. In layers 0–46 the 16
weakest experts of the 224 set (lowest summed router weight on that session) give way to the 16 strongest experts outside
DSLab's 256; layer 47 has one replacement, 753 in total. Same format and commands as the 224 set.

**Which sources it works on.** The ids are original ids (0..511), and in every layer some are outside DSLab's 256, so the
set can only be cut from a checkpoint that still holds all 512 experts, such as the NVIDIA NVFP4 build. There every kept
expert, the newcomers included, keeps its own bytes. A checkpoint that is already pruned (DSLab's 256, or any earlier cut)
does not have those experts. `bin/expert-cut.py` refuses such a source: the ids are out of range
(`test_refuses_already_cut_source`). It reads safetensors only; GGUF files are not supported.

**What was measured, and on which build.** Two different GGUF models carry this set; neither is the HF/NVFP4 cut above,
which is not measured.

| Build | How the newcomers were made | KL to the full model | Live runs |
|---|---|---|---|
| 3.5-bit, DSLab 224 + implants | requantized from Q8_0 to each layer's DSLab quant type (with imatrix), not a byte copy | 0.148 (DSLab 224 on the same setup: 0.202, lower in every token class) | L1a 7.3 min and L2a 23.9 min, both solved |
| 2-bit, cut from DSLab's IQ2_XS 512-expert build | byte copy | 0.886 overall, 0.299 on decision tokens (L2b; another harness, not comparable with 0.148) | none |

The live runs are not paired with the committed DSLab 224 series, so their times do not compare directly.
