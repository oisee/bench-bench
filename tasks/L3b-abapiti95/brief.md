# Lower anonymous TypeScript object literals without a named shape

The TypeScript frontend currently fails or produces blocking diagnostics for object literals whose structural type has no named class or alias. Extend lowering so these literals can become valid HIR and can be emitted by the existing backends.

## Acceptance

- Construct a structural representation for object literals when no named target shape exists. Reuse the same synthesized shape for equivalent field layouts, including literals whose property order differs.
- Handle object literals used in assignments, arguments, return values, nested structures, and contextual union or optional types. Empty object literals should use the appropriate contextual shape when one is available.
- Preserve optional and readonly property information. A readonly view must not create an incompatible physical layout for an otherwise equivalent shape.
- Handle record-like object literals without forcing them into an incompatible class shape. Keep existing named-shape behavior and backend output working.
- The resulting HIR must verify and emit through the existing ABAP and Go backends without regressions.

## Notes

Existing behavior must not regress. You may run the project's tests. Work only inside the current repository.

## Rules

- The Go toolchain and the project's dependencies are installed.
- You may use the internet for documentation and package registries, but do not look up this project's own source repository, its pull requests, issues, branches or forks, and do not search for an existing fix. Solve the task from the code in front of you.
- Leave your changes in the working tree (committing is optional). Stop when you believe the task is complete.
