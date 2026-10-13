# Methodology

## 1. Tasks

Each task is one **merged pull request** from a public repository: `oisee/open-steamgate`, `oisee/abapiti` or
`oisee/minz`. `tasks/<id>/task.env` pins three things:

- `BASE`: the PR's parent commit. The agent starts from this tree.
- `MERGE`: the merged result. This is the reference solution, and the source of the hidden tests.
- `HIDDEN`: the test files that the PR added or changed. They are taken from `MERGE` at grading time, so the
  agent never sees these versions.

It also holds the `SETUP` commands, which install dependencies once into the template, and the grading commands:
`F2P` (fail-to-pass: acceptance tests that must go from red to green) and `P2P` (pass-to-pass: regression tests
that must stay green).

`bin/goldcheck.sh` validates every task. On `BASE`, F2P must fail and P2P must pass. With the upstream patch
applied (hidden tests excluded from the patch, then overlaid), every check must pass.

**Briefs.** The agent does not get the PR description. It gets a brief that states the problem, the public
interface it must provide, and acceptance criteria in behavioural terms. Briefs are drafted by a helper model,
then reviewed for test leakage, ambiguity and gaps (`brief-review.md`). Fixture text from hidden tests is
rewritten as a description of behaviour. Every brief ends with the same rules:

- Work only in the repository.
- The internet is allowed for documentation and package registries.
- Do not look up this project's own repository, pull requests or forks, and do not search for an existing fix.
- Leave the changes in the working tree, then stop.

**Levels.** Levels climb from ordinary languages toward exotic targets:

| Level | Tasks | Kind of work | Limit |
|---|---|---|---|
| L1 | L1a, L1b | contained change in JS or Go | 30 min |
| L2 | L2a, L2b | code generation with ABAP semantics | 45 min |
| L3 | L3a, L3b | compiler front/middle-end work (Z80 language; TypeScript→ABAP) | 60 min |

## 2. Agents

An **executor** is a model, a provider, a harness and an effort level (`matrix.yaml`). The harnesses are
Claude Code, Codex and Pi. Each harness runs headless with its native tools and default system prompt. The only
input is the brief.

- Z.ai GLM-5.3 runs in all three harnesses, to measure the effect of the harness on a fixed model.
- Alibaba-hosted models (Qwen 3.7 Plus, Kimi K2.5, MiniMax M2.5, GLM-5) run in Pi. On L2 they also run in Codex,
  through the LiteLLM bridge in `gateway/`.
- Luna runs in Codex at medium, high and xhigh effort, to measure the effect of effort.
- Opus in Claude Code and Sol 6.1 in Codex are the frontier reference points.

**Selection to L3.** Every executor runs L1 and L2. `bin/select-l3.py` then picks up to four baseline executors
that passed all four L1 and L2 tasks, ranked by the geometric mean of their time relative to the fastest solve.
The selection is frozen in `data/l3-finalists.json`.

The L2a grader fix (§4) changed which executors qualified. The recomputed eligibility is in
`data/l3-corrected-eligibility.json`, but L3 ran with the original cohort.

## 3. Sandbox

`bin/sandbox.sh` runs each session in **bubblewrap** under a `systemd-run` memory cap.

**What the agent can reach:**
- Inside the sandbox it sees only `/work` (a copy of the task template) and a fresh, empty `HOME`.
- It cannot reach the host's projects, real harness histories, GitHub credentials or SSH keys.
- GitHub is blackholed via `/etc/hosts`. The rest of the internet works.

**When the session stops.** It stops when the agent exits or the wall-clock limit expires. A run that hits
the limit is still graded on the working tree as it was at that moment; `stop=TIMEOUT` records this.

**Leakage audit.** After every run, `finalize.sh` scans the transcript and the agent's home for URLs that
point into the task repository's pull requests, commits or issues, and records `leak_suspect`. No run was
flagged.

## 4. Grading

`bin/grade.sh`:

1. Writes `patch.diff`: the agent's changes against `baseline`, untracked files included.
2. Copies the agent's tree and overlays the `HIDDEN` files from `MERGE`.
3. Runs every F2P and P2P command in a second bubblewrap sandbox. This one has **no network**
   (`GOPROXY=off`) and a 15-minute cap per command.

`RESOLVED` means every command exits 0. The logs are in `runs/<task>/<run>/grade/`.

**L2a grader fix.** The hidden L2a test was called `TestParseI8`. Several agents had written a test of the
same name, so the package failed to compile for a reason unrelated to correctness. The grader now renames only
the held-out function; its assertions stay unchanged. All 16 L2a runs were regraded, and 4 changed from FAILED
to RESOLVED. The old results are kept as `result.before-l2a-name-fix.json`, and
`data/l2a-hidden-name-fix-audit.json` records each change.

