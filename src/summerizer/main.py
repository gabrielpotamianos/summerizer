from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import load_config
from .service import MattermostMonitor
from .ui import run_ui


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mattermost summarizer service and UI")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration YAML file (default: ./config.yml)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = load_config(args.config)
    monitor = MattermostMonitor(config)
    run_ui(monitor)


if __name__ == "__main__":
    main()
