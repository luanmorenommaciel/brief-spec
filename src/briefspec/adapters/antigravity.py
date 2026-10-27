from __future__ import annotations

from typing import Any

from briefspec.adapters.base import normalize_common
from briefspec.models import Runtime, RuntimeEvent


def normalize(payload: dict[str, Any], event_name: str | None = None) -> RuntimeEvent:
    return normalize_common(Runtime.ANTIGRAVITY, payload, event_name)
