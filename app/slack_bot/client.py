"""Real Slack API Client.

Production-ready client using httpx for async HTTP calls
to the Slack Web API.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("omnipotent.slack_bot.client")

SLACK_API_BASE = "https://slack.com/api"


class SlackAPIError(Exception):
    """Slack API error."""

    def __init__(self, method: str, error: str, data: dict[str, Any] | None = None):
        self.method = method
        self.error = error
        self.data = data or {}
        super().__init__(f"Slack API error in {method}: {error}")


class SlackRateLimitError(SlackAPIError):
    """Rate limit exceeded."""

    def __init__(self, method: str, retry_after: int):
        self.retry_after = retry_after
        super().__init__(method, f"rate_limited (retry after {retry_after}s)")


@dataclass
class SlackClientConfig:
    """Configuration for the Slack client."""

    bot_token: str
    app_token: str | None = None
    signing_secret: str | None = None
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0


@dataclass
class SlackResponse:
    """Response from Slack API."""

    ok: bool
    data: dict[str, Any]
    error: str | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def cursor(self) -> str | None:
        """Get pagination cursor if present."""
        return self.response_metadata.get("next_cursor")


class SlackClient:
    """Async Slack API client using httpx."""

    def __init__(self, config: SlackClientConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._bot_user_id: str | None = None
        self._team_id: str | None = None

    async def __aenter__(self) -> "SlackClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE,
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Bearer {self.config.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        await self._fetch_bot_info()

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _fetch_bot_info(self) -> None:
        """Fetch bot user info on connect."""
        try:
            resp = await self._call("auth.test")
            self._bot_user_id = resp.data.get("user_id")
            self._team_id = resp.data.get("team_id")
            log.info(
                "Slack client connected: bot=%s team=%s",
                self._bot_user_id,
                self._team_id,
            )
        except SlackAPIError as e:
            log.error("Failed to connect to Slack: %s", e)
            raise

    @property
    def bot_user_id(self) -> str:
        """Get the bot user ID."""
        return self._bot_user_id or ""

    @property
    def team_id(self) -> str:
        """Get the team ID."""
        return self._team_id or ""

    async def _call(
        self,
        method: str,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> SlackResponse:
        """Make a Slack API call with retries."""
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() first.")

        last_error: Exception | None = None

        for attempt in range(self.config.max_retries):
            try:
                if files:
                    headers = {"Authorization": f"Bearer {self.config.bot_token}"}
                    response = await self._client.post(
                        f"/{method}",
                        data=data,
                        files=files,
                        headers=headers,
                    )
                else:
                    response = await self._client.post(f"/{method}", json=data or {})

                result = response.json()

                if result.get("ok"):
                    return SlackResponse(
                        ok=True,
                        data=result,
                        response_metadata=result.get("response_metadata", {}),
                    )

                error = result.get("error", "unknown_error")

                if error == "ratelimited":
                    retry_after = int(response.headers.get("Retry-After", 60))
                    raise SlackRateLimitError(method, retry_after)

                return SlackResponse(ok=False, data=result, error=error)

            except SlackRateLimitError:
                raise
            except httpx.TimeoutException as e:
                last_error = e
                log.warning(
                    "Slack API timeout (attempt %d/%d): %s",
                    attempt + 1,
                    self.config.max_retries,
                    method,
                )
            except httpx.HTTPError as e:
                last_error = e
                log.warning(
                    "Slack API HTTP error (attempt %d/%d): %s - %s",
                    attempt + 1,
                    self.config.max_retries,
                    method,
                    str(e),
                )

            if attempt < self.config.max_retries - 1:
                await self._sleep(self.config.retry_delay * (attempt + 1))

        raise SlackAPIError(method, f"max_retries_exceeded: {last_error}")

    async def _sleep(self, seconds: float) -> None:
        """Sleep for retry backoff."""
        import asyncio
        await asyncio.sleep(seconds)

    def verify_signature(
        self,
        signature: str,
        timestamp: str,
        body: bytes,
    ) -> bool:
        """Verify a Slack request signature."""
        if not self.config.signing_secret:
            log.warning("No signing secret configured, skipping verification")
            return True

        if abs(time.time() - int(timestamp)) > 300:
            return False

        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        computed = (
            "v0="
            + hmac.new(
                self.config.signing_secret.encode(),
                sig_basestring.encode(),
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(computed, signature)

    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
        reply_broadcast: bool = False,
        unfurl_links: bool = False,
        unfurl_media: bool = True,
        mrkdwn: bool = True,
    ) -> SlackResponse:
        """Send a message to a channel."""
        data: dict[str, Any] = {
            "channel": channel,
            "text": text,
            "unfurl_links": unfurl_links,
            "unfurl_media": unfurl_media,
            "mrkdwn": mrkdwn,
        }

        if blocks:
            data["blocks"] = blocks
        if thread_ts:
            data["thread_ts"] = thread_ts
            data["reply_broadcast"] = reply_broadcast

        resp = await self._call("chat.postMessage", data)

        if not resp.ok:
            raise SlackAPIError("chat.postMessage", resp.error or "unknown")

        log.info("Message sent: channel=%s ts=%s", channel, resp.data.get("ts"))
        return resp

    async def send_ephemeral(
        self,
        channel: str,
        user: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackResponse:
        """Send an ephemeral message visible only to one user."""
        data: dict[str, Any] = {
            "channel": channel,
            "user": user,
            "text": text,
        }

        if blocks:
            data["blocks"] = blocks

        resp = await self._call("chat.postEphemeral", data)

        if not resp.ok:
            raise SlackAPIError("chat.postEphemeral", resp.error or "unknown")

        return resp

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackResponse:
        """Update an existing message."""
        data: dict[str, Any] = {
            "channel": channel,
            "ts": ts,
            "text": text,
        }

        if blocks:
            data["blocks"] = blocks

        resp = await self._call("chat.update", data)

        if not resp.ok:
            raise SlackAPIError("chat.update", resp.error or "unknown")

        return resp

    async def delete_message(
        self,
        channel: str,
        ts: str,
    ) -> SlackResponse:
        """Delete a message."""
        resp = await self._call("chat.delete", {"channel": channel, "ts": ts})

        if not resp.ok:
            raise SlackAPIError("chat.delete", resp.error or "unknown")

        return resp

    async def add_reaction(
        self,
        channel: str,
        ts: str,
        name: str,
    ) -> SlackResponse:
        """Add a reaction to a message."""
        return await self._call(
            "reactions.add",
            {"channel": channel, "timestamp": ts, "name": name},
        )

    async def remove_reaction(
        self,
        channel: str,
        ts: str,
        name: str,
    ) -> SlackResponse:
        """Remove a reaction from a message."""
        return await self._call(
            "reactions.remove",
            {"channel": channel, "timestamp": ts, "name": name},
        )

    async def create_channel(
        self,
        name: str,
        is_private: bool = False,
    ) -> SlackResponse:
        """Create a new channel."""
        method = "conversations.create"
        data = {"name": name, "is_private": is_private}

        resp = await self._call(method, data)

        if not resp.ok:
            raise SlackAPIError(method, resp.error or "unknown")

        channel_id = resp.data.get("channel", {}).get("id")
        log.info("Channel created: name=%s id=%s", name, channel_id)
        return resp

    async def archive_channel(self, channel: str) -> SlackResponse:
        """Archive a channel."""
        resp = await self._call("conversations.archive", {"channel": channel})

        if not resp.ok:
            raise SlackAPIError("conversations.archive", resp.error or "unknown")

        return resp

    async def unarchive_channel(self, channel: str) -> SlackResponse:
        """Unarchive a channel."""
        resp = await self._call("conversations.unarchive", {"channel": channel})

        if not resp.ok:
            raise SlackAPIError("conversations.unarchive", resp.error or "unknown")

        return resp

    async def invite_to_channel(
        self,
        channel: str,
        users: list[str],
    ) -> SlackResponse:
        """Invite users to a channel."""
        resp = await self._call(
            "conversations.invite",
            {"channel": channel, "users": ",".join(users)},
        )

        if not resp.ok and resp.error != "already_in_channel":
            raise SlackAPIError("conversations.invite", resp.error or "unknown")

        return resp

    async def kick_from_channel(
        self,
        channel: str,
        user: str,
    ) -> SlackResponse:
        """Remove a user from a channel."""
        return await self._call(
            "conversations.kick",
            {"channel": channel, "user": user},
        )

    async def set_channel_topic(
        self,
        channel: str,
        topic: str,
    ) -> SlackResponse:
        """Set the channel topic."""
        return await self._call(
            "conversations.setTopic",
            {"channel": channel, "topic": topic},
        )

    async def set_channel_purpose(
        self,
        channel: str,
        purpose: str,
    ) -> SlackResponse:
        """Set the channel purpose."""
        return await self._call(
            "conversations.setPurpose",
            {"channel": channel, "purpose": purpose},
        )

    async def rename_channel(
        self,
        channel: str,
        name: str,
    ) -> SlackResponse:
        """Rename a channel."""
        return await self._call(
            "conversations.rename",
            {"channel": channel, "name": name},
        )

    async def get_channel_info(self, channel: str) -> SlackResponse:
        """Get channel information."""
        return await self._call("conversations.info", {"channel": channel})

    async def get_channel_history(
        self,
        channel: str,
        limit: int = 100,
        cursor: str | None = None,
        oldest: str | None = None,
        latest: str | None = None,
    ) -> SlackResponse:
        """Get channel message history."""
        data: dict[str, Any] = {"channel": channel, "limit": limit}

        if cursor:
            data["cursor"] = cursor
        if oldest:
            data["oldest"] = oldest
        if latest:
            data["latest"] = latest

        return await self._call("conversations.history", data)

    async def get_thread_replies(
        self,
        channel: str,
        ts: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SlackResponse:
        """Get thread replies."""
        data: dict[str, Any] = {"channel": channel, "ts": ts, "limit": limit}

        if cursor:
            data["cursor"] = cursor

        return await self._call("conversations.replies", data)

    async def list_channels(
        self,
        limit: int = 200,
        cursor: str | None = None,
        exclude_archived: bool = True,
        types: str = "public_channel,private_channel",
    ) -> SlackResponse:
        """List channels."""
        data: dict[str, Any] = {
            "limit": limit,
            "exclude_archived": exclude_archived,
            "types": types,
        }

        if cursor:
            data["cursor"] = cursor

        return await self._call("conversations.list", data)

    async def pin_message(
        self,
        channel: str,
        ts: str,
    ) -> SlackResponse:
        """Pin a message to a channel."""
        return await self._call("pins.add", {"channel": channel, "timestamp": ts})

    async def unpin_message(
        self,
        channel: str,
        ts: str,
    ) -> SlackResponse:
        """Unpin a message from a channel."""
        return await self._call("pins.remove", {"channel": channel, "timestamp": ts})

    async def list_pins(self, channel: str) -> SlackResponse:
        """List pinned messages in a channel."""
        return await self._call("pins.list", {"channel": channel})

    async def get_user_info(self, user: str) -> SlackResponse:
        """Get user information."""
        return await self._call("users.info", {"user": user})

    async def list_users(
        self,
        limit: int = 200,
        cursor: str | None = None,
    ) -> SlackResponse:
        """List workspace users."""
        data: dict[str, Any] = {"limit": limit}

        if cursor:
            data["cursor"] = cursor

        return await self._call("users.list", data)

    async def lookup_user_by_email(self, email: str) -> SlackResponse:
        """Look up a user by email."""
        return await self._call("users.lookupByEmail", {"email": email})

    async def open_modal(
        self,
        trigger_id: str,
        view: dict[str, Any],
    ) -> SlackResponse:
        """Open a modal view."""
        return await self._call("views.open", {"trigger_id": trigger_id, "view": view})

    async def update_modal(
        self,
        view_id: str,
        view: dict[str, Any],
        hash: str | None = None,
    ) -> SlackResponse:
        """Update a modal view."""
        data: dict[str, Any] = {"view_id": view_id, "view": view}

        if hash:
            data["hash"] = hash

        return await self._call("views.update", data)

    async def push_modal(
        self,
        trigger_id: str,
        view: dict[str, Any],
    ) -> SlackResponse:
        """Push a new modal view onto the stack."""
        return await self._call("views.push", {"trigger_id": trigger_id, "view": view})

    async def upload_file(
        self,
        channels: list[str],
        content: str | bytes,
        filename: str,
        title: str | None = None,
        initial_comment: str | None = None,
        thread_ts: str | None = None,
    ) -> SlackResponse:
        """Upload a file to Slack."""
        data: dict[str, Any] = {
            "channels": ",".join(channels),
            "filename": filename,
        }

        if title:
            data["title"] = title
        if initial_comment:
            data["initial_comment"] = initial_comment
        if thread_ts:
            data["thread_ts"] = thread_ts

        if isinstance(content, str):
            data["content"] = content
            resp = await self._call("files.upload", data)
        else:
            files = {"file": (filename, content)}
            resp = await self._call("files.upload", data, files=files)

        if not resp.ok:
            raise SlackAPIError("files.upload", resp.error or "unknown")

        return resp

    async def respond_to_interaction(
        self,
        response_url: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        replace_original: bool = False,
        delete_original: bool = False,
        response_type: str = "in_channel",
    ) -> None:
        """Respond to a Slack interaction via response_url."""
        if not self._client:
            raise RuntimeError("Client not connected")

        data: dict[str, Any] = {
            "text": text,
            "response_type": response_type,
            "replace_original": replace_original,
            "delete_original": delete_original,
        }

        if blocks:
            data["blocks"] = blocks

        await self._client.post(response_url, json=data)

    async def schedule_message(
        self,
        channel: str,
        text: str,
        post_at: int,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackResponse:
        """Schedule a message for later."""
        data: dict[str, Any] = {
            "channel": channel,
            "text": text,
            "post_at": post_at,
        }

        if blocks:
            data["blocks"] = blocks

        return await self._call("chat.scheduleMessage", data)

    async def delete_scheduled_message(
        self,
        channel: str,
        scheduled_message_id: str,
    ) -> SlackResponse:
        """Delete a scheduled message."""
        return await self._call(
            "chat.deleteScheduledMessage",
            {"channel": channel, "scheduled_message_id": scheduled_message_id},
        )

    async def set_status(
        self,
        status_text: str,
        status_emoji: str = "",
        expiration: int = 0,
    ) -> SlackResponse:
        """Set user status (for bot user)."""
        profile: dict[str, Any] = {
            "status_text": status_text,
            "status_emoji": status_emoji,
        }

        if expiration:
            profile["status_expiration"] = expiration

        return await self._call("users.profile.set", {"profile": profile})

    async def create_usergroup(
        self,
        name: str,
        handle: str,
        description: str = "",
        channels: list[str] | None = None,
    ) -> SlackResponse:
        """Create a user group."""
        data: dict[str, Any] = {
            "name": name,
            "handle": handle,
        }

        if description:
            data["description"] = description
        if channels:
            data["channels"] = ",".join(channels)

        return await self._call("usergroups.create", data)

    async def update_usergroup_members(
        self,
        usergroup: str,
        users: list[str],
    ) -> SlackResponse:
        """Update user group members."""
        return await self._call(
            "usergroups.users.update",
            {"usergroup": usergroup, "users": ",".join(users)},
        )

    async def search_messages(
        self,
        query: str,
        count: int = 20,
        page: int = 1,
        sort: str = "timestamp",
        sort_dir: str = "desc",
    ) -> SlackResponse:
        """Search messages (requires user token with search:read scope)."""
        return await self._call(
            "search.messages",
            {
                "query": query,
                "count": count,
                "page": page,
                "sort": sort,
                "sort_dir": sort_dir,
            },
        )

    async def join_channel(self, channel: str) -> SlackResponse:
        """Join a public channel."""
        return await self._call("conversations.join", {"channel": channel})

    async def leave_channel(self, channel: str) -> SlackResponse:
        """Leave a channel."""
        return await self._call("conversations.leave", {"channel": channel})


_client: SlackClient | None = None


async def get_slack_client() -> SlackClient:
    """Get the default Slack client instance."""
    global _client

    if _client is None:
        import os

        config = SlackClientConfig(
            bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            app_token=os.getenv("SLACK_APP_TOKEN"),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
        )

        _client = SlackClient(config)
        await _client.connect()

    return _client


async def close_slack_client() -> None:
    """Close the default Slack client."""
    global _client

    if _client:
        await _client.close()
        _client = None
