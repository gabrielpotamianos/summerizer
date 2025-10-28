from __future__ import annotations

import logging
import threading
from queue import Queue
from typing import Callable, Iterable, Optional

from .config import AppConfig
from .mattermost_client import Channel, MattermostClient
from .storage import ChannelSnapshot, Storage
from .summarizer import Summarizer

logger = logging.getLogger(__name__)

SummaryCallback = Callable[[ChannelSnapshot], None]


class MattermostMonitor:
    """Background worker that polls Mattermost for unread messages."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._client = MattermostClient(
            base_url=config.mattermost.base_url, token=config.mattermost.token
        )
        self._storage = Storage(config.storage.data_dir)
        self._summarizer = Summarizer(
            model_path=str(config.summarizer.model_path),
            prompt_template=config.summarizer.prompt_template,
            analysis_template=config.summarizer.analysis_template,
            max_tokens=config.summarizer.max_tokens,
            temperature=config.summarizer.temperature,
        )
        self._poll_interval = config.poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._subscribers: list[SummaryCallback] = []
        self._queue: Queue[ChannelSnapshot] = Queue()
        self._user_cache: dict[str, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="mattermost-monitor", daemon=True)
        self._thread.start()
        logger.info("Mattermost monitor started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._client.close()
        logger.info("Mattermost monitor stopped")

    def subscribe(self, callback: SummaryCallback) -> None:
        self._subscribers.append(callback)

    def get_queue(self) -> Queue[ChannelSnapshot]:
        return self._queue

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Failed to poll Mattermost")
            finally:
                self._stop_event.wait(self._poll_interval)

    def _poll_once(self) -> None:
        logger.debug("Polling Mattermost for unread channels")
        for channel in self._client.get_unread_channels():
            since = self._storage.get_last_processed_at(channel.id)
            if since and since > channel.last_viewed_at:
                threshold = since
            else:
                threshold = channel.last_viewed_at
            posts = list(self._client.get_unread_posts(channel, since=threshold))
            if not posts:
                continue
            summary = self._summarize_posts(channel, posts)
            snapshot = ChannelSnapshot(channel=channel, posts=posts, summary=summary)
            self._storage.save_raw_messages(channel, posts)
            self._storage.save_channel_snapshot(snapshot)
            self._storage.update_last_processed_at(channel.id, posts[-1].create_at)
            self._queue.put(snapshot)
            self._notify(snapshot)

    def _summarize_posts(self, channel: Channel, posts: Iterable) -> str:
        posts = list(posts)
        user_ids = [post.user_id for post in posts if post.user_id]
        display_names = self._resolve_user_names(user_ids)
        for post in posts:
            post.user_name = display_names.get(post.user_id)
        messages = [
            f"#{idx + 1} [{post.create_at:%Y-%m-%d %H:%M}] {post.user_name or post.user_id}:\n{post.message.strip()}"
            for idx, post in enumerate(posts)
        ]
        logger.info(
            "Summarizing %d posts for channel %s", len(messages), channel.display_name
        )
        return self._summarizer.summarize_messages(
            messages, channel_name=channel.display_name or channel.name
        )

    def _resolve_user_names(self, user_ids: Iterable[str]) -> dict[str, str]:
        missing = [uid for uid in dict.fromkeys(user_ids) if uid and uid not in self._user_cache]
        if missing:
            try:
                names = self._client.get_user_display_names(missing)
            except Exception:  # pragma: no cover - network failure should not stop polling
                logger.exception("Failed to resolve user names from Mattermost")
            else:
                self._user_cache.update(names)
        return {uid: self._user_cache.get(uid, uid) for uid in user_ids}

    def _notify(self, snapshot: ChannelSnapshot) -> None:
        for callback in list(self._subscribers):
            try:
                callback(snapshot)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Subscriber callback failed")


__all__ = ["MattermostMonitor"]
