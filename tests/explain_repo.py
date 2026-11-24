"""Fixture git repositories for the explain-back tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}

CORE_BASE = """\
def untouched_helper():
    return "kept"


def load_config(path):
    return {"path": path}


def run(config):
    return config["path"]
"""

CORE_BRANCH = """\
def untouched_helper():
    return "kept"


def load_config(path):
    return {"path": path}


def run(config, threshold=3):
    if apply_threshold(config, threshold):
        return config["path"]
    return None


def apply_threshold(config, threshold):
    return len(config) >= threshold
"""

GATE = """\
from src.app.core import load_config


class StopGate:
    def __init__(self, min_files):
        self.min_files = min_files

    def should_block(self, changed_files):
        return len(changed_files) >= self.min_files


def stop_gate(path):
    return StopGate(load_config(path)["min_files"])
"""


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **_ENV, "HOME": str(repo.parent)}
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def write(repo: Path, relative: str, content: str | bytes) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def make_repo(root: Path) -> Path:
    """A branch with 7 changed files; main also moves on after the branch point."""
    repo = root / "fixture-repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    write(repo, "src/app/core.py", CORE_BASE)
    write(repo, "src/app/util.py", "def helper(value):\n    return value\n")
    write(repo, "docs/readme.md", "# Readme\n\n" + "Stable text line.\n" * 12)
    write(repo, "old.py", "def obsolete():\n    return 1\n")
    commit(repo, "base")

    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "src/app/core.py", CORE_BRANCH)
    write(repo, "src/app/gate.py", GATE)
    (repo / "old.py").unlink()
    git(repo, "mv", "docs/readme.md", "docs/guide.md")
    write(repo, "notes/with space.txt", "a note\n")
    write(repo, "src/app/café.py", "def cafe_order():\n    return 'flat white'\n")
    write(repo, "assets/logo.bin", bytes(range(256)))
    commit(repo, "feature work")

    git(repo, "checkout", "-q", "main")
    write(repo, "src/app/util.py", "def helper(value):\n    return value * 2\n")
    commit(repo, "main moves on")
    git(repo, "checkout", "-q", "feature")
    return repo


def name_only(repo: Path, base: str = "main") -> list[str]:
    return [line for line in git(repo, "diff", "--name-only", f"{base}...HEAD").split("\n") if line]
