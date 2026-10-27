from __future__ import annotations

from typing import Any

from briefspec.adapters import antigravity, claude, codex, copilot
from briefspec.adapters.base import normalize_common
from briefspec.models import Runtime, RuntimeEvent


def normalize_event(
    runtime: Runtime,
    payload: dict[str, Any],
    event_name: str | None = None,
) -> RuntimeEvent:
    adapters = {
        Runtime.CODEX: codex.normalize,
        Runtime.CLAUDE: claude.normalize,
        Runtime.COPILOT: copilot.normalize,
        Runtime.ANTIGRAVITY: antigravity.normalize,
    }
    normalizer = adapters.get(runtime)
    return (
        normalizer(payload, event_name)
        if normalizer is not None
        else normalize_common(runtime, payload, event_name)
    )
