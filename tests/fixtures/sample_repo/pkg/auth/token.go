package auth

type Token struct {
	Value string
}

func IssueToken(user string) Token {
	return Token{Value: "tok-" + user}
}

func ValidateToken(t Token) bool {
	return t.Value != ""
}
