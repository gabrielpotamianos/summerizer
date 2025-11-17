"""Background service orchestrating Mattermost fetching and transcript storage."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from queue import Queue
from typing import Dict, Iterable, List, Sequence

from .config import ServiceConfig
from .llm import LocalLLM, SummaryContext, collate_messages
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
    """Threaded service fetching unread messages and storing transcripts."""

    daemon = True

    def __init__(self, config: ServiceConfig, queue: Queue[ChannelSummary]) -> None:
        super().__init__(name="MattermostSummariser")
        self._config = config
        self._queue = queue
        self._client = MattermostClient(config.mattermost)
        self._storage = TranscriptStorage(config.mattermost.storage_dir)
        self._llm = LocalLLM(config.llm)
        self._process_unread_channels()
        self._running = threading.Event()
        self._running.set()

    def stop(self) -> None:
        self._running.clear()

    def run(self) -> None:  # noqa: D401 - thread entry point
        """Continuously poll Mattermost for unread messages and persist them."""

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
                last_viewed_at=unread.last_viewed_at,
                unread_count=unread.unread_count,
            )
            last_processed = self._storage.get_last_processed_timestamp(
                unread.channel_name
            )
            if last_processed is not None:
                posts = [
                    post
                    for post in posts
                    if int(post.get("create_at", 0)) > last_processed
                ]
            if not posts:
                LOGGER.debug("No new messages for %s", unread.display_name)
                continue
            self._storage.save_messages(unread.channel_name, posts)
            timestamps = self._extract_new_message_timestamps(
                posts, unread.last_viewed_at
            )
            if not timestamps:
                LOGGER.debug(
                    "Skipping %s because no unread timestamps were identified",
                    unread.display_name,
                )
                continue
            unread.unread_count = len(timestamps)
            sorted_posts = self._sort_posts(posts)
            summary = self._summarise_channel(unread, sorted_posts, timestamps)
            end_ts = max(timestamps)
            self._storage.update_last_processed_timestamp(unread.channel_name, end_ts)
            self._storage.save_summary(unread.channel_name, summary)
            LOGGER.info(
                "Stored %d new messages for %s",
                unread.unread_count,
                unread.display_name,
            )
            self._queue.put(ChannelSummary(unread, summary))

    @staticmethod
    def _format_timestamp(timestamp: int | None) -> str:
        if timestamp is None:
            return "Unknown"
        dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M %Z")

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

    @staticmethod
    def _extract_new_message_timestamps(
        posts: List[Dict], last_viewed_at: int
    ) -> List[int]:
        timestamps: List[int] = []
        for post in posts:
            created = post.get("create_at")
            if not isinstance(created, (int, float)):
                continue
            created_int = int(created)
            if created_int > last_viewed_at:
                timestamps.append(created_int)
        return timestamps

    def _summarise_channel(
        self,
        unread: ChannelUnread,
        posts: Sequence[Dict],
        timestamps: Sequence[int],
    ) -> str:
        start_ts = min(timestamps)
        end_ts = max(timestamps)
        conversation, _, _ = collate_messages(posts)
        if not conversation:
            return self._fallback_summary(unread, start_ts, end_ts)
        context = SummaryContext(
            group_name=unread.display_name or unread.channel_name,
            start_date=self._format_timestamp(start_ts),
            end_date=self._format_timestamp(end_ts),
        )
        try:
            summary = self._llm.summarise(conversation, context).strip()
        except Exception:
            LOGGER.exception("Failed to generate summary for %s", unread.display_name)
            summary = ""
        return summary or self._fallback_summary(unread, start_ts, end_ts)

    @staticmethod
    def _sort_posts(posts: Sequence[Dict]) -> List[Dict]:
        return sorted(
            posts,
            key=lambda item: (
                int(item.get("create_at", 0))
                if isinstance(item.get("create_at"), (int, float))
                else 0
            ),
        )

    def _fallback_summary(
        self, unread: ChannelUnread, start_ts: int, end_ts: int
    ) -> str:
        start = self._format_timestamp(start_ts)
        end = self._format_timestamp(end_ts)
        window = start if start_ts == end_ts else f"{start} – {end}"
        count = unread.unread_count
        plural = "s" if count != 1 else ""
        return (
            f"{count} new message{plural} captured ({window}).\n"
            "Unable to generate an AI summary at this time."
        )
