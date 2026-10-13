// Visible acceptance examples (benchmark condition "final gate").
// Odd-numbered cases of the held-out object-literal test; the full acceptance check is broader.
// Run: go test ./tsfront -run Visible   Do not modify this file.

package tsfront

import (
	"testing"

	"github.com/oisee/abapiti/hir"
	"github.com/oisee/abapiti/hir/abap"
	gohir "github.com/oisee/abapiti/hir/golang"
)

func TestVisibleAnonymousObjectLiterals(t *testing.T) {
	prog := lowerStatementsProbe(t, map[string]string{"probe.ts": `
 export class Probe {
   take(x: {a: string, b: number}): string { return x.a; }
   run(): string {
     const x = {a: "first", b: 1};
     const y = {b: 2, a: "second"};
     return this.take({a: x.a + y.a, b: 3});
   }
   optional(): {label: string, extra?: number} { return {label: "ok"}; }
   nested() { return {child: {value: 1}}; }
   union(flag: boolean): {title: string} | {size: number} {
     if (flag) { return {title: "named"}; }
     return {size: 2};
   }
   record(x?: {[name: string]: number}): number {
     let total = 0;
     for (const key in x || {}) { total += 1; }
     return total;
   }
 }
 `}, []string{"probe.ts"})
	if _, err := abap.Emit(prog); err != nil {
		t.Fatal(err)
	}
	if _, err := gohir.Emit(prog); err != nil {
		t.Fatal(err)
	}
	if errs := hir.Verify(prog); len(errs) != 0 {
		t.Fatal(errs)
	}
}
