# slack-mcp

A **local, stdio** MCP server that lets Claude Desktop and Claude Code read, search,
and download files across **multiple Slack workspaces** at once — without
disconnecting and reconnecting Slack's built-in connector.

Each workspace uses a **user token (`xoxp-`)** you install yourself. No
"stealth"/`xoxc` browser tokens — safe for institutional or SSO-managed workspaces.

Every tool takes a required `workspace` argument naming an entry in your config.

## Tools

| Tool | What it does |
|------|--------------|
| `list_workspaces` | List configured workspaces (names + descriptions). Start here. |
| `search_messages(workspace, query, count, sort)` | Search messages (Slack operators, e.g. `in:#chan from:@me`). |
| `read_channel(workspace, channel, limit, oldest, latest)` | Channel history by ID or `#name`. |
| `read_thread(workspace, channel, thread_ts, limit)` | All replies in a thread. |
| `list_channels(workspace, name_filter, types, limit)` | List/resolve channels. |
| `get_user(workspace, user)` | Look up a user by ID or email. |
| `download_file(workspace, file_id)` | Download a file (see below). |
| `draft_message(workspace, channel, text)` | Preview a message; **never sends**. |
| `send_message(workspace, channel, text, confirm, thread_ts)` | Sends **only** with `confirm=True`; otherwise echoes the target. |

Messages include file metadata: `id`, `name`, `mimetype`, `size`, `filetype`.

`download_file` resolves the file via `files.info`, then fetches
`url_private_download` with a `Bearer` token:
- **Images** → returned **inline** (downscaled to ≤1568px long edge to stay under
  size limits) **and** a full-resolution copy saved to
  `~/slack-downloads/<workspace>/<channel>/`.
- **Other files** → saved to that same folder; the path is returned.

## 1. Install dependencies

```bash
cd ~/Projects/slack-mcp
uv sync
```

## 2. Create a Slack app per workspace

Do this once **per workspace** you want to access:

1. Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest**.
2. Select the target workspace.
3. Paste the contents of [`manifest.yaml`](manifest.yaml) and create the app.
4. Open **OAuth & Permissions** → **Install to Workspace** → approve the scopes.
   - If your workspace requires admin approval, request it; the app only needs the
     **user** scopes in the manifest (`search:read`, `*:history`, `*:read`,
     `files:read`, and `chat:write` for sending).
5. Copy the **User OAuth Token** — it starts with `xoxp-`.

The scopes requested (all **user** scopes, no bot token):
`search:read`, `channels:history`, `groups:history`, `im:history`, `mpim:history`,
`channels:read`, `groups:read`, `im:read`, `mpim:read`, `users:read`,
`users:read.email`, `files:read`, `chat:write`.

> Note: `search:read` and reading history only cover channels **your user account
> can already see**. Private channels/DMs require you to be a member.

## 3. Configure tokens

Tokens stay **out of the repo** in `~/.config/slack-mcp/workspaces.toml`:

```bash
mkdir -p ~/.config/slack-mcp
cp workspaces.example.toml ~/.config/slack-mcp/workspaces.toml
# edit in your xoxp- tokens, then lock the file down:
chmod 600 ~/.config/slack-mcp/workspaces.toml
```

```toml
[workspaces.workspace_a]
token = "xoxp-..."
description = "Example Workspace A"

[workspaces.workspace_b]
token = "xoxp-..."
description = "Example Workspace B"
```

The `<name>` of each table (`workspace_a`, `workspace_b`) is the `workspace` value you pass to the
tools. The server warns on stderr if the file isn't `600` or a token isn't `xoxp-`.

## 4. Register with Claude

### Claude Code (user scope)

```bash
claude mcp add --scope user slack-mcp -- uv --directory ~/Projects/slack-mcp run slack-mcp
```

### Claude Desktop (`claude_desktop_config.json`)

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "slack-mcp": {
      "command": "uv",
      "args": ["--directory", "/Users/you/Projects/slack-mcp", "run", "slack-mcp"]
    }
  }
}
```

> Claude Desktop launches with a minimal `PATH` and may not find `uv`. If the
> server fails to start, use the absolute path from `which uv` (e.g.
> `/opt/homebrew/bin/uv`) as `"command"`. Restart Claude Desktop after editing.

## 5. Smoke test

**Direct (fastest):** exercises the tools against your real tokens.

```bash
# list workspaces only
uv run python scripts/smoke_test.py

# list workspaces, resolve + read a channel, (optionally) download a file
uv run python scripts/smoke_test.py workspace_a '#general'
uv run python scripts/smoke_test.py workspace_a '#general' F0123ABCD
```

Expected: your workspaces print, the channel resolves to an ID, the last few
messages print (with any file metadata), and a provided image `file_id` downloads
to `~/slack-downloads/workspace_a/<channel>/` and returns an inline `Image`.

**In Claude** (after step 4), ask:

> Using slack-mcp, list my Slack workspaces. Then read the last 10 messages in
> `#general` on `workspace_a`, and download the most recent image there.

## Security notes

- Tokens live only in `~/.config/slack-mcp/workspaces.toml` (chmod 600), never in git.
- The server reads/writes Slack as **you**; it can only see what your account can.
- `send_message` requires an explicit `confirm=True`; by default it just echoes the
  resolved workspace + channel + text so you can verify before anything is posted.
- Downloads are written under `~/slack-downloads/` (git-ignored).
