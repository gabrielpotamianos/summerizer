"""Entry point for the Mattermost summariser desktop application."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from queue import Queue
from typing import Any, Dict

from PyQt6 import QtWidgets

from summarizer.config import LLMConfig, MattermostConfig, ServiceConfig
from summarizer.service import ChannelSummary, SummariserService
from summarizer.ui import SummaryWindow

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mattermost unread message summariser")
    parser.add_argument("config", type=Path, help="Path to JSON configuration file")
    return parser.parse_args()


def load_config(path: Path) -> ServiceConfig:
    payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    mattermost_cfg = MattermostConfig(**payload["mattermost"])
    llm_cfg = LLMConfig(**payload["llm"])
    refresh_ui_interval = payload.get("refresh_ui_interval", 5.0)
    return ServiceConfig(mattermost=mattermost_cfg, llm=llm_cfg, refresh_ui_interval=refresh_ui_interval)


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    queue: Queue[ChannelSummary] = Queue()

    service = SummariserService(config, queue)
    service.start()

    app = QtWidgets.QApplication([])
    window = SummaryWindow(queue, refresh_interval=config.refresh_ui_interval)

    for summary in service.load_existing_summaries():
        queue.put(summary)

    window.show()
    app.exec()
    service.stop()
    service.join(timeout=5)


if __name__ == "__main__":
    main()
