package api

import "fmt"

// HandleLogin is an entrypoint for auth-related HTTP handling.
func HandleLogin(user string) error {
	if user == "" {
		return fmt.Errorf("empty user")
	}
	return authenticate(user)
}

func authenticate(user string) error {
	return nil
}

type Session struct {
	User string
}

func NewSession(user string) *Session {
	return &Session{User: user}
}
