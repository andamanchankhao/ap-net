"""
PIR motion sensor and MOSFET power-latch control for the AP-NET edge node.

IMPORTANT for Raspberry Pi 5: RPi.GPIO does not work there. The Pi 5 moved GPIO behind a
new interface, so use gpiozero (which selects the lgpio backend automatically on
Bookworm) or lgpio directly. Both are covered here; RPi.GPIO deliberately is not.

WIRING (Planning.md 3)

    HC-SR501 PIR              Raspberry Pi
      VCC  -------------------- 5V   (the module needs 4.5-20 V)
      GND  -------------------- GND
      OUT  -------------------- GPIO 4      clean 3.3 V TTL, safe on a Pi input

    MOSFET latch
      HOLD -------------------- GPIO 17     drive HIGH to keep the power gate closed

THE WAKE SEQUENCE

    1. PIR sees a moving thermal source and pulses OUT high.
    2. That pulse closes the P-channel MOSFET latch; the Pi receives power and boots.
    3. The PIR pulse is short (seconds, set by the on-board potentiometer), so the Pi must
       assert GPIO 17 HIGH as early as possible to hold the gate closed itself.
    4. After capture, inference and transmission, the Pi pulls GPIO 17 LOW and halts.
       The latch opens and the whole trap returns to ~0 W until the next intrusion.

Step 3 is the one that bites: if the software takes longer to reach the hold-high line
than the PIR's pulse lasts, power drops mid-boot and the trap wedges in a boot loop. Call
PowerLatch.hold() before any slow import or model load.
"""

import os
import sys
import time
import threading

DEFAULT_PIR_PIN = 4
DEFAULT_LATCH_PIN = 17


class GPIOUnavailable(RuntimeError):
    pass


def _load_gpiozero():
    from gpiozero import MotionSensor, DigitalOutputDevice   # noqa: F401
    import gpiozero
    return gpiozero


# =============================================================================
# PIR motion sensor
# =============================================================================
class PIRSensor:
    """
    HC-SR501 passive infrared sensor.

    backend="auto" tries gpiozero, then lgpio, then falls back to a mock that reports
    motion on a timer so the field pipeline can be exercised off-hardware.
    """

    def __init__(self, pin=DEFAULT_PIR_PIN, backend="auto", mock_interval=5.0, log=print):
        self.pin = pin
        self.log = log
        self.backend = None
        self._mock_interval = mock_interval
        self._device = None
        self._lgpio = None
        self._handle = None

        candidates = ("gpiozero", "lgpio", "mock") if backend == "auto" else (backend,)
        errors = []

        for name in candidates:
            try:
                getattr(self, f"_init_{name}")()
                self.backend = name
                log(f"[PIR] Backend: {name} on GPIO {pin}")
                return
            except Exception as e:
                errors.append(f"{name}: {e}")

        raise GPIOUnavailable("no PIR backend available:\n  " + "\n  ".join(errors))

    def _init_gpiozero(self):
        gpiozero = _load_gpiozero()
        self._device = gpiozero.MotionSensor(self.pin)

    def _init_lgpio(self):
        import lgpio
        self._lgpio = lgpio
        self._handle = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self._handle, self.pin)

    def _init_mock(self):
        self._mock_next = time.time() + self._mock_interval

    # --- reading ---
    @property
    def motion_detected(self):
        if self.backend == "gpiozero":
            return bool(self._device.motion_detected)
        if self.backend == "lgpio":
            return bool(self._lgpio.gpio_read(self._handle, self.pin))
        if time.time() >= self._mock_next:
            self._mock_next = time.time() + self._mock_interval
            return True
        return False

    def wait_for_motion(self, timeout=None, stop_event=None, poll_interval=0.05):
        """
        Block until motion is detected. Returns True on motion, False on timeout/stop.

        gpiozero's own wait_for_motion is used when available (interrupt-driven, so the
        CPU stays idle); other backends poll.
        """
        if self.backend == "gpiozero" and stop_event is None:
            return bool(self._device.wait_for_motion(timeout=timeout))

        deadline = None if timeout is None else time.time() + timeout
        while True:
            if stop_event is not None and stop_event.is_set():
                return False
            if self.motion_detected:
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(poll_interval)

    def close(self):
        try:
            if self.backend == "gpiozero" and self._device is not None:
                self._device.close()
            elif self.backend == "lgpio" and self._handle is not None:
                self._lgpio.gpiochip_close(self._handle)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# =============================================================================
