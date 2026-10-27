"""slack-mcp: a local stdio MCP server for multiple Slack workspaces.

Every tool takes a required `workspace` argument naming a workspace configured in
~/.config/slack-mcp/workspaces.toml. Read tools cover search, channel history,
threads, and user/channel lookup; messages carry file metadata. `download_file`
fetches a file's bytes with a Bearer token: images come back inline (downscaled)
plus a full-resolution copy on disk, other files are saved and the path returned.
Optional write tools draft and send messages, echoing the target before sending.
"""

from __future__ import annotations

import functools
import io
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_mcp.config import ConfigError, get_workspace, load_workspaces

mcp = FastMCP("slack-mcp")

# ---------------------------------------------------------------------------
# Download / image tuning
# ---------------------------------------------------------------------------
DOWNLOAD_ROOT = Path.home() / "slack-downloads"
MAX_IMAGE_EDGE = 1568  # longest-edge px for the inline (not the saved) image
MAX_INLINE_BYTES = 2_500_000  # re-encode target so base64 stays well under API caps
HTTP_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Client + resolver helpers
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _client(workspace: str) -> WebClient:
    """Return a cached WebClient for the given workspace's user token."""
    ws = get_workspace(workspace)
    return WebClient(token=ws.token)


# channel-name -> id cache, keyed by (workspace, lowercased name)
_channel_cache: dict[tuple[str, str], str] = {}

_ID_RE = re.compile(r"^[CGD][A-Z0-9]{6,}$")


def _resolve_channel(workspace: str, channel: str) -> str:
    """Accept a channel ID (C…/G…/D…) or a #name/name and return the channel ID."""
    channel = channel.strip()
    if _ID_RE.match(channel):
        return channel
    name = channel.lstrip("#").lower()
    key = (workspace, name)
    if key in _channel_cache:
        return _channel_cache[key]

    client = _client(workspace)
    cursor = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel,mpim,im",
            limit=1000,
            cursor=cursor,
            exclude_archived=False,
        )
        for ch in resp.get("channels", []):
            cname = ch.get("name", "").lower()
            if cname:
                _channel_cache[(workspace, cname)] = ch["id"]
        if key in _channel_cache:
            return _channel_cache[key]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    raise ValueError(
        f"Channel '{channel}' not found in workspace '{workspace}'. "
        "Pass a channel ID or an exact channel name, or use list_channels."
    )


def _sanitize(name: str) -> str:
    """Make a filesystem-safe filename component."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "file"


def _file_meta(f: dict[str, Any]) -> dict[str, Any]:
    """Compact file metadata as required by the spec: id, name, mimetype, size."""
    return {
        "id": f.get("id"),
        "name": f.get("name") or f.get("title"),
        "mimetype": f.get("mimetype"),
        "size": f.get("size"),
        "filetype": f.get("filetype"),
    }


def _format_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Slack message into a compact dict, including file metadata."""
    out: dict[str, Any] = {
        "ts": msg.get("ts"),
        "user": msg.get("user") or msg.get("bot_id"),
        "username": msg.get("username"),
        "text": msg.get("text", ""),
        "type": msg.get("type"),
        "subtype": msg.get("subtype"),
    }
    if msg.get("thread_ts"):
        out["thread_ts"] = msg["thread_ts"]
        out["reply_count"] = msg.get("reply_count")
    files = msg.get("files")
    if files:
        out["files"] = [_file_meta(f) for f in files]
    return out


