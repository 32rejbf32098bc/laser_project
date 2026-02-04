import yaml
import numpy as np
import cv2
from pathlib import Path

def load_config(cfg_path: str):
	with open(cfg_path, "r") as f:
		return yaml.safe_load(f)	

def ensure_dir(p: str):
	Path(p).mkdir(parents=True, exist_ok=True)
	
def crop_roi(img, roi):
	if roi is None:
		return img, (0, 0)
	x, y, w, h = roi
	return img[y:y+h, x:x+w], (x, y)
	
def hsv_mask_red(bgr, red1_lo, red1_hi, red2_lo, red2_hi):
	hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
	lo1 = np.array(red1_lo, dtype=np.uint8)
	hi1 = np.array(red1_hi, dtype=np.uint8)
	lo2 = np.array(red2_lo, dtype=np.uint8)
	hi2 = np.array(red2_hi, dtype=np.uint8)
	m1 = cv2.inRange(hsv, lo1, hi1)
	m2 = cv2.inRange(hsv, lo2, hi2)
	return cv2.bitwise_or(m1, m2)

def apply_morph(mask, ksize=3,iters=1):
	if not ksize or ksize <= 0:
		return mask
	k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
	out = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=iters)
	out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, k, iterations=iters)
	return out
