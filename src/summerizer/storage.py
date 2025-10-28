from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

from .mattermost_client import Channel, Post

logger = logging.getLogger(__name__)


_filename_safe = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_filename(name: str) -> str:
    sanitized = _filename_safe.sub("_", name).strip("_")
    return sanitized or "channel"


@dataclass(slots=True)
class ChannelSnapshot:
    channel: Channel
    posts: list[Post]
    summary: str

    def to_dict(self) -> dict:
        return {
            "channel": {
                "id": self.channel.id,
                "name": self.channel.name,
                "display_name": self.channel.display_name,
                "type": self.channel.type,
                "last_viewed_at": self.channel.last_viewed_at.isoformat(),
                "last_post_at": self.channel.last_post_at.isoformat(),
                "mention_count": self.channel.mention_count,
                "msg_count": self.channel.msg_count,
            },
            "summary": self.summary,
            "posts": [
                {
                    "id": post.id,
                    "user_id": post.user_id,
                    "user_name": post.user_name,
                    "message": post.message,
                    "create_at": post.create_at.isoformat(),
                }
                for post in self.posts
            ],
        }


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._data_dir / "state.json"
        self._lock = Lock()
        self._state = self._load_state()

    def save_channel_snapshot(self, snapshot: ChannelSnapshot) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"{_sanitize_filename(snapshot.channel.display_name or snapshot.channel.name)}_{timestamp}.json"
        path = self._data_dir / filename
        with path.open("w", encoding="utf-8") as f:
            json.dump(snapshot.to_dict(), f, indent=2)
        logger.info("Saved snapshot for channel %s to %s", snapshot.channel.display_name, path)
        return path

    def save_raw_messages(self, channel: Channel, posts: Iterable[Post]) -> Path:
        filename = f"{_sanitize_filename(channel.display_name or channel.name)}_raw.txt"
        path = self._data_dir / filename
        with path.open("w", encoding="utf-8") as f:
            for post in posts:
                timestamp = post.create_at.strftime("%Y-%m-%d %H:%M")
                author = post.user_name or post.user_id
                f.write(f"[{timestamp}] {author}: {post.message}\n")
        logger.debug("Wrote raw messages for %s to %s", channel.display_name, path)
        return path

    def _load_state(self) -> dict[str, str]:
        if not self._state_path.exists():
            return {}
        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to load state file %s", self._state_path)
        return {}

    def _write_state(self) -> None:
        with self._state_path.open("w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    def get_last_processed_at(self, channel_id: str) -> Optional[datetime]:
        with self._lock:
            value = self._state.get(channel_id)
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            logger.warning("Invalid timestamp for channel %s in state file", channel_id)
            return None

    def update_last_processed_at(self, channel_id: str, timestamp: datetime) -> None:
        with self._lock:
            self._state[channel_id] = timestamp.isoformat()
            self._write_state()


__all__ = ["Storage", "ChannelSnapshot"]
