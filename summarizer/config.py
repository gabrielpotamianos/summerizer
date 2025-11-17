"""Configuration utilities for the Mattermost summarizer service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MattermostConfig:
    """Configuration required to connect to Mattermost."""

    base_url: str
    token: Optional[str] = None
    polling_interval: float = 30.0
    storage_dir: Path = field(default_factory=lambda: Path.cwd() / "data")

    def __post_init__(self) -> None:
        if not self.base_url.endswith("/api/v4"):
            self.base_url = self.base_url.rstrip("/") + "/api/v4"
        self.storage_dir = Path(self.storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMConfig:
    """Configuration for the Groq-hosted LLM used to summarise chats."""

    api_key: Optional[str] = None
    endpoint: str = "https://api.groq.com/openai/v1/chat/completions"
    model_name: str = "llama-3.3-70b-versatile"
    context_window: int = 2048
    temperature: float = 0.3
    max_tokens: int = 512
    request_timeout: int = 60
    max_retries: int = 2
    inter_request_delay: float = 0.0
    batch_size: int = 3
    max_batch_characters: int = 60000
    max_batches: int = 4
    rate_limit_backoff: float = 30.0
    ca_bundle: Optional[str] = None
    model_path: Optional[Path] = None  # legacy; ignored
    threads: Optional[int] = None  # legacy; ignored


@dataclass
class ServiceConfig:
    """Top level configuration aggregating all service specific configuration."""

    mattermost: MattermostConfig
    llm: LLMConfig
    refresh_ui_interval: float = 5.0
