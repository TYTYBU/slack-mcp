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
| `check_access(workspace)` | Report which OAuth scopes a token has and which tools work. |
| `search_messages(workspace, query, count, sort)` | Search messages (Slack operators, e.g. `in:#chan from:@me`). |
| `read_channel(workspace, channel, limit, oldest, latest)` | Channel history by ID or `#name`. |
| `read_thread(workspace, channel, thread_ts, limit)` | All replies in a thread. |
| `list_channels(workspace, name_filter, types, limit)` | List/resolve channels. |
| `get_user(workspace, user)` | Look up a user by ID or email. |
| `download_file(workspace, file_id)` | Download a file (see below). |
| `draft_message(workspace, channel, text)` | Preview a message; **never sends**. |
| `send_message(workspace, channel, text, confirm, thread_ts, file_path, file_title)` | Sends **only** with `confirm=True`; otherwise echoes the target. Pass `file_path` to attach a file. |

Messages include file metadata: `id`, `name`, `mimetype`, `size`, `filetype`.

`download_file` resolves the file via `files.info`, then fetches
`url_private_download` with a `Bearer` token:
- **Images** → returned **inline** (downscaled to ≤1568px long edge to stay under
  size limits) **and** a full-resolution copy saved to
  `~/slack-downloads/<workspace>/<channel>/`.
- **Other files** → saved to that same folder; the path is returned.

**Channels, DMs, and users.** The `channel` argument accepts a channel ID
(`C…`/`G…`), a DM/IM ID (`D…`), a `#name`, or a **user ID** (`U…`/`W…`). Passing a
user ID to `send_message` opens a direct message to that person — pass **your own**
user ID to DM yourself (get it from `get_user(workspace, "you@example.com")`).

**Attachments.** Give `send_message` a `file_path` to upload a local file. If `text`
is also present it becomes the file's comment, so one call posts the message and the
attachment together; `confirm=True` is still required. Uploading needs the
`files:write` scope (and `im:write` to attach to a DM addressed by a user ID) — see
the reinstall note below.

**Scope awareness.** The server reads each token's granted scopes from Slack's
`x-oauth-scopes` header (cached per workspace) and checks them before each call. If a
scope is missing it returns a clear, actionable message naming the scope and workspace
instead of a cryptic Slack `missing_scope` error — and only that workspace/tool is
affected; the others keep working. `check_access(workspace)` reports the full picture.

---

## Prerequisites

- macOS or Linux
- [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)
- Python ≥ 3.11 (uv will fetch one if needed)
- A **member** account on each Slack workspace you want to access

## 1. Get the code + install dependencies

```bash
git clone https://github.com/TYTYBU/slack-mcp.git ~/Projects/slack-mcp
cd ~/Projects/slack-mcp
uv sync
```

## 2. Create a Slack app — once **per workspace**

A Slack app is tied to the workspace it's created in, so repeat this for each
workspace (selecting the right one in the picker each time).

1. Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest**.
2. **Select the target workspace** in the dropdown. ← the only thing that differs between workspaces.
3. Paste the contents of [`manifest.yaml`](manifest.yaml) and continue.
4. On the review screen click **Create and Install**, then authorize.
   - If the workspace requires admin approval, you'll be prompted to request it.
5. Left sidebar → **OAuth & Permissions** → copy the **User OAuth Token** (starts with `xoxp-`).

The scopes requested (all **user** scopes, no bot token):
`search:read`, `channels:history`, `groups:history`, `im:history`, `mpim:history`,
`channels:read`, `groups:read`, `im:read`, `mpim:read`, `users:read`,
`users:read.email`, `files:read`, `chat:write`, `files:write`, `im:write`.

The last two (`files:write`, `im:write`) are only needed to **upload attachments**
with `send_message(file_path=…)`. Everything else works without them.

> **Which token?** You want the **User OAuth Token** (`xoxp-`) only.
> - ❌ **Don't** generate an *app-level token* (`xapp-`) — that's for Socket Mode,
>   which this server doesn't use (the manifest sets `socket_mode_enabled: false`).
> - ❌ Not a *bot token* (`xoxb-`), not a browser token (`xoxc-`/`xoxd-`).
>
> On the review screen, **"What it can do in Slack (15)"** = the 15 scopes above;
> **"What it can respond to in Slack (0)"** = event subscriptions, which this
> server doesn't use — `0` is correct.
>
> `search:read` and reading history only cover channels **your account can already
> see** — private channels/DMs require you to be a member.

> **Adding scopes to an app you already installed** (e.g. turning on attachments
> later): open the app at <https://api.slack.com/apps> → **App Manifest**, paste the
> updated `manifest.yaml`, save, then **OAuth & Permissions → Reinstall to Workspace**.
> The new scopes attach to your existing token — with token rotation off (this
> manifest), the `xoxp-` token **doesn't change**, so there's usually nothing to edit
> in `workspaces.toml`. Just run `check_access(workspace)` to confirm the scope is now
> present; only update the token if it actually changed. Do this per workspace. Until
> you reinstall, `check_access` shows the scope as missing and upload calls fail with a
> clear message — read-only tools keep working.

