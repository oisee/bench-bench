## Findings

- [LEAK-TEST] The acceptance criteria repeat the hidden test’s exact 150,000-line, 100-space input shape.
- [LEAK-TEST] The three trailing spaces, following `'b  '`, and five-character reduction reproduce the hidden test’s assertions.
- [GAP] The hidden comparison covers quote-balanced fragments that need not be complete SQL statements; the draft guarantees behavior only for well-formed input.
- [AMBIGUITY] “Preserve double quotes” could suggest that double-quoted regions prevent trimming. Existing behavior still recognizes single-quote pairs within them.

## Revised brief

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