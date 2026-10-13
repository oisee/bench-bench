# Handle very large SQL string literals safely

## Problem

SQL writes containing a very large single-quoted literal can fail with `RangeError: Maximum call stack size exceeded`. Update the existing `trimLiterals(sql)` export from `tools/sql-literals.mjs` to handle literals of roughly 15 million characters while preserving its string transformation behavior.

## Acceptance

- Remove ordinary spaces immediately before the closing quote of each single-quoted literal, including when a statement contains multiple literals.
- Preserve leading and internal spaces, tabs, newlines, doubled single quotes (`''`), and all characters outside the recognized single-quote pairs.
- Handle empty and space-only literals.
- Preserve the existing behavior for short quote-balanced fragments as well as complete statements. Double quote characters do not change how single-quote pairs are recognized.
- Complete on very large literals without a stack overflow and produce the correctly trimmed result.

## Notes

Existing behavior must not regress. You may run the project’s own tests. Work only inside the current repository.

## Rules

- Node.js, npm and Go are installed and the project's dependencies are already in place.
- You may use the internet for documentation and package registries, but do not look up this project's own source repository, its pull requests, issues, branches or forks, and do not search for an existing fix. Solve the task from the code in front of you.
- Leave your changes in the working tree (committing is optional). Stop when you believe the task is complete.
