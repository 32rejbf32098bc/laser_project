#!/usr/bin/env python3
"""
process_scan_centerlines.py

Batch-run laser line extraction over a scan folder of images named like:
  scan_YYYYMMDD_HHMMSS_####.jpg

Outputs:
  <outdir>/centerlines.csv  with columns: frame,y,x
  (optional) overlay images every N frames for debugging

Orientation:
  - vertical   : for each row y, find x (laser is vertical-ish in image)
  - horizontal : for each column x, find y (laser is horizontal-ish in image)

Config:
  processing:
    laser_orientation: horizontal   # or vertical
"""

import argparse
import re
from pathlib import Path
import numpy as np
import cv2

# Reuse your existing helpers
from utils.utils import load_config, ensure_dir, crop_roi, hsv_mask_red, apply_morph


INDEX_RE = re.compile(r".*_(\d{4})\.(jpg|jpeg|png)$", re.IGNORECASE)


def extract_centerline(
    mask: np.ndarray,
    min_pixels_per_line: int = 5,
    subpixel: bool = True,
    orientation: str = "vertical",
) -> np.ndarray:
    """
    Extract a centerline from a binary mask.

    Returns Nx2 array of (y, x) points.

    orientation:
      - "vertical": iterate rows y, compute x(y)
      - "horizontal": iterate cols x, compute y(x)
    """
    h, w = mask.shape[:2]
    pts = []

    orientation = orientation.lower().strip()
    if orientation not in ("vertical", "horizontal"):
        raise ValueError(f"orientation must be 'vertical' or 'horizontal', got: {orientation}")

    if orientation == "vertical":
        # For each row y: find x position(s)
        for y in range(h):
            xs = np.where(mask[y] > 0)[0]
            if xs.size < min_pixels_per_line:
                continue
            x = float(xs.mean()) if subpixel else float(xs[xs.size // 2])
            pts.append((y, x))

    else:
        # For each column x: find y position(s)
        for x in range(w):
            ys = np.where(mask[:, x] > 0)[0]
            if ys.size < min_pixels_per_line:
                continue
            y = float(ys.mean()) if subpixel else float(ys[ys.size // 2])
            pts.append((y, x))

    return np.array(pts, dtype=np.float32)


def overlay_points(bgr, pts_yx, color=(0, 255, 0)):
    out = bgr.copy()
    for y, x in pts_yx:
        cv2.circle(out, (int(round(x)), int(round(y))), 1, color, -1)
    return out


def get_frame_index(path: Path) -> int:
    m = INDEX_RE.match(path.name)
    if not m:
        return 10**9  # fallback bucket
    return int(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-dir", required=True, help="Folder with raw scan images (e.g. data/raw/scan_001)")
    ap.add_argument("--outdir", required=True, help="Output folder (e.g. data/processed/scan_001)")
    ap.add_argument("--cfg", default="cfg/config.yaml")
    ap.add_argument("--overlay-every", type=int, default=0, help="Save overlay every N frames (0 = off)")
    ap.add_argument("--max-frames", type=int, default=0, help="Limit frames for testing (0 = all)")
    # optional CLI override (otherwise use cfg)
    ap.add_argument("--orientation", choices=["vertical", "horizontal"], default=None,
                    help="Override cfg processing.laser_orientation")
    args = ap.parse_args()

    scan_dir = Path(args.scan_dir)
    outdir = Path(args.outdir)
    ensure_dir(str(outdir))

    cfg = load_config(args.cfg)

    # Orientation: cfg -> CLI override
    orientation = cfg.get("processing", {}).get("laser_orientation", "vertical")
    if args.orientation is not None:
        orientation = args.orientation
    orientation = str(orientation).lower().strip()

    # Find images
    imgs = sorted(
        list(scan_dir.glob("*.jpg")) + list(scan_dir.glob("*.JPG")) +
        list(scan_dir.glob("*.png")) + list(scan_dir.glob("*.jpeg")) + list(scan_dir.glob("*.JPEG")),
        key=lambda p: (get_frame_index(p), p.name),
    )

    if args.max_frames and args.max_frames > 0:
        imgs = imgs[: args.max_frames]

    if not imgs:
        raise SystemExit(f"No images found in {scan_dir}")

    roi = cfg["processing"].get("roi", None)
    blur_k = int(cfg["processing"].get("blur_ksize", 0))
    morph_k = int(cfg["processing"].get("morph_ksize", 0))
    morph_iters = int(cfg["processing"].get("morph_iters", 1))
    min_pixels = int(cfg["processing"].get("min_pixels_per_row", 5))  # keep key name for compatibility
    subpixel = bool(cfg["processing"].get("subpixel", True))

    rows_out = []  # list of (frame, y, x)

    print(f"Processing {len(imgs)} frames from: {scan_dir}")
    print(f"Writing to: {outdir}")
    print(f"Laser orientation: {orientation}")
    print()

    for i, img_path in enumerate(imgs):
        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            print(f"[{i+1}/{len(imgs)}] SKIP unreadable: {img_path.name}")
            continue

        img_roi, (ox, oy) = crop_roi(img, roi)

        if blur_k and blur_k > 0:
            if blur_k % 2 == 0:
                blur_k += 1
            img_roi = cv2.GaussianBlur(img_roi, (blur_k, blur_k), 0)

        mask = hsv_mask_red(
            img_roi,
            cfg["laser"]["hsv_red1_lower"], cfg["laser"]["hsv_red1_upper"],
            cfg["laser"]["hsv_red2_lower"], cfg["laser"]["hsv_red2_upper"],
        )
        mask = apply_morph(mask, ksize=morph_k, iters=morph_iters)

        pts = extract_centerline(
            mask,
            min_pixels_per_line=min_pixels,
            subpixel=subpixel,
            orientation=orientation,
        )

        # Offset points back into full image coords if ROI used
        if roi is not None and pts.size > 0:
            pts[:, 0] += oy
            pts[:, 1] += ox

        frame_idx = get_frame_index(img_path)
        if frame_idx >= 10**9:
            frame_idx = i  # fallback

        if pts.size > 0:
            for (y, x) in pts:
                rows_out.append((frame_idx, float(y), float(x)))

        # Optional overlays for debugging
        if args.overlay_every and args.overlay_every > 0 and (i % args.overlay_every) == 0:
            overlay = overlay_points(img, pts)
            cv2.imwrite(str(outdir / f"{img_path.stem}_overlay.jpg"), overlay)

        print(f"[{i+1}/{len(imgs)}] frame {frame_idx:04d}: {pts.shape[0]} pts")

    # Save combined CSV
    out_csv = outdir / "centerlines.csv"
    if rows_out:
        arr = np.array(rows_out, dtype=np.float32)
        np.savetxt(str(out_csv), arr, delimiter=",", header="frame,y,x", comments="")
        print(f"\nSaved: {out_csv}  ({arr.shape[0]} points total)")
    else:
        print("\nNo points detected across the scan.")
        print("Likely causes: exposure too low, HSV thresholds wrong, or laser not present.")

    print("Done.")


if __name__ == "__main__":
    main()
