"""Command-line workflow for running the model-bank smoke check."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from conformance.model_bank_config import ConfigError, load_model_bank_config
from conformance.runner import run_model_bank_smoke_check

logger = logging.getLogger(__name__)


def run(argv: Sequence[str] | None = None) -> int:
    """Run the model-bank smoke-check command.

    Args:
        argv: Optional argument list to parse instead of `sys.argv`.

    Returns:
        Process-style exit code: 0 for pass, 1 for conformance failure, 2 for
        invalid input, and 3 when the structured result cannot be written.
    """
    parser = argparse.ArgumentParser(description="Run a model-bank smoke check")
    parser.add_argument("config", type=Path, help="Path to the model-bank JSON config")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2

    try:
        config = load_model_bank_config(args.config)
    except ConfigError as error:
        logger.error("Config error: %s", error)
        return 2

    result = run_model_bank_smoke_check(config)
    try:
        config.result_output_path.parent.mkdir(parents=True, exist_ok=True)
        config.result_output_path.write_text(
            json.dumps(result.to_json_object(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        logger.error("Unable to write model-bank smoke check result to %s: %s", config.result_output_path, error)
        return 3

    if result.status == "passed":
        logger.info("Model-bank smoke check passed; wrote %s", config.result_output_path)
        return 0

    logger.error("Model-bank smoke check failed; wrote %s", config.result_output_path)
    return 1
