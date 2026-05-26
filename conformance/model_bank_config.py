"""Load and validate model-bank smoke-check configuration files."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from conformance.json_types import JsonValue
from conformance.url_validation import HttpsUrlValidationError, validate_https_url


class ConfigError(ValueError):
    """Raised when a model-bank config file cannot be loaded or validated."""


FollowUpMode = Literal["jwks", "discovery_only"]


@dataclass(frozen=True)
class TlsConfig:
    """Transport TLS file paths for outbound model-bank requests.

    Attributes:
        ca_bundle_path: Optional CA bundle used to verify the model bank.
        client_certificate_path: Optional client certificate for mTLS.
        client_private_key_path: Optional private key paired with the client certificate.
    """

    ca_bundle_path: Path | None = None
    client_certificate_path: Path | None = None
    client_private_key_path: Path | None = None


@dataclass(frozen=True)
class ModelBankConfig:
    """Validated inputs needed to run the current model-bank smoke check.

    Attributes:
        environment: Human-readable environment name written to the result file.
        discovery_url: HTTPS OpenID Provider discovery document URL.
        timeout_seconds: Per-request timeout for model-bank HTTP calls.
        follow_up_mode: Whether to fetch JWKS after discovery succeeds.
        tls: Transport TLS settings for the HTTP client.
        result_output_path: Path where the structured JSON result should be written.
    """

    environment: str
    discovery_url: str
    timeout_seconds: float = 10.0
    follow_up_mode: FollowUpMode = "jwks"
    tls: TlsConfig = field(default_factory=TlsConfig)
    result_output_path: Path = Path("out/test-results.json")


def load_model_bank_config(config_path: Path) -> ModelBankConfig:
    """Load a model-bank JSON config file from disk.

    Args:
        config_path: Path to the JSON config file.

    Returns:
        Parsed and validated model-bank config.

    Raises:
        ConfigError: If the file cannot be read, parsed, or validated.
    """
    resolved_config_path = config_path.resolve()
    try:
        raw_config = json.loads(resolved_config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid JSON config: {error.msg}") from error
    except OSError as error:
        raise ConfigError(f"Unable to read config file: {error}") from error

    if not isinstance(raw_config, dict):
        raise ConfigError("Config root must be a JSON object")

    return parse_model_bank_config(
        raw_config,
        base_dir=resolved_config_path.parent,
        output_base_dir=Path.cwd(),
    )


def parse_model_bank_config(
    raw_config: dict[str, JsonValue],
    *,
    base_dir: Path,
    output_base_dir: Path | None = None,
) -> ModelBankConfig:
    """Validate raw JSON config data into a typed model-bank config.

    Args:
        raw_config: JSON object loaded from a model-bank config file.
        base_dir: Directory used to resolve certificate paths.
        output_base_dir: Directory used to resolve result output paths.

    Returns:
        Typed model-bank config ready for execution.

    Raises:
        ConfigError: If required fields are missing, unknown fields are present,
            paths are unsafe, or values have invalid types.
    """
    _reject_unknown_keys(
        raw_config,
        allowed_keys={
            "environment",
            "discoveryUrl",
            "timeoutSeconds",
            "followUp",
            "tls",
            "resultOutputPath",
        },
        location="config",
    )

    environment = _required_string(raw_config, "environment")
    discovery_url = _required_https_url(raw_config, "discoveryUrl")
    timeout_seconds = _optional_positive_number(raw_config, "timeoutSeconds", default=10.0)
    follow_up_mode = _parse_follow_up(raw_config)
    tls = _parse_tls_config(raw_config, base_dir=base_dir)
    result_output_path = _optional_path(
        raw_config,
        "resultOutputPath",
        base_dir=output_base_dir or Path.cwd(),
        default=Path("out/test-results.json"),
    )

    return ModelBankConfig(
        environment=environment,
        discovery_url=discovery_url,
        timeout_seconds=timeout_seconds,
        follow_up_mode=follow_up_mode,
        tls=tls,
        result_output_path=result_output_path,
    )


def _parse_follow_up(raw_config: dict[str, JsonValue]) -> FollowUpMode:
    """Parse the optional ``followUp`` section of a model bank config dict.

    If the key is absent, the default follow-up mode ``"jwks"`` is returned.
    The only currently supported modes are ``"jwks"`` and
    ``"discovery_only"``.

    Args:
        raw_config: Top-level raw configuration dictionary from the JSON
            config file.

    Returns:
        The resolved ``FollowUpMode`` value.

    Raises:
        ConfigError: If ``followUp`` is present but not a JSON object, contains
            unknown keys, or specifies an unrecognised mode.
    """
    raw_follow_up = raw_config.get("followUp")
    if raw_follow_up is None:
        return "jwks"
    if not isinstance(raw_follow_up, dict):
        raise ConfigError("followUp must be a JSON object")
    _reject_unknown_keys(raw_follow_up, allowed_keys={"mode"}, location="followUp")
    mode = _required_string(raw_follow_up, "mode")
    if mode == "jwks":
        return "jwks"
    if mode == "discovery_only":
        return "discovery_only"
    raise ConfigError("followUp.mode must be one of: jwks, discovery_only")


def _parse_tls_config(raw_config: dict[str, JsonValue], *, base_dir: Path) -> TlsConfig:
    """Parse the optional ``tls`` section of a model bank config dict.

    If the key is absent a zero-value ``TlsConfig`` (no custom TLS) is
    returned.  Relative certificate paths are resolved against ``base_dir``.
    ``clientCertificatePath`` and ``clientPrivateKeyPath`` must be supplied
    together or not at all.

    Args:
        raw_config: Top-level raw configuration dictionary.
        base_dir: Directory of the config file, used as the root for resolving
            relative certificate paths.

    Returns:
        A populated ``TlsConfig`` dataclass.

    Raises:
        ConfigError: If ``tls`` is not a JSON object, contains unknown keys,
            specifies paths that escape ``certificatePathRoot``, specifies
            paths that do not exist, or supplies only one of the client
            certificate / private key pair.
    """
    raw_tls = raw_config.get("tls")
    if raw_tls is None:
        return TlsConfig()
    if not isinstance(raw_tls, dict):
        raise ConfigError("tls must be a JSON object")

    _reject_unknown_keys(
        raw_tls,
        allowed_keys={"certificatePathRoot", "caBundlePath", "clientCertificatePath", "clientPrivateKeyPath"},
        location="tls",
    )

    certificate_root = _optional_path(raw_tls, "certificatePathRoot", base_dir=base_dir, default=base_dir)
    ca_bundle_path = _optional_existing_child_path(raw_tls, "caBundlePath", root=certificate_root)
    client_certificate_path = _optional_existing_child_path(raw_tls, "clientCertificatePath", root=certificate_root)
    client_private_key_path = _optional_existing_child_path(raw_tls, "clientPrivateKeyPath", root=certificate_root)

    if (client_certificate_path is None) != (client_private_key_path is None):
        raise ConfigError("clientCertificatePath and clientPrivateKeyPath must be supplied together")

    return TlsConfig(
        ca_bundle_path=ca_bundle_path,
        client_certificate_path=client_certificate_path,
        client_private_key_path=client_private_key_path,
    )


def _required_string(raw_config: dict[str, JsonValue], key: str) -> str:
    """Extract a required non-empty string value from a raw config dict.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value must be a non-empty string.

    Returns:
        The stripped string value.

    Raises:
        ConfigError: If the key is missing, the value is not a string, or the
            string is blank after stripping whitespace.
    """
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value.strip()


def _required_https_url(raw_config: dict[str, JsonValue], key: str) -> str:
    """Extract and validate a required HTTPS URL from raw config.

    Args:
        raw_config: Raw configuration dictionary.
        key: Configuration key to extract.

    Returns:
        Validated HTTPS URL string.

    Raises:
        ConfigError: If the value is missing, empty, or not a valid HTTPS URL.
    """
    value = _required_string(raw_config, key)
    try:
        validate_https_url(value, label=key)
    except HttpsUrlValidationError as error:
        raise ConfigError(str(error)) from error
    return value


def _optional_positive_number(raw_config: dict[str, JsonValue], key: str, *, default: float) -> float:
    """Extract an optional positive finite number from a raw config dict.

    If the key is absent, ``default`` is returned unchanged.  ``bool``
    values are explicitly rejected even though they are a subtype of ``int``
    in Python.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value must be a positive finite number.
        default: Value to return when the key is absent.

    Returns:
        The extracted number as a ``float``, or ``default``.

    Raises:
        ConfigError: If the value is present but is not a positive finite
            number (including bool, negative, zero, or non-finite).
    """
    value = raw_config.get(key)
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{key} must be a positive number")
    return float(value)


def _optional_path(raw_config: dict[str, JsonValue], key: str, *, base_dir: Path, default: Path) -> Path:
    """Extract an optional filesystem path, resolving relative paths against ``base_dir``.

    If the key is absent, ``default`` is resolved against ``base_dir`` (or
    returned as-is if already absolute).  If present, the value is resolved
    against ``base_dir`` when relative.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.
        base_dir: Directory used as the base for resolving relative paths.
        default: Path to return when the key is absent.

    Returns:
        Absolute resolved ``Path``.

    Raises:
        ConfigError: If the key is present but the value is not a non-empty
            string.
    """
    value = raw_config.get(key)
    if value is None:
        return (base_dir / default).resolve() if not default.is_absolute() else default.resolve()
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")
    path = Path(value.strip())
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _optional_existing_child_path(raw_config: dict[str, JsonValue], key: str, *, root: Path) -> Path | None:
    """Extract an optional path that must resolve inside ``root`` and point to an existing file.

    Enforces a path-traversal guard: the resolved path must be ``root`` itself
    or a descendant of ``root``.  Both absolute and relative path strings are
    accepted; relative paths are resolved against ``root``.

    Args:
        raw_config: Raw configuration dictionary to read from.
        key: Dictionary key whose value is a path string.
        root: Directory that the resolved path must reside inside (the
            ``certificatePathRoot``).

    Returns:
        Absolute resolved ``Path`` when the key is present, or ``None``.

    Raises:
        ConfigError: If the key is present but the value is not a non-empty
            string, the resolved path escapes ``root``, or the path does not
            point to an existing file.
    """
    value = raw_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")

    raw_path = Path(value.strip())
    resolved_path = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    resolved_root = root.resolve()

    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ConfigError(f"{key} must resolve inside certificatePathRoot")
    if not resolved_path.is_file():
        raise ConfigError(f"{key} must point to an existing file")
    return resolved_path


def _reject_unknown_keys(raw_config: dict[str, JsonValue], *, allowed_keys: set[str], location: str) -> None:
    """Raise ``ConfigError`` if ``raw_config`` contains any key not in ``allowed_keys``.

    This prevents silently ignoring typo'd or unsupported configuration fields
    that would otherwise be swallowed without effect.

    Args:
        raw_config: Raw configuration dictionary to validate.
        allowed_keys: Set of recognised field names.
        location: Human-readable config path used in the error message
            (e.g. ``"tls"`` or ``"followUp"``).

    Raises:
        ConfigError: If one or more unrecognised keys are present, listing
            them in sorted order.
    """
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ConfigError(f"Unknown {location} field(s): {joined_keys}")
