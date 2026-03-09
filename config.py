"""Load ~/.daemon-git/config.toml and provide auth helpers."""

import base64
import re
import sys
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

CONFIG_PATH = Path.home() / ".daemon-git" / "config.toml"

DEFAULT_CONFIG = """\
[settings]
# How often to pull, in seconds
interval = 60

# Directories to scan for git repositories
[directories]
paths = [
    "C:\\\\Users\\\\yourname\\\\projects",
]

# Git credentials per host (for HTTPS remotes).
# Add one [[credentials]] section per account.
# For SSH remotes, credentials are not needed here.

[credentials."github.com"]
username = "your-github-username"
token    = "ghp_your_personal_access_token"

# [credentials."gitlab.com"]
# username = "your-gitlab-username"
# token    = "glpat_your_token"
"""


def load() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config not found: {CONFIG_PATH}\n"
            "Run 'daemon-git init' to create it, then edit it."
        )
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def init_config() -> None:
    """Write the default config template if it does not already exist."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding="utf-8")


def get_dirs(cfg: dict) -> list[str]:
    return cfg.get("directories", {}).get("paths", [])


def get_interval(cfg: dict) -> int:
    return int(cfg.get("settings", {}).get("interval", 60))


def _host(remote_url: str) -> Optional[str]:
    """Extract the hostname from an HTTPS or SSH remote URL."""
    m = re.match(r"^git@([^:]+):", remote_url)
    if m:
        return m.group(1).lower()
    try:
        h = urlparse(remote_url).hostname
        return h.lower() if h else None
    except Exception:
        return None


def auth_args(remote_url: str, cfg: dict) -> list[str]:
    """
    Return ["-c", "http.extraheader=Authorization: Basic <token>"] for HTTPS
    remotes whose host has credentials in the config. Returns [] otherwise.
    """
    if not remote_url or remote_url.startswith("git@"):
        return []
    host = _host(remote_url)
    if not host:
        return []
    creds = cfg.get("credentials", {}).get(host)
    if not creds:
        return []
    encoded = base64.b64encode(
        f"{creds['username']}:{creds['token']}".encode()
    ).decode()
    return ["-c", f"http.extraheader=Authorization: Basic {encoded}"]
