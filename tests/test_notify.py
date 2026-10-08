from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest

from briefspec.cli import main
from briefspec.delivery import load_delivery
from briefspec.notify import (
    Channel,
    NotifyError,
    build_message,
    load_channels,
    notify,
    notify_settings,
    render_discord,
    render_slack,
    render_teams,
    select_channels,
    spawn_background_notify,
)

SLACK_URL = "https://hooks.slack.com/services/T000/B000/" + "z" * 24
BRIEF = """<!-- briefspec:outcome:v1 -->
## Outcome Brief

Status: REVIEW
Outcome: Slack notifications are implemented & ready <for review>.
Human action: Review the channel adapters before enabling the Stop hook trigger.

Proof:
- [direct/pass] `uv run pytest tests/test_notify.py` → 20 passed

Gaps:
- No live post to a real workspace yet.

Next:
- Post once to a test channel.

Open:
- None
<!-- /briefspec -->
"""


class FakeTransport:
    def __init__(self, responses: list[tuple[int, dict[str, str], dict[str, Any] | bytes]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, response_headers, content = self.responses.pop(0)
        raw = content if isinstance(content, bytes) else json.dumps(content).encode()
        return status, response_headers, raw


@pytest.fixture
def delivery() -> dict[str, Any]:
    value, _ = load_delivery(BRIEF)
    return value


@pytest.fixture
def state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "state"
    monkeypatch.setenv("BRIEF_SPEC_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def _write_config(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_channels_load_from_env_references_only(
    state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        state_home,
        """
[channels.eng]
kind = "slack-bot"
target = "C0123ABCD"
secret_env = "BRIEF_SPEC_SLACK_TOKEN"
when_status = ["BLOCKED", "DECIDE", "REVIEW"]

[channels.alerts]
kind = "discord"
secret_env = "BRIEF_SPEC_DISCORD_URL"
""",
    )
    monkeypatch.setenv("BRIEF_SPEC_SLACK_TOKEN", "xoxb-" + "1" * 12 + "-abcdefABCDEF")
    channels = load_channels()
    assert set(channels) == {"eng", "alerts"}
    assert channels["eng"].public()["secret_present"] is True
    assert channels["alerts"].public()["secret_present"] is False
    assert "xoxb" not in json.dumps([channel.public() for channel in channels.values()])


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ('kind = "slack-webhook"\nurl = "https://example.test/x"', "unsupported key"),
        (f'kind = "slack-webhook"\nsecret_env = "{SLACK_URL}"', "URL or credential"),
        ('kind = "slack-webhook"', "needs secret_env"),
        ('kind = "slack-bot"\nsecret_env = "TOKEN_ENV"', "needs target"),
        ('kind = "pager"\nsecret_env = "X_ENV"', "kind must be one of"),
        ('kind = "discord"\nsecret_env = "lowercase"', "environment variable name"),
        ('kind = "webhook"', "needs url_env"),
        ('kind = "discord"\nsecret_env = "D_ENV"\nwhen_status = ["MAYBE"]', "when_status"),
    ],
)
def test_channel_config_rejects_unsafe_or_invalid_tables(
    state_home: Path, table: str, expected: str
) -> None:
    _write_config(state_home, f"[channels.bad]\n{table}\n")
    with pytest.raises(NotifyError, match=expected):
        load_channels()


def test_slack_card_escapes_and_respects_limits(delivery: dict[str, Any]) -> None:
    message = build_message(delivery)
    payload = render_slack(message, "card")
    assert payload["blocks"][0]["type"] == "header"
    assert len(payload["blocks"][0]["text"]["text"]) <= 150
    text = json.dumps(payload)
    assert "&amp;" in text and "&lt;for review&gt;" in text
    assert "Human action" in text and "Gaps" in text and "Next" in text
    assert "Proof" not in text
    assert "Proof" in json.dumps(render_slack(message, "full"))


