#!/usr/bin/env python3
"""
Synthetic laser-line dataset generator.

Creates grayscale images resembling a laser line projected on a surface,
plus optional ground-truth mask and centerline points.

Dependencies:
  - numpy
  - opencv-python (cv2)

Example:
  python synthetic_laser_gen.py --out synth --n 200 --w 4608 --h 2592 --mask --gt
"""

from __future__ import annotations
from utils.io_utils import print_progress

import argparse
import json
import math
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Tuple, List, Dict, Any

import numpy as np
import cv2


@dataclass
class Params:
    w: int = 4608
    h: int = 2592
    n: int = 100
    seed: int = 0

    # Laser stripe characteristics
    thickness_px_min: float = 1.0
    thickness_px_max: float = 10.0
    peak_intensity_min: int = 140
    peak_intensity_max: int = 255

    # Blur / focus
    blur_sigma_min: float = 0.0
    blur_sigma_max: float = 2.0

    # Noise
    gaussian_noise_sigma_min: float = 2.0
    gaussian_noise_sigma_max: float = 15.0

    # Speckle-ish multiplicative noise
    speckle_strength_min: float = 0.00
    speckle_strength_max: float = 0.10

    # Background / texture
    bg_level_min: int = 10
    bg_level_max: int = 60
    gradient_strength_min: float = 0.0
    gradient_strength_max: float = 0.25
    texture_strength_min: float = 0.0
    texture_strength_max: float = 0.20

    # Line shape (straight line + mild warp)
    angle_deg_min: float = -5.0
    angle_deg_max: float = 5.0

    warp_enable: bool = True
    warp_amp_px_min: float = 1.0     # amplitude in pixels
    warp_amp_px_max: float = 50.0     # keep small for "almost straight"
    warp_wavelength_px_min: float = 1.0
    warp_wavelength_px_max: float = 5000.0
    warp_phase_min: float = 0.0
    warp_phase_max: float = 2.0 * math.pi


    # Occlusions (simulate dirt/shadow)
    occlusion_prob: float = 0.6
    occlusion_count_min: int = 0
    occlusion_count_max: int = 5
    occlusion_size_min: int = 20
    occlusion_size_max: int = 140
    occlusion_darkness_min: int = 20
    occlusion_darkness_max: int = 120


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_background(r: np.random.Generator, p: Params) -> np.ndarray:
    """Create a textured-ish grayscale background."""
    h, w = p.h, p.w
    base = r.integers(p.bg_level_min, p.bg_level_max + 1)

    img = np.full((h, w), float(base), dtype=np.float32)

    # Add a smooth gradient
    grad_strength = r.uniform(p.gradient_strength_min, p.gradient_strength_max)
    if grad_strength > 0:
        gx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
        gy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
        # random gradient direction
        ax = r.uniform(-1, 1)
        ay = r.uniform(-1, 1)
        g = (ax * gx + ay * gy)
        g = (g - g.min()) / (g.max() - g.min() + 1e-6)  # 0..1
        img *= (1.0 - grad_strength) + grad_strength * (0.5 + g)  # mild variation

    # Add low-frequency texture using blurred noise
    tex_strength = r.uniform(p.texture_strength_min, p.texture_strength_max)
    if tex_strength > 0:
        noise = r.normal(0, 1, size=(h, w)).astype(np.float32)
        k = int(r.integers(15, 61))  # blur kernel size
        k = k + 1 if k % 2 == 0 else k
        noise = cv2.GaussianBlur(noise, (k, k), sigmaX=0)
        noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-6)  # 0..1
        img = img * (1.0 - tex_strength) + img * (0.7 + 0.6 * noise) * tex_strength

    return img


def _line_center_y(
    x: np.ndarray,
    p0: Tuple[float, float],
    angle_rad: float,
    warp_enable: bool,
    warp_amp_px: float,
    warp_wavelength_px: float,
    warp_phase: float,
) -> np.ndarray:
    """
    Straight line + gentle sinusoidal warp in y(x).
    This keeps curvature bounded and physically plausible.
    """
    x0, y0 = p0
    m = math.tan(angle_rad)
    y = y0 + m * (x - x0)

    if warp_enable and warp_amp_px > 0:
        y = y + np.float32(warp_amp_px) * np.sin((2.0 * math.pi / warp_wavelength_px) * (x - x0) + warp_phase)

    return y



