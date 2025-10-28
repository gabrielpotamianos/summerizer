from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(slots=True)
class MattermostConfig:
    """Configuration related to the Mattermost API."""

    base_url: str
    token: str


@dataclass(slots=True)
class SummarizerConfig:
    """Configuration for the local LLM summarizer."""

    model_path: Path
    prompt_template: str = (
        "You are summarizing unread Mattermost messages for \"{channel_name}\". "
        "Leverage the numbered transcript and the internal analysis to produce a "
        "clear, reference-rich briefing for someone catching up.\n"
        "Requirements:\n"
        "- Start with a short overview naming key participants and referencing message numbers.\n"
        "- Provide bullet lists for Key Updates, Decisions & Actions, and Open Questions.\n"
        "- Quote or reference exact phrases when they clarify context.\n"
        "- If a section has nothing to report, state \"None noted\".\n\n"
        "Numbered transcript:\n{content}\n\nInternal analysis:\n{analysis}\n\nSummary:"
    )
    analysis_template: str | None = (
        "You are reviewing unread Mattermost messages for \"{channel_name}\". "
        "Consider the numbered transcript below and reason carefully about the most "
        "important updates, decisions, blockers, and follow-ups. Cite message numbers "
        "as evidence.\n\nNumbered transcript:\n{content}\n\nDeliberation:"
    )
    max_tokens: int = 512
    temperature: float = 0.2


@dataclass(slots=True)
class StorageConfig:
    """Configuration of how and where to persist raw messages and summaries."""

    data_dir: Path = Path("data")


@dataclass(slots=True)
class AppConfig:
    """Aggregated configuration for the entire application."""

    mattermost: MattermostConfig
    summarizer: SummarizerConfig
    storage: StorageConfig = StorageConfig()
    poll_interval: float = 60.0


def _expand_env_vars(value: str) -> str:
    """Expand environment variables in a configuration value."""

    return os.path.expandvars(value)


def _coerce_path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _load_yaml(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load application configuration from YAML or environment variables."""

    config_data: Mapping[str, Any]
    if path is not None:
        config_data = _load_yaml(_coerce_path(path))
    else:
        default_path = Path.cwd() / "config.yml"
        config_data = _load_yaml(default_path) if default_path.exists() else {}

    # Allow overriding via environment variables
    token = os.getenv("MATTERMOST_TOKEN")
    base_url = os.getenv("MATTERMOST_BASE_URL")
    model_path = os.getenv("LLM_MODEL_PATH")

    mattermost_cfg = config_data.get("mattermost", {})
    summarizer_cfg = config_data.get("summarizer", {})
    storage_cfg = config_data.get("storage", {})
    poll_interval = float(config_data.get("poll_interval", 60))

    base_url = _expand_env_vars(base_url or mattermost_cfg.get("base_url", ""))
    token = token or mattermost_cfg.get("token", "")
    if not base_url or not token:
        raise ValueError("Mattermost base_url and token must be provided in config or env.")

    model_path_value = model_path or summarizer_cfg.get("model_path")
    if not model_path_value:
        raise ValueError("Summarizer model_path must be provided in config or env.")

    prompt_template = summarizer_cfg.get("prompt_template")
    sentinel = object()
    analysis_template = summarizer_cfg.get("analysis_template", sentinel)
    if analysis_template is sentinel:
        analysis_template = SummarizerConfig.__dataclass_fields__["analysis_template"].default  # type: ignore[index]
    max_tokens = int(summarizer_cfg.get("max_tokens", 512))
    temperature = float(summarizer_cfg.get("temperature", 0.2))

    storage_dir = storage_cfg.get("data_dir", "data")

    return AppConfig(
        mattermost=MattermostConfig(base_url=base_url.rstrip("/"), token=token),
        summarizer=SummarizerConfig(
            model_path=_coerce_path(model_path_value),
            prompt_template=prompt_template or SummarizerConfig.__dataclass_fields__["prompt_template"].default,  # type: ignore[index]
            analysis_template=analysis_template,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        storage=StorageConfig(data_dir=_coerce_path(storage_dir)),
        poll_interval=poll_interval,
    )


__all__ = [
    "AppConfig",
    "MattermostConfig",
    "StorageConfig",
    "SummarizerConfig",
    "load_config",
]
