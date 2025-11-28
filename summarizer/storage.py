"""Storage helpers for chat transcripts and summaries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, runtime_checkable


_INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """Return a filesystem safe filename from the provided channel name."""

    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip("._")
    return cleaned or "channel"


@runtime_checkable
class TranscriptStorageProtocol(Protocol):
    """Protocol describing the persistence operations needed by the service."""

    root: Path

    def save_messages(self, channel_name: str, messages: Iterable[Dict]) -> Path:
        ...

    def save_summary(self, channel_name: str, summary: str) -> Path:
        ...

    def get_last_processed_timestamp(self, channel_name: str) -> Optional[int]:
        ...

    def update_last_processed_timestamp(
        self, channel_name: str, timestamp: Optional[int]
    ) -> None:
        ...

    def load_summary(self, channel_name: str) -> str:
        ...

    def list_channels(self) -> List[str]:
        ...


class TranscriptStorage(TranscriptStorageProtocol):
    """Save transcripts and summary data to disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def channel_dir(self, channel_name: str) -> Path:
        path = self.root / safe_filename(channel_name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_messages(self, channel_name: str, messages: Iterable[Dict]) -> Path:
        channel_dir = self.channel_dir(channel_name)
        path = channel_dir / "messages.json"
        data: List[Dict] = list(messages)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def save_summary(self, channel_name: str, summary: str) -> Path:
        channel_dir = self.channel_dir(channel_name)
        path = channel_dir / "summary.txt"
        path.write_text(summary, encoding="utf-8")
        return path

    def _metadata_path(self, channel_name: str) -> Path:
        return self.channel_dir(channel_name) / "metadata.json"

    def load_metadata(self, channel_name: str) -> Dict[str, int]:
        path = self._metadata_path(channel_name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save_metadata(self, channel_name: str, metadata: Dict[str, int]) -> Path:
        path = self._metadata_path(channel_name)
        path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def get_last_processed_timestamp(self, channel_name: str) -> Optional[int]:
        metadata = self.load_metadata(channel_name)
        value = metadata.get("last_processed_timestamp")
        return int(value) if isinstance(value, (int, float)) else None

    def update_last_processed_timestamp(
        self, channel_name: str, timestamp: Optional[int]
    ) -> None:
        if timestamp is None:
            return
        metadata = self.load_metadata(channel_name)
        current = metadata.get("last_processed_timestamp")
        if isinstance(current, (int, float)) and int(timestamp) <= int(current):
            return
        metadata["last_processed_timestamp"] = int(timestamp)
        self.save_metadata(channel_name, metadata)

    def load_summary(self, channel_name: str) -> str:
        path = self.channel_dir(channel_name) / "summary.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def list_channels(self) -> List[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
