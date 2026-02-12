from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RpiCamConfig:
    width: int = 4608
    height: int = 2592
    enc: str = "jpg"           # "jpg" or "png"
    quality: int = 95
    timeout_ms: int = 300
    shutter_us: int = 8000
    gain: float = 1.0
    awb_gains: str = ""        # "R,B" or ""
    lens_pos: float | None = None

class RpiCamStill:
    def __init__(self, cfg: RpiCamConfig):
        self.cfg = cfg

    def _build_cmd(self, out_path: Path) -> list[str]:
        cmd = [
            "rpicam-still",
            "--nopreview",
            "-o", str(out_path),
            "--width", str(self.cfg.width),
            "--height", str(self.cfg.height),
            "--timeout", str(self.cfg.timeout_ms),
            "--shutter", str(self.cfg.shutter_us),
            "--gain", str(self.cfg.gain),
        ]

        if self.cfg.enc == "png":
            cmd += ["--encoding", "png"]
        else:
            cmd += ["--encoding", "jpg", "--quality", str(self.cfg.quality)]

        if self.cfg.awb_gains:
            cmd += ["--awb", "manual", "--awbgains", self.cfg.awb_gains]

        if self.cfg.lens_pos is not None:
            cmd += ["--autofocus-mode", "manual", "--lens-position", str(self.cfg.lens_pos)]

        return cmd

    def capture(self, out_path: Path) -> None:
        cmd = self._build_cmd(out_path)
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"rpicam-still failed ({e.returncode}): {e.stderr.strip()}") from e
