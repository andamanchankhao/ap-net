"""
AP-NET field node - the real camera trap loop for a Raspberry Pi 5.

    PIR motion -> hold power latch -> capture -> YOLO inference
                                                  |
                              human -> compress -> fragment -> transmit -> ARQ
                              animal -> cache full-res to SD, radio stays silent

This is the pipeline Planning.md describes, running on actual hardware. The one part still
emulated is the radio: with no SX1262 present, fragments go out over UDP to the base
station instead of over 923 MHz. The MAC layer, fragmentation, BLOCKNACK retransmission
and duty-cycle ceiling are all identical, so swapping in a real transceiver later means
replacing one transport function.

USAGE

    # continuous watch, base station on this machine
    python3 edge_node/field_node.py

    # base station is the laptop, this Pi is the trap
    python3 edge_node/field_node.py --base-host 192.168.1.42 --node-id 2

    # one power-gated cycle, then halt (the real deployment mode)
    sudo python3 edge_node/field_node.py --once --halt-after

    # no PIR wired up yet - trigger on a timer instead
    python3 edge_node/field_node.py --trigger interval --interval 10
"""

import os
import sys
import time
import socket
import argparse
import threading
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lora_protocol as lp
from paths import (CAPTURE_DIR, SD_CARD_DIR, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW,
                   BASE_STATION_HOST, BASE_STATION_PORT, SENDER_PORT, ensure_dir)
import edge_node as pipeline
from detector import detect_human, DEFAULT_THRESHOLD
from camera import open_camera, CameraError
from pir_sensor import PIRSensor, PowerLatch

# Field value is 3 hours (Planning.md 11.1 - every transmission costs battery).
# Shortened here so the dashboard shows a node coming online within one demo.
HEARTBEAT_INTERVAL_S = 5.0

# After an alert, ignore further motion for this long. A poacher lingering in frame would
# otherwise trigger back-to-back transmissions and blow through the 10% duty cycle.
COOLDOWN_S = 15.0

ACK_TIMEOUT_S = 10.0
MAX_ARQ_ROUNDS = 5


# =============================================================================
# Radio (UDP stand-in for the SX1262)
# =============================================================================
def heartbeat_loop(node_id, base_addr, stop_event, interval=HEARTBEAT_INTERVAL_S):
    """Tell the base station this trap is alive. One tiny frame, cheap on airtime."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    message = f"HEARTBEAT:Camera-Trap-{node_id:02d}".encode("utf-8")

    while not stop_event.is_set():
        try:
            sock.sendto(message, base_addr)
        except OSError:
            pass
        stop_event.wait(interval)

    sock.close()


def transmit(b64_payload, node_id, base_addr, listen_port=SENDER_PORT,
             delay=0.01, max_rounds=MAX_ARQ_ROUNDS, log=print):
    """
    Fragment and send, honouring BLOCKNACKs. Returns True once the base station confirms.
    """
    packets = lp.fragment_payload(b64_payload, node_id=node_id, trans_id=1)
    packet_map = dict(packets)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", listen_port))
    except OSError as e:
        log(f"[RADIO] Cannot bind port {listen_port}: {e}")
        sock.close()
        return False

    sock.settimeout(ACK_TIMEOUT_S)

    try:
        log(f"[RADIO] Transmitting {len(packets)} fragments to {base_addr[0]}:{base_addr[1]}")
        for _seq, pkt in packets:
            sock.sendto(pkt, base_addr)
            time.sleep(delay)

        for round_no in range(1, max_rounds + 1):
            try:
                data, _addr = sock.recvfrom(1024)
            except socket.timeout:
                log(f"[RADIO] No response after {ACK_TIMEOUT_S:g} s. Giving up.")
                return False

            _n, _t, status, missing = lp.parse_blocknack(data)

            if status == lp.STATUS_SUCCESS:
                log("[RADIO] Base station confirmed receipt.")
                return True

            if status == lp.STATUS_NACK:
                log(f"[RADIO] [Round {round_no}/{max_rounds}] retransmitting {len(missing)} fragments")
                for seq in missing:
                    if seq in packet_map:
                        sock.sendto(packet_map[seq], base_addr)
                        time.sleep(delay)

        log(f"[RADIO] Gave up after {max_rounds} ARQ rounds - preserving duty-cycle budget.")
        return False
    finally:
        sock.close()


# =============================================================================
# One detection cycle
# =============================================================================
def run_cycle(camera, args, base_addr, log=print):
    """
    Capture one frame and act on it. Returns "alert", "clear" or "error".
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # With the 'file' backend, carry the source name through. It changes nothing for a
    # real model, but the mock detector reads the filename, so a demo run against
    # human_intruder_01.jpg would otherwise always come back "no threat".
    prefix = "capture"
    if getattr(camera, "name", "") == "file":
        prefix = os.path.splitext(os.path.basename(camera.source_path))[0]

    capture_path = os.path.join(CAPTURE_DIR, f"{prefix}_{stamp}.jpg")

    t0 = time.time()
    try:
        camera.capture(capture_path)
    except CameraError as e:
        log(f"[NODE] Capture failed: {e}")
        return "error"
    t_capture = time.time() - t0

    t0 = time.time()
    try:
        result = detect_human(capture_path, args.model, args.threshold, allow_mock=args.allow_mock)
    except RuntimeError as e:
        log(f"[NODE] {e}")
        return "error"
    t_infer = time.time() - t0

    log(f"[NODE] capture {t_capture*1000:.0f} ms | inference {t_infer*1000:.0f} ms "
        f"| backend {result.backend} | person {result.confidence:.2%}")

    if not result.human_detected:
        dest = pipeline.cache_to_sd_card(capture_path, args.sd_card)
        log(f"[NODE] No threat. Cached to SD: {os.path.basename(dest)}. Radio stays silent.")
        return "clear"

    log(f"[NODE] *** HUMAN DETECTED ({result.confidence:.2%}) ***")
    for x1, y1, x2, y2, score in result.boxes:
        log(f"       person {score:.2f} at ({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})")

    try:
        pipeline.process_image(capture_path, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW)
        with open(COMPRESSED_PAYLOAD, encoding="utf-8") as f:
            payload = f.read()
    except Exception as e:
        log(f"[NODE] Compression failed: {e}")
        return "error"

    transmit(payload, args.node_id, base_addr, args.listen_port, log=log)
    return "alert"


