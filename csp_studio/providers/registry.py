from __future__ import annotations

from typing import Any

from ..local_env import get_local_setting
from .base import ProviderError
from .nvidia_nim import NvidiaNimProvider


def get_provider(name: str | None = None, **kwargs: Any):
    selected = (name or get_local_setting("CSP_AI_PROVIDER") or "nvidia_nim").strip().lower()
    aliases = {
        "nim": "nvidia_nim",
        "nvidia": "nvidia_nim",
        "nvidia-nim": "nvidia_nim",
    }
    selected = aliases.get(selected, selected)
    if selected == "nvidia_nim":
        return NvidiaNimProvider(**kwargs)
    raise ProviderError(f"Unknown CSP AI provider: {selected}")
