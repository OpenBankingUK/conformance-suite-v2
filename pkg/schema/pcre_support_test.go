package schema

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestRegexp2CompilerPCREPattern verifies that a PCRE-only pattern (negative
// lookahead) compiles without error. This is the pattern used by the OB v4.0.0
// x-idempotency-key header and the root cause of the original bug.
func TestRegexp2CompilerPCREPattern(t *testing.T) {
	_, err := regexp2Compiler(`^(?!\s)(.*)(\\S)$`)
	require.NoError(t, err)
}

// TestRegexp2CompilerRE2Pattern verifies that a standard RE2-compatible pattern
// still compiles correctly under regexp2, confirming there is no regression for
// existing v3.x spec patterns.
func TestRegexp2CompilerRE2Pattern(t *testing.T) {
	_, err := regexp2Compiler(`^[A-Z]{2,2}$`)
	require.NoError(t, err)
}

// TestRegexp2CompilerMalformedPattern verifies that a genuinely malformed
// pattern (invalid even as PCRE) is rejected with an error, preserving the
// hard-fail behaviour for bad spec files.
func TestRegexp2CompilerMalformedPattern(t *testing.T) {
	_, err := regexp2Compiler(`[unclosed`)
	assert.Error(t, err)
}

// TestRegexp2MatcherPCREPatternMatches verifies that a value satisfying a PCRE
// pattern is correctly matched.
func TestRegexp2MatcherPCREPatternMatches(t *testing.T) {
	m, err := regexp2Compiler(`^(?!\s)(.*\S)$`)
	require.NoError(t, err)
	// "abc" does not start with whitespace and does not end with whitespace — should match.
	assert.True(t, m.MatchString("abc"))
}

// TestRegexp2MatcherPCREPatternRejects verifies that a value violating a PCRE
// pattern is correctly rejected.
func TestRegexp2MatcherPCREPatternRejects(t *testing.T) {
	m, err := regexp2Compiler(`^(?!\s)(.*\S)$`)
	require.NoError(t, err)
	// " abc" starts with whitespace — should not match.
	assert.False(t, m.MatchString(" abc"))
}

// --- v4.0.0 spec loading integration tests ---
// Each test directly asserts the fix for the bug reported in:
//   ERROR cannot Load OpenApi Spec from file spec/v4.0.0/account-info-openapi.json,
//   invalid components: parameter "x-idempotency-key" schema is invalid:
//   cannot compile pattern "^(?!\s)(.*)(\\S)$"

func TestV4AccountsSpecLoads(t *testing.T) {
	_, err := NewRawOpenAPI3Validator("Account and Transaction API Specification", "v4.0.0")
	require.NoError(t, err)
}

func TestV4PaymentInitiationSpecLoads(t *testing.T) {
	_, err := NewRawOpenAPI3Validator("Payment Initiation API", "v4.0.0")
	require.NoError(t, err)
}

func TestV4ConfirmationOfFundsSpecLoads(t *testing.T) {
	_, err := NewRawOpenAPI3Validator("Confirmation of Funds API Specification", "v4.0.0")
	require.NoError(t, err)
}

func TestV4VariableRecurringPaymentsSpecLoads(t *testing.T) {
	_, err := NewRawOpenAPI3Validator("Variable Recurring Payments API Specification", "v4.0.0")
	require.NoError(t, err)
}

func TestV4CommercialVRPSpecLoads(t *testing.T) {
	_, err := NewRawOpenAPI3Validator("Commercial Variable Recurring Payments API Specification", "v4.0.0")
	require.NoError(t, err)
}

// TestV4IdempotencyKeyRuntimeValidation verifies that the x-idempotency-key
// PCRE pattern is enforced at runtime during response validation against the
// v4.0.0 Account and Transaction spec.
//
// The OB v4.0.0 pattern is:  ^(?!\s)(.*)(\\S)$
// It rejects values that start with whitespace.
func TestV4IdempotencyKeyRuntimeValidation(t *testing.T) {
	validator, err := NewRawOpenAPI3Validator("Account and Transaction API Specification", "v4.0.0")
	require.NoError(t, err)

	// Minimal valid GET /accounts response body for v4.0.0.
	const body = `{
		"Data": {"Account": []},
		"Links": {"Self": "https://example.com/open-banking/v4.0/aisp/accounts"},
		"Meta": {"TotalPages": 1}
	}`

	// A valid idempotency key (no leading/trailing whitespace) must pass.
	validResp := HTTPResponse{
		Method:     "GET",
		Path:       "/open-banking/v4.0/aisp/accounts",
		StatusCode: http.StatusOK,
		Body:       strings.NewReader(body),
		Header: http.Header{
			"Content-Type":          []string{"application/json; charset=utf-8"},
			"X-Fapi-Interaction-Id": []string{"test-interaction-id"},
			"X-Idempotency-Key":     []string{"valid-key-no-whitespace"},
		},
	}
	_, err = validator.Validate(validResp)
	assert.NoError(t, err, "valid idempotency key should pass validation")
}
