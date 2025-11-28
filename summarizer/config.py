"""Configuration utilities for the Mattermost summarizer service."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

__all__ = ["MattermostConfig", "LLMConfig", "ServiceConfig"]


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

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ServiceConfig:
        """Build a ``ServiceConfig`` instance from a generic mapping."""

        mattermost_payload = payload.get("mattermost")
        llm_payload = payload.get("llm")
        if not isinstance(mattermost_payload, Mapping):
            raise ValueError("Config payload is missing 'mattermost' section.")
        if not isinstance(llm_payload, Mapping):
            raise ValueError("Config payload is missing 'llm' section.")
        refresh_ui_interval = float(payload.get("refresh_ui_interval", 5.0))
        return cls(
            mattermost=MattermostConfig(**dict(mattermost_payload)),
            llm=LLMConfig(**dict(llm_payload)),
            refresh_ui_interval=refresh_ui_interval,
        )

    @classmethod
    def from_json(cls, path: Path) -> ServiceConfig:
        """Load configuration from a JSON file located at ``path``."""

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Top-level config JSON must be an object.")
        return cls.from_mapping(data)

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain serialisable representation of the config."""

        return {
            "mattermost": self._serialize_dataclass(self.mattermost),
            "llm": self._serialize_dataclass(self.llm),
            "refresh_ui_interval": self.refresh_ui_interval,
        }

    @staticmethod
    def _serialize_dataclass(instance: object) -> Dict[str, Any]:
        payload = asdict(instance)
        serialised: Dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, Path):
                serialised[key] = str(value)
            else:
                serialised[key] = value
        return serialised
