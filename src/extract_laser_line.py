import argparse
import numpy as np
import cv2
from utils import load_config, ensure_dir, crop_roi, hsv_mask_red, apply_morph

def extract_centerline(mask: np.ndarray, min_pixels_per_row: int = 5, subpixel: bool = True):
    """
    Returns list of (row_y, col_x) points in image coordinates for each row that contains enough laser pixels.
    If subpixel=True, uses intensity-weighted center-of-mass on the binary mask.
    """
    h, w = mask.shape[:2]
    pts = []

    # For each row, find x position of laser
    for y in range(h):
        xs = np.where(mask[y] > 0)[0]
        if xs.size < min_pixels_per_row:
            continue

        if subpixel:
            # Weighted center (on binary mask, weights all 1s)
            x = float(xs.mean())
        else:
            x = float(xs[xs.size // 2])

        pts.append((y, x))

    return np.array(pts, dtype=np.float32)  # (N,2) with (y,x)

def overlay_points(bgr, pts_yx, color=(0, 255, 0)):
    out = bgr.copy()
    for y, x in pts_yx:
        cv2.circle(out, (int(round(x)), int(round(y))), 1, color, -1)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfg", default="cfg/config.yaml")
    ap.add_argument("--in", dest="inp", required=True, help="Input image path")
    ap.add_argument("--outdir", default="data/processed")
    args = ap.parse_args()

    cfg = load_config(args.cfg)
    ensure_dir(args.outdir)

    img = cv2.imread(args.inp, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Could not read image: {args.inp}")

    roi = cfg["processing"].get("roi", None)
    blur_k = int(cfg["processing"].get("blur_ksize", 0))
    morph_k = int(cfg["processing"].get("morph_ksize", 0))
    morph_iters = int(cfg["processing"].get("morph_iters", 1))

    img_roi, (ox, oy) = crop_roi(img, roi)

    if blur_k and blur_k > 0:
        if blur_k % 2 == 0:
            blur_k += 1
        img_roi = cv2.GaussianBlur(img_roi, (blur_k, blur_k), 0)

    mask = hsv_mask_red(
        img_roi,
        cfg["laser"]["hsv_red1_lower"], cfg["laser"]["hsv_red1_upper"],
        cfg["laser"]["hsv_red2_lower"], cfg["laser"]["hsv_red2_upper"]
    )

    mask = apply_morph(mask, ksize=morph_k, iters=morph_iters)

    pts = extract_centerline(
        mask,
        min_pixels_per_row=int(cfg["processing"].get("min_pixels_per_row", 5)),
        subpixel=bool(cfg["processing"].get("subpixel", True))
    )

    # Offset points back into full image coords if ROI used
    if roi is not None and pts.size > 0:
        pts[:, 0] += oy
        pts[:, 1] += ox

    overlay = overlay_points(img, pts)

    base = args.inp.split("/")[-1].rsplit(".", 1)[0]
    cv2.imwrite(f"{args.outdir}/{base}_mask.png", mask)
    cv2.imwrite(f"{args.outdir}/{base}_overlay.png", overlay)

    # Save points as CSV
    if pts.size > 0:
        np.savetxt(f"{args.outdir}/{base}_centerline.csv", pts, delimiter=",", header="y,x", comments="")
        print(f"Saved {pts.shape[0]} points.")
    else:
        print("No laser line points detected. Tune HSV thresholds / exposure.")

    print(f"Wrote outputs to {args.outdir}/")

if __name__ == "__main__":
    main()
