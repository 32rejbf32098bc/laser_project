# Laser Line 3D Surface Scanner (Raspberry Pi)

This project implements a low-cost **laser triangulation surface scanning system**
using a Raspberry Pi, stepper-driven linear stage, and camera.

It captures sequential images of a laser line projected onto a surface and
reconstructs a 3D profile using computer vision.

---

## 📸 System Overview

The system consists of:

- Raspberry Pi 5
- Camera Module (IMX708 / Camera v3)
- Red line laser
- NEMA 17 stepper motor
- Lead screw linear stage (TR8x8)
- Stepper motor driver (TB67S581FNG / similar)
- Custom Python control and processing pipeline

The stage moves the sample through the laser line while images are captured.
Each frame is processed to extract the laser centerline for 3D reconstruction.

---

## ⚙️ Features

- Stepper-controlled linear scanning
- Configurable microstepping (1/8, 1/16, 1/32)
- Automated image capture with `rpicam-still`
- Batch laser line extraction
- Sub-pixel centerline estimation
- CSV export for 3D reconstruction
- Optional overlay debugging images

---

## 📁 Project Structure

```text
laser_project/
├── src/                     # Main scripts
│   ├── scan_step_capture.py
│   ├── process_scan_centerlines.py
│
├── cfg/                     # Configuration files
│   └── config.yaml
│
├── data/
│   ├── raw/                 # Raw captured scans (ignored by git)
│   └── processed/           # Processed outputs (ignored by git)
│
├── utils/                   # Helper functions
│
└── README.md
```
---

## Hardware Setup

### Stepper Motor

- NEMA17 stepper (1.8° per step)
- Lead screw: TR8x8 (8 mm per revolution)
- Microstepping: typically 1/8

### Driver

- TB67S581FNG carrier
- VMOT: 12 V
- Logic: 3.3 V from Pi
- VREF set to limit current
- Decoupling capacitor (≥100 µF) on VMOT

### Wiring (BCM)

| Function | GPIO |
|----------|------|
| STEP     | 23   |
| DIR      | 24   |
| EN       | Optional |

Motor coils connect to A1/A2 and B1/B2 on driver.

### Camera

- Raspberry Pi Camera Module 3
- Fixed focus using manual lens position
- Mounted rigidly to avoid recalibration

---

## Software Setup

### Dependencies:

sudo apt update
sudo apt install -y python3-opencv python3-yaml git
pip install gpiozero numpy

### Enable camera:

sudo raspi-config
Enable Camera
sudo reboot

---

## Scanning Workflow

### Capture

python3 src/scan_step_capture.py \
  --outdir data/raw/scan_001 \
  --total-mm 120 \
  --step-mm 1.0 \
  --mm-per-s 20 \
  --microstep 8

### Process

python3 src/process_scan_centerlines.py \
  --scan-dir data/raw/scan_001 \
  --outdir data/processed/scan_001 \
  --cfg cfg/config.yaml \
  --orientation horizontal

---

## Output

centerlines.csv format:

frame,y,x
0,1240.5,2312.3
0,1241.5,2312.8

---

## Calibration

Recalibrate if any of the following change:

- Focus
- Resolution
- Camera position
- Laser angle
- Mounting geometry

Keep lens position fixed for repeatability.

---

## Development Notes

- Lock exposure and gain
- Lock focus
- Tune VREF correctly
- Manual homing using hard stops
- 1/8 microstep ≈ 0.005 mm per microstep

---

## Limitations

- No limit switches
- No absolute encoder
- Manual homing
- Mesh generation in progress

---

## Future Work

- Limit switches
- Continuous scan mode
- Full triangulation
- Point cloud export
- Defect detection

---

## Git Usage on Pi

Initial setup:

git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <REPO_URL>
git push -u origin main

Update:

git add -A
git commit -m "Update"
git push

---

## Author

Luis Nunes
University of Bristol — MEng Mechanical Engineering (2026)