# =============================================================================
# Main loop
# =============================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="AP-NET field camera trap (Raspberry Pi)")

    parser.add_argument("--node-id", type=int, default=1, help="Hardware node ID (1-255)")
    parser.add_argument("--base-host", default=BASE_STATION_HOST,
                        help="Base station address (use the dashboard machine's LAN IP)")
    parser.add_argument("--base-port", type=int, default=BASE_STATION_PORT)
    parser.add_argument("--listen-port", type=int, default=SENDER_PORT, help="Local port for BLOCKNACKs")

    parser.add_argument("--trigger", default="pir", choices=("pir", "interval", "manual"),
                        help="pir: wait on the sensor; interval: fire on a timer; manual: press Enter")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between interval triggers")
    parser.add_argument("--pir-pin", type=int, default=4, help="BCM pin for the PIR OUT line")
    parser.add_argument("--latch-pin", type=int, default=17, help="BCM pin holding the MOSFET latch")
    parser.add_argument("--gpio-backend", default="auto", choices=("auto", "gpiozero", "lgpio", "mock"))

    parser.add_argument("--camera", default="auto",
                        choices=("auto", "picamera2", "rpicam", "opencv", "file"))
    parser.add_argument("--camera-device", type=int, default=0, help="OpenCV device index")
    parser.add_argument("--source-image", default="", help="Static image for the 'file' camera backend")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)

    parser.add_argument("--model", default="", help="Model path (default: auto-discover)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--allow-mock", action="store_true",
                        help="Permit the filename fallback when no model is installed")
    parser.add_argument("--sd-card", default=SD_CARD_DIR)

    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--cooldown", type=float, default=COOLDOWN_S)
    parser.add_argument("--halt-after", action="store_true",
                        help="Release the power latch and halt the OS when done (needs root)")
    parser.add_argument("--no-heartbeat", action="store_true")

    return parser.parse_args()


def main():
    args = parse_args()
    base_addr = (args.base_host, args.base_port)

    ensure_dir(CAPTURE_DIR)
    ensure_dir(args.sd_card)

    print("=" * 78)
    print(f"  AP-NET FIELD NODE  |  Camera-Trap-{args.node_id:02d}")
    print(f"  Base station : {args.base_host}:{args.base_port}")
    print(f"  Trigger      : {args.trigger}")
    print("=" * 78)

    # Hold the power gate before anything slow. If the PIR pulse expires while we are
    # still importing torch, the board loses power mid-boot (see pir_sensor.py).
    latch = PowerLatch(args.latch_pin, args.gpio_backend)
    latch.hold()

    stop_event = threading.Event()
    if not args.no_heartbeat:
        threading.Thread(target=heartbeat_loop,
                         args=(args.node_id, base_addr, stop_event), daemon=True).start()
        print(f"[NODE] Heartbeat every {HEARTBEAT_INTERVAL_S:g} s")

    pir = None
    if args.trigger == "pir":
        try:
            pir = PIRSensor(args.pir_pin, args.gpio_backend)
        except Exception as e:
            print(f"[NODE] PIR unavailable ({e}); falling back to interval triggering.")
            args.trigger = "interval"

    try:
        camera = open_camera(args.camera, (args.width, args.height),
                             device=args.camera_device,
                             source_path=args.source_image or None)
    except CameraError as e:
        print(f"[NODE] ERROR: {e}", file=sys.stderr)
        print("[NODE] Run `python3 edge_node/camera.py --check` to see what is available.",
              file=sys.stderr)
        stop_event.set()
        latch.close()
        return 1

    cycles = 0
    try:
        while True:
            if args.trigger == "pir":
                print("\n[NODE] Armed. Waiting for motion...")
                if not pir.wait_for_motion(timeout=None, stop_event=stop_event):
                    break
                print("[NODE] Motion detected!")
            elif args.trigger == "interval":
                print(f"\n[NODE] Next capture in {args.interval:g} s...")
                if stop_event.wait(args.interval):
                    break
            else:
                try:
                    input("\n[NODE] Press Enter to capture (Ctrl+C to quit)... ")
                except EOFError:
                    break

            outcome = run_cycle(camera, args, base_addr)
            cycles += 1

            if args.once:
                break

            if outcome == "alert" and args.cooldown > 0:
                print(f"[NODE] Cooldown {args.cooldown:g} s (duty-cycle guard)...")
                if stop_event.wait(args.cooldown):
                    break

    except KeyboardInterrupt:
        print("\n[NODE] Stopped by operator.")
    finally:
        stop_event.set()
        camera.close()
        if pir:
            pir.close()
        print(f"[NODE] {cycles} cycle(s) completed.")

        if args.halt_after:
            print("[NODE] Releasing power latch and halting.")
            latch.release()
            latch.close()
            time.sleep(1.0)
            subprocess.run(["sudo", "halt"])
        else:
            latch.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
