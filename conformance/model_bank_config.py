from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from conformance.json_types import JsonObject, JsonValue


class ConfigError(ValueError):
    """Raised when a model-bank config file cannot be loaded or validated."""


FollowUpMode = Literal["jwks", "discovery_only"]


@dataclass(frozen=True)
class TlsConfig:
    ca_bundle_path: Path | None = None
    client_certificate_path: Path | None = None
    client_private_key_path: Path | None = None


@dataclass(frozen=True)
class ModelBankConfig:
    environment: str
    discovery_url: str
    timeout_seconds: float = 10.0
    follow_up_mode: FollowUpMode = "jwks"
    tls: TlsConfig = TlsConfig()
    result_output_path: Path = Path("test-results.json")


def load_model_bank_config(config_path: Path) -> ModelBankConfig:
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
        default=Path("test-results.json"),
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
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value.strip()


def _required_https_url(raw_config: dict[str, JsonValue], key: str) -> str:
    value = _required_string(raw_config, key)
    parsed_url = urlparse(value)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ConfigError(f"{key} must be an HTTPS URL")
    return value


def _optional_positive_number(raw_config: dict[str, JsonValue], key: str, *, default: float) -> float:
    value = raw_config.get(key)
    if value is None:
        return default
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive number")
    return float(value)


def _optional_path(raw_config: dict[str, JsonValue], key: str, *, base_dir: Path, default: Path) -> Path:
    value = raw_config.get(key)
    if value is None:
        return (base_dir / default).resolve() if not default.is_absolute() else default.resolve()
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string when supplied")
    path = Path(value.strip())
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _optional_existing_child_path(raw_config: dict[str, JsonValue], key: str, *, root: Path) -> Path | None:
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
    unknown_keys = sorted(set(raw_config) - allowed_keys)
    if unknown_keys:
        joined_keys = ", ".join(unknown_keys)
        raise ConfigError(f"Unknown {location} field(s): {joined_keys}")


def to_json_object(config: ModelBankConfig) -> JsonObject:
    tls: JsonObject = {}
    if config.tls.ca_bundle_path is not None:
        tls["caBundlePath"] = str(config.tls.ca_bundle_path)
    if config.tls.client_certificate_path is not None:
        tls["clientCertificatePath"] = str(config.tls.client_certificate_path)
    if config.tls.client_private_key_path is not None:
        tls["clientPrivateKeyPath"] = str(config.tls.client_private_key_path)

    return {
        "environment": config.environment,
        "discoveryUrl": config.discovery_url,
        "timeoutSeconds": config.timeout_seconds,
        "followUp": {"mode": config.follow_up_mode},
        "tls": tls,
        "resultOutputPath": str(config.result_output_path),
    }
