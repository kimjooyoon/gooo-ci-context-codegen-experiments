package bodycodegen

import (
	"encoding/json"
	"testing"
)

type probeCase struct { Input int64 `json:"input"`; Expected int64 `json:"expected"` }
type probeObservation struct {
	Index int `json:"index"`
	Input int64 `json:"input"`
	Expected int64 `json:"expected"`
	Actual int64 `json:"actual"`
	Passed bool `json:"passed"`
}

func TestKnownBadTrainingProbe(t *testing.T) {
	cases := []probeCase{{Input: -3, Expected: 0}, {Input: -2, Expected: 0}, {Input: -1, Expected: 0}}
	for index, testCase := range cases {
		actual := ClampNegativeToZero(testCase.Input)
		observation := probeObservation{Index: index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}
		encoded, err := json.Marshal(observation)
		if err != nil { t.Fatal(err) }
		t.Logf("PROBE_ACTUAL:%s", encoded)
		if !observation.Passed { t.Errorf("PROBE_MISMATCH:%s", encoded) }
	}
}