def test_teams_card_and_discord_embed_shapes(delivery: dict[str, Any]) -> None:
    message = build_message(delivery, link="https://example.test/brief.html")
    teams = render_teams(message, "card")
    card = teams["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["actions"][0]["url"] == "https://example.test/brief.html"
    discord = render_discord(message, "card")
    embed = discord["embeds"][0]
    assert embed["title"].startswith("REVIEW")
    assert discord["allowed_mentions"] == {"parse": []}
    assert all(len(field["value"]) <= 1024 for field in embed["fields"])


def test_dry_run_needs_no_secret_and_sends_nothing(delivery: dict[str, Any]) -> None:
    channel = Channel(name="eng", kind="slack-webhook", secret_env="UNSET_ENV")
    preview = notify(delivery, channel, consent_network=False, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["payload"]["blocks"]


def test_send_requires_network_consent(state_home: Path, delivery: dict[str, Any]) -> None:
    channel = Channel(name="eng", kind="slack-webhook", secret_env="SLACK_URL_ENV")
    with pytest.raises(NotifyError, match="consent-network"):
        notify(delivery, channel, consent_network=False)


def test_webhook_send_writes_receipt_without_secret_and_dedupes(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_URL_ENV", SLACK_URL)
    channel = Channel(name="eng", kind="slack-webhook", secret_env="SLACK_URL_ENV")
    transport = FakeTransport([(200, {}, b"ok")])
    receipt = notify(delivery, channel, consent_network=True, transport=transport)
    assert receipt["kind"] == "brief-spec-notify-receipt"
    assert transport.calls[0]["url"] == SLACK_URL
    stored = json.dumps(receipt) + "".join(
        path.read_text() for path in (state_home / "notify").rglob("*.json")
    )
    assert SLACK_URL not in stored and "zzzz" not in stored
    again = notify(delivery, channel, consent_network=True, transport=FakeTransport([]))
    assert "already delivered" in again["skipped"]
    resent = notify(
        delivery,
        channel,
        consent_network=True,
        resend=True,
        transport=FakeTransport([(200, {}, b"ok")]),
    )
    assert resent["receipt_id"] != receipt["receipt_id"]


def test_retry_honors_retry_after_then_succeeds(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_URL_ENV", SLACK_URL)
    channel = Channel(name="eng", kind="slack-webhook", secret_env="SLACK_URL_ENV")
    waits: list[float] = []
    transport = FakeTransport([(429, {"Retry-After": "2"}, b""), (200, {}, b"ok")])
    receipt = notify(
        delivery, channel, consent_network=True, transport=transport, sleep=waits.append
    )
    assert waits == [2.0]
    assert receipt["metadata"]["attempts"] == 2


def test_persistent_server_errors_fail_after_three_attempts(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_URL_ENV", SLACK_URL)
    channel = Channel(name="eng", kind="slack-webhook", secret_env="SLACK_URL_ENV")
    transport = FakeTransport([(503, {}, b"")] * 3)
    with pytest.raises(NotifyError, match="HTTP 503"):
        notify(delivery, channel, consent_network=True, transport=transport, sleep=lambda _: None)
    assert len(transport.calls) == 3


def test_errors_never_echo_the_secret(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_URL_ENV", SLACK_URL)
    channel = Channel(name="eng", kind="slack-webhook", secret_env="SLACK_URL_ENV")

    def broken(method: str, url: str, headers: dict[str, str], body: bytes):
        raise OSError(f"connection refused while posting to {url}")

    with pytest.raises(NotifyError) as error:
        notify(delivery, channel, consent_network=True, transport=broken)
    assert SLACK_URL not in str(error.value)
    assert "[redacted]" in str(error.value)


def test_timeout_is_reported_as_unknown_not_retried(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_URL_ENV", SLACK_URL)
    channel = Channel(name="eng", kind="slack-webhook", secret_env="SLACK_URL_ENV")
    calls: list[str] = []

    def slow(method: str, url: str, headers: dict[str, str], body: bytes):
        calls.append(url)
        raise TimeoutError("timed out")

    with pytest.raises(NotifyError, match="may or may not"):
        notify(delivery, channel, consent_network=True, transport=slow)
    assert len(calls) == 1


def test_slack_bot_threads_follow_ups_and_updates_root(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BOT_TOKEN_ENV", "xoxb-" + "2" * 12 + "-abcdefABCDEF")
    channel = Channel(name="eng", kind="slack-bot", secret_env="BOT_TOKEN_ENV", target="C0123ABCD")
    first = FakeTransport(
        [
            (200, {}, {"ok": True, "ts": "111.1"}),
            (200, {}, {"ok": True, "permalink": "https://example.slack.test/p111"}),
        ]
    )
    receipt = notify(delivery, channel, consent_network=True, transport=first, thread_key="task-1")
    assert receipt["destination"]["permalink"] == "https://example.slack.test/p111"
    assert first.calls[0]["headers"]["Authorization"].startswith("Bearer xoxb-")
    assert "thread_ts" not in json.loads(first.calls[0]["body"])

    attachment = tmp_path / "brief.pdf"
    attachment.write_bytes(b"%PDF-1.7 test")
    follow = FakeTransport(
        [
            (200, {}, {"ok": True, "ts": "222.2"}),
            (200, {}, {"ok": True}),
            (200, {}, {"ok": True, "permalink": "https://example.slack.test/p222"}),
            (200, {}, {"ok": True, "upload_url": "https://files.example.test/u", "file_id": "F1"}),
            (200, {}, b"OK"),
            (200, {}, {"ok": True}),
        ]
    )
    second = notify(
        delivery,
        channel,
        consent_network=True,
        transport=follow,
        thread_key="task-1",
        resend=True,
        attach=attachment,
    )
    posted = json.loads(follow.calls[0]["body"])
    assert posted["thread_ts"] == "111.1"
    assert follow.calls[1]["url"].endswith("chat.update")
    assert json.loads(follow.calls[1]["body"])["ts"] == "111.1"
    assert second["metadata"]["root_updated"] is True
    assert (
        second["metadata"]["attachment"]["sha256"] == hashlib.sha256(b"%PDF-1.7 test").hexdigest()
    )
    complete = json.loads(follow.calls[5]["body"])
    assert complete["thread_ts"] == "111.1" and complete["channel_id"] == "C0123ABCD"


def test_standard_webhook_signature_verifies(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    monkeypatch.setenv("HOOK_URL_ENV", "https://hooks.example.test/brief")
    monkeypatch.setenv("HOOK_SECRET_ENV", f"whsec_{key}")
    channel = Channel(
        name="ci", kind="webhook", url_env="HOOK_URL_ENV", secret_env="HOOK_SECRET_ENV"
    )
    transport = FakeTransport([(204, {}, b"")])
    notify(delivery, channel, consent_network=True, transport=transport)
    call = transport.calls[0]
    headers = call["headers"]
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + call["body"]
    expected = base64.b64encode(
        hmac.new(base64.b64decode(key), signed, hashlib.sha256).digest()
    ).decode()
    assert headers["webhook-signature"] == f"v1,{expected}"
    body = json.loads(call["body"])
    assert body["type"] == "brief-spec.outcome-brief"
    assert body["data"]["status"] == "REVIEW"


def test_discord_waits_for_id_and_google_chat_threads(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISCORD_ENV", "https://discord.example.test/api/webhooks/1/abc")
    discord = Channel(name="dev", kind="discord", secret_env="DISCORD_ENV")
    transport = FakeTransport([(200, {}, {"id": "998877"})])
    receipt = notify(delivery, discord, consent_network=True, transport=transport)
    assert transport.calls[0]["url"].endswith("?wait=true")
    assert receipt["destination"]["message_id"] == "998877"

    monkeypatch.setenv("GCHAT_ENV", "https://chat.example.test/v1/spaces/AAA/messages?key=k")
    chat = Channel(name="team", kind="google-chat", secret_env="GCHAT_ENV")
    transport = FakeTransport(
        [(200, {}, {"name": "spaces/AAA/messages/1", "thread": {"name": "spaces/AAA/threads/t"}})]
    )
    receipt = notify(delivery, chat, consent_network=True, transport=transport, thread_key="k1")
    assert "threadKey=k1" in transport.calls[0]["url"]
    assert receipt["destination"]["thread_ref"] == {"thread": "spaces/AAA/threads/t"}


def test_plain_http_urls_are_refused(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAIN_ENV", "http://example.test/hook")
    channel = Channel(name="x", kind="discord", secret_env="PLAIN_ENV")
    with pytest.raises(NotifyError, match="https"):
        notify(delivery, channel, consent_network=True, transport=FakeTransport([]))


def test_select_channels_filters_all_by_status() -> None:
    channels = {
        "loud": Channel(name="loud", kind="discord", secret_env="A_ENV"),
        "quiet": Channel(
            name="quiet", kind="discord", secret_env="B_ENV", when_status=("BLOCKED",)
        ),
    }
    assert [c.name for c in select_channels(channels, ["all"], "REVIEW")] == ["loud"]
    assert [c.name for c in select_channels(channels, ["quiet"], "REVIEW")] == ["quiet"]
    with pytest.raises(NotifyError, match="Unknown channel"):
        select_channels(channels, ["nope"], "DONE")


def test_cli_notify_dry_run_and_channels_list(
    state_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(
        state_home,
        '[channels.eng]\nkind = "slack-webhook"\nsecret_env = "SLACK_URL_ENV"\n',
    )
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    assert main(["notify", str(brief), "--to", "eng", "--dry-run", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output[0]["dry_run"] is True and output[0]["status"] == "REVIEW"
    assert main(["notify", str(brief), "--to", "eng"]) == 1
    assert "consent-network" in capsys.readouterr().out
    assert main(["channels", "list"]) == 0
    assert "eng: slack-webhook (SLACK_URL_ENV missing)" in capsys.readouterr().out


def test_stop_hook_spawns_only_with_explicit_opt_in(state_home: Path, outcome_text: Any) -> None:
    launched: list[list[str]] = []
    assert spawn_background_notify(outcome_text(), launcher=lambda c, _: launched.append(c)) is None
    _write_config(
        state_home,
        '[notify]\non_stop = true\nchannels = ["eng"]\n',
    )
    assert notify_settings()["consent_network"] is False
    assert spawn_background_notify(outcome_text(), launcher=lambda c, _: launched.append(c)) is None
    _write_config(
        state_home,
        '[notify]\non_stop = true\nconsent_network = true\nchannels = ["eng"]\n',
    )
    command = spawn_background_notify(outcome_text(), launcher=lambda c, _: launched.append(c))
    assert command is not None and launched == [command]
    assert command[command.index("--to") + 1] == "eng"
    assert "--consent-network" in command
    assert command[command.index("--trigger") + 1] == "hook"
    assert command[command.index("--source-revision") + 1]
    pending = Path(command[command.index("notify") + 1])
    assert pending.read_text(encoding="utf-8").strip() == outcome_text().strip()


def test_slack_bot_follow_up_failure_still_records_receipt(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOT_TOKEN_ENV", "xoxb-" + "3" * 12 + "-abcdefABCDEF")
    channel = Channel(name="eng", kind="slack-bot", secret_env="BOT_TOKEN_ENV", target="C0123ABCD")

    calls: list[str] = []

    def flaky(method: str, url: str, headers: dict[str, str], body: bytes):
        calls.append(url)
        if url.endswith("chat.postMessage"):
            return 200, {}, json.dumps({"ok": True, "ts": "333.3"}).encode()
        raise OSError("permalink service down")

    receipt = notify(delivery, channel, consent_network=True, transport=flaky)
    assert receipt["destination"]["message_id"] == "333.3"
    assert any("permalink" in item for item in receipt["metadata"]["warnings"])
    again = notify(delivery, channel, consent_network=True, transport=flaky)
    assert "already delivered" in again["skipped"]
    assert sum(url.endswith("chat.postMessage") for url in calls) == 1


def test_malformed_url_is_rejected_without_echoing_it(
    state_home: Path, delivery: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_url = "https://discord.example.test/api/webhooks/1/" + "t" * 30 + " oops"
    monkeypatch.setenv("BAD_ENV", secret_url)
    channel = Channel(name="dev", kind="discord", secret_env="BAD_ENV")
    with pytest.raises(NotifyError) as error:
        notify(delivery, channel, consent_network=True)
    assert "t" * 30 not in str(error.value)


def test_hook_pending_file_is_removed_even_when_sending_fails(
    state_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pending_dir = state_home / "notify" / "pending"
    pending_dir.mkdir(parents=True)
    pending = pending_dir / "abc.md"
    pending.write_text("not a brief at all", encoding="utf-8")
    assert main(["notify", str(pending), "--to", "eng", "--trigger", "hook"]) == 1
    assert not pending.exists()


def test_identity_ignores_load_time_metadata(delivery: dict[str, Any]) -> None:
    from briefspec.notify import brief_identity

    later = json.loads(json.dumps(delivery))
    later["source"]["created_at"] = "2030-01-01T00:00:00Z"
    later["source"]["source_revision"] = "f" * 40
    assert brief_identity(later) == brief_identity(delivery)


def test_checkpoints_reach_only_channels_that_list_them() -> None:
    channels = {
        "all-status": Channel(name="all-status", kind="discord", secret_env="A_ENV"),
        "with-checkpoints": Channel(
            name="with-checkpoints",
            kind="discord",
            secret_env="B_ENV",
            when_status=("BLOCKED", "CHECKPOINT"),
        ),
    }
    assert [c.name for c in select_channels(channels, ["all"], "CHECKPOINT")] == [
        "with-checkpoints"
    ]


def test_http_transport_maps_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error

    from briefspec import notify as module

    class Response(io.BytesIO):
        status = 201
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda request, timeout: Response(b"{}"))
    status, _, body = module.http_transport("POST", "https://example.test/x", {}, b"{}")
    assert status == 201 and body == b"{}"

    def failing(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 429, "slow down", {"Retry-After": "1"}, io.BytesIO(b"busy")
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", failing)
    status, headers, body = module.http_transport("POST", "https://example.test/x", {}, b"{}")
    assert status == 429 and headers["Retry-After"] == "1" and body == b"busy"
    with pytest.raises(NotifyError, match="https"):
        module.http_transport("POST", "http://example.test/x", {}, b"{}")


def test_google_chat_and_teams_render_checkpoints_and_full_template(
    delivery: dict[str, Any],
) -> None:
    from briefspec.notify import render_google_chat

    message = build_message(delivery, link="https://example.test/b")
    text = render_google_chat(message, "full")["text"]
    assert "*REVIEW" in text and "*Proof*" in text and "<https://example.test/b|Full brief>" in text
    checkpoint = (
        "<!-- briefspec:checkpoint:v1 mode=orient -->\nHeadline: Halfway through the notify work.\n"
        "Current state: Adapters done.\nCompleted:\n- Slack\nDecisions:\n- None\n"
        "Proof:\n- [direct/pass] `pytest tests/test_notify.py` → passed\nNext:\n- Teams\n"
        "Open:\n- None\n<!-- /briefspec -->\n"
    )
    loaded, _ = load_delivery(checkpoint)
    card = render_teams(build_message(loaded), "card")
    body = card["attachments"][0]["content"]["body"]
    assert body[0]["text"] == "Checkpoint" and "Halfway" in body[1]["text"]
