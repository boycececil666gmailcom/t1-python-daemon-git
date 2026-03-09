"""daemon-git — pull all local git repos on a schedule."""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

import click

import config as cfg_mod

_CONFLICT_WORDS = (
    "conflict",
    "automatic merge failed",
    "error: your local changes",
    "please commit your changes or stash",
    "would be overwritten",
)

_SKIP_DIRS = {
    "$recycle.bin", "system volume information", "windows",
    "program files", "program files (x86)", "programdata",
}


# ── core helpers ───────────────────────────────────────────

def _find_repos(roots: list[str]) -> list[str]:
    """Walk each root directory and return paths of git repositories."""
    repos = []
    for root in roots:
        for dirpath, dirnames, _ in os.walk(root, topdown=True, onerror=lambda e: None):
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in _SKIP_DIRS and not d.startswith(".")
            ]
            if os.path.isdir(os.path.join(dirpath, ".git")):
                repos.append(dirpath)
                dirnames.clear()  # don't recurse into nested repos
    return repos


def _remote_url(repo: str) -> str:
    r = subprocess.run(
        ["git", "-C", repo, "remote", "get-url", "origin"],
        capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def _pull(repo: str, cfg: dict) -> str:
    """
    Pull a single repo. Returns one of:
      'ok'         — new commits fetched
      'current'    — already up to date
      'conflict'   — clash detected, nothing done
      'error: …'   — any other failure
    """
    remote = _remote_url(repo)
    extra = cfg_mod.auth_args(remote, cfg) if remote else []

    try:
        r = subprocess.run(
            ["git", "-C", repo] + extra + ["pull", "--ff-only"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "error: timed out"
    except Exception as e:
        return f"error: {e}"

    out = (r.stdout + r.stderr).lower()

    if any(w in out for w in _CONFLICT_WORDS):
        return "conflict"
    if r.returncode != 0:
        return f"error: exit {r.returncode}"
    if "already up to date" in out:
        return "current"
    return "ok"


def _sync_all(cfg: dict, verbose: bool = False) -> None:
    roots = cfg_mod.get_dirs(cfg)
    if not roots:
        click.echo("No directories in config. Edit ~/.daemon-git/config.toml")
        return

    # Clone any missing GitHub repos into the first configured directory
    _ensure_cloned(cfg, roots[0])

    repos = _find_repos(roots)
    if not repos:
        click.echo("No git repos found in configured directories.")
        return

    counts = {"ok": 0, "current": 0, "conflict": 0, "error": 0}
    for repo in repos:
        status = _pull(repo, cfg)
        key = status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        if verbose or key not in ("ok", "current"):
            colour = {"ok": "green", "current": "cyan",
                      "conflict": "yellow"}.get(key, "red")
            click.echo(click.style(f"  [{key}]", fg=colour) + f" {repo}")

    click.echo(
        click.style(f"{counts['ok']} updated", fg="green") + ", " +
        click.style(f"{counts['current']} current", fg="cyan") + ", " +
        click.style(f"{counts['conflict']} conflict(s) skipped", fg="yellow") + ", " +
        click.style(f"{counts.get('error', 0)} error(s)", fg="red")
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ── GitHub helpers ────────────────────────────────────────

def _github_creds(cfg: dict) -> tuple[str, str] | None:
    """Return (username, token) for github.com if configured."""
    creds = cfg.get("credentials", {}).get("github.com")
    if creds:
        return creds["username"], creds["token"]
    return None


def _list_github_repos(username: str, token: str) -> list[dict]:
    """Fetch all repos owned by *username* from the GitHub API."""
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/user/repos?per_page=100&page={page}&affiliation=owner"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            click.echo(click.style(f"  GitHub API error: {e}", fg="red"))
            break
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def _ensure_cloned(cfg: dict, dest_root: str) -> None:
    """Clone any GitHub repos not yet present in *dest_root*."""
    creds = _github_creds(cfg)
    if not creds:
        return
    username, token = creds

    click.echo(f"Checking GitHub repos for {username}…")
    remote_repos = _list_github_repos(username, token)
    if not remote_repos:
        return

    # Build a set of clone URLs already present locally
    existing = _find_repos([dest_root])
    existing_urls = set()
    for r in existing:
        url = _remote_url(r)
        if url:
            # Normalise: strip trailing .git and lowercase
            existing_urls.add(url.rstrip("/").removesuffix(".git").lower())

    cloned = skipped = 0
    for repo in remote_repos:
        clone_url = repo["clone_url"]  # HTTPS
        normalised = clone_url.rstrip("/").removesuffix(".git").lower()
        if normalised in existing_urls:
            continue
        name = repo["name"]
        target = os.path.join(dest_root, name)
        if os.path.exists(target):
            skipped += 1
            continue
        click.echo(f"  Cloning {name}…")
        # Inject credentials into the clone URL
        auth_url = clone_url.replace("https://", f"https://{username}:{token}@")
        r = subprocess.run(
            ["git", "clone", auth_url, target],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0:
            click.echo(click.style(f"  ✔ Cloned {name}", fg="green"))
            cloned += 1
        else:
            click.echo(click.style(f"  ✘ Failed to clone {name}: {r.stderr.strip()}", fg="red"))

    click.echo(f"  {cloned} cloned, {skipped} skipped (folder exists).")


@click.group()
def cli():
    """daemon-git — auto-sync local git repos on a schedule."""


@cli.command()
def init():
    """Create the default config at ~/.daemon-git/config.toml."""
    if cfg_mod.CONFIG_PATH.exists():
        click.echo(f"Config already exists: {cfg_mod.CONFIG_PATH}")
        return
    cfg_mod.init_config()
    click.echo(click.style("✔ Created", fg="green") + f" {cfg_mod.CONFIG_PATH}")
    click.echo("Edit it to set your directories and credentials, then run 'daemon-git run'.")


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Show every repo, not just issues.")
def sync(verbose: bool):
    """Pull all repos once right now."""
    try:
        cfg = cfg_mod.load()
    except FileNotFoundError as e:
        click.echo(str(e))
        sys.exit(1)
    _sync_all(cfg, verbose)


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Show every repo, not just issues.")
def run(verbose: bool):
    """Start the daemon — pulls every configured interval (default: 60s)."""
    try:
        cfg = cfg_mod.load()
    except FileNotFoundError as e:
        click.echo(str(e))
        sys.exit(1)

    interval = cfg_mod.get_interval(cfg)
    click.echo(
        click.style("daemon-git running", fg="green", bold=True)
        + f" — syncing every {interval}s. Press Ctrl-C to stop."
    )

    try:
        while True:
            click.echo(f"\n[{_now()}]")
            _sync_all(cfg, verbose)
            time.sleep(interval)
            cfg = cfg_mod.load()  # re-read config each cycle so edits take effect live
    except KeyboardInterrupt:
        click.echo(click.style("\ndaemon-git stopped.", fg="yellow"))


def main():
    cli()


if __name__ == "__main__":
    main()

