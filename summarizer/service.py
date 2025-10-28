"""Background service orchestrating Mattermost fetching and summarisation."""

from __future__ import annotations

import logging
import threading
import time
from queue import Queue
from typing import Dict, Iterable

from .config import ServiceConfig
from .llm import LocalLLM, collate_messages
from .mattermost import ChannelUnread, MattermostClient
from .storage import TranscriptStorage

LOGGER = logging.getLogger(__name__)


class ChannelSummary:
    """Container for summarised channel data."""

    def __init__(self, unread: ChannelUnread, summary: str) -> None:
        self.unread = unread
        self.summary = summary

    def to_dict(self) -> Dict[str, object]:
        return {
            "team_id": self.unread.team_id,
            "channel_id": self.unread.channel_id,
            "channel_name": self.unread.channel_name,
            "display_name": self.unread.display_name,
            "unread_count": self.unread.unread_count,
            "summary": self.summary,
        }


class SummariserService(threading.Thread):
    """Threaded service fetching unread messages and producing summaries."""

    daemon = True

    def __init__(self, config: ServiceConfig, queue: Queue[ChannelSummary]) -> None:
        super().__init__(name="MattermostSummariser")
        self._config = config
        self._queue = queue
        self._client = MattermostClient(config.mattermost)
        self._storage = TranscriptStorage(config.mattermost.storage_dir)
        self._llm = LocalLLM(config.llm)
        self._running = threading.Event()
        self._running.set()

    def stop(self) -> None:
        self._running.clear()

    def run(self) -> None:  # noqa: D401 - thread entry point
        """Continuously poll Mattermost for unread messages and summarise them."""

        interval = self._config.mattermost.polling_interval
        LOGGER.info("Starting Mattermost summariser loop with interval %.1fs", interval)
        while self._running.is_set():
            try:
                self._process_unread_channels()
            except Exception:  # pragma: no cover - defensive logging
                LOGGER.exception("Unexpected error while processing unread channels")
            finally:
                time.sleep(interval)

    def _process_unread_channels(self) -> None:
        for unread in self._client.list_unread_channels():
            posts = self._client.get_unread_posts(
                unread.channel_id,
                since=unread.last_viewed_at,
            )
            formatted_messages = collate_messages(posts)
            if not formatted_messages:
                LOGGER.debug("No new messages for %s", unread.display_name)
                continue
            self._storage.save_messages(unread.channel_name, posts)
            summary = self._llm.summarise(formatted_messages)
            self._storage.save_summary(unread.channel_name, summary)
            LOGGER.info("Updated summary for %s", unread.display_name)
            self._queue.put(ChannelSummary(unread, summary))

    def load_existing_summaries(self) -> Iterable[ChannelSummary]:
        for channel in self._storage.list_channels():
            summary = self._storage.load_summary(channel)
            dummy_unread = ChannelUnread(
                team_id="",
                channel_id=channel,
                channel_name=channel,
                display_name=channel,
                unread_count=0,
                last_viewed_at=0,
            )
            yield ChannelSummary(dummy_unread, summary)
