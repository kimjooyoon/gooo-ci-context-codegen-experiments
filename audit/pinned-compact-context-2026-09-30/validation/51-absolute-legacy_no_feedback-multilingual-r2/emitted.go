package bodycodegen

//gooo:generated:start id="bodycodegen://activity/abs-wrap-int64" kind="activity"
func AbsWrapInt64(input int64) int64 {
	var output = input
	if input < 0 {
		output = (-input)
	} else {
		output = input
	}
	return output
}

//gooo:generated:end id="bodycodegen://activity/abs-wrap-int64" kind="activity"
