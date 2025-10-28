from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

try:
    from llama_cpp import Llama
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise RuntimeError(
        "llama_cpp library is required. Install with `pip install llama-cpp-python`."
    ) from exc

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Summarizer:
    model_path: str
    prompt_template: str
    max_tokens: int = 512
    temperature: float = 0.2

    @property
    @lru_cache(maxsize=1)
    def _llm(self) -> Llama:
        logger.info("Loading LLaMA model from %s", self.model_path)
        return Llama(model_path=str(self.model_path))

    def summarize_messages(self, messages: Iterable[str], *, channel_name: str | None = None) -> str:
        content = "\n".join(messages).strip()
        if not content:
            return "No unread messages to summarize."

        prompt = self.prompt_template.format(
            content=content,
            channel_name=channel_name or "this channel",
        )
        logger.debug("Sending %d characters to summarizer", len(prompt))
        completion = self._llm.create_completion(
            prompt=prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        text = completion["choices"][0]["text"].strip()
        return text or "Summary unavailable."


__all__ = ["Summarizer"]
