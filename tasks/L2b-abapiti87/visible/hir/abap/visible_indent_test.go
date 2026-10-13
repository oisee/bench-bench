// Visible acceptance examples (benchmark condition "visible checks").
// Odd-numbered cases of the held-out indentation tests; the full acceptance check is broader.
// Run: go test ./hir/abap -run Visible   Do not modify this file.

package abap

import (
	"strings"
	"testing"
)

func TestVisibleIndentBlocks(t *testing.T) {
	want := `CLASS zcl_test DEFINITION.
  PUBLIC SECTION.
    METHODS run.
  PROTECTED SECTION.
    DATA: a TYPE i,
    b TYPE i.
  PRIVATE SECTION.
ENDCLASS.
CLASS zcl_test IMPLEMENTATION.
  METHOD run.
    IF a = 1.
      LOOP AT tab INTO row.
        TRY.
            CASE a.
              WHEN 1.
                WRITE a.
              WHEN OTHERS.
                WRITE b.
            ENDCASE.
          CATCH cx_root.
            WRITE a.
          CLEANUP.
            WRITE b.
        ENDTRY.
      ENDLOOP.
    ELSEIF a = 2.
      WHILE a > 0.
        a = a - 1.
      ENDWHILE.
    ELSE.
      DO 2 TIMES.
* column-zero comment
        WRITE 'ENDIF. ''TRY.'''. " IF ignored
      ENDDO.
    ENDIF.
  ENDMETHOD.
ENDCLASS.
INTERFACE zif_test.
  METHODS run.
ENDINTERFACE.
START-OF-SELECTION.
  WRITE 'start'.
END-OF-SELECTION.
  WRITE 'end'.
FORM run.
  WRITE 'form'.
ENDFORM.
`
	var input strings.Builder
	for _, line := range strings.Split(want, "\n") {
		input.WriteString(strings.TrimLeft(line, " "))
		input.WriteByte('\n')
	}
	got := indent(strings.TrimSuffix(input.String(), "\n"))
	if got != want {
		t.Fatalf("unexpected indentation:\n%s", got)
	}
	if indent(got) != got {
		t.Fatal("indent is not idempotent")
	}
}

func TestVisibleIndentStatementBoundaries(t *testing.T) {
	src := "CLASS zcl DEFINITION DEFERRED.\nFORM run.\nGET RUN TIME FIELD reading.\nDATA: a TYPE i, b TYPE i.\nIF a = 1. WRITE `a.b`. ENDIF.\nWRITE |escaped \\| period. template|.\nENDFORM.\n"
	want := "CLASS zcl DEFINITION DEFERRED.\nFORM run.\n  GET RUN TIME FIELD reading.\n  DATA: a TYPE i, b TYPE i.\n  IF a = 1. WRITE `a.b`. ENDIF.\n  WRITE |escaped \\| period. template|.\nENDFORM.\n"
	if got := indent(src); got != want {
		t.Fatalf("got:\n%s", got)
	}
}
