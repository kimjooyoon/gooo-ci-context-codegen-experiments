package bodycodegen

//gooo:generated:start id="bodycodegen://activity/negative-to-seven" kind="activity"
func NegativeToSeven(input int64) int64 {
	var output = input
	if input < 0 {
		output = 7
	} else {
		output = input
	}
	return output
}

//gooo:generated:end id="bodycodegen://activity/negative-to-seven" kind="activity"
