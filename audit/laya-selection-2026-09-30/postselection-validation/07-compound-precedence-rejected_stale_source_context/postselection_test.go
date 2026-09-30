package bodycodegen

import (
	"encoding/json"
	"testing"
)

type probeCase struct { Suite string; Index int; Input int64; Expected int64 }
type probeObservation struct { Suite string `json:"suite"`; Index int `json:"index"`; Input int64 `json:"input"`; Expected int64 `json:"expected"`; Actual int64 `json:"actual"`; Passed bool `json:"passed"` }

func TestSelectedBodyAgainstPostSelectionSuites(t *testing.T) {
	cases := []probeCase{{Suite: "training", Index: 0, Input: -1, Expected: 100}, {Suite: "training", Index: 1, Input: 10, Expected: 100}, {Suite: "training", Index: 2, Input: 11, Expected: 100}, {Suite: "training", Index: 3, Input: 13, Expected: 100}, {Suite: "holdout", Index: 0, Input: 0, Expected: 0}, {Suite: "holdout", Index: 1, Input: 9, Expected: 9}, {Suite: "holdout", Index: 2, Input: 12, Expected: 12}, {Suite: "holdout", Index: 3, Input: 14, Expected: 100}}
	for _, testCase := range cases {
		actual := CompoundPrecedence(testCase.Input)
		observation := probeObservation{Suite: testCase.Suite, Index: testCase.Index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}
		encoded, err := json.Marshal(observation)
		if err != nil { t.Fatal(err) }
		t.Logf("CASE_RESULT:%s", encoded)
		if !observation.Passed { t.Errorf("CASE_MISMATCH:%s", encoded) }
	}
}
