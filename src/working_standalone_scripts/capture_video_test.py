#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import cv2
from picamera2 import Picamera2


OUTDIR = Path("captures/video_tests")
OUTDIR.mkdir(parents=True, exist_ok=True)

# -------- Settings --------
W = 1280
H = 720
FPS = 30
DURATION_S = 10.0

SHUTTER_US = 8000
GAIN = 1.0
LENS_POS = 2.3276

AE_ENABLE = False
AWB_ENABLE = False

# Video output: MJPG AVI is simple and reliable for testing
FOURCC = "MJPG"
# --------------------------


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_path = OUTDIR / f"video_test_{ts}.avi"
    meta_path = OUTDIR / f"video_test_{ts}.json"

    picam2 = Picamera2()

    video_config = picam2.create_video_configuration(
        main={"size": (W, H), "format": "RGB888"},
        controls={"FrameRate": FPS},
    )
    picam2.configure(video_config)

    print("[INFO] Starting camera...")
    picam2.start()
    print("[INFO] Camera started")
    time.sleep(1.0)

    controls = {
        "AeEnable": 1 if AE_ENABLE else 0,
        "AwbEnable": 1 if AWB_ENABLE else 0,
        "AfMode": 0,  # manual focus
        "LensPosition": float(LENS_POS),
    }

    if not AE_ENABLE:
        controls["ExposureTime"] = int(SHUTTER_US)
        controls["AnalogueGain"] = float(GAIN)

    picam2.set_controls(controls)
    time.sleep(0.5)

    fourcc = cv2.VideoWriter_fourcc(*FOURCC)
    writer = cv2.VideoWriter(str(video_path), fourcc, FPS, (W, H))

    if not writer.isOpened():
        raise RuntimeError(f"Failed to open video writer for {video_path}")

    print(f"[INFO] Writing video to: {video_path}")
    print(f"[INFO] Duration: {DURATION_S:.2f} s")
    print(f"[INFO] Resolution: {W}x{H} @ {FPS} fps")
    print(f"[INFO] Shutter: {SHUTTER_US} us, Gain: {GAIN}, Lens: {LENS_POS}")

    frame_metadata: list[dict] = []

    start_ns = time.monotonic_ns()
    frame_idx = 0

    try:
        while True:
            now_ns = time.monotonic_ns()
            elapsed_s = (now_ns - start_ns) / 1e9
            if elapsed_s >= DURATION_S:
                break

            with picam2.captured_request() as request:
                frame_rgb = request.make_array("main")
                md = request.get_metadata() or {}

            # Convert for OpenCV writer
            # Picamera2 frame is already in the correct channel order for our OpenCV use here.
            # Do not apply RGB->BGR conversion, or the red laser appears blue.
                                        #frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            frame_bgr = frame_rgb
            writer.write(frame_bgr)

            frame_entry = {
                "frame_index": frame_idx,
                "capture_elapsed_s": elapsed_s,
                "sensor_timestamp_ns": md.get("SensorTimestamp"),
                "exposure_time_us": md.get("ExposureTime"),
                "analogue_gain": md.get("AnalogueGain"),
                "lens_position": md.get("LensPosition"),
                "frame_duration_us": md.get("FrameDuration"),
                "colour_gains": md.get("ColourGains"),
                "lux": md.get("Lux"),
            }
            frame_metadata.append(frame_entry)

            if frame_idx % 10 == 0:
                print(
                    f"[INFO] frame={frame_idx:04d} "
                    f"t={elapsed_s:6.3f}s "
                    f"SensorTimestamp={frame_entry['sensor_timestamp_ns']}"
                )

            frame_idx += 1

    except KeyboardInterrupt:
        print("\n[INFO] Recording interrupted by user")

    print("[INFO] Releasing video writer...")
    writer.release()
    print("[INFO] Video writer released")

    save = {
        "created": ts,
        "video_file": str(video_path),
        "duration_target_s": DURATION_S,
        "frames_written": frame_idx,
        "resolution": {"w": W, "h": H},
        "fps_target": FPS,
        "capture_settings": {
            "shutter_us": SHUTTER_US,
            "gain": GAIN,
            "lens_pos": LENS_POS,
            "ae_enable": AE_ENABLE,
            "awb_enable": AWB_ENABLE,
            "codec": FOURCC,
        },
        "frame_metadata": frame_metadata,
    }

    print(f"[INFO] Writing metadata to: {meta_path}")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(save, f, indent=2)
    print("[INFO] Metadata written")

    print("[INFO] Stopping camera...")
    picam2.stop()
    print("[INFO] Done")

    if frame_metadata:
        first_ts = frame_metadata[0]["sensor_timestamp_ns"]
        last_ts = frame_metadata[-1]["sensor_timestamp_ns"]
        print(f"[INFO] First SensorTimestamp: {first_ts}")
        print(f"[INFO] Last  SensorTimestamp: {last_ts}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())