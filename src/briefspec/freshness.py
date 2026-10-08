from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_ok(args: list[str], cwd: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def git_head(path: Path) -> str | None:
    """Return the HEAD commit of the repository containing path, or None outside Git."""
    directory = path if path.is_dir() else path.parent
    if not directory.exists():
        return None
    head = _git(["rev-parse", "HEAD"], directory)
    return head if head and _REVISION.fullmatch(head) else None


def freshness(recorded: object, workspace: Path) -> dict[str, Any] | None:
    """Compare a brief's recorded revision with the workspace HEAD.

    Returns None when there is nothing to compare: no recorded revision or no Git workspace.
    """
    if not isinstance(recorded, str) or not _REVISION.fullmatch(recorded.lower()):
        return None
    head = git_head(workspace)
    if head is None:
        return None
    recorded = recorded.lower()
    directory = workspace if workspace.is_dir() else workspace.parent
    if _git(["cat-file", "-t", recorded], directory) != "commit":
        # The brief describes another repository or an unfetched commit; do not guess.
        return None
    if head.startswith(recorded) or recorded.startswith(head):
        return {"status": "fresh", "recorded": recorded, "head": head}
    if not _git_ok(["merge-base", "--is-ancestor", recorded, head], directory):
        return {"status": "diverged", "recorded": recorded, "head": head, "commits_since": None}
    commits = _git(["rev-list", "--count", f"{recorded}..{head}"], directory)
    return {
        "status": "stale",
        "recorded": recorded,
        "head": head,
        "commits_since": int(commits) if commits and commits.isdigit() else None,
    }
