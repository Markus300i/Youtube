from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TextIO

DEFAULT_LOG_TAIL_BYTES = 64 * 1024
DEFAULT_ERROR_MESSAGE_CHARS = 4000

_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)(\bauthorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)([\"']?\b[A-Z0-9_.-]*"
    r"(?:API[_-]?KEY|ACCESS[_-]?KEY(?:[_-]?ID)?|TOKEN|PASSWORD|PASSWD|SECRET)"
    r"[A-Z0-9_.-]*\b[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SENSITIVE_CLI_PATTERN = re.compile(
    r"(?i)(\B--?[A-Z0-9_.-]*"
    r"(?:API[-_]?KEY|TOKEN|PASSWORD|PASSWD|SECRET)"
    r"[A-Z0-9_.-]*\s+)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)

_SENSITIVE_PATTERNS = (
    _AUTHORIZATION_PATTERN,
    _SENSITIVE_ASSIGNMENT_PATTERN,
    _SENSITIVE_CLI_PATTERN,
)


def redact_sensitive_text(content: str) -> str:
    """Mask common credential forms before text is persisted or displayed."""

    for pattern in _SENSITIVE_PATTERNS:
        content = pattern.sub(r"\1[REDACTED]", content)
    return content


def sanitize_error_message(
    message: str,
    *,
    max_chars: int = DEFAULT_ERROR_MESSAGE_CHARS,
) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    redacted = redact_sensitive_text(message)
    if len(redacted) <= max_chars:
        return redacted
    return redacted[: max_chars - 1] + "…"


def safe_exception_message(exc: BaseException) -> str:
    return sanitize_error_message(f"{type(exc).__name__}: {exc}")


def sanitize_persisted_task_error(message: str) -> str:
    summary = str(message).split(" | log:", 1)[0]
    return sanitize_error_message(summary)


def copy_redacted_stream(source: TextIO, target: TextIO) -> None:
    """Copy process output line-by-line without persisting recognized secrets."""

    for line in source:
        target.write(redact_sensitive_text(line))
        target.flush()


def _drop_leading_partial_line(chunk: bytes, previous_byte: bytes) -> bytes:
    if previous_byte in {b"\n", b"\r"}:
        return chunk

    breaks = [index for marker in (b"\n", b"\r") if (index := chunk.find(marker)) >= 0]
    if not breaks:
        return b""
    chunk = chunk[min(breaks) + 1 :]
    if chunk.startswith(b"\n"):
        chunk = chunk[1:]
    return chunk


def read_redacted_log_tail(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_LOG_TAIL_BYTES,
) -> dict[str, Any]:
    """Read a bounded, line-safe and redacted tail from a task log."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    target = Path(path)
    if not target.is_file():
        return {
            "available": False,
            "content": "",
            "size_bytes": 0,
            "truncated": False,
            "max_bytes": max_bytes,
        }

    size = target.stat().st_size
    start = max(0, size - max_bytes)
    with target.open("rb") as handle:
        if start > 0:
            handle.seek(start - 1)
            previous_byte = handle.read(1)
        else:
            previous_byte = b"\n"
        handle.seek(start)
        chunk = handle.read(max_bytes)

    if start > 0:
        chunk = _drop_leading_partial_line(chunk, previous_byte)

    content = chunk.decode("utf-8", errors="replace")
    return {
        "available": True,
        "content": redact_sensitive_text(content),
        "size_bytes": size,
        "truncated": start > 0,
        "max_bytes": max_bytes,
    }
