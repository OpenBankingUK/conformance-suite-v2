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
    parser = argparse.ArgumentParser(description="Run a model-bank smoke check")
    parser.add_argument("config", type=Path, help="Path to the model-bank JSON config")
    args = parser.parse_args(argv)

    try:
        config = load_model_bank_config(args.config)
    except ConfigError as error:
        logger.error("Config error: %s", error)
        return 2

    result = run_model_bank_smoke_check(config)
    config.result_output_path.parent.mkdir(parents=True, exist_ok=True)
    config.result_output_path.write_text(
        json.dumps(result.to_json_object(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if result.status == "passed":
        logger.info("Model-bank smoke check passed; wrote %s", config.result_output_path)
        return 0

    logger.error("Model-bank smoke check failed; wrote %s", config.result_output_path)
    return 1
