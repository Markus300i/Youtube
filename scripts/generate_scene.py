from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import yaml

from common import ROOT, load_yaml


def _resilient_wait_history(
    base_url: str,
    prompt_id: str,
    timeout: int,
    poll: int,
) -> dict[str, Any]:
    """Poll ComfyUI without failing on transient API stalls while GPU is busy.

    Some ComfyUI workloads can make /history temporarily unresponsive for more
    than the generator's old 30-second per-request timeout. A read timeout does
    not mean the submitted prompt failed, so keep polling until the configured
    global timeout expires.
    """

    deadline = time.time() + timeout
    last_error: Exception | None = None
    timeout_count = 0

    while time.time() < deadline:
        remaining = max(1.0, deadline - time.time())
        request_timeout = min(30.0, remaining)
        try:
            response = requests.get(
                f"{base_url}/history/{prompt_id}",
                timeout=request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if prompt_id in payload:
                history = payload[prompt_id]
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(
                        "Błąd wykonania ComfyUI: "
                        + json.dumps(status, ensure_ascii=False)
                    )
                if timeout_count:
                    print(
                        f"COMFY: history API recovered after {timeout_count} transient timeout(s)"
                    )
                return history
            last_error = None
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            timeout_count += 1
            print(
                "WARN: ComfyUI /history chwilowo nie odpowiada "
                f"({type(exc).__name__}); generacja nadal trwa, ponawiam polling "
                f"[{timeout_count}]"
            )

        sleep_for = max(1, int(poll))
        if time.time() + sleep_for < deadline:
            time.sleep(sleep_for)

    suffix = f"; last API error: {last_error}" if last_error else ""
    raise TimeoutError(f"Timeout ComfyUI dla prompt_id={prompt_id}{suffix}")


def _run_generator(temp_path: Path) -> int:
    """Run generate_images in-process so scene jobs can harden ComfyUI polling."""

    import generate_images

    generate_images.wait_history = _resilient_wait_history
    previous_argv = sys.argv[:]
    sys.argv = [
        str(ROOT / "scripts" / "generate_images.py"),
        str(temp_path),
        "--force",
    ]
    try:
        generate_images.main()
        return 0
    finally:
        sys.argv = previous_argv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("short_file")
    parser.add_argument("scene", type=int, choices=range(1, 9))
    args = parser.parse_args()

    data = load_yaml(args.short_file)
    selected = [scene for scene in (data.get("scenes") or []) if int(scene.get("id", 0)) == args.scene]
    if not selected:
        raise SystemExit(f"Scene {args.scene} not found in {args.short_file}")

    data["scenes"] = selected
    with tempfile.TemporaryDirectory(prefix="csp-scene-") as temp_dir:
        temp_path = Path(temp_dir) / "short.yaml"
        temp_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return _run_generator(temp_path)


if __name__ == "__main__":
    raise SystemExit(main())
