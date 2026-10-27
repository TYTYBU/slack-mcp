#!/usr/bin/env python3
"""Quick smoke test that exercises the tools directly (no MCP transport needed).

It reads your real ~/.config/slack-mcp/workspaces.toml, so it makes live Slack
calls with your user token.

Usage:
    uv run python scripts/smoke_test.py                       # list workspaces only
    uv run python scripts/smoke_test.py <workspace> <#channel> [file_id]

Example:
    uv run python scripts/smoke_test.py workspace_a '#general'
    uv run python scripts/smoke_test.py workspace_a '#general' F0123ABCD
"""

from __future__ import annotations

import sys

from slack_mcp.server import (
    download_file,
    list_channels,
    list_workspaces,
    read_channel,
)


def main() -> None:
    print("== list_workspaces ==")
    for ws in list_workspaces():
        print(f"  {ws['workspace']}: {ws['description']}")

    if len(sys.argv) < 3:
        print("\n(Pass <workspace> <#channel> [file_id] to test reading/downloading.)")
        return

    workspace, channel = sys.argv[1], sys.argv[2]

    print(f"\n== list_channels({workspace}, name_filter={channel!r}) ==")
    chans = list_channels(workspace, name_filter=channel.lstrip("#"), limit=5)
    for c in chans["channels"]:
        print(f"  {c['id']}  #{c['name']}  (member={c['is_member']})")

    print(f"\n== read_channel({workspace}, {channel}, limit=5) ==")
    hist = read_channel(workspace, channel, limit=5)
    for m in hist["messages"]:
        text = (m.get("text") or "").replace("\n", " ")[:80]
        files = m.get("files")
        print(f"  [{m['ts']}] {m.get('user')}: {text}")
        if files:
            for f in files:
                print(f"       file {f['id']} {f['name']} ({f['mimetype']}, {f['size']} bytes)")

    if len(sys.argv) >= 4:
        file_id = sys.argv[3]
        print(f"\n== download_file({workspace}, {file_id}) ==")
        for part in download_file(workspace, file_id):
            # Image parts are mcp Image objects; everything else is a string.
            print("  ", type(part).__name__ if not isinstance(part, str) else part)


if __name__ == "__main__":
    main()
