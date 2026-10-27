from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
AUTHORIZATION_STEP = "Create exact-SHA release authorization"
BUILD_AND_SMOKE_STEPS = (
    "Install build tools",
    "Build distributions",
    "Check package metadata",
    "Verify wheel resource projections",
    "Clean-room wheel install smoke",
    "Clean-room sdist install smoke",
)
EVENT_CLAUSE = re.compile(r"github\.event_name == '(?P<event>[a-z_]+)'")
REF_CLAUSE = re.compile(r"startsWith\(github\.ref, '(?P<prefix>[^']+)'\)")

# (event_name, github.ref) pairs as GitHub Actions sets them for each trigger in ci.yml.
SCHEDULE = ("schedule", "refs/heads/main")
PULL_REQUEST = ("pull_request", "refs/pull/17/merge")
PUSH_MAIN = ("push", "refs/heads/main")
PUSH_TAG = ("push", "refs/tags/v0.7.0")
DISPATCH = ("workflow_dispatch", "refs/heads/main")


def _job_block(text: str, job: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(f"  {job}:") + 1
    block = []
    for line in lines[start:]:
        if re.match(r"  \S", line):
            break
        block.append(line)
    return block


def _job_if(block: list[str]) -> str | None:
    for line in block:
        if line.startswith("    if: "):
            return line.removeprefix("    if: ").strip()
    return None


def _steps(block: list[str]) -> dict[str, str | None]:
    steps_at = block.index("    steps:") + 1
    steps: list[list[str]] = []
    for line in block[steps_at:]:
        if line.startswith("      - "):
            steps.append(["        " + line.removeprefix("      - ")])
        elif steps:
            steps[-1].append(line)
    result: dict[str, str | None] = {}
    for step in steps:
        keys = {}
        for line in step:
            match = re.match(r"        (name|uses|if): (.+)", line)
            if match:
                keys[match.group(1)] = match.group(2).strip()
        label = keys.get("name") or keys["uses"]
        assert label not in result, f"duplicate step {label!r}"
        result[label] = keys.get("if")
    return result


def _condition(expression: str) -> Callable[[str, str], bool]:
    """Evaluate the only expression shape allowed here: OR-ed event or ref-prefix checks."""
    events: set[str] = set()
    prefixes: set[str] = set()
    for clause in (part.strip() for part in expression.split("||")):
        if event := EVENT_CLAUSE.fullmatch(clause):
            events.add(event.group("event"))
        elif ref := REF_CLAUSE.fullmatch(clause):
            prefixes.add(ref.group("prefix"))
        else:
            pytest.fail(f"unrecognised condition clause {clause!r} in {expression!r}")
    return lambda event_name, ref: event_name in events or any(map(ref.startswith, prefixes))


@pytest.fixture(scope="module")
def release_job() -> list[str]:
    return _job_block(CI_WORKFLOW.read_text(encoding="utf-8"), "release")


def test_authorization_runs_only_on_release_tag_or_manual_dispatch(
    release_job: list[str],
) -> None:
    expression = _steps(release_job)[AUTHORIZATION_STEP]
    assert expression, "release authorization step must be gated"
    allowed = _condition(expression)
    assert allowed(*PUSH_TAG)
    assert allowed(*DISPATCH)
    assert not allowed(*SCHEDULE)
    assert not allowed(*PULL_REQUEST)
    assert not allowed(*PUSH_MAIN)
    assert not allowed("push", "refs/heads/v-feature")
    assert not allowed("pull_request", "refs/tags/x")


def test_only_the_authorization_step_runs_the_authorization_script() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("scripts/build-release-authorization.py") == 1
    block = "\n".join(_job_block(text, "release"))
    step = block.split(f"- name: {AUTHORIZATION_STEP}", 1)[1].split("\n      - ", 1)[0]
    assert "scripts/build-release-authorization.py" in step


def test_build_and_smoke_steps_are_not_gated_and_still_run_on_schedule(
    release_job: list[str],
) -> None:
    steps = _steps(release_job)
    for name in BUILD_AND_SMOKE_STEPS:
        assert name in steps, f"missing step {name!r}"
        assert steps[name] is None, f"{name!r} must not carry a step condition"
    job_if = _job_if(release_job)
    assert job_if is not None
    assert _condition(job_if)(*SCHEDULE), "scheduled runs must still build and smoke"
    names = list(steps)
    authorization_at = names.index(AUTHORIZATION_STEP)
    assert all(names.index(name) < authorization_at for name in BUILD_AND_SMOKE_STEPS)
