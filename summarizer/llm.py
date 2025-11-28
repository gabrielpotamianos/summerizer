"""Groq-hosted LLM wrapper used for remote summarisation."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    runtime_checkable,
)

from email.utils import parsedate_to_datetime
import requests
from requests import Response
from requests.exceptions import RequestException

from .config import LLMConfig

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryContext:
    group_name: str
    start_date: str
    end_date: str


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol implemented by LLM adapters used by the service."""

    def summarise(self, messages: Sequence[str], context: SummaryContext) -> str:
        ...

    def summarise_groups(
        self,
        groups: Sequence[Tuple[str, SummaryContext, Sequence[str]]],
    ) -> Dict[str, str]:
        ...


class LocalLLM(LLMBackend):
    """Thin wrapper around Groq's hosted LLaMA models."""

    DEFAULT_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, config: LLMConfig, session: Optional[requests.Session] = None) -> None:
        self._config = config
        self._api_key = getattr(config, "api_key", None) or os.getenv("GROQ_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "Groq API key not provided. Set GROQ_API_KEY or llm.api_key in config."
            )

        self._endpoint = getattr(config, "endpoint", self.DEFAULT_ENDPOINT)
        self._model = getattr(config, "model_name", self.DEFAULT_MODEL)
        self._request_timeout = getattr(config, "request_timeout", 60)
        self._max_retries = max(0, getattr(config, "max_retries", 2))
        self._min_delay = max(0.0, float(getattr(config, "inter_request_delay", 0.0)))
        self._last_request = 0.0

        self._session_owner = session is None
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )
        self._ca_bundle = getattr(config, "ca_bundle", None)
        if self._ca_bundle:
            self._session.verify = self._ca_bundle

    def close(self) -> None:
        """Close the owned HTTP session if this instance created it."""

        if self._session_owner:
            self._session.close()

    def __enter__(self) -> LocalLLM:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def summarise(self, messages: Sequence[str], context: SummaryContext) -> str:
        if not messages:
            return ""

        conversation = self._prepare_conversation(messages)
        system_prompt = """
            You are summarizing an internal chat to enable fast catch-up; 
            keep it confidential, IMPORTANT! -> factual (no guesses), 
            preserve names/dates/numbers, and focus on decisions, action items, 
            blockers, and next steps; output in English as concise bullets without 
            losing the essence.
        """
        user_prompt = (
            f"Group: {context.group_name}\n"
            f"Dates: {context.start_date} – {context.end_date}\n\n"
            f"Conversation:\n{conversation}\n\n"
            "Provide up to 3-10 concise bullet points that capture the most important"
            " takeaways. . NO GUESSES ALL FACTUAL. Each bullet should be a standalone sentence not too long."
            " Better more concise bullet points than little bulletpoints but long"
        )

        chat_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        LOGGER.info("Groq requests temporarily disabled; returning empty summary.")
        return self._run_llm(chat_messages)
        # return ""

    def summarise_groups(
        self,
        groups: Sequence[Tuple[str, SummaryContext, Sequence[str]]],
    ) -> Dict[str, str]:
        """Summarise multiple groups in a single LLM request.

        Each entry in `groups` is a tuple of (group_id, context, messages).
        The returned dictionary maps the same group_id to its bullet-point summary.
        """

        prepared: List[Tuple[str, SummaryContext, str]] = []
        for group_id, context, messages in groups:
            if not messages:
                continue
            conversation = self._prepare_conversation(messages)
            if not conversation.strip():
                continue
            prepared.append((group_id, context, conversation))

        if not prepared:
            return {}

        system_prompt = (
            "You are summarizing multiple internal chat groups to enable fast catch-up. "
            "Stay factual (no guesses), keep names/dates/numbers intact, and focus on "
            "decisions, action items, blockers, and next steps. "
            "Respond with a VALID JSON object that maps each provided group_id to an "
            "array of 3 to 10 concise bullet point strings. No additional commentary."
        )
        user_prompt = self._render_batch_prompt(prepared)
        chat_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        # LOGGER.info(
        #     "Groq batch requests temporarily disabled; skipping summarisation for %d group(s).",
        #     len(prepared),
        # )
        response = self._run_llm(chat_messages)
        return self._parse_batch_response(response, [item[0] for item in prepared])
        # return {}

    def summarise_directory(self, data_dir: Path) -> None:
        """Walk every channel folder within `data_dir` and regenerate summaries."""

        data_dir = Path(data_dir)
        if not data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        entries: List[Dict[str, Any]] = []
        for channel_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
            messages_path = channel_dir / "messages.json"
            posts = _load_messages(messages_path)
            if not posts:
                LOGGER.debug("Skipping %s (no messages)", channel_dir.name)
                continue

            conversation, start_ts, end_ts = collate_messages(posts)
            if not conversation:
                LOGGER.debug("Skipping %s (no textual content)", channel_dir.name)
                continue

            metadata_path = channel_dir / "metadata.json"
            metadata = _load_metadata(metadata_path)
            group_name = _derive_group_name(channel_dir.name, metadata)

            context = SummaryContext(
                group_name=group_name,
                start_date=_format_timestamp(start_ts),
                end_date=_format_timestamp(end_ts),
            )
            entries.append(
                {
                    "group_id": channel_dir.name,
                    "context": context,
                    "messages": conversation,
                    "message_count": len(posts),
                    "group_name": group_name,
                    "channel_dir": channel_dir,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                }
            )

        if not entries:
            return

        total_entries = len(entries)
        configured_batch_size = max(1, int(getattr(self._config, "batch_size", 3)))
        max_batches = max(0, int(getattr(self._config, "max_batches", 0)))
        if max_batches:
            target_batch_size = max(1, math.ceil(total_entries / max_batches))
            if target_batch_size > configured_batch_size:
                LOGGER.info(
                    "Adjusting batch size from %d to %d to stay within %d total batches",
                    configured_batch_size,
                    target_batch_size,
                    max_batches,
                )
            batch_size = max(configured_batch_size, target_batch_size)
        else:
            batch_size = configured_batch_size
        max_batch_chars = max(
            1024, int(getattr(self._config, "max_batch_characters", 60000))
        )
        LOGGER.info(
            "Preparing %d channel summaries with batch size %d (max batches %s, char cap %d)",
            total_entries,
            batch_size,
            max_batches if max_batches else "unbounded",
            max_batch_chars,
        )

        batches: List[Tuple[List[Dict[str, Any]], int]] = []
        current_batch: List[Dict[str, Any]] = []
        current_chars = 0

        for entry in entries:
            message_chars = len(entry["messages"])
            if current_batch and (
                len(current_batch) >= batch_size
                or current_chars + message_chars > max_batch_chars
            ):
                batches.append((current_batch, current_chars))
                current_batch = []
                current_chars = 0

            current_batch.append(entry)
            current_chars += message_chars

        if current_batch:
            batches.append((current_batch, current_chars))

        batch_summaries: Dict[str, str] = {}
        failed_group_ids: Set[str] = set()
        for batch_entries, char_count in batches:
            group_ids = [entry["group_id"] for entry in batch_entries]
            human_names = ", ".join(entry["group_name"] for entry in batch_entries)
            LOGGER.info(
                "Submitting batch with %d group(s): %s (approx %d chars)",
                len(group_ids),
                human_names or "unknown groups",
                char_count,
            )
            try:
                batch_payload = [
                    (entry["group_id"], entry["context"], entry["messages"])
                    for entry in batch_entries
                ]
                partial = self.summarise_groups(batch_payload)
                batch_summaries.update(partial)
                missing = [
                    entry["group_id"]
                    for entry in batch_entries
                    if entry["group_id"] not in partial
                ]
                if missing:
                    failed_group_ids.update(missing)
                    LOGGER.warning(
                        "Batch response omitted %d group(s); will retry individually: %s",
                        len(missing),
                        ", ".join(missing),
                    )
            except Exception:
                LOGGER.exception(
                    "Batch summarisation failed for %d channel(s); will retry individually",
                    len(batch_entries),
                )
                failed_group_ids.update(group_ids)

        for entry in entries:
            group_id = entry["group_id"]
            summary_source = "batch"
            summary = batch_summaries.get(group_id, "").strip()
            if not summary:
                if group_id in failed_group_ids:
                    LOGGER.debug(
                        "Retrying %s with single request after batch failure", group_id
                    )
                summary_source = "single"
                try:
                    summary = self.summarise(
                        entry["messages"], entry["context"]
                    ).strip()
                except Exception:
                    LOGGER.exception(
                        "Failed to generate summary for %s via single request",
                        entry["group_name"],
                    )
                    summary = ""
            if not summary:
                summary_source = "fallback"
                summary = _fallback_summary(
                    entry["group_name"],
                    entry["message_count"],
                    entry["start_ts"],
                    entry["end_ts"],
                )

            summary_path = entry["channel_dir"] / "summary.txt"
            summary_path.write_text(summary, encoding="utf-8")
            LOGGER.info(
                "Wrote summary for %s via %s (%d messages) to %s",
                entry["group_name"],
                summary_source,
                entry["message_count"],
                summary_path,
            )

    def _prepare_conversation(self, messages: Sequence[str]) -> str:
        joined = "\n".join(messages)
        window = getattr(self._config, "context_window", 0)
        if window and window > 0:
            # Approximate 1 token ≈ 4 characters to keep payload within context.
            char_limit = window * 4
            if len(joined) > char_limit:
                LOGGER.debug(
                    "Truncating conversation from %d to %d characters to respect context window",
                    len(joined),
                    char_limit,
                )
                joined = joined[-char_limit:]
        return joined

    def _run_llm(self, chat_messages: Sequence[Dict[str, str]]) -> str:
        # LOGGER.info("Groq API calls disabled; skipping remote summarisation request.")
        payload = {
            "model": self._model,
            "messages": list(chat_messages),
            "temperature": getattr(self._config, "temperature", 0.3),
            "max_tokens": getattr(self._config, "max_tokens", 512),
        }
        attempt = 0
        while True:
            attempt += 1
            try:
                self._throttle_requests()
                response = self._session.post(
                    self._endpoint, json=payload, timeout=self._request_timeout
                )
                self._last_request = time.monotonic()
            except RequestException as exc:
                if attempt <= self._max_retries:
                    delay = max(0.5, min(2**attempt, 10))
                    LOGGER.warning(
                        "Groq request failed (%s). Retrying in %.1fs (%d/%d)...",
                        exc,
                        delay,
                        attempt,
                        self._max_retries,
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError("Unable to contact Groq API") from exc
            summary = self._handle_response(response)
            if summary is not None:
                return summary.strip()
            if attempt > self._max_retries:
                raise RuntimeError(
                    f"Groq API repeatedly returned {response.status_code}"
                )
            delay = self._compute_retry_delay(response, attempt)
            LOGGER.warning(
                "Groq API returned status %d. Retrying in %.1fs (%d/%d)...",
                response.status_code,
                delay,
                attempt,
                self._max_retries,
            )
            time.sleep(delay)
        # return ""

    def _handle_response(self, response: Response) -> Optional[str]:
        if response.status_code >= 500:
            return None
        if response.status_code == 429:
            return None
        try:
            response.raise_for_status()
        except RequestException as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"Groq API error: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise RuntimeError("Invalid JSON payload from Groq") from exc

        choices = payload.get("choices")
        if not choices:
            raise RuntimeError("Groq API returned no choices")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Groq API returned an unexpected response format")
        return content

    def _render_batch_prompt(
        self, prepared: Sequence[Tuple[str, SummaryContext, str]]
    ) -> str:
        sections: List[str] = [
            "You will be given multiple chat groups. For each section:",
            "- `group_id` is the identifier you must use as the JSON key.",
            "- `group_name` is the human readable name.",
            "- `date_range` is the relevant time period.",
            "- `conversation` lists the messages.",
            'Return JSON in the form {"<group_id>": ["bullet", ...], ...}.',
            "Do not include any text outside the JSON object.",
        ]

        for idx, (group_id, context, conversation) in enumerate(prepared, start=1):
            sections.append(f"\nGroup {idx}")
            sections.append(f"group_id: {group_id}")
            sections.append(f"group_name: {context.group_name}")
            sections.append(f"date_range: {context.start_date} – {context.end_date}")
            sections.append("conversation:")
            sections.append(conversation)
            sections.append("END_OF_CONVERSATION")

        return "\n".join(sections)

    def _parse_batch_response(
        self, response: str, expected_ids: Sequence[str]
    ) -> Dict[str, str]:
        payload = self._coerce_json(response)
        if not isinstance(payload, dict):
            raise RuntimeError("Batch response is not a JSON object")

        summaries: Dict[str, str] = {}
        for group_id in expected_ids:
            raw = payload.get(group_id)
            summary = self._normalise_summary_value(raw)
            if summary:
                summaries[group_id] = summary
        return summaries

    @staticmethod
    def _coerce_json(response: str) -> Dict[str, Any]:
        response = response.strip()
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1 and end > start:
                fragment = response[start : end + 1]
                try:
                    payload = json.loads(fragment)
                except json.JSONDecodeError as inner_exc:
                    raise RuntimeError(
                        "Batch summary response was not valid JSON"
                    ) from inner_exc
            else:
                raise RuntimeError("Batch summary response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Batch summary response must be a JSON object")
        return payload

    def _normalise_summary_value(self, value: Any) -> str:
        if isinstance(value, list):
            return self._format_bullets(value)
        if isinstance(value, str):
            lines = [
                line.strip(" -•\t")
                for line in value.strip().splitlines()
                if line.strip()
            ]
            if not lines:
                return ""
            return self._format_bullets(lines)
        if isinstance(value, dict):
            # Support common nested shapes.
            for key in ("summary", "bullets", "points"):
                nested = value.get(key)
                result = self._normalise_summary_value(nested)
                if result:
                    return result
        return ""

    @staticmethod
    def _format_bullets(items: Sequence[str]) -> str:
        bullets: List[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            prefix = "- " if not text.startswith("-") else ""
            bullets.append(f"{prefix}{text}")
        return "\n".join(bullets)

    def _throttle_requests(self) -> None:
        if not self._min_delay:
            return
        now = time.monotonic()
        elapsed = now - self._last_request
        remaining = self._min_delay - elapsed
        if remaining > 0:
            LOGGER.debug("Sleeping %.2fs to respect inter-request delay", remaining)
            time.sleep(remaining)

    def _compute_retry_delay(self, response: Response, attempt: int) -> float:
        if response.status_code == 429:
            base_delay = max(
                0.0, float(getattr(self._config, "rate_limit_backoff", 30.0))
            )
            retry_after = response.headers.get("Retry-After")
            header_delay: Optional[float] = None
            if retry_after:
                retry_after = retry_after.strip()
                if retry_after.isdigit():
                    header_delay = max(0.0, float(retry_after))
                else:
                    try:
                        retry_time = parsedate_to_datetime(retry_after)
                        if retry_time.tzinfo is None:
                            retry_time = retry_time.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        header_delay = max(0.0, (retry_time - now).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        header_delay = None
            if header_delay is not None:
                base_delay = max(base_delay, header_delay)
            return base_delay or 1.0
        return max(0.5, min(2**attempt, 10))

    def _summarise_channel(self, channel_dir: Path) -> None:
        messages_path = channel_dir / "messages.json"
        posts = _load_messages(messages_path)
        if not posts:
            LOGGER.debug("Skipping %s (no messages)", channel_dir.name)
            return

        conversation, start_ts, end_ts = collate_messages(posts)
        if not conversation:
            LOGGER.debug("Skipping %s (no textual content)", channel_dir.name)
            return

        metadata_path = channel_dir / "metadata.json"
        metadata = _load_metadata(metadata_path)
        group_name = _derive_group_name(channel_dir.name, metadata)

        context = SummaryContext(
            group_name=group_name,
            start_date=_format_timestamp(start_ts),
            end_date=_format_timestamp(end_ts),
        )

        summary = self.summarise(conversation, context).strip()
        if not summary:
            summary = _fallback_summary(group_name, len(posts), start_ts, end_ts)

        summary_path = channel_dir / "summary.txt"
        summary_path.write_text(summary, encoding="utf-8")
        LOGGER.info("Wrote summary for %s to %s", group_name, summary_path)


def collate_messages(
    posts: Iterable[Dict[str, object]],
) -> Tuple[List[str], Optional[int], Optional[int]]:
    formatted: List[str] = []
    timestamps: List[int] = []
    for post in posts:
        message = post.get("message", "")
        timestamp = post.get("create_at")
        if isinstance(timestamp, (int, float)):
            timestamps.append(int(timestamp))
        formatted.append(f"[{timestamp}] {post.get('user_id', 'unknown')}: {message}")
    start = min(timestamps) if timestamps else None
    end = max(timestamps) if timestamps else None
    return formatted, start, end


def _load_messages(path: Path) -> List[Dict[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.debug("Messages file missing: %s", path)
        return []
    except json.JSONDecodeError:
        LOGGER.warning("Unable to parse messages file: %s", path)
        return []

    posts: List[Dict[str, object]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        post = dict(item)
        timestamp = post.get("create_at")
        if isinstance(timestamp, (int, float)):
            post["create_at"] = int(timestamp)
        posts.append(post)

    posts.sort(
        key=lambda entry: (
            entry.get("create_at", 0)
            if isinstance(entry.get("create_at"), (int, float))
            else 0
        )
    )
    return posts


def _load_metadata(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("Unable to parse metadata file: %s", path)
        return {}
    return payload if isinstance(payload, dict) else {}


def _derive_group_name(channel_dir_name: str, metadata: Dict[str, object]) -> str:
    for key in ("display_name", "channel_name", "name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    pretty = channel_dir_name.replace("_", " ").replace("-", " ").strip()
    return pretty or channel_dir_name


def _format_timestamp(timestamp: Optional[int]) -> str:
    if timestamp is None:
        return "Unknown"
    dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def _fallback_summary(
    group_name: str,
    message_count: int,
    start_ts: Optional[int],
    end_ts: Optional[int],
) -> str:
    window_start = _format_timestamp(start_ts)
    if start_ts and end_ts and start_ts != end_ts:
        window = f"{window_start} – {_format_timestamp(end_ts)}"
    else:
        window = window_start
    plural = "s" if message_count != 1 else ""
    return (
        f"{message_count} message{plural} captured for {group_name} ({window}).\n"
        "Unable to generate an AI summary at this time."
    )


def _load_config(path: Path) -> Tuple[LLMConfig, Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    llm_cfg = LLMConfig(**payload["llm"])
    storage_dir = Path(payload.get("mattermost", {}).get("storage_dir", "./data"))
    return llm_cfg, storage_dir


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate summaries directly from stored messages."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.json"),
        help="Path to the service configuration JSON (defaults to config.json).",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Override the data directory (defaults to storage_dir in config).",
    )
    args = parser.parse_args(argv)

    llm_cfg, default_dir = _load_config(args.config)
    data_dir = args.data if args.data is not None else default_dir
    data_dir = data_dir.expanduser()

    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Summarising stored messages under %s", data_dir)

    llm = LocalLLM(llm_cfg)
    llm.summarise_directory(data_dir)


if __name__ == "__main__":
    main()
