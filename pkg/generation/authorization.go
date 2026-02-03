package generation

import (
	"github.com/OpenBankingUK/conformance-suite/pkg/model"
	"github.com/OpenBankingUK/conformance-suite/pkg/permissions"
	"github.com/sirupsen/logrus"
)

func setHeader(consentRequirements []model.SpecConsentRequirements, tc model.TestCase) model.TestCase {
	logrus.WithFields(logrus.Fields{
		"testcase_id":   tc.ID,
		"testcase_name": tc.Name,
		"endpoint":      tc.Input.Endpoint,
	}).Debug("Setting header for testcase")

	if isAccountAccessConsentEndpoint(tc.Input.Endpoint) {
		// do nothing it's a special case
		logrus.Debug("Skipping header set for account access consent endpoint")
		return tc
	}

	if tc.Input.Headers == nil {
		logrus.Debug("Headers map is nil, initializing new map")
		tc.Input.Headers = map[string]string{}
	}

	nameSet, ok := authorizationNamedSet(consentRequirements, tc.ID)
	if ok {
		logrus.WithFields(logrus.Fields{
			"named_set": nameSet,
		}).Debug("Found authorization named set, setting Authorization header")
		tc.Input.Headers["Authorization"] = "Bearer $" + nameSet
	} else {
		logrus.Debug("No authorization named set found for testcase")
	}

	return tc
}

// authorizationNamedSet find named set in consent requirements for a testId
func authorizationNamedSet(consentRequirements []model.SpecConsentRequirements, testID string) (string, bool) {
	for _, consentRequirement := range consentRequirements {
		for _, namedPermissions := range consentRequirement.NamedPermissions {
			for _, namedTestID := range namedPermissions.CodeSet.TestIds {
				if permissions.TestId(testID) == namedTestID {
					return namedPermissions.Name, true
				}
			}
		}
	}
	return "", false
}

func isAccountAccessConsentEndpoint(path string) bool {
	return path == "/account-access-consents"
}
