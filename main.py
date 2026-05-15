import logging
import sys

from conformance.cli import run

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Hello from conformance-suite-v2!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) == 1:
        main()
    else:
        raise SystemExit(run())
