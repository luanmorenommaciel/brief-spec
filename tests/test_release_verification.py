from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def check_badge(tmp_path: Path) -> Callable[[str], list[str]]:
    path = Path(__file__).parents[1] / "scripts" / "verify-release.py"
    spec = importlib.util.spec_from_file_location("brief_spec_release_verification", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/verification.md").write_text(
        "<!-- briefspec:verification:v1 version=1.2.3 -->\n", encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\n", encoding="utf-8")

    def check(markup: str) -> list[str]:
        (tmp_path / "README.md").write_text(
            markup + "\nPublic release: v1.0.0\nSource candidate: v1.2.3\n",
            encoding="utf-8",
        )
        verifier = module.Verifier()
        module.check_versioned_release_evidence(verifier, "1.2.3")
        return verifier.errors

    return check


def test_release_badge_html(check_badge: Callable[[str], list[str]]) -> None:
    assert (
        check_badge(
            '<a href="docs/verification.md"><img alt="Source candidate 1.2.3" '
            'src="https://img.shields.io/badge/source_candidate-1.2.3-29313A"></a>'
        )
        == []
    )


def test_release_badge_markdown(check_badge: Callable[[str], list[str]]) -> None:
    assert (
        check_badge(
            "[![Source candidate 1.2.3]"
            "(https://img.shields.io/badge/source_candidate-1.2.3-29313A)](docs/verification.md)"
        )
        == []
    )


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="https://img.shields.io/badge/source_candidate-1.2.3-29313A" '
        'alt="Source candidate 1.2.2">',
        "[![Source candidate 1.2.3]"
        "(https://img.shields.io/badge/source_candidate-1.2.2-29313A)](docs/verification.md)",
    ],
)
def test_release_badge_mismatch(check_badge: Callable[[str], list[str]], markup: str) -> None:
    assert check_badge(markup)
