"""Mattermost API client helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional

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
        """Yield unread information for the current user across all teams."""

        teams = self.list_teams()
        LOGGER.debug("Fetched %d teams", len(teams))

        for team in teams:
            team_id = team["id"]
            channels = {channel["id"]: channel for channel in self.list_channels(team_id)}
            members = self.list_channel_members(team_id)
            for member in members:
                mention_count = member.get("mention_count", 0)
                msg_count = member.get("msg_count", 0)
                last_viewed_at = member.get("last_viewed_at", 0)
                channel_id = member["channel_id"]
                channel = channels.get(channel_id)
                if not channel:
                    continue
                total_unread = mention_count or max(0, msg_count - member.get("msg_count_root", msg_count))
                if total_unread <= 0:
                    continue
                unread = ChannelUnread(
                    team_id=team_id,
                    channel_id=channel_id,
                    channel_name=channel.get("name", channel_id),
                    display_name=channel.get("display_name", channel.get("name", channel_id)),
                    unread_count=total_unread,
                    last_viewed_at=last_viewed_at,
                )
                LOGGER.debug("Channel %s has %d unread messages", unread.display_name, total_unread)
                yield unread

    def get_unread_posts(
        self,
        channel_id: str,
        last_viewed_at: Optional[int] = None,
        unread_count: int = 0,
    ) -> List[Dict[str, str]]:
        """Return only unread posts for the given channel.

        The Mattermost posts API returns conversation history ordered by
        descending timestamps. When a channel has never been viewed the
        ``last_viewed_at`` timestamp is ``0`` which causes the API to return the
        entire channel history. To make sure we only summarise unread
        conversations we post-filter the response so that we only keep messages
        that were created after ``last_viewed_at``. If the channel has never
        been viewed we fall back to the ``unread_count`` provided by the unread
        listing to select the most recent messages.
        """

        params: Dict[str, int] = {}
        if last_viewed_at is not None and last_viewed_at > 0:
            params["since"] = last_viewed_at
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

        # ``order`` is returned newest-first; sort the selected posts so the
        # conversation flows chronologically.
        ordered_posts.sort(key=lambda post: post.get("create_at", 0))

        if last_viewed_at is not None and last_viewed_at > 0:
            unread_posts = [
                post for post in ordered_posts if post.get("create_at", 0) > last_viewed_at
            ]
        else:
            unread_limit = max(unread_count, 0)
            unread_posts = ordered_posts[-unread_limit:] if unread_limit else []

        LOGGER.debug(
            "Filtered %d unread posts (from %d fetched) for channel %s",
            len(unread_posts),
            len(ordered_posts),
            channel_id,
        )
        return unread_posts

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
