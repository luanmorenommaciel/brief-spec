from __future__ import annotations

import pytest

from briefspec.delivery import load_delivery
from briefspec.secret_scan import find_secrets, redact, scan_value

SLACK_HOOK = "https://hooks.slack.com/services/T000/B000/" + "a" * 24
FAKE_TOKENS = {
    "Slack token": "xoxb-" + "1" * 12 + "-abcdefABCDEF",
    "Slack webhook URL": SLACK_HOOK,
    "GitHub token": "ghp_" + "A" * 36,
    "AWS access key": "AKIA" + "B" * 16,
    "private key": "-----BEGIN OPENSSH PRIVATE KEY-----",
    "Discord webhook URL": "https://discord.com/api/webhooks/123456/" + "x" * 30,
    "credentials in URL": "https://user:hunter22@example.test/repo.git",
}


@pytest.mark.parametrize(("kind", "value"), FAKE_TOKENS.items())
def test_find_secrets_names_the_kind(kind: str, value: str) -> None:
    assert kind in find_secrets(f"see {value} here")


def test_ordinary_evidence_is_not_flagged() -> None:
    for text in (
        "[direct/pass] `uv run pytest` → 612 passed",
        "commit 3781d0d36f1608eeffdd1f3e7225bc1d18096c46",
        "https://github.com/luanmorenommaciel/brief-spec/actions/runs/37514566139",
        "sha256 9187a50dec1390798504cce48f67a4c2cd5a7bcfff46628445de88d0e91965d1",
    ):
        assert find_secrets(text) == []


def test_scan_value_reports_paths_not_values() -> None:
    found = scan_value({"brief": {"proof": [{"label": f"posted to {SLACK_HOOK}"}]}})
    assert found == [("brief.proof[0].label", "Slack webhook URL")]


def test_redact_replaces_known_values() -> None:
    assert redact("failed POST to https://x/abc123secret", ["abc123secret"]) == (
        "failed POST to https://x/[redacted]"
    )


def test_export_refuses_brief_with_secret() -> None:
    brief = (
        "<!-- briefspec:outcome:v1 -->\n## Outcome Brief\n\nStatus: DONE\n"
        "Outcome: Notifications work.\n"
        f"Proof: [direct/pass] `curl {SLACK_HOOK}` → 200\n<!-- /briefspec -->\n"
    )
    with pytest.raises(ValueError) as error:
        load_delivery(brief)
    assert "Slack webhook URL" in str(error.value)
    assert SLACK_HOOK not in str(error.value)
