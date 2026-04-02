package schema

import (
	"github.com/dlclark/regexp2"
	"github.com/getkin/kin-openapi/openapi3"
)

// regexp2Compiler is an openapi3.RegexCompilerFunc that uses the regexp2 engine,
// which supports PCRE features such as lookahead and lookbehind. Because RE2 is a
// strict subset of PCRE, all existing RE2-compatible patterns continue to compile
// and match identically.
func regexp2Compiler(expr string) (openapi3.RegexMatcher, error) {
	re, err := regexp2.Compile(expr, 0)
	if err != nil {
		return nil, err
	}
	return &regexp2Matcher{re: re}, nil
}

// regexp2Matcher wraps a *regexp2.Regexp to satisfy openapi3.RegexMatcher.
type regexp2Matcher struct {
	re *regexp2.Regexp
}

func (m *regexp2Matcher) MatchString(s string) bool {
	matched, err := m.re.MatchString(s)
	if err != nil {
		return false
	}
	return matched
}
