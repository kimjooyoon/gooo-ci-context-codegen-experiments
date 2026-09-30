package bodycodegen

import (
	"encoding/json"
	"testing"
)

type probeCase struct { Suite string; Index int; Input int64; Expected int64 }
type probeObservation struct { Suite string `json:"suite"`; Index int `json:"index"`; Input int64 `json:"input"`; Expected int64 `json:"expected"`; Actual int64 `json:"actual"`; Passed bool `json:"passed"` }

func TestSelectedBodyAgainstPostSelectionSuites(t *testing.T) {
	cases := []probeCase{{Suite: "training", Index: 0, Input: -3, Expected: 7}, {Suite: "training", Index: 1, Input: -2, Expected: 7}, {Suite: "training", Index: 2, Input: -1, Expected: 7}, {Suite: "holdout", Index: 0, Input: -8, Expected: 7}, {Suite: "holdout", Index: 1, Input: 0, Expected: 0}, {Suite: "holdout", Index: 2, Input: 7, Expected: 7}, {Suite: "holdout", Index: 3, Input: 8, Expected: 8}}
	for _, testCase := range cases {
		actual := NegativeToSeven(testCase.Input)
		observation := probeObservation{Suite: testCase.Suite, Index: testCase.Index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}
		encoded, err := json.Marshal(observation)
		if err != nil { t.Fatal(err) }
		t.Logf("CASE_RESULT:%s", encoded)
		if !observation.Passed { t.Errorf("CASE_MISMATCH:%s", encoded) }
	}
}
