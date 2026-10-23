# Patch review

I ran each snippet below as `input.ts` with `strict: true` and `target: "es2020"`, using `tsfront.Load`, checker diagnostics, and `Lower`. All snippets were accepted by the TypeScript checker. `go test ./tsfront -count=1` also passed.

## [P1] Inferred object shape containing an empty array is rejected

Location: `tsfront/lower_types.go:733-737`

```ts
export const box = { items: [] };
export const size = box.items.length;
```

Observed: `Lower` reports `unsupported-type: object literal with an empty array field items matches no declared shape ("items":array<void>;)`, then cannot lower `box`. Expected: this valid anonymous object literal should receive a structural shape and produce verifiable, emittable HIR even without an annotated array element type. The same input succeeds when `box` has an explicit `{items: number[]}` annotation, showing the missing path is inferred shape construction.

## [P1] Adding a property to a record spread forces an incompatible shape

Location: `tsfront/lower_expr.go:2140-2151`, `tsfront/lower_expr.go:2238-2249`

```ts
export const rec: Record<string, number> = { a: 1 };
export const copy = { ...rec, s: 2 };
```

Observed: `Lower` reports `unsupported-type: spread needs a shape value` at `...rec`. The inferred literal is routed to a shape with field `s`, whose spread handler accepts only class references; `rec` is an ordered map. Expected: the valid literal should copy the record entries and add `s`, while retaining a compatible runtime representation and emitting without a blocking diagnostic. `{...rec}` alone succeeds, so the added field changes the route incorrectly.

## [P2] Numeric keys in record literals are rejected

Location: `tsfront/lower_expr2.go:555-563`

```ts
export const rec: Record<string, number> = { 1: 2 };
export const value = rec["1"];
```

Observed: `Lower` reports `unsupported-type: record property name KindNumericLiteral is not lowered`. Expected: the numeric property name should be converted to the string key `"1"` and lowered as a map entry, as JavaScript and the TypeScript checker do.

## [P2] Reusing a mutable shape discards a readonly view

Location: `tsfront/lower_types.go:628-675`

```ts
export const mutable: { x: number } = { x: 1 };
export const readonly: { readonly x: number } = { x: 2 };
export const value = readonly.x + mutable.x;
```

Observed: `Lower` creates one `shape.*` class whose `x` field has `Readonly=false`; the readonly view is mapped to that preexisting field. Expected: readonly property information should survive the view while the two views retain a compatible physical layout. Reversing which view is encountered first changes the class metadata, so the result depends on source order.

## [P2] Reusing a required reference shape discards optional field information

Location: `tsfront/lower_types.go:636-675`

```ts
export const present = { x: { y: 1 } };
export const missing: { x?: { y: number } } = {};
export function read(): number { return missing.x?.y ?? 9; }
```

Observed: `Lower` reuses the class created for `present`, and its `x` field has type `classref<shape.*>` rather than `optional<classref<shape.*>>`. It verifies and emits, but the optional declaration's field information is absent from the HIR shape. Expected: `x?` should remain represented as optional in the contextual view while sharing the reference storage layout.
