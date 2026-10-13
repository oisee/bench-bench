# Two-critic builder protocol

This protocol is opt-in: `BENCH_INTERNAL_REVIEW_ROUNDS=2 bin/run.sh ...`.
`bin/run.sh` defaults to the single-session baseline again, because the 3-run
pilot showed no gain in solved tasks at 2–10× the time. It does not change the
64 graded single-session runs or the locked L3 finalist selection.
The reviewed protocol defaults to `BENCH_ROUND=2`,
so its results remain separate from the L1/L2/L3 baseline in selection and
report summaries.
The historical L2/L3 queue and retry scripts explicitly pin the old protocol
so a recovery cannot silently fill a round-1 cell with a reviewed run.

## Sequence

`bin/run-reviewed.sh` orchestrates five same-model, same-harness sessions in
the task sandbox:

1. Builder makes an initial patch in the task worktree.
2. Critic 1 receives a private copy of that worktree and the original task
   brief. It reviews correctness, coverage, edge cases and tests, then writes
   `review.md`. Its edits cannot change the builder worktree.
3. Builder receives the critic's written findings, verifies them, repairs the
   patch and runs relevant tests.
4. Critic 2 receives a fresh private copy of the repaired worktree. It reviews
   regressions, API semantics, boundaries and maintainability, then writes a
   second `review.md`.
5. Builder verifies the second findings, repairs and tests again. Only then
   does the normal hidden-test grader run.

The script requires both critic sessions to exit successfully and produce
nonempty review files. A critic failure leaves the run incomplete; it cannot
be counted as a two-review result. The critics never receive hidden tests or
grading logs. Existing external blind jury reviews remain a separate layer.

The first builder stage gets roughly two thirds of the task's original time
limit. Each critic gets 5–10 minutes, depending on task level, and each repair
stage gets half the original limit. These are per-stage ceilings, not expected durations.
Total measured time includes all five stages, so compare reviewed runs with
single-session runs as different workflows. The run uses the same memory cap
as the enclosing `systemd-run --scope` command.

## Run and verify

```bash
# Example: new reviewed run, round 2, in an 18 GB scope
systemd-run --user --scope -p MemoryHigh=14G -p MemoryMax=18G \
  ./bin/run.sh \
  ./tasks/L3a-minz39 zai-glm53-codex

# Explicit old-protocol reproduction
BENCH_INTERNAL_REVIEW_ROUNDS=0 bin/run.sh tasks/L3a-minz39 zai-glm53-codex
```

The reviewed run directory contains `internal-reviews.json`,
`internal-reviews/critic-1.md`, `internal-reviews/critic-2.md`, stage logs and
the final `result.json`. `result.json` records
`protocol=two-independent-critic-rounds-v1` and `internal_review_rounds=2`.
`meta.json` records `round=2` and the same protocol. `bin/export.py` adds token and tool use across all five stages;
unknown stage metrics remain unknown in the total.
The HTML report shows a separate "Two-critic reviewed runs" matrix scope and
paired original/reviewed outcomes. The original L2/L3 finalist cards continue
to use round-1 results. The first live pilot covered three Luna/Codex failed
task/effort cases: both L2b cases stayed failed, and L2a xhigh passed in both
rounds after an unrelated hidden-test name collision was corrected. See
`HANDOVER.md` and `data/l2a-hidden-name-fix-audit.json` before interpreting
those results.

These critics are separate agent sessions coordinated by the benchmark runner,
not a claim that every harness has a native subagent tool. This makes the two
review rounds available to Codex, Claude Code and Pi with the same structure.
The reviewers use the builder's model, which controls for a model change but
does not make them statistically independent. Always inspect the review files
and stage log before claiming the review protocol was followed meaningfully.

For Codex-specific native delegation, see the [official OpenAI multi-agent
documentation](https://developers.openai.com/api/docs/guides/agents-api/multi-agent).
This benchmark protocol uses separate CLI sessions so the same review rule
works for all three harnesses.
