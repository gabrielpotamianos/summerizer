"""Mattermost API client helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from .config import MattermostConfig

LOGGER = logging.getLogger(__name__)


@dataclass
class ChannelUnread:
    """Represents unread payload fetched from Mattermost."""

    team_id: str
    channel_id: str
    channel_name: str
    display_name: str
    unread_count: int
    last_viewed_at: int


class MattermostClient:
    """Lightweight Mattermost REST API client."""

    def __init__(self, config: MattermostConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json",
        })
        LOGGER.debug("Mattermost client initialised with base url %s", config.base_url)

    def _url(self, path: str) -> str:
        return f"{self._config.base_url}{path}"

    def get_user(self) -> Dict[str, str]:
        response = self._session.get(self._url("/users/me"), timeout=30)
        response.raise_for_status()
        return response.json()

    def list_teams(self) -> List[Dict[str, str]]:
        response = self._session.get(self._url("/users/me/teams"), timeout=30)
        response.raise_for_status()
        return response.json()

    def list_channels(self, team_id: str) -> List[Dict[str, str]]:
        response = self._session.get(
            self._url(f"/users/me/teams/{team_id}/channels"),
            params={"include_deleted": "false"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_channel_members(self, team_id: str) -> List[Dict[str, str]]:
        response = self._session.get(
            self._url(f"/users/me/teams/{team_id}/channels/members"),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_unread_channels(self) -> Iterable[ChannelUnread]:
        """Yield unread channel metadata sorted by most recent activity.

        Muted channels are ignored and only conversations that the current user
        belongs to are returned. Channels are yielded in descending order of the
        most recent post so the freshest conversations are processed first.
        """

        teams = self.list_teams()
        LOGGER.debug("Fetched %d teams", len(teams))

        candidates: List[Tuple[int, ChannelUnread]] = []

        for team in teams:
            team_id = team["id"]
            channels = {channel["id"]: channel for channel in self.list_channels(team_id)}
            members = self.list_channel_members(team_id)
            for member in members:
                channel_id = member.get("channel_id")
                if not channel_id:
                    continue
                channel = channels.get(channel_id)
                if not channel:
                    continue
                if int(channel.get("delete_at", 0) or 0) != 0:
                    continue
                if self._is_channel_muted(member):
                    LOGGER.debug(
                        "Skipping muted channel %s", channel.get("display_name", channel_id)
                    )
                    continue
                channel_type = channel.get("type")
                last_viewed_at = self._coerce_int(member.get("last_viewed_at"))
                last_post_at = self._coerce_int(channel.get("last_post_at"))
                mention_count = self._coerce_int(member.get("mention_count"))
                total_msg_count = self._coerce_int(channel.get("total_msg_count"))
                read_msg_count = self._coerce_int(member.get("msg_count"))
                unread_from_counts = max(0, total_msg_count - read_msg_count)
                unread_total = max(unread_from_counts, mention_count)
                if last_post_at <= last_viewed_at and unread_total <= 0:
                    continue
                if unread_total <= 0 and last_post_at > last_viewed_at:
                    unread_total = 1
                if channel_type == "G" and not self._is_channel_highlighted(channel, member):
                    LOGGER.debug(
                        "Skipping non-highlighted group channel %s",
                        channel.get("display_name", channel_id),
                    )
                    continue
                unread = ChannelUnread(
                    team_id=team_id,
                    channel_id=channel_id,
                    channel_name=channel.get("name", channel_id),
                    display_name=channel.get(
                        "display_name", channel.get("name", channel_id)
                    ),
                    unread_count=unread_total,
                    last_viewed_at=last_viewed_at,
                )
                LOGGER.debug(
                    "Channel %s has %d unread messages (last post %d)",
                    unread.display_name,
                    unread_total,
                    last_post_at,
                )
                candidates.append((last_post_at, unread))

        for _, unread in sorted(candidates, key=lambda item: item[0], reverse=True):
            yield unread

    def get_unread_posts(
        self,
        channel_id: str,
        last_viewed_at: Optional[int] = None,
        unread_count: int = 0,
    ) -> List[Dict[str, str]]:
        """Return unread posts in newest-first order for the given channel.

        When a channel has been viewed previously the response includes unread
        posts newer than ``last_viewed_at`` followed by the most recent message
        that the user has already seen. This provides context without requiring
        a full history fetch. Channels that have never been viewed fall back to
        ``unread_count`` (or a small default) to avoid downloading the complete
        history.
        """

        params: Dict[str, int] = {}
        last_viewed_ts = self._coerce_int(last_viewed_at)
        if last_viewed_ts > 0:
            params["since"] = max(last_viewed_ts - 1, 0)
        response = self._session.get(
            self._url(f"/channels/{channel_id}/posts"),
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        posts_payload = response.json()
        order = posts_payload.get("order", [])
        posts = posts_payload.get("posts", {})
        ordered_posts = [posts[post_id] for post_id in order if post_id in posts]

        unread_posts: List[Dict[str, str]] = []
        if last_viewed_ts > 0:
            last_read_post: Optional[Dict[str, str]] = None
            for post in ordered_posts:
                created = self._coerce_int(post.get("create_at"))
                if created > last_viewed_ts:
                    unread_posts.append(post)
                    continue
                last_read_post = post
                break
            if last_read_post is not None:
                unread_posts.append(last_read_post)
        else:
            unread_limit = max(self._coerce_int(unread_count), 0)
            if unread_limit:
                unread_posts = ordered_posts[:unread_limit]
            else:
                unread_posts = ordered_posts[:50]

        LOGGER.debug(
            "Filtered %d unread posts (from %d fetched) for channel %s",
            len(unread_posts),
            len(ordered_posts),
            channel_id,
        )
        return unread_posts

    @staticmethod
    def _is_channel_highlighted(
        channel: Dict[str, object], member: Dict[str, object]
    ) -> bool:
        """Return True if Mattermost marks the channel as highlighted for the user."""

        highlight_keys = ("is_channel_highlighted", "is_highlighted", "highlighted")
        for key in highlight_keys:
            for source in (channel, member):
                value = source.get(key)
                if isinstance(value, bool):
                    if value:
                        return True
                elif isinstance(value, str):
                    if value.lower() == "true":
                        return True
        mention_fields = (
            member.get("mention_count"),
            member.get("mention_count_root"),
            member.get("mention_count_threads"),
        )
        return any(MattermostClient._coerce_int(field) > 0 for field in mention_fields)

    @staticmethod
    def _is_channel_muted(member: Dict[str, object]) -> bool:
        notify_props = member.get("notify_props")
        if isinstance(notify_props, dict):
            muted = notify_props.get("muted")
            if isinstance(muted, str) and muted.lower() == "true":
                return True
            mark_unread = notify_props.get("mark_unread")
            if isinstance(mark_unread, str) and mark_unread.lower() == "mention":
                return True
        return False

    @staticmethod
    def _coerce_int(value: object) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    def acknowledge_channel(self, channel_id: str, viewed_at: Optional[datetime] = None) -> None:
        payload: Dict[str, int] = {}
        if viewed_at is not None:
            payload["viewed_at"] = int(viewed_at.timestamp() * 1000)
        response = self._session.post(
            self._url(f"/channels/{channel_id}/members/me/view"),
            json=payload or None,
            timeout=30,
        )
        if response.status_code >= 400:
            LOGGER.warning("Failed to acknowledge channel %s: %s", channel_id, response.text)