def _wrap_slack_errors(fn):
    """Turn SlackApiError / ConfigError into clean ValueErrors for the MCP client.

    Uses functools.wraps so FastMCP still introspects the real signature/annotations
    (otherwise every tool would expose only *args/**kwargs).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SlackApiError as exc:
            err = exc.response.get("error", "unknown_error")
            raise ValueError(f"Slack API error: {err}") from exc
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc

    return wrapper


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------
@mcp.tool()
def list_workspaces() -> list[dict[str, str]]:
    """List the Slack workspaces configured in workspaces.toml (names + descriptions).

    Call this first to discover the `workspace` values accepted by every other tool.
    Tokens are never returned.
    """
    return [
        {"workspace": ws.name, "description": ws.description}
        for ws in sorted(load_workspaces().values(), key=lambda w: w.name)
    ]


@mcp.tool()
@_wrap_slack_errors
def search_messages(
    workspace: str,
    query: str,
    count: int = 20,
    sort: str = "score",
) -> dict[str, Any]:
    """Search messages across a workspace (Slack search operators supported).

    Args:
        workspace: configured workspace name.
        query: Slack search query, e.g. 'in:#general from:@alice variant calling'.
        count: max matches to return (1-100).
        sort: 'score' (relevance) or 'timestamp' (newest first).
    """
    client = _client(workspace)
    resp = client.search_messages(query=query, count=max(1, min(count, 100)), sort=sort)
    matches = resp.get("messages", {}).get("matches", [])
    results = []
    for m in matches:
        item = _format_message(m)
        ch = m.get("channel") or {}
        item["channel"] = {"id": ch.get("id"), "name": ch.get("name")}
        item["permalink"] = m.get("permalink")
        results.append(item)
    return {
        "workspace": workspace,
        "query": query,
        "total": resp.get("messages", {}).get("total"),
        "matches": results,
    }


@mcp.tool()
@_wrap_slack_errors
def read_channel(
    workspace: str,
    channel: str,
    limit: int = 50,
    oldest: str | None = None,
    latest: str | None = None,
) -> dict[str, Any]:
    """Read recent message history from a channel (by ID or #name).

    Args:
        workspace: configured workspace name.
        channel: channel ID (C…/G…/D…) or a name like '#general'.
        limit: max messages, newest first (1-200).
        oldest: optional lower-bound Slack ts (inclusive).
        latest: optional upper-bound Slack ts.
    """
    client = _client(workspace)
    channel_id = _resolve_channel(workspace, channel)
    resp = client.conversations_history(
        channel=channel_id,
        limit=max(1, min(limit, 200)),
        oldest=oldest,
        latest=latest,
    )
    return {
        "workspace": workspace,
        "channel": channel_id,
        "has_more": resp.get("has_more", False),
        "messages": [_format_message(m) for m in resp.get("messages", [])],
    }


@mcp.tool()
@_wrap_slack_errors
def read_thread(
    workspace: str,
    channel: str,
    thread_ts: str,
    limit: int = 200,
) -> dict[str, Any]:
    """Read all replies in a thread.

    Args:
        workspace: configured workspace name.
        channel: channel ID (C…/G…/D…) or a name like '#general'.
        thread_ts: the ts of the thread's parent message.
        limit: max messages (1-1000).
    """
    client = _client(workspace)
    channel_id = _resolve_channel(workspace, channel)
    resp = client.conversations_replies(
        channel=channel_id,
        ts=thread_ts,
        limit=max(1, min(limit, 1000)),
    )
    return {
        "workspace": workspace,
        "channel": channel_id,
        "thread_ts": thread_ts,
        "messages": [_format_message(m) for m in resp.get("messages", [])],
    }


@mcp.tool()
@_wrap_slack_errors
def list_channels(
    workspace: str,
    name_filter: str = "",
    types: str = "public_channel,private_channel",
    limit: int = 200,
) -> dict[str, Any]:
    """List channels in a workspace, optionally filtered by a name substring.

    Args:
        workspace: configured workspace name.
        name_filter: case-insensitive substring to match channel names (empty = all).
        types: comma-separated: public_channel, private_channel, mpim, im.
        limit: max channels to return after filtering (1-1000).
    """
    client = _client(workspace)
    needle = name_filter.lower().lstrip("#")
    out: list[dict[str, Any]] = []
    cursor = None
    while len(out) < limit:
        resp = client.conversations_list(types=types, limit=1000, cursor=cursor)
        for ch in resp.get("channels", []):
            cname = ch.get("name", "")
            if needle and needle not in cname.lower():
                continue
            out.append(
                {
                    "id": ch.get("id"),
                    "name": cname,
                    "is_private": ch.get("is_private"),
                    "is_member": ch.get("is_member"),
                    "num_members": ch.get("num_members"),
                    "topic": (ch.get("topic") or {}).get("value", ""),
                }
            )
            if len(out) >= limit:
                break
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return {"workspace": workspace, "count": len(out), "channels": out}


@mcp.tool()
@_wrap_slack_errors
def get_user(workspace: str, user: str) -> dict[str, Any]:
    """Look up a user by Slack ID (U…) or email address.

    Args:
        workspace: configured workspace name.
        user: a user ID (U…/W…) or an email address.
    """
    client = _client(workspace)
    if "@" in user:
        resp = client.users_lookupByEmail(email=user)
    else:
        resp = client.users_info(user=user)
    u = resp["user"]
    p = u.get("profile", {})
    return {
        "id": u.get("id"),
        "name": u.get("name"),
        "real_name": u.get("real_name") or p.get("real_name"),
        "display_name": p.get("display_name"),
        "email": p.get("email"),
        "title": p.get("title"),
        "is_bot": u.get("is_bot"),
        "deleted": u.get("deleted"),
        "tz": u.get("tz"),
    }


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------
def _downscaled_image(raw: bytes) -> tuple[bytes, str]:
    """Downscale raw image bytes to <= MAX_IMAGE_EDGE and re-encode for inline use.

    Returns (encoded_bytes, format) where format is 'jpeg' or 'png'.
    """
    img = PILImage.open(io.BytesIO(raw))
    img.load()
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

    longest = max(img.size)
    if longest > MAX_IMAGE_EDGE:
        scale = MAX_IMAGE_EDGE / longest
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, PILImage.LANCZOS)

    buf = io.BytesIO()
    if has_alpha:
        img.convert("RGBA").save(buf, format="PNG", optimize=True)
        fmt = "png"
    else:
        quality = 85
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        # Step quality down if the encoded image is still too large.
        while buf.tell() > MAX_INLINE_BYTES and quality > 40:
            quality -= 15
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        fmt = "jpeg"
    return buf.getvalue(), fmt


@mcp.tool()
@_wrap_slack_errors
def download_file(workspace: str, file_id: str) -> list[Any]:
    """Download a Slack file by its file ID.

    Resolves the file via files.info, then fetches url_private_download with the
    workspace's Bearer token. Images are returned inline (downscaled to fit size
    limits) AND a full-resolution copy is saved to disk. Other files are saved to
    ~/slack-downloads/<workspace>/<channel>/ and the path is returned.

    Args:
        workspace: configured workspace name.
        file_id: the Slack file ID (e.g. 'F0123ABCD'), as found in message file metadata.
    """
    ws = get_workspace(workspace)
    client = _client(workspace)
    info = client.files_info(file=file_id)
    f = info["file"]

    url = f.get("url_private_download") or f.get("url_private")
    if not url:
        raise ValueError(f"File {file_id} has no downloadable URL (it may be external).")

    name = _sanitize(f.get("name") or f.get("title") or file_id)
    mimetype = f.get("mimetype") or "application/octet-stream"
    channels = f.get("channels") or f.get("groups") or f.get("ims") or []
    channel_dir = _sanitize(channels[0]) if channels else "no-channel"

    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as http:
        resp = http.get(url, headers={"Authorization": f"Bearer {ws.token}"})
        resp.raise_for_status()
        raw = resp.content

    dest_dir = DOWNLOAD_ROOT / _sanitize(workspace) / channel_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Prefix with file_id to avoid collisions between same-named files.
    dest = dest_dir / f"{file_id}_{name}"
    dest.write_bytes(raw)

    meta = _file_meta(f)
    meta["saved_path"] = str(dest)
    meta["bytes_downloaded"] = len(raw)

    if mimetype.startswith("image/"):
        try:
            inline_bytes, fmt = _downscaled_image(raw)
            return [
                Image(data=inline_bytes, format=fmt),
                f"Full-resolution image saved to: {dest}\nmetadata: {meta}",
            ]
        except Exception as exc:  # noqa: BLE001 - fall back to path-only on decode issues
            return [
                f"Image saved to: {dest} (could not build inline preview: {exc})\n"
                f"metadata: {meta}"
            ]

    return [f"File saved to: {dest}\nmetadata: {meta}"]


# ---------------------------------------------------------------------------
# Write tools (draft / send) — send echoes the target and requires confirm=True
# ---------------------------------------------------------------------------
@mcp.tool()
@_wrap_slack_errors
def draft_message(workspace: str, channel: str, text: str) -> dict[str, Any]:
    """Preview a message WITHOUT sending it. Resolves the channel and echoes the target.

    Use this to review exactly where a message would go before calling send_message.

    Args:
        workspace: configured workspace name.
        channel: channel ID (C…/G…/D…) or a name like '#general'.
        text: the message body (Slack mrkdwn).
    """
    channel_id = _resolve_channel(workspace, channel)
    return {
        "draft": True,
        "sent": False,
        "workspace": workspace,
        "channel_input": channel,
        "channel_id": channel_id,
        "text": text,
        "note": "Nothing was sent. To send, call send_message with confirm=True.",
    }


@mcp.tool()
@_wrap_slack_errors
def send_message(
    workspace: str,
    channel: str,
    text: str,
    confirm: bool = False,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Send a message to a channel — ONLY when confirm=True.

    With confirm=False (the default) nothing is sent: the tool echoes back the
    workspace, resolved channel, and text so they can be verified first. Call again
    with confirm=True to actually post.

    Args:
        workspace: configured workspace name.
        channel: channel ID (C…/G…/D…) or a name like '#general'.
        text: the message body (Slack mrkdwn).
        confirm: must be True to actually send; False returns a preview only.
        thread_ts: optional parent ts to reply in a thread.
    """
    channel_id = _resolve_channel(workspace, channel)
    preview = {
        "workspace": workspace,
        "channel_input": channel,
        "channel_id": channel_id,
        "text": text,
        "thread_ts": thread_ts,
    }
    if not confirm:
        return {
            "sent": False,
            "needs_confirmation": True,
            **preview,
            "note": "Preview only — re-call with confirm=True to send this message.",
        }

    client = _client(workspace)
    resp = client.chat_postMessage(channel=channel_id, text=text, thread_ts=thread_ts)
    return {
        "sent": True,
        **preview,
        "ts": resp.get("ts"),
        "permalink": None,
    }


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
