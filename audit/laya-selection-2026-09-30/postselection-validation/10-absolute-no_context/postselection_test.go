package bodycodegen

import (
	"encoding/json"
	"testing"
)

type probeCase struct { Suite string; Index int; Input int64; Expected int64 }
type probeObservation struct { Suite string `json:"suite"`; Index int `json:"index"`; Input int64 `json:"input"`; Expected int64 `json:"expected"`; Actual int64 `json:"actual"`; Passed bool `json:"passed"` }

func TestSelectedBodyAgainstPostSelectionSuites(t *testing.T) {
	cases := []probeCase{{Suite: "training", Index: 0, Input: -3, Expected: 3}, {Suite: "training", Index: 1, Input: -2, Expected: 2}, {Suite: "training", Index: 2, Input: -1, Expected: 1}, {Suite: "holdout", Index: 0, Input: -9223372036854775808, Expected: -9223372036854775808}, {Suite: "holdout", Index: 1, Input: 9223372036854775807, Expected: 9223372036854775807}, {Suite: "holdout", Index: 2, Input: 0, Expected: 0}, {Suite: "holdout", Index: 3, Input: 4, Expected: 4}}
	for _, testCase := range cases {
		actual := AbsWrapInt64(testCase.Input)
		observation := probeObservation{Suite: testCase.Suite, Index: testCase.Index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}
		encoded, err := json.Marshal(observation)
		if err != nil { t.Fatal(err) }
		t.Logf("CASE_RESULT:%s", encoded)
		if !observation.Passed { t.Errorf("CASE_MISMATCH:%s", encoded) }
	}
}
