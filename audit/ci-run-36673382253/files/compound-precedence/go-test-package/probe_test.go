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
	cases := []probeCase{{Input: -1, Expected: 100}, {Input: 10, Expected: 100}, {Input: 11, Expected: 100}, {Input: 13, Expected: 100}}
	for index, testCase := range cases {
		actual := CompoundPrecedence(testCase.Input)
		observation := probeObservation{Index: index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}
		encoded, err := json.Marshal(observation)
		if err != nil { t.Fatal(err) }
		t.Logf("PROBE_ACTUAL:%s", encoded)
		if !observation.Passed { t.Errorf("PROBE_MISMATCH:%s", encoded) }
	}
}
