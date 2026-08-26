"""
Camera abstraction for the AP-NET edge node.

One capture() call works across every environment the project runs in, so the same
field_node.py drives a Pi Camera Module on a Raspberry Pi 5 and a laptop webcam during
development.

Backends, tried in this order when backend="auto":

  picamera2  Pi Camera Module via libcamera. The right choice on a Pi 5 - RPi.GPIO-era
             libraries and raspistill do not work there; Bookworm ships picamera2.
  rpicam     Shells out to rpicam-still (the renamed libcamera-still). Useful when
             picamera2's Python bindings are unavailable but the CLI works.
  opencv     Any USB / UVC webcam through cv2.VideoCapture.
  file       Copies a fixed image. For testing the pipeline with no hardware at all.

Planning.md 4 specifies the NoIR (no infrared filter) variant with a wide lens: dense
canopy leaves the forest floor at twilight levels even at midday, and the system leans on
ambient near-infrared rather than power-hungry IR floodlights.
"""

import os
import sys
import time
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ensure_parent

DEFAULT_RESOLUTION = (1920, 1080)
DEFAULT_WARMUP_S = 2.0        # auto-exposure and white balance need time to settle


class CameraError(RuntimeError):
    pass


class BaseCamera:
    name = "base"

    def capture(self, output_path):
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# =============================================================================
# Raspberry Pi Camera Module (libcamera / picamera2)
# =============================================================================
class Picamera2Camera(BaseCamera):
    name = "picamera2"

    def __init__(self, resolution=DEFAULT_RESOLUTION, warmup=DEFAULT_WARMUP_S):
        from picamera2 import Picamera2

        self._cam = Picamera2()
        config = self._cam.create_still_configuration(main={"size": tuple(resolution)})
        self._cam.configure(config)
        self._cam.start()
        time.sleep(warmup)

    def capture(self, output_path):
        ensure_parent(output_path)
        self._cam.capture_file(output_path)
        return output_path

    def close(self):
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:
            pass


# =============================================================================
# rpicam-still / libcamera-still CLI
# =============================================================================
class RpicamCamera(BaseCamera):
    name = "rpicam"

    def __init__(self, resolution=DEFAULT_RESOLUTION, warmup=DEFAULT_WARMUP_S):
        self.binary = shutil.which("rpicam-still") or shutil.which("libcamera-still")
        if not self.binary:
            raise CameraError("neither rpicam-still nor libcamera-still is on PATH")
        self.resolution = resolution
        self.warmup_ms = max(1, int(warmup * 1000))

    def capture(self, output_path):
        ensure_parent(output_path)
        cmd = [self.binary, "-o", output_path, "--width", str(self.resolution[0]),
               "--height", str(self.resolution[1]), "-t", str(self.warmup_ms), "-n"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise CameraError(f"{os.path.basename(self.binary)} failed: {result.stderr.strip()}")
        return output_path


# =============================================================================
# USB / UVC webcam
# =============================================================================
class OpenCVCamera(BaseCamera):
    name = "opencv"

    def __init__(self, device=0, resolution=DEFAULT_RESOLUTION, warmup=DEFAULT_WARMUP_S):
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(device)
        if not self._cap.isOpened():
            raise CameraError(f"could not open video device {device}")

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

        # Drain a few frames: the first ones come back black or badly exposed while
        # auto-exposure converges, and a black frame is a guaranteed missed detection.
        deadline = time.time() + warmup
        while time.time() < deadline:
            self._cap.read()

    def read_frame(self):
        """Return a raw BGR frame, or None. Used by the live preview utility."""
        ok, frame = self._cap.read()
        return frame if ok else None

    def capture(self, output_path):
        frame = self.read_frame()
        if frame is None:
            raise CameraError("failed to grab a frame")
        ensure_parent(output_path)
        if not self._cv2.imwrite(output_path, frame):
            raise CameraError(f"failed to write {output_path}")
        return output_path

    def close(self):
        try:
            self._cap.release()
        except Exception:
            pass


# =============================================================================
# Static file (no hardware)
# =============================================================================
class FileCamera(BaseCamera):
    name = "file"

    def __init__(self, source_path, **_kwargs):
        if not os.path.exists(source_path):
            raise CameraError(f"source image not found: {source_path}")
        self.source_path = source_path

    def capture(self, output_path):
        ensure_parent(output_path)
        shutil.copy(self.source_path, output_path)
        return output_path


# =============================================================================
# Selection
# =============================================================================
BACKENDS = ("picamera2", "rpicam", "opencv", "file")


def open_camera(backend="auto", resolution=DEFAULT_RESOLUTION, warmup=DEFAULT_WARMUP_S,
                device=0, source_path=None, log=print):
    """
    Open the first camera backend that actually works.

    Raises CameraError when an explicitly requested backend is unavailable; in "auto"
    mode it falls through the list and only raises if nothing at all can be opened.
    """
    if backend == "file" or (backend == "auto" and source_path):
        if not source_path:
            raise CameraError("backend 'file' requires source_path")
        log(f"[CAMERA] Using static image: {source_path}")
        return FileCamera(source_path)

    candidates = BACKENDS[:-1] if backend == "auto" else (backend,)

    errors = []
    for name in candidates:
        try:
            if name == "picamera2":
                cam = Picamera2Camera(resolution, warmup)
            elif name == "rpicam":
                cam = RpicamCamera(resolution, warmup)
            elif name == "opencv":
                cam = OpenCVCamera(device, resolution, warmup)
            else:
                raise CameraError(f"unknown backend: {name}")
            log(f"[CAMERA] Backend: {name} @ {resolution[0]}x{resolution[1]}")
            return cam
        except (ImportError, CameraError, Exception) as e:
            errors.append(f"{name}: {e}")
            if backend != "auto":
                raise CameraError(f"backend '{backend}' unavailable - {e}")

    raise CameraError("no camera backend available:\n  " + "\n  ".join(errors))


def probe(log=print):
    """Report which camera backends this machine can use. Used by --check."""
    status = {}

    try:
        import picamera2  # noqa: F401
        status["picamera2"] = "available"
    except ImportError:
        status["picamera2"] = "not installed (sudo apt install -y python3-picamera2)"

    binary = shutil.which("rpicam-still") or shutil.which("libcamera-still")
    status["rpicam"] = f"available ({binary})" if binary else "not on PATH"

    try:
        import cv2
        status["opencv"] = f"available (cv2 {cv2.__version__})"
    except ImportError:
        status["opencv"] = "not installed (pip install opencv-python)"

    status["file"] = "always available"
    for name, state in status.items():
        log(f"  {name:<11} {state}")
    return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AP-NET camera check / test capture")
    parser.add_argument("--check", action="store_true", help="Report available backends")
    parser.add_argument("--backend", default="auto", choices=("auto",) + BACKENDS)
    parser.add_argument("--output", default="", help="Where to write the test capture")
    parser.add_argument("--device", type=int, default=0, help="OpenCV device index")
    parser.add_argument("--source", default="", help="Source image for the 'file' backend")
    args = parser.parse_args()

    if args.check:
        print("Camera backends on this machine:")
        probe()
        sys.exit(0)

    from paths import CAPTURE_DIR
    output = args.output or os.path.join(CAPTURE_DIR, "camera_test.jpg")

    try:
        with open_camera(args.backend, device=args.device, source_path=args.source or None) as cam:
            path = cam.capture(output)
        size = os.path.getsize(path)
        print(f"Captured {path} ({size:,} bytes) using backend '{cam.name}'")
    except CameraError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
