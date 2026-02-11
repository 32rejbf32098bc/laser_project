# Laser Profiling Project — Working Notes

Owner: Luis Nunes
Project: Low-Cost Laser-Based Pipe Inspection
Start: Feb 2026

---

## 2026-02-04

### Setup
- Raspberry Pi 5 + Camera Module 3 configured
- Remote access via VS Code SSH working
- Git repository initialised and pushed to remote
- Project directory structure established

### Camera Testing
- Implemented `capture_calib.sh` for automated image capture
- Verified 4608×2592 max-resolution still capture
- Autofocus probe performed
- Focus lock value obtained: LENS_POS = 7.879
- Tested exposure settings (shutter ~8000 us, gain 1.0)
- Current settings considered exploratory only

### Calibration Pipeline
- Calibration capture workflow tested using wall images
- No checkerboard available yet (to be printed next day)
- Verified file naming and output directories

### Processing Pipeline
- Laser extraction script tested on sample images
- HSV masking and centreline extraction functional
- Confirmed CSV and overlay outputs
- Centreline currently computed using a row-wise centroid of detected laser pixels
- Works well for a single thin, dominant laser stripe

### Issues
- PNG encoding occasionally failed due to YUV/BGR mismatch
- `--format` option unsupported on current rpicam build
- Resolved by keeping default capture arguments
- Occasional confusion from reusing output directories

### Documentation
- Updated LaTeX planning/methods document
- Created `docs/notes.md` for informal development logging
- Overleaf project successfully linked to GitHub repository
- LaTeX source files now synchronised between Overleaf, GitHub, and local Pi
- Figures stored in docs/figures and referenced directly in report
- VS Code workflow verified via git pull/push
- Documentation and code now maintained in single unified repository

### Notes
- Current camera parameters are provisional
- Final focus and exposure to be set under rigid lab mounting
- Full calibration will be repeated in controlled environment
- Centreline method is sensitive to reflections and multiple blobs per row
- Not suitable for ring, ellipse, or arc laser patterns
- More robust extraction methods may be required later
- Centreline method assumes near-vertical stripe (one intersection per image row)
- Horizontal or near-horizontal lines are not handled correctly

### Next
- Print checkerboard calibration target
- Mount camera and laser in lab setup
- Re-run autofocus and exposure tuning
- Capture full calibration dataset (20–30 images)
- Perform intrinsic calibration

---

## 2026-02-05

### Setup
- Continued development using VS Code Remote SSH
- Verified stable connection via local network and Tailscale
- Tested tmux for persistent remote sessions

### Camera & Calibration
- Performed two full camera calibration runs

#### Calibration Run 1 (A4 Checkerboard)
- Used A4-sized checkerboard (7×10 inner corners)
- Captured ≈30 calibration images
- Initial exposure tuning (8000–12000 µs)
- Focus partially locked
- Calibration produced higher reprojection error

#### Calibration Run 2 (A5 Checkerboard)
- Used smaller A5-sized checkerboard (7×10 inner corners)
- Improved framing and coverage in images
- Tuned exposure range (6000–8000 µs)
- Focus locked using metadata
- Captured ≈35 calibration images
- Generated significantly improved results

### Processing
- Ran `calibrate_camera.py` on both datasets
- Generated debug corner images
- Verified successful corner detection on majority of frames

### Results
- Best calibration (A5 board):
  - RMS error ≈ 1.16 px
  - Mean reprojection error ≈ 1.16 px
  - Saved to `calib2/camera.yaml`
- A4 board calibration showed poorer accuracy

### Issues
- Occasional corrupted PNG files
- Preview window unreliable over SSH
- Limited ability to monitor framing remotely

### Notes
- Smaller checkerboard improved corner coverage
- Better distribution across image area
- Exposure and focus stability critical for calibration quality
- Current intrinsics suitable for laser triangulation

### Next
- Finalise camera mounting
- Begin laser integration and testing
- Prepare laser plane calibration setup

--

## 2026-02-06

### Mechanical Setup
- Assembled aluminium extrusion frame
- Mounted camera and sample platform
- Prepared fixture for checkerboard mounting
- Awaiting laser module delivery

### 3D Printing
- Designed and printed surface test samples:
  - Cracks
  - Grooves
  - Steps
  - Roughness patterns
