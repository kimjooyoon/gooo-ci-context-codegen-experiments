package bodycodegen

//gooo:generated:start id="bodycodegen://activity/clamp-negative-to-zero" kind="activity"
func ClampNegativeToZero(input int64) int64 {
	var output = input
	if input < 0 {
		output = input
	} else {
		output = input
	}
	return output
}

//gooo:generated:end id="bodycodegen://activity/clamp-negative-to-zero" kind="activity"
