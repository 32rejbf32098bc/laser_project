#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import yaml

import cv2
from picamera2 import Picamera2


OUTDIR = Path("captures")
OUTDIR.mkdir(parents=True, exist_ok=True)

# ---------- Tunables ----------
EXP_STEP_US = 200
EXP_MIN_US = 100
EXP_MAX_US = 20000

LENS_STEP = 0.1
LENS_MIN = 0.0
LENS_MAX = 10.0

GAIN_STEP = 0.1
GAIN_MIN = 1.0
GAIN_MAX = 16.0

# Path to your config (adjust if needed)
CONFIG_PATH = Path("cfg/config.yaml")

def load_roi():
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    return tuple(cfg["processing"]["roi"])

ROI = load_roi()

# Camera / preview sizes
FULL_W, FULL_H = 4608, 2592
PREVIEW_W, PREVIEW_H = 1280, 720
# -----------------------------


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


picam2 = Picamera2()

# Explicit format helps keep colour conversions predictable
preview_cfg = picam2.create_preview_configuration(
    main={"size": (FULL_W, FULL_H), "format": "RGB888"}
)
picam2.configure(preview_cfg)
picam2.start()

ae_enabled = True
af_continuous = True
manual_exp_us = 2000
manual_gain = 1.0
manual_lens = 4.0

# Laser-friendly defaults
awb_enabled = False  # AWB OFF is usually what you want


def apply_controls():
    controls = {}

    # Auto Exposure
    controls["AeEnable"] = 1 if ae_enabled else 0
    if not ae_enabled:
        controls["ExposureTime"] = int(manual_exp_us)
        controls["AnalogueGain"] = float(manual_gain)

    # Auto White Balance
    controls["AwbEnable"] = 1 if awb_enabled else 0
    # Optional: lock colour gains when AWB off
    # if not awb_enabled:
    #     controls["ColourGains"] = (1.5, 1.2)

    # Autofocus
    controls["AfMode"] = 2 if af_continuous else 0
    if not af_continuous:
        controls["LensPosition"] = float(manual_lens)

    try:
        picam2.set_controls(controls)
    except Exception as ex:
        print(f"[warn] set_controls failed: {ex} (controls={controls})")


apply_controls()

print("Preview running forever.")
print("Keys:")
print("  SPACE  capture JPG+JSON")
print("  e      toggle auto exposure (AE)")
print("  w      toggle auto white balance (AWB)")
print("  [ / ]  exposure -/+ (forces AE off)")
print("  - / =  gain -/+ (forces AE off)")
print("  a      toggle autofocus continuous/manual")
print("  , / .  lens position -/+ (forces AF manual)")
print("  q/ESC  quit")

cv2.namedWindow("Pi Cam Preview (SPACE=capture)", cv2.WINDOW_NORMAL)

while True:
    # Single request per frame: image + metadata together
    req = picam2.capture_request()
    try:
        frame_rgb = req.make_array("main")  # RGB888
        md = req.get_metadata()
    finally:
        req.release()

    # Convert the same frame to BGR for OpenCV display / saving
    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    exp_us = md.get("ExposureTime")
    gain = md.get("AnalogueGain")
    lens = md.get("LensPosition")
    af_state = md.get("AfState")

    lines = [
        f"AE: {'ON' if ae_enabled else 'OFF'} | AWB: {'ON' if awb_enabled else 'OFF'} | AF: {'CONT' if af_continuous else 'MAN'}",
        f"Exp: {exp_us if exp_us is not None else '?'} us" + ("" if ae_enabled else f" (set {int(manual_exp_us)})"),
        (f"Gain: {gain:.2f}" if gain is not None else "Gain: ?") + ("" if ae_enabled else f" (set {manual_gain:.2f})"),
        f"Lens: {lens:.2f}" if lens is not None else f"Lens: ? (set {manual_lens:.2f})",
    ]
    if af_state is not None:
        lines.append(f"AF State: {af_state}")

    display = cv2.resize(frame_bgr, (PREVIEW_W, PREVIEW_H))

    # Draw ROI scaled from full-res to preview size
    x0, y0, x1, y1 = ROI
    sx = PREVIEW_W / FULL_W
    sy = PREVIEW_H / FULL_H

    x0p = int(x0 * sx)
    y0p = int(y0 * sy)
    x1p = int(x1 * sx)
    y1p = int(y1 * sy)

    cv2.rectangle(display, (x0p, y0p), (x1p, y1p), (0, 255, 0), 2)
    cv2.putText(
        display,
        "ROI",
        (x0p, max(20, y0p - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    y = 28
    for t in lines:
        cv2.putText(
            display,
            t,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        y += 30

    cv2.imshow("Pi Cam Preview (SPACE=capture)", display)

    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord("q")):
        break

    if key == ord("e"):
        ae_enabled = not ae_enabled
        apply_controls()

    if key == ord("w"):
        awb_enabled = not awb_enabled
        apply_controls()

    if key == ord("["):
        manual_exp_us = clamp(manual_exp_us - EXP_STEP_US, EXP_MIN_US, EXP_MAX_US)
        ae_enabled = False
        apply_controls()

    if key == ord("]"):
        manual_exp_us = clamp(manual_exp_us + EXP_STEP_US, EXP_MIN_US, EXP_MAX_US)
        ae_enabled = False
        apply_controls()

    if key == ord("-"):
        manual_gain = clamp(manual_gain - GAIN_STEP, GAIN_MIN, GAIN_MAX)
        ae_enabled = False
        apply_controls()

    if key == ord("="):
        manual_gain = clamp(manual_gain + GAIN_STEP, GAIN_MIN, GAIN_MAX)
        ae_enabled = False
        apply_controls()

    if key == ord("a"):
        af_continuous = not af_continuous
        apply_controls()

    if key == ord(","):
        manual_lens = clamp(manual_lens - LENS_STEP, LENS_MIN, LENS_MAX)
        af_continuous = False
        apply_controls()

    if key == ord("."):
        manual_lens = clamp(manual_lens + LENS_STEP, LENS_MIN, LENS_MAX)
        af_continuous = False
        apply_controls()

    if key == 32:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        jpg_path = OUTDIR / f"cap_{ts}.jpg"
        json_path = OUTDIR / f"cap_{ts}.json"

        cv2.imwrite(str(jpg_path), frame_bgr)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(md, f, indent=2, sort_keys=True)

        print(f"Saved {jpg_path} + {json_path}")

cv2.destroyAllWindows()
picam2.stop()