- Printed camera and laser mounting brackets
- Cut backing plate for calibration board

### Design & CAD
- Created parametric base model in Fusion 360
- Implemented derived part workflow for sample variants
- Added embossed identifiers to test blocks
- Investigated limitations of parameter inheritance

### Motion System
- Planned lead screw + linear rail mechanism
- Selected TB67S581FNG stepper driver
- Designed preliminary wiring layout (Pi + driver + motor)

### Documentation
- Linked Overleaf project to GitHub repository
- Synced LaTeX report and figures directory
- Updated project planning notes

### Issues
- Derived parameters not editable in child models
- Text features locked in derived components
- Limited preview feedback during captures

### Notes
- Test samples now suitable for controlled validation
- Mechanical rig nearing readiness
- System ready for laser calibration phase

### Next
- Integrate laser module (waiting for laser arival)
- Perform laser plane calibration
- Validate triangulation accuracy
- Begin controlled scanning experiments
- Order stepper driver and pi active cooling module.

--

## 2026-02-10

### Stepper Motor Integration (Initial Testing)

- Began integrating NEMA 17 stepper motor with motor driver and Raspberry Pi 5
- Attempted wiring using initial driver board and DuPont connections
- Set up STEP/DIR/EN control via GPIO (gpiozero and RPi.GPIO)
- Wrote and tested basic step pulse scripts

### Issues Encountered
- Motor failed to rotate despite correct logic signals
- Driver overheated during early tests
- Inconsistent current draw from PSU
- ENABLE pin behaviour unclear
- Motor occasionally jittered when idle
- Suspected wiring and current-limit issues

### GPIO / Hardware Debugging

- Ran `pintest` utility to verify GPIO functionality
- Test reported failures on:
  - GPIO17
  - GPIO22
  - GPIO27
- Pins failed to drive HIGH or configure pull-ups correctly
- Same errors observed with nothing connected
- Suggested possible:
  - Pin damage
  - Driver conflict
  - Kernel / firmware issue
- Avoided unreliable pins in later wiring

### Debugging

- Measured coil resistance to verify motor health
- Checked VMOT, VDD, and GND connections
- Tested multiple GPIO pin combinations
- Added pull-down resistor to STEP line to reduce noise
- Investigated capacitor requirements for driver stability

### Outcome
- Stepper system not fully operational
- Likely driver configuration or hardware issue
- Possible GPIO reliability problems identified
- Decided to replace driver with newer TB67S581FNG driver board

### Notes
- Identified importance of correct SLEEP/RESET and VREF setup
- Learned current limiting and grounding are critical for stability

--

## 2026-02-11

### Stepper Motor System (Successful Setup)

- Installed and soldered headers onto new TB67S581FNG driver board
- Correctly wired VMOT, GND, STEP, DIR, SLEEP, RESET, and motor coils
- Configured SLEEP and RESET high to enable driver
- Adjusted VREF to ~0.42 V for safe current limiting
- Verified stable current draw and motor locking behaviour

### Motion Control

- Set microstepping to 1/8 via MODE pins
- Implemented distance-based motion control (mm → steps)
- Calculated lead screw resolution (TR8×8, 8 mm/rev)
- Confirmed reliable linear motion

### Scan Automation

- Developed `scan_step_capture.py` script
- Integrated:
  - Stepper motion
  - rpicam-still capture
  - Configurable step size
  - Total scan distance
- Added automatic return-to-start at end of scan
- Implemented terminal progress bar for scan monitoring

### Camera Integration

- Integrated camera capture with motor stepping
- Tuned shutter and gain for scan images
- Reduced terminal spam using `--quiet` flag
- Added error handling for failed captures

### Debugging

- Resolved GPIO allocation issues
- Fixed EN pin and MODE wiring problems
- Identified importance of common ground
- Diagnosed failed driver behaviour from earlier setup

### Outcome

- Fully working motor + camera scan pipeline
- System now capable of:
  - Moving precise distances
  - Capturing synchronized images
  - Performing automated linear scans

### Notes

- 1/8 microstepping chosen as balance between smoothness and reliability
- Digital zoom avoided; physical positioning preferred
- Platform now ready for laser integration

### Next

- Integrate laser module
- Implement laser plane calibration
- Run full structured-light scan tests
- Begin multi-frame 3D reconstruction pipeline
