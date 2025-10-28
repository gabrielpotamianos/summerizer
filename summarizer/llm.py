"""Local LLM wrapper used for summarisation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from llama_cpp import Llama

from .config import LLMConfig

LOGGER = logging.getLogger(__name__)

_SUMMARY_PROMPT = """You are an expert conversation summariser.
All messages are unread posts from a Mattermost group that the user belongs to.
Use only the provided conversation content. Ignore greetings or small talk and
focus on decisions, blockers, actions, and unresolved questions.

Follow these rules:
- Provide up to three bullet points in the Summary section.
- Use a leading hyphen ("-") for each bullet point in any list section.
- If a section has no relevant information, write "None" on its own line.
- Keep the tone professional and neutral.
- Output must match the template exactly without extra commentary.

Conversation:
{conversation}

Respond using this exact template:

Chat Group Analysis Template:

Group Name: {group_name}
Date Range: {start_date} – {end_date}

Summary:

Key Decisions / Actions:

Tone / Sentiment:

Notable Questions / Issues:
"""

_SEGMENT_PROMPT = """You are preparing notes for a portion of an unread
Mattermost conversation. Capture critical information only.

Conversation Segment:
{conversation}

Segment Notes:
- Provide up to 5 succinct bullet points focused on decisions, blockers,
  actions, or important context.
