# Add short-circuit logical operators to Nanz

Nanz expressions need boolean `&&` and `||`. The current parser and lowering pipeline do not handle them correctly. Implement the operators so compiled programs preserve normal boolean behavior and side effects.

## Acceptance

- Parse `&&` and `||` as distinct operators. Logical OR binds less tightly than logical AND; both bind less tightly than bitwise operators and comparisons. Preserve the existing precedence of `&`, `|`, `xor`, arithmetic, and shifts relative to each other.
- Evaluate the right operand of `&&` only when the left is true. Evaluate the right operand of `||` only when the left is false. This must hold when the right operand has side effects.
- Produce a canonical boolean result usable as a value or as a condition in `if` and `while` statements. Nested expressions should retain the expected precedence.
- Preserve existing bitwise operators and make logical expressions work through the existing HIR, VM, and compiler paths.

## Notes

Existing behavior must not regress. You may run the project's tests. Work only inside the current repository.

## Rules

- The Go toolchain and the project's dependencies are installed.
- You may use the internet for documentation and package registries, but do not look up this project's own source repository, its pull requests, issues, branches or forks, and do not search for an existing fix. Solve the task from the code in front of you.
- Leave your changes in the working tree (committing is optional). Stop when you believe the task is complete.
