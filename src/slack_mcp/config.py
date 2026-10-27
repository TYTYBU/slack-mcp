"""Workspace configuration loading.

Tokens live in ~/.config/slack-mcp/workspaces.toml (never in the repo).
The file should be chmod 600. Example:

    [workspaces.workspace_a]
    token = "xoxp-..."
    description = "Example Workspace A"   # optional

    [workspaces.workspace_b]
    token = "xoxp-..."
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("SLACK_MCP_CONFIG", Path.home() / ".config" / "slack-mcp" / "workspaces.toml")
)


@dataclass(frozen=True)
class Workspace:
    name: str
    token: str
    description: str = ""


class ConfigError(RuntimeError):
    """Raised when the config file is missing, malformed, or has bad permissions."""


@lru_cache(maxsize=1)
def load_workspaces() -> dict[str, Workspace]:
    """Load and cache the workspace table from the TOML config file."""
    if not CONFIG_PATH.exists():
        raise ConfigError(
            f"Config file not found: {CONFIG_PATH}\n"
            "Create it with a [workspaces.<name>] table per workspace, e.g.:\n\n"
            '    [workspaces.myworkspace]\n    token = "xoxp-..."\n\n'
            "Then: chmod 600 " + str(CONFIG_PATH)
        )

    # Warn (to stderr, never stdout — stdout is the MCP transport) if the file is
    # readable by group/other. Tokens are secrets.
    mode = CONFIG_PATH.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(
            f"[slack-mcp] WARNING: {CONFIG_PATH} is accessible by group/other. "
            f"Run: chmod 600 {CONFIG_PATH}",
            file=sys.stderr,
        )

    with CONFIG_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    raw = data.get("workspaces")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(
            f"No [workspaces.<name>] tables found in {CONFIG_PATH}. "
            "Add at least one, e.g. [workspaces.myworkspace] with a token."
        )

    workspaces: dict[str, Workspace] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"[workspaces.{name}] must be a table, got {type(entry).__name__}.")
        token = entry.get("token")
        if not token or not isinstance(token, str):
            raise ConfigError(f"[workspaces.{name}] is missing a string 'token'.")
        if not token.startswith("xoxp-"):
            # Not fatal, but this server expects user tokens (xoxp-).
            print(
                f"[slack-mcp] WARNING: token for workspace '{name}' does not start with "
                "'xoxp-' (expected a user token).",
                file=sys.stderr,
            )
        workspaces[name] = Workspace(
            name=name,
            token=token,
            description=str(entry.get("description", "")),
        )
    return workspaces


def get_workspace(name: str) -> Workspace:
    """Return a single workspace by name, or raise a helpful ConfigError."""
    workspaces = load_workspaces()
    if name not in workspaces:
        available = ", ".join(sorted(workspaces)) or "(none)"
        raise ConfigError(
            f"Unknown workspace '{name}'. Configured workspaces: {available}. "
            "Use the list_workspaces tool to see them."
        )
    return workspaces[name]
