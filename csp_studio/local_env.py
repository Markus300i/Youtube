from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"

ALLOWED_LOCAL_KEYS = {
    "NVIDIA_API_KEY",
    "NVIDIA_NIM_BASE_URL",
    "CSP_AI_PROVIDER",
    "CSP_NIM_MODEL",
    "CSP_NIM_VISION_MODEL",
    "CSP_NIM_EMBED_MODEL",
    "CSP_NIM_TIMEOUT",
    "CSP_NIM_VISION_TIMEOUT",
    "CSP_NIM_VISION_RETRIES",
}


def _env_file() -> Path:
    override = str(os.getenv("CSP_ENV_FILE") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_ENV_FILE


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_LOCAL_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def get_local_setting(name: str, default: str | None = None) -> str | None:
    """Read a CSP setting with process environment taking precedence over local .env.

    The loader intentionally accepts only an allow-list of known CSP/NIM keys and
    never mutates os.environ, so secrets are not propagated to subprocesses unless
    callers explicitly choose to do so.
    """

    process_value = os.getenv(name)
    if process_value is not None and process_value.strip():
        return process_value
    if name not in ALLOWED_LOCAL_KEYS:
        return default
    file_value = _parse_env_file(_env_file()).get(name)
    return file_value if file_value is not None and file_value.strip() else default


def setting_source(name: str) -> str | None:
    process_value = os.getenv(name)
    if process_value is not None and process_value.strip():
        return "environment"
    if name in ALLOWED_LOCAL_KEYS:
        file_value = _parse_env_file(_env_file()).get(name)
        if file_value is not None and file_value.strip():
            return ".env"
    return None


def local_env_status() -> dict[str, object]:
    path = _env_file()
    return {
        "path": str(path),
        "exists": path.is_file(),
        "allowed_keys": sorted(ALLOWED_LOCAL_KEYS),
    }
