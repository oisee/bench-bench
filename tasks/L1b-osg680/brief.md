# Support Base64 text decoding in compiled ABAP

## Problem

ABAP programs calling `cl_http_utility=>decode_base64` currently fail with `NOT_COMPILED` under osgo. Make the call compile and return text consistent with Node’s `Buffer.from(encoded, "base64").toString()`.

The Base64 step must follow Node’s handling of standard and URL-safe alphabets, optional padding, and extraneous characters. Interpret the decoded bytes as UTF-8. Preserve valid text and use Node-compatible U+FFFD replacements for malformed sequences.

Provide the public Go interface `func Decode(b string) string` in package `nodeutf8`, importable as `osg/gogen/nodeutf8` from `tools/gogen/go/nodeutf8`. Its input is a string of raw bytes, and its output is their Node-compatible UTF-8 text interpretation.

## Acceptance

- `cl_http_utility=>decode_base64` compiles and executes successfully for supported inputs.
- Padded and unpadded Base64 produce the same decoded text. Standard and URL-safe input follow Node’s behavior.
- Valid multibyte UTF-8 becomes the corresponding Unicode text; ABAP character operations such as `strlen` reflect the resulting characters.
- Malformed UTF-8 produces the same replacement characters as Node, including for invalid continuations, invalid code points, and incomplete sequences. Valid text around malformed bytes is preserved.
- `nodeutf8.Decode` has the specified public signature and behavior.

## Notes

Existing behavior must not regress. You may run the project’s own tests. Work only inside the current repository.

## Rules

- Node.js, npm and Go are installed and the project's dependencies are already in place.
- You may use the internet for documentation and package registries, but do not look up this project's own source repository, its pull requests, issues, branches or forks, and do not search for an existing fix. Solve the task from the code in front of you.
- Leave your changes in the working tree (committing is optional). Stop when you believe the task is complete.
