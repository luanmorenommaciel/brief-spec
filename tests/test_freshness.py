from __future__ import annotations

import subprocess
from pathlib import Path

from briefspec.cli import main
from briefspec.freshness import freshness, git_head

BRIEF = """<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: DONE
Outcome: The parser accepts empty input.
Proof: [direct/pass] `pytest tests/test_parser.py` → 3 passed
<!-- /briefspec -->
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "one")
    return repo


def test_freshness_reports_fresh_then_stale(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = git_head(repo)
    assert first is not None
    assert freshness(first, repo) == {"status": "fresh", "recorded": first, "head": first}
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "two")
    state = freshness(first[:12], repo)
    assert state is not None
    assert state["status"] == "stale"
    assert state["commits_since"] == 1


def test_freshness_skips_unknown_commits_and_non_git(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert freshness("0123456789abcdef0123456789abcdef01234567", repo) is None
    assert freshness(None, repo) is None
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_head(plain) is None


def test_export_records_head_and_verify_warns_when_stale(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    brief = repo / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    head = git_head(repo)
    out = tmp_path / "out"
    assert main(["export", str(brief), "--formats", "json", "--output-dir", str(out)]) == 0
    capsys.readouterr()
    assert f'"source_revision": "{head}"' in (out / "brief.json").read_text(encoding="utf-8")
    assert main(["verify", str(out / "brief.json"), "--workspace", str(repo), "--json"]) == 0
    assert '"freshness"' in capsys.readouterr().out
    (repo / "a.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "later")
    assert main(["verify", str(out / "brief.json"), "--workspace", str(repo), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"WARN"' in output and "1 commit(s) later" in output


def test_export_can_opt_out_of_revision(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    brief = repo / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    out = tmp_path / "out"
    assert (
        main(
            [
                "export",
                str(brief),
                "--formats",
                "json",
                "--output-dir",
                str(out),
                "--source-revision",
                "none",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert "source_revision" not in (out / "brief.json").read_text(encoding="utf-8")


def test_v05_derived_pass_delivery_still_verifies_with_warning(tmp_path: Path) -> None:
    import json

    from briefspec.delivery import load_delivery, validate_delivery

    brief = BRIEF.replace("[direct/pass]", "[direct/pass]")
    delivery, _ = load_delivery(brief)
    delivery["brief"]["proof"][0]["basis"] = "derived"
    delivery["source"]["brief_spec_version"] = "0.5.0"
    result = validate_delivery(json.loads(json.dumps(delivery)))
    assert result.valid, result.errors
    assert any("< 0.6" in warning for warning in result.warnings)
    delivery["source"]["brief_spec_version"] = "0.6.0"
    assert not validate_delivery(delivery).valid


def test_freshness_tolerates_non_string_revision(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert freshness(12345, repo) is None
