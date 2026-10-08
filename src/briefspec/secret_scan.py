from __future__ import annotations

import re
from typing import Any

# High-precision credential shapes. A match blocks export, bundling, and notification, so the
# patterns favor known token formats over generic "looks random" heuristics.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Slack token", re.compile(r"\bxox[abpers]-[A-Za-z0-9-]{10,}")),
    (
        "Slack webhook URL",
        re.compile(r"hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{20,}"),
    ),
    (
        "Discord webhook URL",
        re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{20,}"),
    ),
    (
        "Teams or Power Automate webhook URL",
        re.compile(
            r"\.(?:logic\.azure\.com|webhook\.office\.com)[^\s\"'<>]*sig=[A-Za-z0-9%_-]{20,}"
        ),
    ),
    (
        "Google Chat webhook URL",
        re.compile(r"chat\.googleapis\.com/v1/spaces/[^\s]*token=[^\s&]{20,}"),
    ),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}")),
    ("webhook signing secret", re.compile(r"\bwhsec_[A-Za-z0-9+/=]{20,}")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}")),
    ("credentials in URL", re.compile(r"https?://[^\s/:@]+:[^\s/@]{6,}@")),
)


def find_secrets(text: str) -> list[str]:
    """Return the kinds of credential found in text, never the matched value."""
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(text)]


def scan_value(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Walk JSON-like data and return (field path, credential kind) pairs."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(scan_value(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(scan_value(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        found.extend((path or "value", kind) for kind in find_secrets(value))
    return found


def redact(text: str, secrets: list[str]) -> str:
    """Replace known secret values (for example resolved tokens) in diagnostic text."""
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text
