"""Verify that a daemon-loaded image preserves its archive identity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class LoadedImageError(ValueError):
    """Raised when a daemon-loaded image differs from its verified archive."""


def _object(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LoadedImageError(f"Expected {description} to be a JSON object.")
    return value


def _docker_default(value: object) -> bool:
    return value is None or value is False or value == "" or value == [] or value == {}


def verify_loaded_image(evidence: dict[str, object], inspected: dict[str, object]) -> None:
    """Compare the daemon image with archive-verified platform, config and layers.

    Docker may materialize unset OCI config fields as empty defaults in inspect
    output. Those defaults carry no image configuration; every field present in
    the archive config must still match, and non-default daemon-only fields fail.
    """
    expected_platform = evidence.get("platform")
    actual_os = inspected.get("Os")
    actual_architecture = inspected.get("Architecture")
    actual_platform = f"{actual_os}/{actual_architecture}"
    if not isinstance(expected_platform, str) or actual_platform != expected_platform:
        raise LoadedImageError(
            f"Loaded image platform differs from the verified archive "
            f"(expected {expected_platform!r}, got {actual_platform!r})."
        )

    expected_config = _object(evidence.get("image_config"), "verified image config")
    actual_config = _object(inspected.get("Config"), "daemon image config")
    mismatched = [
        key for key, value in expected_config.items() if key not in actual_config or actual_config[key] != value
    ]
    unexpected = [
        key for key, value in actual_config.items() if key not in expected_config and not _docker_default(value)
    ]
    if mismatched or unexpected:
        fields = sorted(set(mismatched + unexpected))
        raise LoadedImageError(f"Loaded image runtime Config differs from the verified archive (fields: {fields!r}).")

    expected_layers = evidence.get("rootfs_layers")
    rootfs = _object(inspected.get("RootFS"), "daemon image RootFS")
    actual_layers = rootfs.get("Layers")
    if not isinstance(expected_layers, list) or not all(isinstance(item, str) for item in expected_layers):
        raise LoadedImageError("Verified archive RootFS layers are invalid.")
    if expected_layers and actual_layers != expected_layers:
        actual_count = len(actual_layers) if isinstance(actual_layers, list) else "invalid"
        raise LoadedImageError(
            f"Loaded image RootFS layers differ from the verified archive "
            f"(expected {len(expected_layers)} layers, got {actual_count})."
        )


def main(argv: list[str] | None = None) -> int:
    """Compare a saved Docker inspect result with verified archive evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--inspect", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        evidence = _object(json.loads(args.evidence.read_text()), "archive evidence")
        inspected = _object(json.loads(args.inspect.read_text()), "Docker inspect result")
        verify_loaded_image(evidence, inspected)
    except (LoadedImageError, OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
