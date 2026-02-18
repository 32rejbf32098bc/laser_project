#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# capture_calib.sh — Capture checkerboard calibration images
# Camera: Raspberry Pi Camera Module 3 (IMX708)
#
# Usage:
#   ./scripts/capture_calib.sh [OUT_DIR] [N] [SLEEP_S]
#
# Optional env overrides:
#   SHUTTER_US=8000 GAIN=1.0
#   AWB_GAINS="2.0,1.9"      # "R,B"  (locks AWB)
#   LENS_POS=2.3276          # locks focus (recommended!)
#   W=4608 H=2592 ENC=png
#   MODE="4608:2592"         # try to force sensor mode (if supported)
#   SAVE_META=1              # save metadata JSON per frame
#   PROBE_AF=1               # run autofocus probe, print LensPosition, exit
# ============================================================

OUT_DIR="${1:-data/raw/calib}"
N="${2:-30}"
SLEEP_S="${3:-1.0}"

W="${W:-4608}"
H="${H:-2592}"
ENC="${ENC:-png}"

SHUTTER_US="${SHUTTER_US:-6000}"
GAIN="${GAIN:-1.0}"

AWB_GAINS="${AWB_GAINS:-}"
LENS_POS="${LENS_POS:-}"

MODE="${MODE:-4608:2592}"
SAVE_META="${SAVE_META:-0}"
PROBE_AF="${PROBE_AF:-0}"

mkdir -p "$OUT_DIR"

echo "=== capture_calib.sh (Cam v3) ==="
echo "Out dir  : $OUT_DIR"
echo "Images   : $N"
echo "Interval : ${SLEEP_S}s"
echo "Res      : ${W}x${H}"
echo "Mode     : ${MODE:-auto}"
echo "Enc      : $ENC"
echo "Exposure : shutter=${SHUTTER_US}us gain=${GAIN}"
echo "AWB      : ${AWB_GAINS:-auto}"
echo "Focus    : ${LENS_POS:-auto/unchanged}"
echo "Meta     : SAVE_META=$SAVE_META"
echo

# ------------------------------------------------------------
# Autofocus probe mode (one shot)
# ------------------------------------------------------------
if [[ "$PROBE_AF" == "1" ]]; then
  echo "Running autofocus probe (one shot)..."
  PROBE_IMG="$OUT_DIR/af_probe.${ENC}"
  PROBE_META="$OUT_DIR/af_probe_meta.json"

  ARGS=(--width "$W" --height "$H" --timeout 2000 )

  # Try force mode (ignore failure)
  if [[ -n "$MODE" ]]; then
    ARGS+=(--mode "$MODE")
  fi

  if [[ "$ENC" == "png" ]]; then
    ARGS+=(--encoding png -o "$PROBE_IMG")
  else
    ARGS+=(--encoding jpg --quality 95 -o "$PROBE_IMG")
  fi

  # Allow autofocus, dump metadata
  ARGS+=(--autofocus --metadata "$PROBE_META")

  rpicam-still "${ARGS[@]}"

  echo
  echo "Saved: $PROBE_IMG"
  echo "Meta : $PROBE_META"
  echo
  echo "LensPosition:"
  python3 - <<'PY'
import json, sys
p = sys.argv[1]
with open(p,"r") as f:
    d = json.load(f)
print(d.get("LensPosition", "NOT_FOUND"))
PY
"$PROBE_META"

  echo
  echo "Now rerun with: LENS_POS=<that_value> AWB_GAINS=<optional> ./scripts/capture_calib.sh"
  exit 0
fi

# ------------------------------------------------------------
# Main capture loop
# ------------------------------------------------------------
for i in $(seq -w 1 "$N"); do
  TS="$(date +%Y%m%d_%H%M%S_%3N)"   # includes milliseconds
  OUT="$OUT_DIR/chess_${TS}_${i}.${ENC}"

  ARGS=(--width "$W" --height "$H" --timeout 2000 -o "$OUT")

  # Try to force sensor mode (if supported). If it errors, you’ll see it.
  if [[ -n "$MODE" ]]; then
    ARGS+=(--mode "$MODE")
  fi

  # Encoding
  if [[ "$ENC" == "png" ]]; then
    ARGS+=(--encoding png)
  else
    ARGS+=(--encoding jpg --quality 95)
  fi

  # Manual exposure (stable across images)
  ARGS+=(--shutter "$SHUTTER_US" --gain "$GAIN")

  # Optional: lock AWB
  if [[ -n "$AWB_GAINS" ]]; then
    ARGS+=(--awb manual --awbgains "$AWB_GAINS")
  fi

  # Optional: lock focus
  if [[ -n "$LENS_POS" ]]; then
    ARGS+=(--autofocus-mode manual --lens-position "$LENS_POS")
  fi

  # Optional: metadata per frame
  if [[ "$SAVE_META" == "1" ]]; then
    ARGS+=(--metadata "$OUT_DIR/chess_${i}_meta.json")
  fi

  echo "[${i}/${N}] Capturing: $OUT"
  rpicam-still "${ARGS[@]}"

  sleep "$SLEEP_S"
done

echo
echo "Done. Saved $N images in $OUT_DIR"