def _render_stripe(
    bg: np.ndarray,
    r: np.random.Generator,
    params: Params,
) -> Tuple[np.ndarray, np.ndarray, List[Tuple[float, float]]]:
    """
    Render a laser stripe on background.

    Returns:
      img_float: float32 image (0..255-ish)
      mask: uint8 mask 0/255 (stripe region)
      gt_pts: list of centerline points (x,y) for visible stripe (subpixel)
    """
    h, w = params.h, params.w
    img = bg.copy()

    # Random line parameters
    angle_deg = r.uniform(params.angle_deg_min, params.angle_deg_max)
    angle_rad = math.radians(angle_deg)

    # Anchor point near center
    x0 = r.uniform(0.25 * w, 0.75 * w)
    y0 = r.uniform(0.25 * h, 0.75 * h)

    warp_amp = r.uniform(params.warp_amp_px_min, params.warp_amp_px_max) if params.warp_enable else 0.0
    warp_wavelength = r.uniform(params.warp_wavelength_px_min, params.warp_wavelength_px_max) if params.warp_enable else 1e9
    warp_phase = r.uniform(params.warp_phase_min, params.warp_phase_max) if params.warp_enable else 0.0


    thickness = r.uniform(params.thickness_px_min, params.thickness_px_max)
    peak = float(r.integers(params.peak_intensity_min, params.peak_intensity_max + 1))

    # Build stripe intensity using a Gaussian across distance-to-centerline
    xs = np.arange(w, dtype=np.float32)
    ys_center = _line_center_y(
        xs,
        (x0, y0),
        angle_rad,
        params.warp_enable,
        warp_amp,
        warp_wavelength,
        warp_phase,
    ).astype(np.float32)


    # Distance from each pixel row to centerline at that column
    yy = np.arange(h, dtype=np.float32)[:, None]              # h x 1
    yc = ys_center[None, :]                                   # 1 x w
    dist = (yy - yc)                                          # h x w

    sigma = thickness / 2.355  # approx: FWHM -> sigma
    stripe = peak * np.exp(-0.5 * (dist / (sigma + 1e-6)) ** 2)

    # Clip stripe where centerline is far outside image to avoid wrap-around artifacts
    valid_cols = (ys_center > -3 * thickness) & (ys_center < h + 3 * thickness)
    stripe[:, ~valid_cols] = 0.0

    img += stripe

    # Ground-truth mask for stripe pixels above a threshold fraction of peak
    mask = (stripe > (0.25 * peak)).astype(np.uint8) * 255

    # Centerline GT points (subpixel) — sample every few pixels to keep JSON smaller
    step = max(1, w // 400)  # ~ up to 400 points
    gt_pts: List[Tuple[float, float]] = []
    for xi in range(0, w, step):
        yi = float(ys_center[xi])
        if -5.0 <= yi <= (h - 1) + 5.0:
            gt_pts.append((float(xi), yi))

    return img, mask, gt_pts


def _apply_occlusions(img: np.ndarray, r: np.random.Generator, p: Params) -> np.ndarray:
    """Random dark blobs/rectangles to simulate occlusions."""
    if r.random() > p.occlusion_prob:
        return img

    out = img.copy()
    count = int(r.integers(p.occlusion_count_min, p.occlusion_count_max + 1))
    h, w = out.shape

    for _ in range(count):
        sz = int(r.integers(p.occlusion_size_min, p.occlusion_size_max + 1))
        x = int(r.integers(0, max(1, w - sz)))
        y = int(r.integers(0, max(1, h - sz)))
        darkness = float(r.integers(p.occlusion_darkness_min, p.occlusion_darkness_max + 1))

        # Use an ellipse mask for softer look
        mask = np.zeros((sz, sz), dtype=np.float32)
        cv2.ellipse(mask, (sz // 2, sz // 2), (sz // 2, sz // 3), r.uniform(0, 180), 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sz / 10)

        patch = out[y:y+sz, x:x+sz]
        patch = patch - darkness * mask
        out[y:y+sz, x:x+sz] = patch

    return out


def _apply_noise_and_blur(img: np.ndarray, r: np.random.Generator, p: Params) -> np.ndarray:
    """Add blur, speckle-ish multiplicative noise, and Gaussian sensor noise."""
    out = img.copy().astype(np.float32)

    # Blur
    sigma_blur = r.uniform(p.blur_sigma_min, p.blur_sigma_max)
    if sigma_blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), sigmaX=sigma_blur)

    # Speckle-ish multiplicative noise (small)
    speckle = r.uniform(p.speckle_strength_min, p.speckle_strength_max)
    if speckle > 0:
        mult = 1.0 + speckle * r.normal(0, 1, size=out.shape).astype(np.float32)
        out *= mult

    # Additive Gaussian noise
    sigma_n = r.uniform(p.gaussian_noise_sigma_min, p.gaussian_noise_sigma_max)
    out += r.normal(0, sigma_n, size=out.shape).astype(np.float32)

    # Clamp to display range
    out = np.clip(out, 0, 255)
    return out


def generate_one_rgb(r: np.random.Generator, p: Params) -> Dict[str, Any]:
    """
    Generate an RGB frame where the laser is mainly in the Red channel,
    roughly matching what you get from Pi Cam v3 JPEG output.
    """
    bg = _make_background(r, p)  # float32 grayscale background (0..~255)

    # Render stripe in grayscale first (we'll inject it into channels)
    stripe_img, mask, gt_pts = _render_stripe(bg*0.0, r, p)  # stripe only

    # Base RGB background: start from grayscale but tint slightly (real cameras aren't perfectly neutral)
    bg_rgb = np.stack([bg, bg, bg], axis=-1).astype(np.float32)
    tint = r.uniform(-0.03, 0.03, size=(1, 1, 3)).astype(np.float32)
    bg_rgb *= (1.0 + tint)

    # Laser spectral behaviour: mostly red, some green, little blue
    # (tweak if you use a strong 650nm bandpass filter later)
    red_gain   = r.uniform(1.00, 1.20)
    green_leak = r.uniform(0.05, 0.18)
    blue_leak  = r.uniform(0.00, 0.06)

    laser_rgb = np.zeros_like(bg_rgb)
    laser_rgb[..., 0] = stripe_img * blue_leak   # B
    laser_rgb[..., 1] = stripe_img * green_leak  # G
    laser_rgb[..., 2] = stripe_img * red_gain    # R


    img = bg_rgb + laser_rgb

    # Optional occlusions and noise/blur (operate on each channel similarly)
    # Occlusions: apply on luminance then broadcast
    lum = img.mean(axis=-1)
    lum = _apply_occlusions(lum, r, p)
    img *= (lum / (img.mean(axis=-1) + 1e-6))[..., None]

    # Blur: slightly different per-channel (focus + demosaic-ish softness)
    sigma_blur = r.uniform(p.blur_sigma_min, p.blur_sigma_max)
    if sigma_blur > 0:
        for c in range(3):
            img[..., c] = cv2.GaussianBlur(img[..., c], (0, 0), sigmaX=sigma_blur)

    # Speckle-ish multiplicative noise
    speckle = r.uniform(p.speckle_strength_min, p.speckle_strength_max)
    if speckle > 0:
        mult = 1.0 + speckle * r.normal(0, 1, size=img.shape[:2]).astype(np.float32)
        img *= mult[..., None]

    # Additive Gaussian noise (sensor-ish)
    sigma_n = r.uniform(p.gaussian_noise_sigma_min, p.gaussian_noise_sigma_max)
    img += r.normal(0, sigma_n, size=img.shape).astype(np.float32)

    # Clamp + cast
    img = np.clip(img, 0, 255).astype(np.uint8)

    return {"img": img, "mask": mask, "gt_pts": gt_pts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=str, required=True, help="Output directory")
    ap.add_argument("--n", type=int, default=100, help="Number of images")
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mask", action="store_true", help="Write mask images")
    ap.add_argument("--gt", action="store_true", help="Write ground-truth centerline JSON")
    ap.add_argument("--png", action="store_true", help="Write PNG (default JPEG)")
    args = ap.parse_args()

    p = Params(w=args.w, h=args.h, n=args.n, seed=args.seed)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    img_dir = outdir / "images"
    img_dir.mkdir(exist_ok=True)

    mask_dir = outdir / "masks"
    if args.mask:
        mask_dir.mkdir(exist_ok=True)

    gt_path = outdir / "ground_truth.json"

    r = _rng(p.seed)
    gt_all: Dict[str, Any] = {
        "params": asdict(p),
        "items": []
    }

    ext = "png" if args.png else "jpg"
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 95] if ext == "jpg" else []

    for i in range(p.n):
        print_progress(i + 1, p.n)
        sample = generate_one_rgb(r, p)
        img = sample["img"]
        mask = sample["mask"]
        gt_pts = sample["gt_pts"]

        name = f"laser_{i:05d}.{ext}"
        img_path = img_dir / name

        ok = cv2.imwrite(str(img_path), img, encode_params)
        if not ok:
            raise RuntimeError(f"Failed to write {img_path}")

        if args.mask:
            mask_name = f"laser_{i:05d}_mask.png"
            cv2.imwrite(str(mask_dir / mask_name), mask)

        if args.gt:
            gt_all["items"].append({
                "image": str(Path("images") / name),
                "centerline_xy": gt_pts
            })

    if args.gt:
        with open(gt_path, "w", encoding="utf-8") as f:
            json.dump(gt_all, f, indent=2)

    print(f"Done. Wrote {p.n} images to: {img_dir}")
    if args.mask:
        print(f"Masks: {mask_dir}")
    if args.gt:
        print(f"Ground truth: {gt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
