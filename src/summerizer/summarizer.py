from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional

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
    analysis_template: Optional[str] = None
    max_tokens: int = 512
    temperature: float = 0.2

    @property
    @lru_cache(maxsize=1)
    def _llm(self) -> Llama:
        logger.info("Loading LLaMA model from %s", self.model_path)
        return Llama(model_path=str(self.model_path))

    def summarize_messages(
        self, messages: Iterable[str], *, channel_name: str | None = None
    ) -> str:
        content = "\n".join(messages).strip()
        if not content:
            return "No unread messages to summarize."

        analysis = ""
        if self.analysis_template:
            analysis_prompt = self.analysis_template.format(
                content=content,
                channel_name=channel_name or "this channel",
            )
            logger.debug(
                "Sending %d characters to analysis prompt for channel %s",
                len(analysis_prompt),
                channel_name,
            )
            analysis = self._complete(analysis_prompt)

        prompt = self.prompt_template.format(
            content=content,
            channel_name=channel_name or "this channel",
            analysis=analysis,
        )
        logger.debug(
            "Sending %d characters to summarizer prompt for channel %s",
            len(prompt),
            channel_name,
        )
        summary = self._complete(prompt)
        return summary or "Summary unavailable."

    def _complete(self, prompt: str) -> str:
        completion = self._llm.create_completion(
            prompt=prompt,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return completion["choices"][0]["text"].strip()


__all__ = ["Summarizer"]
