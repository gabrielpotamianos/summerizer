from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Mapping, Optional, Sequence

import requests

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Channel:
    id: str
    team_id: str
    display_name: str
    name: str
    type: str
    last_viewed_at: datetime
    last_post_at: datetime
    mention_count: int
    msg_count: int


@dataclass(slots=True)
class Post:
    id: str
    user_id: str
    message: str
    create_at: datetime
    user_name: str | None = None


class MattermostClient:
    """Lightweight wrapper around the Mattermost REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def close(self) -> None:
        self._session.close()

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}" if path.startswith("/") else f"{self._base_url}/{path}"

    def _get(self, path: str, *, params: Mapping[str, str | int | float] | None = None) -> Mapping:
        response = self._session.get(self._url(path), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_teams(self) -> List[Mapping]:
        return list(self._get("/api/v4/users/me/teams"))

    def get_channels(self, team_id: str) -> List[Mapping]:
        params = {"include_deleted": "false", "include_unread_count": "true"}
        return list(self._get(f"/api/v4/users/me/teams/{team_id}/channels", params=params))

    def get_channel_membership(self, team_id: str) -> Mapping[str, Mapping]:
        params = {"per_page": 200}
        memberships = self._get(f"/api/v4/users/me/teams/{team_id}/channels/members", params=params)
        return {member["channel_id"]: member for member in memberships}

    def get_unread_channels(self) -> Iterable[Channel]:
        for team in self.get_teams():
            team_id = team["id"]
            memberships = self.get_channel_membership(team_id)
            for channel in self.get_channels(team_id):
                member = memberships.get(channel["id"])
                if not member:
                    continue
                channel_type = channel.get("type", "")
                # Only surface group messages ("G") so the UI matches the unread badge.
                if channel_type != "G":
                    continue
                mention_count = int(member.get("mention_count", 0))
                msg_count = int(member.get("msg_count", 0))
                if mention_count <= 0 and msg_count <= 0:
                    continue
                last_viewed_raw = member.get("last_viewed_at", 0)
                last_post_raw = channel.get("last_post_at", 0)
                last_viewed_at = datetime.fromtimestamp(last_viewed_raw / 1000)
                last_post_at = datetime.fromtimestamp(last_post_raw / 1000)
                if last_post_at <= last_viewed_at:
                    # No new posts since the channel was last viewed.
                    continue
                yield Channel(
                    id=channel["id"],
                    team_id=team_id,
                    display_name=channel.get("display_name") or channel.get("name", ""),
                    name=channel.get("name", ""),
                    type=channel_type,
                    last_viewed_at=last_viewed_at,
                    last_post_at=last_post_at,
                    mention_count=mention_count,
                    msg_count=msg_count,
                )

    def get_unread_posts(
        self, channel: Channel, *, since: Optional[datetime] = None, limit: int = 200
    ) -> List[Post]:
        params = {"page": 0, "per_page": limit}
        payload = self._get(f"/api/v4/channels/{channel.id}/posts", params=params)
        posts_data = payload.get("posts", {})
        order = payload.get("order", [])
        unread_posts: List[Post] = []
        for post_id in reversed(order):  # posts ordered newest first
            post_payload = posts_data[post_id]
            created_at = datetime.fromtimestamp(post_payload["create_at"] / 1000)
            threshold = since or channel.last_viewed_at
            if created_at <= threshold:
                continue
            message = post_payload.get("message", "").strip()
            if not message:
                continue
            unread_posts.append(
                Post(
                    id=post_id,
                    user_id=post_payload.get("user_id", ""),
                    message=message,
                    create_at=created_at,
                )
            )
        if channel.msg_count and len(unread_posts) > channel.msg_count:
            unread_posts = unread_posts[-channel.msg_count :]
        logger.debug("Fetched %d unread posts for channel %s", len(unread_posts), channel.display_name)
        return unread_posts

    def get_user_display_names(self, user_ids: Sequence[str]) -> Mapping[str, str]:
        unique_ids = [uid for uid in dict.fromkeys(user_ids) if uid]
        if not unique_ids:
            return {}
        response = self._session.post(
            self._url("/api/v4/users/ids"), json=unique_ids, timeout=30
        )
        response.raise_for_status()
        users = response.json()
        display_names: dict[str, str] = {}
        for user in users:
            name_parts = [user.get("first_name", "").strip(), user.get("last_name", "").strip()]
            full_name = " ".join(part for part in name_parts if part)
            nickname = user.get("nickname", "").strip()
            username = user.get("username", "").strip()
            display_names[user["id"]] = next(
                (
                    value
                    for value in (full_name, nickname, username)
                    if value
                ),
                user["id"],
            )
        return display_names


__all__ = ["MattermostClient", "Channel", "Post"]