## 5. Metrics

The metrics are parsed from each harness's own transcript by `bin/export.py`. Unknown values stay empty;
nothing is estimated.

- **Time:** wall clock from handing over the brief until the agent stops.
- **Claude Code tokens:** summed over all `result` events. For sessions killed at the limit, they are summed per
  message from the session log. The two sources agree exactly where both exist.
- **Codex tokens:** summed over `turn.completed` events.
- **Pi tokens:** summed over the assistant messages in its session files.
- **Input tokens include cached input.** Agents resend their context every turn, so 92–99% of input is cache reads.
- **API cost** is a list-price equivalent from `prices.json`: Claude Code's own `total_cost_usd` for Opus, the
  Z.ai list price for GLM-5.3, and third-party price trackers (checked 2026-10-13) for the other models. Cached input
  is priced at the cache-read rate. It is an estimate for comparison, not what was paid: most runs used
  subscription plans or credits: Claude Max 20x ($200/month), ChatGPT Pro ($200/month), Z.ai GLM Coding Max
  (~$130/month), Alibaba Coding Plan Pro (~$50/month); only Sol 6.1 as agent was billed per token (Azure credits).

## 6. Critics

### 6.1 L1 jury (with test results)

Four critics reviewed every L1 run (`reviews/jury/`, `data/reviews.csv`). They saw the patch, the brief and
the hidden-test outcome, and returned:

- a verdict: correct, incorrect or uncertain;
- a quality score from 1 to 5;
- findings rated P0–P3, each with a reproduction.

A finding confirmed by hand overrides a test pass (`jury-override.json`). This happened once.

The composite `score` and `leaderboard.csv` mix this jury data with test results. The jury covered only L1,
and runs without a jury score got a flat value. **Do not use the composite score for comparisons.** The report
counts only solved tasks, time and tokens.

### 6.2 Blind critic screening (without test results)

**Question.** Which model and harness can tell a correct patch from a broken one *without* seeing the tests?

**Setup** (`bin/critic-screen.sh`, `REVIEW_BLIND=1`):
- Six existing patches on the two most discriminating tasks, L2b and L3b. Three pass the hidden tests and three
  fail them.
- Six critics: Sol in Codex; Opus, Fable and GLM-5.3 in Claude Code; GLM-5.3 and Qwen 3.7 Plus in Pi.
- Each critic gets the full repository with the patch applied, the brief and the diff. It does not get the test
  results or the identity of the patch's author.
- It may run anything for up to 30 minutes, and returns the same review format as the jury. Sol also reviewed one extra patch (Luna xhigh, L2b), which is in the data but outside the six-patch comparison.

**Scoring** (`bin/critic-screen.py` → `data/critic-screen.csv`):
- *Caught:* the critic said incorrect on a FAILED patch.
- *Rejection of a passing patch:* the critic said incorrect on a RESOLVED patch. This is not automatically a
  false alarm, because hidden tests can be incomplete.

**Adjudication** (`data/critic-adjudication.json`). For every rejection of a passing patch, the critic's own
reproduction is run twice: on the patch and on the upstream `MERGE` tree.
- If the two behave differently, the defect is **real and the tests miss it**.
- If they behave the same, it is a **false alarm**.

Among the six screening patches, two of Sol's three such rejections were real bugs in Opus patches: continuation-line indentation on L2b, and
functions returning `{}` on L3b. One was a false alarm: the reference panics the same way. Sol's rejection of the extra
Luna xhigh patch was confirmed as well. Two of its four findings reproduce only on the patch: a `* 3.` continuation is
turned into a comment, and `GET BADI` is treated as a report event. The other two behave the same on the reference.

### 6.3 Two-critic build loop (pilot)

`bin/run-reviewed.sh` runs five sessions with the same model and harness: build → critic → fix → critic → fix.
The critics are fresh sessions that do not see the tests. The loop was tried on three Luna runs, recorded as
round 2 (`docs/internal-review-protocol.md`).

## 7. Known limitations

- **One run per cell.** No repeats yet, so differences of one task are within noise.
- **One ecosystem.** All tasks come from one author's ABAP-centred tooling; transfer to other codebases is untested.
- **Public reference solutions.** All upstream PRs are public, so future models may have trained on them.
  The leakage audit covers lookups during a run, not training data.
- **Effort levels differ** between executors (see `matrix.yaml`; for example Sol 6.1 ran at medium). This
  matches how each was configured, not a common setting.
- **Costs** for models other than Opus and GLM-5.3 are estimates from third-party price trackers, which disagree by up to 2×.
- **Agent transcripts are not published.** They contain each harness's full tool I/O and are 37 GB in total.
  Patches, grading logs and all derived metrics are included.
