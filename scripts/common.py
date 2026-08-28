from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.getenv("CSP_OUTPUT_DIR", str(ROOT / "output"))).expanduser()
if not OUTPUT_ROOT.is_absolute():
    OUTPUT_ROOT = (ROOT / OUTPUT_ROOT).resolve()


def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    ascii_value = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-")
    return ascii_value.lower() or "short"


def short_output_dir(short: dict) -> Path:
    slug = f"{short['id']}-{slugify(str(short['title']))}"
    path = OUTPUT_ROOT / slug
    path.mkdir(parents=True, exist_ok=True)
    return path
