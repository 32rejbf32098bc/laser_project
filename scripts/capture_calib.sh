#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# capture_calib.sh — Capture checkerboard calibration images
# Camera: Raspberry Pi Camera Module 3
#
# Default: max resolution (4608x2592), PNG, manual exposure.
#
# Usage:
#   ./scripts/capture_calib.sh [OUT_DIR] [N] [SLEEP_S]
#
# Examples:
#   ./scripts/capture_calib.sh
#   ./scripts/capture_calib.sh data/raw/calib 25 1.2
#
# Optional environment overrides:
#   SHUTTER_US=8000 GAIN=1.0
#   AWB_GAINS="1.6,1.7"
#   LENS_POS=2.34
#   W=4608 H=2592 ENC=png
# ============================================================

OUT_DIR="${1:-data/raw/calib}"
N="${2:-25}"
SLEEP_S="${3:-1.2}"

# Camera Module 3 max still resolution (12MP 16:9)
W="${W:-4608}"
H="${H:-2592}"

# Use PNG for calibration (lossless). Set ENC=jpg if you really want.
ENC="${ENC:-png}"

# Manual exposure for consistency (tweak if too bright/dark)
SHUTTER_US="${SHUTTER_US:-8000}"   # microseconds (8000us ~ 1/125s)
GAIN="${GAIN:-1.0}"

# Optional: lock white balance (recommended once you have a good pair)
# Format: "R,B" e.g. "1.6,1.7"
AWB_GAINS="${AWB_GAINS:-}"

# Optional: lock focus (highly recommended). Set after autofocus probe.
LENS_POS="${LENS_POS:-}"

mkdir -p "$OUT_DIR"

echo "=== capture_calib.sh (Cam v3) ==="
echo "Out dir : $OUT_DIR"
echo "Images  : $N"
echo "Interval: ${SLEEP_S}s"
echo "Res     : ${W}x${H}"
echo "Enc     : $ENC"
echo "Exposure: shutter=${SHUTTER_US}us gain=${GAIN}"
echo "AWB     : ${AWB_GAINS:-auto}"
echo "Focus   : ${LENS_POS:-auto/unchanged}"
echo

# If LENS_POS not set, do one autofocus probe to get it (recommended workflow).
if [[ -z "$LENS_POS" ]]; then
  echo "Tip: For best calibration, lock focus."
  echo "Run once:"
  echo "  rpicam-still --autofocus --metadata /tmp/meta.json --width $W --height $H --encoding png -o /tmp/af_probe.png --nopreview --timeout 2000"
  echo "  grep -i LensPosition /tmp/meta.json"
  echo "Then rerun with: LENS_POS=<value> ./scripts/capture_calib.sh"
  echo
fi

for i in $(seq -w 1 "$N"); do
  TS="$(date +%Y%m%d_%H%M%S)"
  OUT="$OUT_DIR/chess_${TS}_${i}.${ENC}"

  ARGS=(--nopreview -o "$OUT" --width "$W" --height "$H" --timeout 1)

  # Encoding
  if [[ "$ENC" == "png" ]]; then
    ARGS+=(--encoding png)
  else
    ARGS+=(--encoding jpg --quality 95)
  fi

  # Manual exposure (stable across images)
  ARGS+=(--shutter "$SHUTTER_US" --gain "$GAIN")

  # Optional: lock AWB if provided
  if [[ -n "$AWB_GAINS" ]]; then
    ARGS+=(--awb manual --awbgains "$AWB_GAINS")
  fi

  # Optional: lock focus if provided (Cam v3)
  if [[ -n "$LENS_POS" ]]; then
    ARGS+=(--autofocus-mode manual --lens-position "$LENS_POS")
  fi

  echo "[${i}/${N}] Capturing: $OUT"
  rpicam-still "${ARGS[@]}"

  sleep "$SLEEP_S"
done

echo
echo "Done. Saved $(ls -1 "$OUT_DIR" | wc -l) files in $OUT_DIR"
