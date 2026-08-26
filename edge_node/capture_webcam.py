"""
Live preview and aiming tool for the AP-NET camera trap.

Shows the camera feed with detection boxes drawn on it, so you can frame the trap on the
trail you actually want to watch and confirm the detector fires before walking away.

This is a bench tool, not the deployment path - a real trap is asleep at 0 W until the PIR
wakes it (Planning.md 3). For that, use field_node.py. Detection here comes from
detector.py, the same code the field node runs, rather than the separate OpenCV HOG/Haar
detectors this file used to carry.

    python3 edge_node/capture_webcam.py                       # laptop webcam
    python3 edge_node/capture_webcam.py --camera picamera2    # Pi Camera Module
    python3 edge_node/capture_webcam.py --transmit            # also send alerts on detect

KEYS
    q  quit          SPACE  force an alert on the current frame
    s  save frame    d      toggle detection on/off
"""

import os
import sys
import time
import argparse
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paths import (CAPTURE_DIR, SD_CARD_DIR, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW,
                   BASE_STATION_HOST, BASE_STATION_PORT, ensure_dir)
import edge_node as pipeline
from detector import detect_human, DEFAULT_THRESHOLD
from field_node import transmit, heartbeat_loop

# Detection runs every Nth frame: the preview stays smooth while inference gets a whole
# frame interval to finish, which matters on a Pi where YOLO costs tens of milliseconds.
DETECT_EVERY_N_FRAMES = 5
CONSECUTIVE_HITS_TO_ALERT = 3
COOLDOWN_S = 15.0


def parse_args():
    parser = argparse.ArgumentParser(description="AP-NET live preview / aiming tool")
    parser.add_argument("--camera", default="auto", choices=("auto", "picamera2", "rpicam", "opencv"))
    parser.add_argument("--device", type=int, default=0, help="OpenCV device index")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--model", default="", help="Model path (default: auto-discover)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--allow-mock", action="store_true",
                        help="Permit the filename fallback when no model is installed")
    parser.add_argument("--transmit", action="store_true", help="Send alerts to the base station")
    parser.add_argument("--node-id", type=int, default=1)
    parser.add_argument("--base-host", default=BASE_STATION_HOST)
    parser.add_argument("--base-port", type=int, default=BASE_STATION_PORT)
    parser.add_argument("--cooldown", type=float, default=COOLDOWN_S)
    return parser.parse_args()


def draw_overlay(cv2, frame, boxes, confidence, alerting, detecting, fps, backend):
    h, w = frame.shape[:2]

    for x1, y1, x2, y2, score in boxes:
        colour = (0, 0, 255) if alerting else (0, 220, 0)
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), colour, 2)
        cv2.putText(frame, f"person {score:.2f}", (int(x1), max(14, int(y1) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)

    banner_colour = (0, 0, 200) if alerting else (25, 25, 25)
    cv2.rectangle(frame, (0, 0), (w, 34), banner_colour, -1)

    if alerting:
        status = "HUMAN INTRUDER CONFIRMED - TRANSMITTING"
    elif not detecting:
        status = "DETECTION PAUSED"
    else:
        status = f"SCANNING  |  person {confidence:.0%}  |  {backend}"
    cv2.putText(frame, status, (12, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.rectangle(frame, (0, h - 26), (w, h), (25, 25, 25), -1)
    cv2.putText(frame, f"{fps:4.1f} fps   q quit | SPACE alert | s save | d detect on/off",
                (12, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (190, 190, 190), 1)
    return frame


def handle_alert(frame_path, args, log=print):
    """Compress the frame and, if asked, push it to the base station."""
    log(f"[PREVIEW] Alert frame saved: {frame_path}")
    try:
        pipeline.process_image(frame_path, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW)
    except Exception as e:
        log(f"[PREVIEW] Compression failed: {e}")
        return

    if not args.transmit:
        log("[PREVIEW] --transmit not set; payload written but not sent.")
        return

    with open(COMPRESSED_PAYLOAD, encoding="utf-8") as f:
        payload = f.read()
    transmit(payload, args.node_id, (args.base_host, args.base_port), log=log)


def main():
    args = parse_args()

    try:
        import cv2
    except ImportError:
        print("ERROR: OpenCV is required for the preview window.", file=sys.stderr)
        print("       pip install opencv-python", file=sys.stderr)
        print("       (headless machines should use field_node.py instead)", file=sys.stderr)
        return 1

    from camera import open_camera, CameraError, OpenCVCamera

    ensure_dir(CAPTURE_DIR)
    ensure_dir(SD_CARD_DIR)

    try:
        camera = open_camera(args.camera, (args.width, args.height), device=args.device)
    except CameraError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    stop_event = threading.Event()
    if args.transmit:
        threading.Thread(target=heartbeat_loop,
                         args=(args.node_id, (args.base_host, args.base_port), stop_event),
                         daemon=True).start()
        print(f"[PREVIEW] Heartbeat -> {args.base_host}:{args.base_port}")

    print("[PREVIEW] Opening window. Aim the trap, then press q.")

    frame_no = 0
    hits = 0
    boxes, confidence, backend = [], 0.0, "-"
    detecting = True
    alerting_until = 0.0
    cooldown_until = 0.0
    last_time = time.time()
    fps = 0.0

    try:
        while True:
            if isinstance(camera, OpenCVCamera):
                frame = camera.read_frame()
            else:
                probe_path = os.path.join(CAPTURE_DIR, "_preview.jpg")
                camera.capture(probe_path)
                frame = cv2.imread(probe_path)

            if frame is None:
                print("[PREVIEW] Lost the camera feed.")
                break

            frame_no += 1
            now = time.time()
            fps = 0.85 * fps + 0.15 * (1.0 / max(now - last_time, 1e-6))
            last_time = now

            if detecting and frame_no % DETECT_EVERY_N_FRAMES == 0 and now >= cooldown_until:
                probe_path = os.path.join(CAPTURE_DIR, "_detect.jpg")
                cv2.imwrite(probe_path, frame)
                try:
                    result = detect_human(probe_path, args.model, args.threshold,
                                          allow_mock=args.allow_mock)
                    boxes, confidence, backend = result.boxes, result.confidence, result.backend
                    hits = hits + 1 if result.human_detected else max(0, hits - 1)
                except RuntimeError as e:
                    print(f"[PREVIEW] {e}")
                    detecting = False

                if hits >= CONSECUTIVE_HITS_TO_ALERT:
                    hits = 0
                    alerting_until = now + 1.5
                    cooldown_until = now + args.cooldown
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    alert_path = os.path.join(CAPTURE_DIR, f"human_alert_{stamp}.jpg")
                    cv2.imwrite(alert_path, frame)
                    handle_alert(alert_path, args)

            display = draw_overlay(cv2, frame.copy(), boxes, confidence,
                                   now < alerting_until, detecting, fps, backend)
            cv2.imshow("AP-NET Camera Trap - Live Preview", display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("d"):
                detecting = not detecting
                print(f"[PREVIEW] Detection {'on' if detecting else 'off'}")
            if key == ord("s"):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = os.path.join(CAPTURE_DIR, f"manual_{stamp}.jpg")
                cv2.imwrite(path, frame)
                print(f"[PREVIEW] Saved {path}")
            if key == ord(" "):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = os.path.join(CAPTURE_DIR, f"human_forced_{stamp}.jpg")
                cv2.imwrite(path, frame)
                alerting_until = time.time() + 1.5
                handle_alert(path, args)

    except KeyboardInterrupt:
        print("\n[PREVIEW] Stopped.")
    finally:
        stop_event.set()
        camera.close()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    sys.exit(main())