# MOSFET power latch
# =============================================================================
class PowerLatch:
    """
    Holds the hardware power gate closed while the trap does its work.

    On a development machine this degrades to a no-op so the same code path runs
    unchanged - release() simply logs instead of cutting power.
    """

    def __init__(self, pin=DEFAULT_LATCH_PIN, backend="auto", log=print):
        self.pin = pin
        self.log = log
        self.backend = None
        self._device = None
        self._lgpio = None
        self._handle = None
        self._held = False

        candidates = ("gpiozero", "lgpio", "mock") if backend == "auto" else (backend,)
        for name in candidates:
            try:
                getattr(self, f"_init_{name}")()
                self.backend = name
                if name != "mock":
                    log(f"[LATCH] Backend: {name} on GPIO {pin}")
                return
            except Exception:
                continue

        self.backend = "mock"

    def _init_gpiozero(self):
        gpiozero = _load_gpiozero()
        self._device = gpiozero.DigitalOutputDevice(self.pin, initial_value=False)

    def _init_lgpio(self):
        import lgpio
        self._lgpio = lgpio
        self._handle = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self._handle, self.pin, 0)

    def _init_mock(self):
        pass

    def hold(self):
        """Assert the hold line HIGH. Call this as early as possible after boot."""
        if self._held:
            return
        if self.backend == "gpiozero":
            self._device.on()
        elif self.backend == "lgpio":
            self._lgpio.gpio_write(self._handle, self.pin, 1)
        self._held = True
        self.log(f"[LATCH] Power gate held ({self.backend})")

    def release(self):
        """Pull the hold line LOW. On real hardware the next halt cuts power entirely."""
        if self.backend == "gpiozero" and self._device is not None:
            self._device.off()
        elif self.backend == "lgpio" and self._handle is not None:
            self._lgpio.gpio_write(self._handle, self.pin, 0)
        self._held = False
        self.log(f"[LATCH] Power gate released ({self.backend})")

    def close(self):
        try:
            if self.backend == "gpiozero" and self._device is not None:
                self._device.close()
            elif self.backend == "lgpio" and self._handle is not None:
                self._lgpio.gpiochip_close(self._handle)
        except Exception:
            pass

    def __enter__(self):
        self.hold()
        return self

    def __exit__(self, *exc):
        self.close()


# =============================================================================
# Diagnostics
# =============================================================================
def probe(log=print):
    status = {}
    try:
        _load_gpiozero()
        status["gpiozero"] = "available"
    except ImportError as e:
        status["gpiozero"] = f"not installed ({e})"
    except Exception as e:
        status["gpiozero"] = f"present but not usable ({e})"

    try:
        import lgpio  # noqa: F401
        status["lgpio"] = "available"
    except ImportError:
        status["lgpio"] = "not installed (sudo apt install -y python3-lgpio)"

    try:
        import RPi.GPIO  # noqa: F401
        status["RPi.GPIO"] = "installed - NOT used, it does not support the Pi 5"
    except ImportError:
        status["RPi.GPIO"] = "not installed (correct for a Pi 5)"

    status["mock"] = "always available"
    for name, state in status.items():
        log(f"  {name:<10} {state}")
    return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PIR sensor / power latch test")
    parser.add_argument("--check", action="store_true", help="Report GPIO backends")
    parser.add_argument("--pin", type=int, default=DEFAULT_PIR_PIN)
    parser.add_argument("--latch-pin", type=int, default=DEFAULT_LATCH_PIN)
    parser.add_argument("--backend", default="auto", choices=("auto", "gpiozero", "lgpio", "mock"))
    parser.add_argument("--watch", type=float, default=0.0,
                        help="Watch for motion for N seconds and report each trigger")
    args = parser.parse_args()

    if args.check:
        print("GPIO backends on this machine:")
        probe()
        sys.exit(0)

    if args.watch > 0:
        print(f"Watching GPIO {args.pin} for {args.watch:g} s. Wave a hand in front of the sensor.")
        with PIRSensor(args.pin, args.backend) as pir:
            deadline = time.time() + args.watch
            triggers = 0
            while time.time() < deadline:
                if pir.wait_for_motion(timeout=min(1.0, max(0.1, deadline - time.time()))):
                    triggers += 1
                    print(f"  [{time.strftime('%H:%M:%S')}] MOTION #{triggers}")
                    time.sleep(0.5)   # let the HC-SR501 output settle back low
            print(f"Done. {triggers} trigger(s).")
        sys.exit(0)

    latch = PowerLatch(args.latch_pin)
    latch.hold()
    time.sleep(0.5)
    latch.release()
    latch.close()
