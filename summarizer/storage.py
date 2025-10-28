"""Storage helpers for chat transcripts and summaries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List


_INVALID_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """Return a filesystem safe filename from the provided channel name."""

    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip("._")
    return cleaned or "channel"


class TranscriptStorage:
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

    def load_summary(self, channel_name: str) -> str:
        path = self.channel_dir(channel_name) / "summary.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def list_channels(self) -> List[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
