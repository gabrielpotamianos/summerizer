"""Entry point for the Mattermost summariser desktop application."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from queue import Queue

from PyQt6 import QtWidgets

from summarizer.config import ServiceConfig
from summarizer.llm import LocalLLM
from summarizer.mattermost import MattermostClient
from summarizer.service import ChannelSummary, SummariserService
from summarizer.storage import TranscriptStorage
from summarizer.ui import LoginDialog, SummaryWindow

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mattermost unread message summariser")
    parser.add_argument("config", type=Path, help="Path to JSON configuration file")
    return parser.parse_args()


def load_config(path: Path) -> ServiceConfig:
    return ServiceConfig.from_json(path)


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    app = QtWidgets.QApplication([])
    login_dialog = LoginDialog(config.mattermost.base_url)
    if login_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        LOGGER.info("Login cancelled; exiting application")
        return
    token = login_dialog.token
    if not token:
        LOGGER.error("Login dialog closed without providing a token")
        return
    config.mattermost.token = token

    queue: Queue[ChannelSummary] = Queue()
    mattermost_client = MattermostClient(config.mattermost)
    storage = TranscriptStorage(config.mattermost.storage_dir)
    llm = LocalLLM(config.llm)
    service = SummariserService(
        config,
        queue,
        mattermost_client=mattermost_client,
        storage=storage,
        llm=llm,
    )
    service.start()

    for summary in service.load_existing_summaries():
        queue.put(summary)

    window = SummaryWindow(queue, refresh_interval=config.refresh_ui_interval)
    window.show()

    def _shutdown() -> None:
        LOGGER.info("Stopping Mattermost polling service")
        service.stop()
        service.join(timeout=15)
        service.close()

    app.aboutToQuit.connect(_shutdown)  # type: ignore[attr-defined]
    app.exec()


if __name__ == "__main__":
    main()
