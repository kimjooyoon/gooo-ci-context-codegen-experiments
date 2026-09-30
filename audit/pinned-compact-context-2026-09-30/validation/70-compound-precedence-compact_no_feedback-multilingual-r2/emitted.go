package bodycodegen

//gooo:generated:start id="bodycodegen://activity/compound-precedence" kind="activity"
func CompoundPrecedence(input int64) int64 {
	var output = input
	if input < 0 || input >= 10 && input != 12 {
		output = 100
	} else {
		output = input
	}
	return output
}

//gooo:generated:end id="bodycodegen://activity/compound-precedence" kind="activity"