## 3. Configure tokens

Tokens stay **out of the repo** in `~/.config/slack-mcp/workspaces.toml` (the repo
only ships `workspaces.example.toml` as a template):

```bash
mkdir -p ~/.config/slack-mcp
cp workspaces.example.toml ~/.config/slack-mcp/workspaces.toml
# edit in your xoxp- tokens, then lock the file down:
chmod 600 ~/.config/slack-mcp/workspaces.toml
```

```toml
[workspaces.myworkspace]
token = "xoxp-..."
description = "My Workspace"       # optional, for your reference

[workspaces.another-lab]
token = "xoxp-..."
description = "Another Lab"
```

- The table name (`myworkspace`, `another-lab`) is the `workspace` value you pass to
  the tools — keep it short; letters, digits, `_` and `-` are allowed.
- One block per workspace; names must be unique.
- The server warns on stderr if the file isn't `600` or a token isn't `xoxp-`.

> Config path can be overridden with the `SLACK_MCP_CONFIG` environment variable.

## 4. Register with Claude

### Claude Code (available in every project)

```bash
claude mcp add --scope user slack-mcp -- uv --directory ~/Projects/slack-mcp run slack-mcp
```

Verify:

```bash
claude mcp list        # should show: slack-mcp ... ✔ Connected
```

`--scope user` makes it available in **all** your projects/sessions. A session that
was already open won't see it until you **start a fresh session**.

### Claude Desktop (`claude_desktop_config.json`)

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "slack-mcp": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/you/Projects/slack-mcp", "run", "slack-mcp"]
    }
  }
}
```

> Claude Desktop launches with a minimal `PATH` and usually **can't find `uv`** by
> name, so use its absolute path — run `which uv` (Apple Silicon Homebrew:
> `/opt/homebrew/bin/uv`; Intel: `/usr/local/bin/uv`). Use an absolute `--directory`
> too. After editing, **fully quit Claude Desktop (⌘Q) and reopen it** — closing the
> window isn't enough.

## 5. Smoke test

**Direct (fastest):** exercises the tools against your real tokens, no Claude needed.

```bash
# list workspaces only (no network)
uv run python scripts/smoke_test.py

# list workspaces, resolve + read a channel, (optionally) download a file
uv run python scripts/smoke_test.py myworkspace '#general'
uv run python scripts/smoke_test.py myworkspace '#general' F0123ABCD
```

Expected: your workspaces print, the channel resolves to an ID, the last few
messages print (with any file metadata), and a provided image `file_id` downloads
to `~/slack-downloads/myworkspace/<channel>/` and returns an inline `Image`.
(An empty/unused channel prints no messages — that's not an error.)

**In Claude** (after step 4), ask:

> Using slack-mcp, list my Slack workspaces. Then read the last 10 messages in
> `#general` on `myworkspace`, and download the most recent image there.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Slack API error: invalid_auth` / `not_authed` | Wrong/expired token, or you pasted an `xapp-`/`xoxb-` token. Use the **User OAuth Token** (`xoxp-`). |
| `Slack API error: missing_scope` | App installed before a scope was added. Re-paste the manifest / reinstall the app; the token usually stays the same (token rotation is off), so just re-run `check_access` — only update `workspaces.toml` if it changed. |
| `Channel '…' not found` | Not a member, wrong name, or private. Use `list_channels` to find the ID, and join the channel. |
| `read_channel` returns 0 messages, no error | The channel is genuinely empty (common for unused `#general`). Try an active channel. |
| Tool doesn't appear in Claude | Start a **new** Claude Code session; for Desktop, **⌘Q and reopen**. |
| Desktop server fails to start | Use the **absolute** `uv` path in `claude_desktop_config.json` (`which uv`). |
| `Config file not found` | Create `~/.config/slack-mcp/workspaces.toml` (step 3), or set `SLACK_MCP_CONFIG`. |

## Using alongside the official Slack connector

Both can be enabled at once — tool names don't collide, so nothing breaks. The only
ambiguity is which Claude picks for a generic "check Slack" request. Name the server
explicitly ("using **slack-mcp**, …") when you want multi-workspace access or file
downloads, or disable the official connector if this one covers your needs.

## Security notes

- Tokens live only in `~/.config/slack-mcp/workspaces.toml` (chmod 600), never in git.
- The server reads/writes Slack as **you**; it can only see what your account can.
- `send_message` requires an explicit `confirm=True`; by default it just echoes the
  resolved workspace + channel + text so you can verify before anything is posted.
- Downloads are written under `~/slack-downloads/` (git-ignored).
