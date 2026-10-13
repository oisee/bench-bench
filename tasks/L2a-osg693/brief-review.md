## Findings
- [LEAK-TEST] The acceptance list copies the hidden tests’ input values, boundary cases, and expected results. State the behavior and limits without those fixtures.
- [AMBIGUITY] “Blanks” could imply all whitespace, while the merged behavior trims spaces. “Signed numeric text” should make clear that the sign is optional.

## Revised brief

# Support string-to-int8 conversion

Assigning an ABAP `string` to an `int8` currently aborts code generation with `no Go for conversion c2n string->int8`. Support this conversion in both Go and JavaScript output while preserving signed 64-bit values exactly.

Expose these runtime interfaces:
- Go: `abap.ParseI8(string) int64` in package `osg/gogen/abap`, at `tools/gogen/go/abap`.
- JavaScript: exported `ParseI8(v)` in `tools/gogen/js/abap.mjs`, accepting a string and returning a `BigInt`.

## Acceptance

- Ignore surrounding spaces. Empty or spaces-only input converts to zero.
- Accept numeric text with an optional plus or minus sign. Round decimal values to the nearest integer, with halfway values rounded away from zero.
- Preserve every value within the signed 64-bit range exactly, including both endpoints. Raise `CX_SY_CONVERSION_OVERFLOW` when the value, including a rounded value, falls outside that range.
- Raise `CX_SY_CONVERSION_NO_NUMBER` for invalid numeric text.
- Go runtime failures panic with an `ArithmeticError` whose `Class` identifies the corresponding exception.
- Generated programs in both languages support string-to-int8 assignment, exact decimal formatting of the result, and catching overflow with ABAP `CATCH cx_sy_conversion_overflow`.

## Notes

Existing behavior must not regress. You may run the project’s own tests. Work only inside the current repository.