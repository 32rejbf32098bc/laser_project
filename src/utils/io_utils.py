# utils/io_utils.py
from __future__ import annotations
from pathlib import Path

import sys
import time
import json
import yaml
import shutil

def ensure_dir(path: str | Path) -> Path:
    """Create a directory if needed and return it as Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_config(cfg_path: str | Path) -> dict:
    """Load YAML config into dict."""
    cfg_path = Path(cfg_path)
    with cfg_path.open("r") as f:
        return yaml.safe_load(f)

def now_stamp() -> str:
    """Timestamp string for filenames."""
    return time.strftime("%Y%m%d_%H%M%S")


def print_progress(k: int, n: int):
    cols = shutil.get_terminal_size((80, 20)).columns
    bar_w = max(10, cols - 30)

    frac = k / n
    filled = int(bar_w * frac)
    bar = "█" * filled + "░" * (bar_w - filled)

    sys.stdout.write(f"   \r[{bar}] {k}/{n} ({frac*100:5.1f}%)")
    sys.stdout.flush()

    if k == n:
        sys.stdout.write("\n")

def save_json(path: Path | str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

