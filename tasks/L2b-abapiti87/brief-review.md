## Findings
- [LEAK-TEST] The draft reproduces hidden-test statements for forward declarations, runtime `GET`, and a single-line `IF`; describe those behaviors without the fixture text.
- [AMBIGUITY] “Nested methods” suggests methods nest within methods; the requirement concerns method bodies within class implementations.
- [GAP] The draft requires consistent generated output but does not explicitly say that generated ABAP must pass through `Format`, as the required interface specifies.

## Revised brief

# Consistent indentation for generated ABAP

Generated ABAP needs consistent indentation following abaplint’s default indentation style. Apply it to generated classes, interfaces, runtime helpers, auxiliary drivers, and reports. Preserve program semantics, literal values, and comment contents. Use one fixed style without configuration options.

In package `hir/abap`, provide `indent(src string) string` for indentation and `Format(src string) (string, error)` for indentation and line wrapping. Generated ABAP must pass through `Format`.

## Acceptance

- Use two spaces per ordinary block level, including method bodies, control flow, interfaces, and forms. Closing statements align with their openers; alternative branches align with their opening branch.
- Class headers and `ENDCLASS` start at column zero. Class sections have two leading spaces and their declarations have four. Methods in class implementations have two leading spaces; their bodies have four.
- `WHEN` clauses sit two spaces inside `CASE`, and their bodies two spaces further in. `ENDCASE` aligns with `CASE`.
- `TRY` bodies sit four spaces inside `TRY`. `CATCH` and `CLEANUP` sit two spaces inside it, with their bodies two spaces further in. `ENDTRY` aligns with `TRY`.
- Report event headers start at column zero with two-space-indented bodies. A subsequent form starts at column zero. Forward class declarations do not open blocks, and runtime `GET` statements do not open report events.
- Continuation lines of multiline statements retain the statement’s indentation. Multiple statements on one line remain together and update indentation for following lines correctly.
- Ignore apparent keywords and statement-ending periods inside literals, string templates, escaped delimiters, and comments. Full-line `*` comments remain at column zero.
- Wrapped lines retain their statement’s indentation and do not exceed 255 bytes, including leading spaces. Both formatting operations are idempotent.

## Notes

Existing behavior must not regress. You may run the project’s tests. Work only inside the current repository.