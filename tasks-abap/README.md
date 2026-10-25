# Native ABAP tasks

A second task set, separate from the six PR tasks in `tasks/`. The tasks are holes cut into real ABAP code, either a
line, a block, a loop or IF body, or a whole method, in open-abap-core, abapGit and newer ABAP projects. Each one is
graded by that code's own ABAP Unit tests, run through a transpiler with no SAP system. The corpus, generator,
workspaces, graders and hidden parts live in a separate, privately distributed package; bench-bench holds only this
adapter:

- `PACKAGE`: the pinned commit of the package's descriptor repository.
- `tiers/{smoke,medium,full}.txt`: fixed task lists, chosen with a seed before any run and never re-picked after
  results.
  - `smoke`: 1 task per family.
  - `medium`: 2–3 per family.
  - `full`: every task.
  - A family is task type × source group (old public, own public, recent) × repository.

How to use it (needs the package; ask the maintainers):

```bash
export ABAP_TASKS=~/dev/abap-tasks/<sha> ABAP_TASKS_REPO=~/dev/abap-tasks/repo
bin/abap-tasks.sh smoke                                  # writes tasks-abap/<id>/ (git-ignored)
BENCH_TASKS_DIR=tasks-abap bin/setup.sh --check          # clone, PREPARE, hole FAILS / solution PASSES
BENCH_TASKS_DIR=tasks-abap bin/bench.sh <executor-id>
```

What the adapter does with a descriptor (`bin/abap-tasks.sh` header has the details):

- **Source.** `URL` is the source repository.
- **PREPARE.** It cuts the hole and removes held-out tests in the template, before the baseline commit.
- **Agent tool.** `osg-abap` (lint, check, test) is mounted read-only at `/agent-tool` in the agent's sandbox.
- **Grading.** `HIDDEN_DIR` (pristine sources and the oracle) is copied from the package into the grade copy only.
  The grader runs from `/tool`, mounted only in the offline grading sandbox. It takes only the target method body
  from the agent's tree.
- **Separate results.** These runs stay out of the six-task tables and report.
