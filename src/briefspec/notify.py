"""One-way status updates from a validated brief to chat and webhook channels.

Brief-Spec stays a handoff layer: it posts what a brief already says, never reads replies,
and never approves or dispatches work. Every send needs explicit network consent, secrets
come only from environment variables, and receipts record hashes and message ids, never
the secret.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import random
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefspec.artifacts import build_receipt
from briefspec.config import briefspec_home, legacy_briefspec_home
from briefspec.delivery import canonical_sha256
from briefspec.secret_scan import find_secrets, redact
from briefspec.state import atomic_write

CHANNEL_KINDS = (
    "slack-webhook",
    "slack-bot",
    "teams-workflow",
    "discord",
    "google-chat",
    "webhook",
)
STATUSES = ("DONE", "REVIEW", "DECIDE", "BLOCKED", "FAILED")
_ALLOWED_KEYS = {
    "kind",
    "target",
    "secret_env",
    "url_env",
    "template",
    "when_status",
    "thread_by",
    "mention",
}
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SLACK_CHANNEL = re.compile(r"^[CDG][A-Z0-9]{6,}$")
_STATUS_LABEL = {
    "DONE": "DONE",
    "REVIEW": "REVIEW: needs your review",
    "DECIDE": "DECIDE: needs your decision",
    "BLOCKED": "BLOCKED",
    "FAILED": "FAILED",
}
_STATUS_COLOR = {
    "DONE": 0x2E7D32,
    "REVIEW": 0x1565C0,
    "DECIDE": 0x6A1B9A,
    "BLOCKED": 0xEF6C00,
    "FAILED": 0xC62828,
    "CHECKPOINT": 0x546E7A,
}
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
TIMEOUT_SECONDS = 15
USER_AGENT = "brief-spec-notify"

Transport = Callable[[str, str, dict[str, str], bytes], tuple[int, dict[str, str], bytes]]


class NotifyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Channel:
    name: str
    kind: str
    secret_env: str | None = None
    url_env: str | None = None
    target: str | None = None
    template: str = "card"
    when_status: tuple[str, ...] = STATUSES
    thread_by: str = "task"
    mention: str | None = None
    source: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "target": self.target,
            "template": self.template,
            "when_status": list(self.when_status),
            "thread_by": self.thread_by,
            "secret_env": self.secret_env,
            "secret_present": bool(self.secret_env and os.environ.get(self.secret_env)),
            "url_env": self.url_env,
            "source": self.source,
        }


@dataclass(slots=True)
class Message:
    """Channel-neutral content extracted from a delivery."""

    kind: str
    status: str
    title: str
    human_action: str | None
    next_items: list[str]
    gaps: list[str]
    open_items: list[str]
    proof: list[str]
    work_type: str
    subject: str
    revision: str | None
    link: str | None
    mention: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------- config


def _channel_from_table(name: str, value: Any, source: Path) -> Channel:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
        raise NotifyError(f"{source}: channel name {name!r} must be a lowercase slug")
    if not isinstance(value, dict):
        raise NotifyError(f"{source}: [channels.{name}] must be a table")
    unknown = sorted(set(value) - _ALLOWED_KEYS)
    if unknown:
        raise NotifyError(
            f"{source}: [channels.{name}] has unsupported key(s) {', '.join(unknown)}; "
            "secrets and URLs belong in environment variables named by secret_env or url_env"
        )
    for key, item in value.items():
        text = json.dumps(item)
        if find_secrets(text) or re.search(r"https?://", text):
            raise NotifyError(
                f"{source}: [channels.{name}].{key} contains a URL or credential; store it in "
                "an environment variable and reference it with secret_env or url_env"
            )
    kind = str(value.get("kind", ""))
    if kind not in CHANNEL_KINDS:
        raise NotifyError(
            f"{source}: [channels.{name}].kind must be one of {', '.join(CHANNEL_KINDS)}"
        )
    for key in ("secret_env", "url_env"):
        env = value.get(key)
        if env is not None and not _ENV_NAME.fullmatch(str(env)):
            raise NotifyError(
                f"{source}: [channels.{name}].{key} must be an environment variable name"
            )
    target = value.get("target")
    if kind == "slack-bot":
        if not value.get("secret_env"):
            raise NotifyError(f"{source}: slack-bot channel {name} needs secret_env (bot token)")
        if not target or not _SLACK_CHANNEL.fullmatch(str(target)):
            raise NotifyError(f"{source}: slack-bot channel {name} needs target = a channel id")
    elif kind == "webhook":
        if not value.get("url_env"):
            raise NotifyError(f"{source}: webhook channel {name} needs url_env")
    elif not value.get("secret_env"):
        raise NotifyError(f"{source}: {kind} channel {name} needs secret_env (the webhook URL)")
    template = str(value.get("template", "card"))
    if template not in {"card", "full"}:
        raise NotifyError(f"{source}: [channels.{name}].template must be card or full")
    statuses = value.get("when_status", list(STATUSES))
    if not isinstance(statuses, list) or not all(
        str(item) in (*STATUSES, "CHECKPOINT") for item in statuses
    ):
        raise NotifyError(
            f"{source}: [channels.{name}].when_status must list statuses from "
            + ", ".join((*STATUSES, "CHECKPOINT"))
        )
    thread_by = str(value.get("thread_by", "task"))
    if thread_by not in {"task", "none"}:
        raise NotifyError(f"{source}: [channels.{name}].thread_by must be task or none")
    mention = value.get("mention")
    return Channel(
        name=name,
        kind=kind,
        secret_env=value.get("secret_env"),
        url_env=value.get("url_env"),
        target=str(target) if target is not None else None,
        template=template,
        when_status=tuple(str(item) for item in statuses),
        thread_by=thread_by,
        mention=str(mention) if mention else None,
        source=str(source),
    )


def config_paths(cwd: Path | None = None) -> list[Path]:
    project = cwd or Path.cwd()
    return [
        legacy_briefspec_home() / "config.toml",
        briefspec_home() / "config.toml",
        project / ".briefspec.toml",
        project / ".brief-spec.toml",
    ]


def load_channels(cwd: Path | None = None) -> dict[str, Channel]:
    """Read [channels.*] from user then project config; later files override earlier ones."""
    channels: dict[str, Channel] = {}
    for path in config_paths(cwd):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise NotifyError(f"{path}: cannot read config: {exc}") from exc
        tables = data.get("channels", {})
        if not isinstance(tables, dict):
            raise NotifyError(f"{path}: [channels] must contain named tables")
        for name, value in tables.items():
            channels[name] = _channel_from_table(str(name), value, path)
    return channels


def notify_settings(cwd: Path | None = None) -> dict[str, Any]:
    """Read the optional [notify] table used by the Stop hook."""
    settings: dict[str, Any] = {"on_stop": False, "channels": [], "consent_network": False}
    for path in config_paths(cwd):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                table = tomllib.load(handle).get("notify", {})
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(table, dict):
            for key in ("on_stop", "consent_network"):
                if isinstance(table.get(key), bool):
                    settings[key] = table[key]
            if isinstance(table.get("channels"), list):
                settings["channels"] = [str(item) for item in table["channels"]]
    return settings


# ---------------------------------------------------------------------------- content


def _items(value: Any) -> list[str]:
    if isinstance(value, list):
        values = [str(item).strip() for item in value]
    elif value is None:
        values = []
    else:
        values = [str(value).strip()]
    return [item for item in values if item and item.lower() not in {"none", "n/a"}]


def _proof_label(item: Any) -> str:
    if isinstance(item, dict):
        basis = item.get("basis", "reported")
        result = item.get("result", "info")
        return f"[{basis}/{result}] {item.get('label') or item.get('locator')}"
    return str(item)


def build_message(
    delivery: dict[str, Any], *, link: str | None = None, mention: str | None = None
) -> Message:
    brief = delivery.get("brief", {})
    classification = delivery.get("classification", {})
    source = delivery.get("source", {})
    if brief.get("kind") == "outcome-brief":
        status = str(brief.get("status"))
        title = str(brief.get("outcome") or "")
        human_action = brief.get("human_action")
        human = str(human_action) if human_action and str(human_action) != "None" else None
        next_items = _items(brief.get("next"))
        gaps = _items(brief.get("gaps"))
        open_items = _items(brief.get("open"))
    else:
        status = "CHECKPOINT"
        mode = str(brief.get("mode", "orient"))
        title = str(brief.get("headline") or f"{mode.title()} checkpoint")
        human = None
        next_items = _items(brief.get("next"))
        gaps = []
        open_items = _items(brief.get("open"))
    return Message(
        kind=str(brief.get("kind")),
        status=status,
        title=title,
        human_action=human,
        next_items=next_items,
        gaps=gaps,
        open_items=open_items,
        proof=[_proof_label(item) for item in brief.get("proof", [])],
        work_type=str(classification.get("work_type", "general")),
        subject=str(classification.get("subject", "general")),
        revision=source.get("source_revision"),
        link=link,
        mention=mention,
    )


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _context_line(message: Message) -> str:
    parts = [f"{message.work_type} + {message.subject}"]
    if message.revision:
        parts.append(f"at {message.revision[:12]}")
    return " · ".join(parts)


def _plain_sections(message: Message, template: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if message.human_action:
        sections.append(("Human action", message.human_action))
    if message.gaps:
        sections.append(("Gaps", "\n".join(f"• {item}" for item in message.gaps[:5])))
    if message.next_items:
        sections.append(("Next", "\n".join(f"• {item}" for item in message.next_items[:3])))
    if message.open_items and message.status == "DECIDE":
        sections.append(("Open", "\n".join(f"• {item}" for item in message.open_items[:3])))
    if template == "full" and message.proof:
        sections.append(("Proof", "\n".join(f"• {item}" for item in message.proof[:5])))
    return sections


def _status_text(message: Message) -> str:
    return _STATUS_LABEL.get(message.status, message.status.title())


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(message: Message, template: str) -> dict[str, Any]:
    header = _clip(f"{_status_text(message)} · {message.title}", 150)
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
    ]
    if message.mention:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": _clip(message.mention, 200)}}
        )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _clip(_slack_escape(message.title), 3000)},
        }
    )
    for label, body in _plain_sections(message, template):
        text = f"*{label}*\n{_slack_escape(body)}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip(text, 3000)}})
    context = _slack_escape(_context_line(message))
    if message.link:
        context += f" · <{message.link}|full brief>"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": context}]})
    return {"text": _clip(header, 3000), "blocks": blocks[:50]}


def render_teams(message: Message, template: str) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": _status_text(message),
            "weight": "Bolder",
            "size": "Medium",
            "color": "Attention" if message.status in {"BLOCKED", "FAILED"} else "Accent",
        },
        {"type": "TextBlock", "text": _clip(message.title, 2000), "wrap": True},
    ]
    for label, text in _plain_sections(message, template):
        body.append({"type": "TextBlock", "text": label, "weight": "Bolder", "spacing": "Medium"})
        body.append({"type": "TextBlock", "text": _clip(text, 2000), "wrap": True})
    body.append(
        {"type": "TextBlock", "text": _context_line(message), "isSubtle": True, "size": "Small"}
    )
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if message.link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Full brief", "url": message.link}]
    payload = {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }
    if len(json.dumps(payload).encode()) > 28_000:
        raise NotifyError("Teams card exceeds the 28 KB limit; use template = card")
    return payload


def render_discord(message: Message, template: str) -> dict[str, Any]:
    fields = [
        {"name": label, "value": _clip(text, 1024), "inline": False}
        for label, text in _plain_sections(message, template)
    ][:25]
    embed: dict[str, Any] = {
        "title": _clip(_status_text(message), 256),
        "description": _clip(message.title, 4096),
        "color": _STATUS_COLOR.get(message.status, 0x546E7A),
        "fields": fields,
        "footer": {"text": _clip(_context_line(message), 2048)},
    }
    if message.link:
        embed["url"] = message.link
    total = len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
    total += sum(len(item["name"]) + len(item["value"]) for item in fields)
    while total > 6000 and embed["fields"]:
        removed = embed["fields"].pop()
        total -= len(removed["name"]) + len(removed["value"])
    payload: dict[str, Any] = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if message.mention:
        payload["content"] = _clip(message.mention, 2000)
    return payload


def render_google_chat(message: Message, template: str) -> dict[str, Any]:
    lines = [f"*{_status_text(message)}*", message.title]
    for label, text in _plain_sections(message, template):
        lines.extend(["", f"*{label}*", text])
    lines.extend(["", f"_{_context_line(message)}_"])
    if message.link:
        lines.append(f"<{message.link}|Full brief>")
    text = "\n".join(lines)
    if len(text.encode()) > 32_000:
        text = _clip(text, 30_000)
    return {"text": text}


def render_webhook(message: Message, template: str, delivery: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": message.status,
        "outcome": message.title,
        "human_action": message.human_action,
        "next": message.next_items,
        "gaps": message.gaps,
        "open": message.open_items,
        "work_type": message.work_type,
        "subject": message.subject,
        "source_revision": message.revision,
        "link": message.link,
        "delivery_sha256": canonical_sha256(delivery),
    }
    if template == "full":
        data["delivery"] = delivery
    return {"type": f"brief-spec.{message.kind}", "data": data}


def render(channel: Channel, message: Message, delivery: dict[str, Any]) -> dict[str, Any]:
    if channel.kind in {"slack-webhook", "slack-bot"}:
        return render_slack(message, channel.template)
    if channel.kind == "teams-workflow":
        return render_teams(message, channel.template)
    if channel.kind == "discord":
        return render_discord(message, channel.template)
    if channel.kind == "google-chat":
        return render_google_chat(message, channel.template)
    return render_webhook(message, channel.template, delivery)


# ---------------------------------------------------------------------------- transport


def http_transport(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, dict[str, str], bytes]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise NotifyError("Channels must use https URLs")
    request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, dict(response.headers.items()), response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read(100_000)


def _check_url(url: str) -> None:
    if not url.startswith("https://"):
        raise NotifyError("Channels must use https URLs")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise NotifyError("Channel URL contains whitespace or control characters")


def _retry_delay(headers: dict[str, str], attempt: int) -> float:
    lowered = {key.lower(): value for key, value in headers.items()}
    retry_after = lowered.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    return min(2**attempt + random.random(), MAX_RETRY_AFTER_SECONDS)  # noqa: S311


def _request(
    transport: Transport,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    *,
    secrets: list[str],
    sleep: Callable[[float], None],
) -> tuple[int, dict[str, str], bytes, int]:
    _check_url(url)
    attempt = 0
    while True:
        attempt += 1
        try:
            status, response_headers, content = transport(method, url, headers, body)
        except TimeoutError as exc:
            # The server may have accepted the post; retrying could duplicate it.
            raise NotifyError(
                "Channel timed out; the message may or may not have been posted"
            ) from exc
        except NotifyError:
            raise
        except Exception as exc:  # any transport error may quote the secret URL
            raise NotifyError(
                redact(f"Channel request failed: {type(exc).__name__}: {exc}", secrets)
            ) from None
        if (status == 429 or status >= 500) and attempt < MAX_ATTEMPTS:
            sleep(_retry_delay(response_headers, attempt))
            continue
        return status, response_headers, content, attempt


def _json(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def standard_webhook_headers(secret: str, body: bytes, *, msg_id: str, timestamp: int) -> dict:
    """Sign a payload per the Standard Webhooks spec (webhook-id/timestamp/signature)."""
    key = secret.removeprefix("whsec_")
    try:
        key_bytes = base64.b64decode(key, validate=True)
    except ValueError:
        key_bytes = key.encode()
    signed = f"{msg_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(hmac.new(key_bytes, signed, hashlib.sha256).digest()).decode()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": f"v1,{signature}",
    }


# ---------------------------------------------------------------------------- ledger


def _ledger_dir() -> Path:
    return briefspec_home() / "notify"


@contextlib.contextmanager
def _ledger_lock(timeout: float = 60.0) -> Iterator[None]:
    folder = _ledger_dir()
    folder.mkdir(parents=True, exist_ok=True)
    lock_path = folder / ".lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 300:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise NotifyError("Another notification is still sending; try again") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def brief_identity(delivery: dict[str, Any]) -> str:
    """Hash what the brief says, not when or where it was loaded.

    Excludes source metadata such as created_at or the current Git HEAD, so a re-run or an
    acknowledgment still matches the same brief after a commit or a file touch.
    """
    classification = delivery.get("classification", {})
    material = {
        "brief": delivery.get("brief"),
        "explanation": delivery.get("explanation"),
        "work_type": classification.get("work_type"),
        "subject": classification.get("subject"),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def idempotency_key(delivery_sha: str, channel: Channel, thread_key: str | None) -> str:
    material = f"{delivery_sha}|{channel.name}|{channel.kind}|{channel.template}|{thread_key}"
    return hashlib.sha256(material.encode()).hexdigest()


# ---------------------------------------------------------------------------- send


def _resolve(env: str | None, label: str) -> str:
    if not env:
        raise NotifyError(f"{label} is not configured")
    value = os.environ.get(env, "").strip()
    if not value:
        raise NotifyError(f"Environment variable {env} is not set")
    return value


def _post_json(
    transport: Transport,
    url: str,
    payload: dict[str, Any],
    *,
    secrets: list[str],
    sleep: Callable[[float], None],
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], int, bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        **(extra_headers or {}),
    }
    status, _, content, attempts = _request(
        transport, "POST", url, headers, body, secrets=secrets, sleep=sleep
    )
    return status, _json(content), attempts, body


def _send_slack_bot(
    channel: Channel,
    payload: dict[str, Any],
    thread: dict[str, Any] | None,
    attach: Path | None,
    transport: Transport,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    token = _resolve(channel.secret_env, "secret_env")
    secrets = [token]
    auth = {"Authorization": f"Bearer {token}"}
    body = {**payload, "channel": channel.target, "unfurl_links": False}
    root_ts = thread.get("ts") if thread else None
    if root_ts:
        body["thread_ts"] = root_ts
    status, value, attempts, sent = _post_json(
        transport,
        "https://slack.com/api/chat.postMessage",
        body,
        secrets=secrets,
        sleep=sleep,
        extra_headers=auth,
    )
    if status != 200 or not value.get("ok"):
        raise NotifyError(f"Slack chat.postMessage failed: {value.get('error') or status}")
    message_ts = str(value.get("ts"))
    result: dict[str, Any] = {
        "http_status": status,
        "attempts": attempts,
        "message_id": message_ts,
        "thread_ref": {"ts": root_ts or message_ts},
        "payload_sha256": hashlib.sha256(sent).hexdigest(),
    }
    # The message is posted. Everything below is best-effort, so a later failure is recorded
    # in the receipt instead of leaving a posted message without one (and posting it twice).
    warnings: list[str] = []
    if root_ts:
        # Keep the root message current so the channel shows the latest status at a glance.
        update = {"channel": channel.target, "ts": root_ts, **payload}
        try:
            update_status, update_value, _, _ = _post_json(
                transport,
                "https://slack.com/api/chat.update",
                update,
                secrets=secrets,
                sleep=sleep,
                extra_headers=auth,
            )
            result["root_updated"] = bool(update_status == 200 and update_value.get("ok"))
        except NotifyError as exc:
            result["root_updated"] = False
            warnings.append(f"root update failed: {exc}")
    try:
        permalink_query = urllib.parse.urlencode(
            {"channel": channel.target, "message_ts": message_ts}
        )
        link_status, _, link_content, _ = _request(
            transport,
            "GET",
            f"https://slack.com/api/chat.getPermalink?{permalink_query}",
            {**auth, "User-Agent": USER_AGENT},
            b"",
            secrets=secrets,
            sleep=sleep,
        )
        permalink = _json(link_content).get("permalink") if link_status == 200 else None
        if permalink:
            result["permalink"] = str(permalink)
    except NotifyError as exc:
        warnings.append(f"permalink lookup failed: {exc}")
    if attach is not None:
        try:
            result["attachment"] = _slack_upload(
                channel, attach, result["thread_ref"]["ts"], auth, secrets, transport, sleep
            )
        except (NotifyError, OSError, KeyError) as exc:
            warnings.append(redact(f"attachment upload failed: {exc}", secrets))
    if warnings:
        result["warnings"] = warnings
    return result


def _slack_upload(
    channel: Channel,
    path: Path,
    thread_ts: str,
    auth: dict[str, str],
    secrets: list[str],
    transport: Transport,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    content = path.read_bytes()
    if len(content) > 25 * 1024 * 1024:
        raise NotifyError("Attachment is larger than 25 MB")
    query = urllib.parse.urlencode({"filename": path.name, "length": len(content)})
    status, _, raw, _ = _request(
        transport,
        "GET",
        f"https://slack.com/api/files.getUploadURLExternal?{query}",
        {**auth, "User-Agent": USER_AGENT},
        b"",
        secrets=secrets,
        sleep=sleep,
    )
    value = _json(raw)
    if status != 200 or not value.get("ok"):
        raise NotifyError(f"Slack upload URL request failed: {value.get('error') or status}")
    upload_url = str(value["upload_url"])
    file_id = str(value["file_id"])
    upload_status, _, _, _ = _request(
        transport,
        "POST",
        upload_url,
        {"Content-Type": "application/octet-stream", "User-Agent": USER_AGENT},
        content,
        secrets=[*secrets, upload_url],
        sleep=sleep,
    )
    if upload_status != 200:
        raise NotifyError(f"Slack file upload failed with HTTP {upload_status}")
    complete = {
        "files": [{"id": file_id, "title": path.name}],
        "channel_id": channel.target,
        "thread_ts": thread_ts,
    }
    done_status, done, _, _ = _post_json(
        transport,
        "https://slack.com/api/files.completeUploadExternal",
        complete,
        secrets=secrets,
        sleep=sleep,
        extra_headers=auth,
    )
    if done_status != 200 or not done.get("ok"):
        raise NotifyError(f"Slack upload completion failed: {done.get('error') or done_status}")
    return {
        "file_id": file_id,
        "name": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _send_webhook_kind(
    channel: Channel,
    payload: dict[str, Any],
    thread: dict[str, Any] | None,
    thread_key: str | None,
    transport: Transport,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    if channel.kind == "webhook":
        url = _resolve(channel.url_env, "url_env")
        secret = os.environ.get(channel.secret_env or "", "").strip() or None
        secrets = [url, *([secret] if secret else [])]
    else:
        url = _resolve(channel.secret_env, "secret_env")
        secret = None
        secrets = [url]
    if not url.startswith("https://"):
        raise NotifyError(f"{channel.kind} channel {channel.name} must use an https URL")
    headers: dict[str, str] = {}
    body_payload = payload
    if channel.kind == "discord":
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}wait=true"
        if channel.target:
            url += f"&thread_id={urllib.parse.quote(channel.target)}"
    elif channel.kind == "google-chat" and thread_key:
        separator = "&" if "?" in url else "?"
        url += separator + urllib.parse.urlencode(
            {
                "threadKey": thread_key,
                "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
            }
        )
    elif channel.kind == "webhook":
        message_id = f"msg_{uuid.uuid4().hex}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if secret:
            headers = standard_webhook_headers(
                secret, body, msg_id=message_id, timestamp=int(time.time())
            )
        else:
            headers = {"webhook-id": message_id}
    status, value, attempts, sent = _post_json(
        transport, url, body_payload, secrets=secrets, sleep=sleep, extra_headers=headers
    )
    if status >= 300:
        raise NotifyError(f"{channel.kind} channel {channel.name} returned HTTP {status}")
    result: dict[str, Any] = {
        "http_status": status,
        "attempts": attempts,
        "payload_sha256": hashlib.sha256(sent).hexdigest(),
    }
    if channel.kind == "discord" and value.get("id"):
        result["message_id"] = str(value["id"])
    if channel.kind == "google-chat":
        if value.get("name"):
            result["message_id"] = str(value["name"])
        if isinstance(value.get("thread"), dict) and value["thread"].get("name"):
            result["thread_ref"] = {"thread": value["thread"]["name"]}
    if channel.kind == "webhook":
        result["message_id"] = headers.get("webhook-id")
    return result


def notify(
    delivery: dict[str, Any],
    channel: Channel,
    *,
    consent_network: bool,
    dry_run: bool = False,
    thread_key: str | None = None,
    link: str | None = None,
    attach: Path | None = None,
    resend: bool = False,
    trigger: str = "manual",
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Send one brief to one channel and return a receipt (or a dry-run preview)."""
    if attach is not None and channel.kind != "slack-bot":
        raise NotifyError("Attachments are supported only by slack-bot channels")
    message = build_message(delivery, link=link, mention=channel.mention)
    payload = render(channel, message, delivery)
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    found = find_secrets(payload_bytes.decode("utf-8"))
    if found:
        raise NotifyError(f"Refusing to post: the message contains a {found[0]}")
    delivery_sha = brief_identity(delivery)
    if channel.thread_by == "none":
        thread_key = None
    elif thread_key is None:
        thread_key = str(delivery.get("classification", {}).get("decision_id") or "") or None
    key = idempotency_key(delivery_sha, channel, thread_key)
    preview = {
        "channel": channel.name,
        "kind": channel.kind,
        "status": message.status,
        "thread_key": thread_key,
        "idempotency_key": key,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    if dry_run:
        return {**preview, "dry_run": True, "payload": payload}
    if not consent_network:
        raise NotifyError("Posting to a channel needs --consent-network (or --dry-run)")
    with _ledger_lock():
        return _send_and_record(
            delivery,
            channel,
            message=message,
            payload=payload,
            delivery_sha=delivery_sha,
            thread_key=thread_key,
            key=key,
            attach=attach,
            resend=resend,
            trigger=trigger,
            transport=transport or http_transport,
            sleep=sleep,
        )


def _send_and_record(
    delivery: dict[str, Any],
    channel: Channel,
    *,
    message: Message,
    payload: dict[str, Any],
    delivery_sha: str,
    thread_key: str | None,
    key: str,
    attach: Path | None,
    resend: bool,
    trigger: str,
    transport: Transport,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    ledger_dir = _ledger_dir()
    sends_path = ledger_dir / "sends.json"
    threads_path = ledger_dir / "threads.json"
    sends = _load_json(sends_path)
    if key in sends and not resend:
        return {**sends[key], "skipped": "already delivered; use --resend to post again"}
    threads = _load_json(threads_path)
    thread_state = threads.get(channel.name, {}).get(thread_key) if thread_key else None
    if channel.kind == "slack-bot":
        result = _send_slack_bot(channel, payload, thread_state, attach, transport, sleep)
    else:
        result = _send_webhook_kind(channel, payload, thread_state, thread_key, transport, sleep)
    delivered_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    destination = {
        "kind": channel.kind,
        "channel": channel.name,
        "target": channel.target,
        "message_id": result.get("message_id"),
        "permalink": result.get("permalink"),
        "thread_ref": result.get("thread_ref"),
    }
    receipt = build_receipt(
        kind="brief-spec-notify-receipt",
        schema_version="1.0",
        receipt_id=str(uuid.uuid4()),
        content_sha256=delivery_sha,
        created_at=delivered_at,
        destination={name: value for name, value in destination.items() if value is not None},
        metadata={
            "status": message.status,
            "trigger": trigger,
            "template": channel.template,
            "thread_key": thread_key,
            "idempotency_key": key,
            "payload_sha256": result.get("payload_sha256"),
            "http_status": result.get("http_status"),
            "attempts": result.get("attempts"),
            **({"attachment": result["attachment"]} if result.get("attachment") else {}),
            **({"root_updated": result["root_updated"]} if "root_updated" in result else {}),
            **({"warnings": result["warnings"]} if result.get("warnings") else {}),
            "delivery_sha256": canonical_sha256(delivery),
        },
    )
    sends[key] = receipt
    _save_json(sends_path, sends)
    if thread_key and result.get("thread_ref") and not thread_state:
        threads.setdefault(channel.name, {})[thread_key] = result["thread_ref"]
        _save_json(threads_path, threads)
    receipts_dir = ledger_dir / "receipts"
    _save_json(receipts_dir / f"{receipt['receipt_id']}.json", receipt)
    return receipt


def select_channels(
    channels: dict[str, Channel], names: list[str], status: str | None
) -> list[Channel]:
    """Resolve --to names; `all` respects each channel's when_status filter."""
    if not channels:
        raise NotifyError("No channels configured; add a [channels.<name>] table to config")
    if names == ["all"]:
        return [
            channel
            for channel in channels.values()
            if status is None or status in channel.when_status
        ]
    missing = [name for name in names if name not in channels]
    if missing:
        raise NotifyError(
            f"Unknown channel(s): {', '.join(missing)}; configured: {', '.join(sorted(channels))}"
        )
    return [channels[name] for name in names]


# ---------------------------------------------------------------------------- hook trigger

MAX_PENDING_BYTES = 256 * 1024


def spawn_background_notify(
    assistant: str,
    *,
    cwd: Path | None = None,
    created_at: str | None = None,
    launcher: Callable[[list[str], Path], None] | None = None,
) -> list[str] | None:
    """Post a just-validated brief from the Stop hook without ever blocking the host.

    Runs only when [notify] has on_stop = true, consent_network = true, and channels. The
    brief is written to a private pending file and a detached `brief-spec notify` process
    sends it, so network latency or failure never reaches the host session.
    """
    settings = notify_settings(cwd)
    if not (settings["on_stop"] and settings["consent_network"] and settings["channels"]):
        return None
    from briefspec.freshness import git_head
    from briefspec.markdown import extract_bounded

    try:
        brief = extract_bounded(assistant)
    except ValueError:
        return None
    content = brief.encode("utf-8")[:MAX_PENDING_BYTES]
    if find_secrets(content.decode("utf-8", "ignore")):
        return None
    workdir = cwd or Path.cwd()
    pending = _ledger_dir() / "pending" / f"{uuid.uuid4().hex}.md"
    atomic_write(pending, content)
    executable = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    entry = (
        [sys.executable, str(executable)]
        if executable is not None and executable.is_file()
        else [sys.executable, "-m", "briefspec"]
    )
    command = [
        *entry,
        "notify",
        str(pending),
        "--to",
        ",".join(settings["channels"]),
        "--consent-network",
        "--trigger",
        "hook",
        "--source-revision",
        git_head(workdir) or "none",
    ]
    if created_at:
        command.extend(["--created-at", created_at])
    (launcher or _launch_detached)(command, workdir)
    return command


def _launch_detached(command: list[str], cwd: Path) -> None:
    import subprocess

    options: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)  # noqa: S603


# ---------------------------------------------------------------------------- acknowledgment


def acknowledge(delivery: dict[str, Any], *, choice: str, by: str | None = None) -> dict[str, Any]:
    """Record that a human read a brief that needed them, and what they chose.

    Silence is not confirmation: a DECIDE, BLOCKED, or REVIEW brief stays unacknowledged until
    this receipt exists. The receipt binds the choice to the exact brief bytes by hash.
    """
    brief = delivery.get("brief", {})
    status = str(brief.get("status")) if brief.get("kind") == "outcome-brief" else None
    if status not in {"DECIDE", "BLOCKED", "REVIEW"}:
        raise NotifyError("Only DECIDE, BLOCKED, or REVIEW briefs need an acknowledgment")
    choice = choice.strip()
    if not choice:
        raise NotifyError("--choice must describe the decision or action taken")
    if find_secrets(choice):
        raise NotifyError("The acknowledgment text looks like it contains a credential")
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    receipt = build_receipt(
        kind="brief-spec-ack-receipt",
        schema_version="1.0",
        receipt_id=str(uuid.uuid4()),
        content_sha256=brief_identity(delivery),
        created_at=created_at,
        destination={"kind": "local", "store": "notify/acks"},
        metadata={
            "status": status,
            "choice": _clip(choice, 500),
            "by": _clip(by, 120) if by else None,
            "decision_id": delivery.get("classification", {}).get("decision_id"),
        },
    )
    _save_json(_ledger_dir() / "acks" / f"{receipt['receipt_id']}.json", receipt)
    return receipt


def acknowledgments(delivery: dict[str, Any]) -> list[dict[str, Any]]:
    digest = brief_identity(delivery)
    folder = _ledger_dir() / "acks"
    if not folder.is_dir():
        return []
    found = [_load_json(path) for path in sorted(folder.glob("*.json"))]
    return [item for item in found if item.get("content_sha256") == digest]
