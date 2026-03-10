"""Load ~/.daemon-git/config.toml and provide auth helpers."""

import re
import subprocess
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

# Authentication is handled via the GitHub CLI.
# Run 'gh auth login' once before using daemon-git.
# No credentials need to be stored here.
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
    Return ["-c", "credential.helper=!gh auth git-credential"] for HTTPS
    remotes when the gh CLI is authenticated. Returns [] for SSH remotes
    or if gh is unavailable.
    """
    if not remote_url or remote_url.startswith("git@"):
        return []
    try:
        subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, timeout=5, check=True,
        )
    except Exception:
        return []
    return ["-c", "credential.helper=!gh auth git-credential"]
