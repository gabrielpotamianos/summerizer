"""Configuration utilities for the Mattermost summarizer service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MattermostConfig:
    """Configuration required to connect to Mattermost."""

    base_url: str
    token: str
    polling_interval: float = 30.0
    storage_dir: Path = field(default_factory=lambda: Path.cwd() / "data")

    def __post_init__(self) -> None:
        if not self.base_url.endswith("/api/v4"):
            self.base_url = self.base_url.rstrip("/") + "/api/v4"
        self.storage_dir = Path(self.storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMConfig:
    """Configuration for the local LLM used to summarise chats."""

    model_path: Path
    context_window: int = 2048
    temperature: float = 0.3
    max_tokens: int = 512
    threads: Optional[int] = None


@dataclass
class ServiceConfig:
    """Top level configuration aggregating all service specific configuration."""

    mattermost: MattermostConfig
    llm: LLMConfig
    refresh_ui_interval: float = 5.0
