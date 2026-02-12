from gpiozero import DigitalOutputDevice
from time import sleep

# Pins (BCM)
STEP = 23
DIR  = 24

step = DigitalOutputDevice(STEP)
dirp = DigitalOutputDevice(DIR)

# Enable driver (LOW = enable)

#dirp.off() # Left
dirp.on() # Right

print("Stepper running... Ctrl+C to stop")
try:
	while True:
		step.on();  sleep(0.001)
		step.off(); sleep(0.001)
		
except KeyboardInterrupt:
    print("Stopping...")

finally:
    # Disable driver + ensure STEP is low
    step.off()
    dirp.off()
    step.close()
    dirp.close()
    print("Stopped")
