"""OB DCR/FAPI callback policy is separate from outbound HTTPS validation."""

import pytest

from conformance.url_validation import HttpsUrlValidationError, validate_https_url, validate_oauth_redirect_uri

pytestmark = pytest.mark.unit

_OVERLONG_HOSTNAME = ".".join(["a" * 63] * 4) + ".example.com"


@pytest.mark.parametrize(
    "uri",
    [
        "https://tpp.example.com/callback",
        "https://tpp.example.com:9443/callback?registration=one",
        "https://tpp.example.com/callback/*",
        "https://tpp.example.com/callback?value=*",
        "https://127.0.0.1.example.com/callback",
        "https://127.0.0.1/conformancesuite/callback",
        "https://127.0.0.1:8443/conformancesuite/callback",
        "https://127.0.0.1:9443/callback/",
        "https://127.0.0.1:65535/callback?registration=one",
        "https://127.0.0.2:8443/callback",
        "https://[::1]/conformancesuite/callback",
        "https://[::1]:8443/conformancesuite/callback",
        "https://192.168.1.10:8443/callback",
        "https://10.0.0.5/callback",
        "https://[fd00::1]:8443/callback",
        "https://[2001:db8::1]:8443/callback",
    ],
)
def test_supported_redirects(uri: str) -> None:
    validate_oauth_redirect_uri(uri, label="redirectUri")


@pytest.mark.parametrize(
    "uri",
    [
        "https://localhost:8443/callback",
        "https://LOCALHOST:8443/callback",
        "https://localhost.:8443/callback",
        "https://callback.localhost:8443/callback",
        "https://[::ffff:127.0.0.1]:8443/callback",
        "https://[::]:8443/callback",
        "https://[fe80::1]:8443/callback",
        "https://[fe80::1%25eth0]:8443/callback",
        "https://[ff02::1]:8443/callback",
        "https://[127.0.0.1]:8443/callback",
        "https://[v1.example.com]:8443/callback",
        "https://::1/callback",
        "https://0.1.2.3/callback",
        "https://224.0.0.1/callback",
        "https://240.0.0.1/callback",
        "https://255.255.255.255/callback",
        "https://127.1:8443/callback",
        "https://2130706433:8443/callback",
        "https://0x7f000001:8443/callback",
        "https://0177.0.0.1:8443/callback",
        "https://127.000.000.001/callback",
        "https://127.0.0.1.:8443/callback",
        "https://%31%32%37.0.0.1:8443/callback",
        "https://0:8443/conformancesuite/callback",
        "https://0.0.0.0/callback",
        "https://0.0.0.0:9443/conformancesuite/callback",
        "https://0.0.0.0:8443/different-callback",
        "https://0.0.0.0:8443/conformancesuite/callback?registration=one",
        "https://0.0.0.0:8443/conformancesuite/callback?",
        "https://0.0.0.0:8443/conformancesuite/callback#",
        "https://0.0.0.0:8443/conformancesuite/callback;parameters",
        "https://0.0.0.0:8443/conformancesuite/callback;",
        "http://127.0.0.1:8443/callback",
        "http://tpp.example.com/callback",
        "https://user@127.0.0.1:8443/callback",
        "https://user:password@tpp.example.com/callback",  # pragma: allowlist secret
        "https://tpp.example.com/callback#fragment",
        "https://127.0.0.1:8443/callback#",
        "https://*.example.com/callback",
        "https://t\u00e4pp.example.com/callback",
        "https://./callback",
        f"https://{_OVERLONG_HOSTNAME}/callback",
        "https://tpp.example.com\\callback",
        "https://127.0.0.1:0/callback",
        "https://127.0.0.1:65536/callback",
        "https://127.0.0.1:invalid/callback",
        "https://[::1/callback",
        "https://tpp.example.com/\ncallback",
        "https://tpp.example.com/\x00callback",
    ],
)
def test_unsupported_redirects(uri: str) -> None:
    with pytest.raises(HttpsUrlValidationError, match="redirectUri"):
        validate_oauth_redirect_uri(uri, label="redirectUri")


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("https://localhost:8443/callback", "must not use localhost \\(Open Banking DCR 3.4\\)"),
        ("https://tpp.example.com/callback#fragment", "must not include a fragment"),
        ("https://0.0.0.0:9443/conformancesuite/callback", "only as the FCS v1 callback"),
        ("https://[::]:8443/callback", "must use a unicast IP address or DNS hostname"),
        ("https://127.1:8443/callback", "dotted-decimal IPv4 or a bracketed IPv6 literal"),
    ],
)
def test_unsupported_redirects_explain_the_reason(uri: str, message: str) -> None:
    with pytest.raises(HttpsUrlValidationError, match=message):
        validate_oauth_redirect_uri(uri, label="redirectUri")


@pytest.mark.parametrize("suffix", ["", "/"])
def test_fcs_v1_callback_remains_supported(suffix: str) -> None:
    validate_oauth_redirect_uri(f"https://0.0.0.0:8443/conformancesuite/callback{suffix}", label="redirectUri")


@pytest.mark.parametrize(
    "uri",
    [
        "https://127.0.0.1:8443/conformancesuite/callback",
        "https://[::1]:8443/conformancesuite/callback",
        "https://192.168.1.10:8443/callback",
        "https://0.0.0.0:8443/conformancesuite/callback",
    ],
)
def test_callback_policy_does_not_relax_outbound_targets(uri: str) -> None:
    with pytest.raises(HttpsUrlValidationError, match="not an IP literal"):
        validate_https_url(uri, label="discoveryUrl")


def test_outbound_localhost_behavior_is_unchanged() -> None:
    validate_https_url("https://localhost:8443/health/", label="url")
