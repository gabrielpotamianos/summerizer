"""Local LLM wrapper used for summarisation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence

from llama_cpp import Llama

from .config import LLMConfig

LOGGER = logging.getLogger(__name__)

_SUMMARY_PROMPT = """Summarise the following Mattermost chat conversation.
Return a concise but comprehensive digest that highlights decisions, blockers,
action items, and any open questions. Use bullet points for clarity and keep
the tone professional and neutral.

Conversation:
{conversation}

Summary:
"""


class LocalLLM:
    """Thin wrapper around :class:`llama_cpp.Llama` for summarisation."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        model_path = Path(config.model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found at {model_path}")
        LOGGER.info("Loading local LLM from %s", model_path)
        self._llama = Llama(
            model_path=str(model_path),
            n_ctx=config.context_window,
            temperature=config.temperature,
            n_threads=config.threads,
        )

    def summarise(self, messages: Sequence[str]) -> str:
        conversation = "\n".join(messages)
        prompt = _SUMMARY_PROMPT.format(conversation=conversation)
        LOGGER.debug("Generating summary for %d messages", len(messages))
        output = self._llama(
            prompt,
            max_tokens=self._config.max_tokens,
            stop=["\n\n"],
        )
        choices = output.get("choices", [])
        if not choices:
            LOGGER.warning("LLM returned no choices, falling back to raw prompt")
            return conversation[: self._config.max_tokens]
        text = choices[0].get("text", "").strip()
        return text


def collate_messages(posts: Iterable[dict]) -> Sequence[str]:
    """Turn Mattermost post payloads into readable conversation snippets."""

    formatted = []
    for post in posts:
        user = post.get("user_id", "unknown")
        message = post.get("message", "")
        timestamp = post.get("create_at")
        formatted.append(f"[{timestamp}] {user}: {message}")
    return formatted
