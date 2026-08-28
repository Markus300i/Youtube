from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from common import ROOT, load_yaml


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
        command = [
            sys.executable,
            str(ROOT / "scripts" / "generate_images.py"),
            str(temp_path),
            "--force",
        ]
        return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