- Ignore greetings or small talk.
"""


@dataclass(frozen=True)
class SummaryContext:
    """Metadata describing the conversation being summarised."""

    group_name: str
    start_date: str
    end_date: str


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

    def summarise(self, messages: Sequence[str], context: SummaryContext) -> str:
        """Summarise a set of messages using the configured local LLM."""

        if not messages:
            return ""

        LOGGER.debug(
            "Generating summary for %d messages in %s", len(messages), context.group_name
        )
        chunks = self._chunk_messages(messages)
        if len(chunks) == 1:
            return self._generate_final_summary(chunks[0], context)

        LOGGER.debug(
            "Conversation exceeds chunk limit; splitting into %d segments", len(chunks)
        )
        segment_summaries = [self._summarise_segment(chunk) for chunk in chunks]
        segment_context = [
            f"Segment {index + 1} Notes:\n{summary}"
            for index, summary in enumerate(segment_summaries)
        ]
        return self._generate_final_summary(segment_context, context)

    def _summarise_segment(self, messages: Sequence[str]) -> str:
        prompt = self._build_prompt(_SEGMENT_PROMPT, messages)
        return self._run_llm(prompt)

    def _generate_final_summary(
        self, conversation_items: Sequence[str], context: SummaryContext
    ) -> str:
        prompt = self._build_prompt(
            _SUMMARY_PROMPT,
            conversation_items,
            group_name=context.group_name,
            start_date=context.start_date,
            end_date=context.end_date,
        )
        return self._run_llm(prompt)

    def _run_llm(self, prompt: str) -> str:
        prompt_tokens = self._count_tokens(prompt)
        available_for_completion = max(
            self._config.context_window - prompt_tokens - 1, 1
        )
        max_tokens = max(1, min(self._config.max_tokens, available_for_completion))
        output = self._llama(
            prompt,
            max_tokens=max_tokens,
        )
        choices = output.get("choices", [])
        if not choices:
            LOGGER.warning("LLM returned no choices, falling back to raw prompt")
            return prompt[: self._config.max_tokens]
        text = choices[0].get("text", "").strip()
        return text

    def _build_prompt(
        self,
        template: str,
        conversation_items: Sequence[str],
        **template_kwargs: str,
    ) -> str:
        base_prompt = template.format(conversation="", **template_kwargs)
        prompt_budget = max(self._config.context_window - 32, 1)
        base_tokens = self._count_tokens(base_prompt)
        available_for_conversation = max(
            prompt_budget - self._config.max_tokens - base_tokens,
            0,
        )
        trimmed_items = self._trim_conversation_items(
            conversation_items, available_for_conversation
        )
        conversation = "\n".join(trimmed_items)
        prompt = template.format(conversation=conversation, **template_kwargs)
        prompt_tokens = self._count_tokens(prompt)
        budget = self._config.context_window - 1
        while prompt_tokens > budget and trimmed_items:
            LOGGER.debug(
                "Prompt exceeds context window (%d > %d); trimming conversation",
                prompt_tokens,
                budget,
            )
            if len(trimmed_items) == 1:
                trimmed_items = [
                    self._trim_text_to_token_budget(
                        trimmed_items[0], max(8, budget // 2)
                    )
                ]
            else:
                trimmed_items = trimmed_items[1:]
            conversation = "\n".join(item for item in trimmed_items if item)
            prompt = template.format(conversation=conversation, **template_kwargs)
            prompt_tokens = self._count_tokens(prompt)
        return prompt

    def _trim_conversation_items(
        self, conversation_items: Sequence[str], token_limit: int
    ) -> Sequence[str]:
        if not conversation_items:
            return []
        if token_limit <= 0:
            fallback_tokens = max(8, min(self._config.max_tokens, 64))
            snippet = self._trim_text_to_token_budget(
                conversation_items[-1], fallback_tokens
            )
            return [snippet] if snippet else []
        trimmed: list[str] = []
        used_tokens = 0
        for item in reversed(conversation_items):
            item_tokens = self._count_tokens(item + "\n")
            if used_tokens + item_tokens <= token_limit:
                trimmed.insert(0, item)
                used_tokens += item_tokens
                continue
            remaining = token_limit - used_tokens
            if remaining <= 0:
                break
            snippet = self._trim_text_to_token_budget(item, remaining)
            if snippet:
                trimmed.insert(0, snippet)
            break
        if not trimmed:
            snippet = self._trim_text_to_token_budget(
                conversation_items[-1], max(token_limit, 8)
            )
            return [snippet] if snippet else []
        return trimmed

    def _trim_text_to_token_budget(self, text: str, max_tokens: int) -> str:
        if not text or max_tokens <= 0:
            return ""
        if self._count_tokens(text) <= max_tokens:
            return text
        approx_chars = max(1, max_tokens * 4)
        snippet = text[-approx_chars:]
        while snippet and self._count_tokens(snippet) > max_tokens and approx_chars > 1:
            approx_chars = max(1, int(approx_chars * 0.9))
            snippet = text[-approx_chars:]
        while snippet and self._count_tokens(snippet) > max_tokens:
            snippet = snippet[1:]
        snippet = snippet.lstrip()
        if not snippet:
            return ""
        candidate = snippet
        if not candidate.startswith("…"):
            candidate = f"… {candidate}"
        while candidate and self._count_tokens(candidate) > max_tokens:
            candidate = candidate[1:]
        return candidate

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        tokens = self._llama.tokenize(text.encode("utf-8"), add_bos=False)
        return len(tokens)

    def _chunk_messages(self, messages: Sequence[str]) -> Sequence[Sequence[str]]:
        """Split messages into chunks that fit comfortably within the context."""

        max_chars = max(512, int(self._config.context_window * 4 * 0.8))
        chunks: list[list[str]] = []
        current: list[str] = []
        current_length = 0
        for message in messages:
            message_length = len(message) + 1
            if current and current_length + message_length > max_chars:
                chunks.append(current)
                current = []
                current_length = 0
            current.append(message)
            current_length += message_length
        if current:
            chunks.append(current)
        return chunks


def collate_messages(posts: Iterable[dict]) -> Tuple[Sequence[str], Optional[int], Optional[int]]:
    """Turn Mattermost post payloads into readable conversation snippets.

    Returns a tuple of formatted message strings, the earliest timestamp, and
    the latest timestamp (all timestamps expressed in milliseconds since epoch).
    """

    formatted = []
    timestamps: list[int] = []
    for post in posts:
        user = post.get("user_id", "unknown")
        message = post.get("message", "")
        timestamp = post.get("create_at")
        if isinstance(timestamp, (int, float)):
            timestamps.append(int(timestamp))
        formatted.append(f"[{timestamp}] {user}: {message}")
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None
    return formatted, start, end
