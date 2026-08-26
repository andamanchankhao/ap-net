"""
End-to-end AP-NET emulation over real UDP sockets, driven as separate processes.

Covers what run_demo_queue.py cannot: actual socket binding, process isolation, and the
exit-code contract between run_demo.sh and edge_node.py. Needs loopback permission; if
the OS blocks it, use run_demo_queue.py instead.
"""

import os
import sys
import glob
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import (PROJECT_ROOT, EDGE_DIR, BASE_STATION_DIR, MOCK_HUMAN_IMG, MOCK_DEER_IMG,
                   MOCK_SD_CARD_DEER, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW,
                   RECEIVED_IMAGES_DIR)

EDGE_NODE_SCRIPT = os.path.join(EDGE_DIR, "edge_node.py")
SENDER_SCRIPT = os.path.join(EDGE_DIR, "lora_sender_emulator.py")
RECEIVER_SCRIPT = os.path.join(BASE_STATION_DIR, "lora_receiver_emulator.py")

# edge_node.py exit contract (FIX_PLAN.md B4)
EXIT_HUMAN, EXIT_NO_THREAT, EXIT_ERROR = 0, 1, 2

PY = sys.executable   # same interpreter, so a venv on the Pi is respected


def banner(text):
    print("\n" + "=" * 80)
    print(f" {text.center(78)} ")
    print("=" * 80)


def run(cmd, **kwargs):
    return subprocess.run([PY] + cmd, cwd=PROJECT_ROOT, text=True, **kwargs)


def test_true_positive():
    banner("TEST 1: TRUE POSITIVE (HUMAN) - UDP TRANSPORT WITH 15% LOSS")

    before = set(glob.glob(os.path.join(RECEIVED_IMAGES_DIR, "reassembled_*.webp")))

    print("[SYSTEM] Starting base station receiver...")
    receiver = subprocess.Popen([PY, RECEIVER_SCRIPT, "--loss-rate", "0.15",
                                 "--burst-timeout", "0.4"], cwd=PROJECT_ROOT)
    time.sleep(1.5)

    try:
        print("[SYSTEM] Running edge AI pipeline on the intruder frame...")
        edge = run([EDGE_NODE_SCRIPT, "--image", MOCK_HUMAN_IMG, "--allow-mock"])

        if edge.returncode == EXIT_ERROR:
            print("[FAIL] Edge node errored (exit 2).")
            return False
        if edge.returncode != EXIT_HUMAN:
            print(f"[FAIL] Expected exit {EXIT_HUMAN} (human detected), got {edge.returncode}.")
            return False

        print("[SYSTEM] Transmitting (shuffled fragments)...")
        sender = run([SENDER_SCRIPT, "--shuffle", "--delay", "0.005"])
        if sender.returncode != 0:
            print(f"[FAIL] Sender exited {sender.returncode}.")
            return False

        time.sleep(1.0)
    finally:
        receiver.terminate()
        try:
            receiver.wait(timeout=5)
        except subprocess.TimeoutExpired:
            receiver.kill()

    produced = set(glob.glob(os.path.join(RECEIVED_IMAGES_DIR, "reassembled_*.webp"))) - before
    if not produced:
        print("[FAIL] Base station produced no reassembled image.")
        return False

    newest = max(produced, key=os.path.getmtime)
    with open(COMPRESSED_PREVIEW, "rb") as f1, open(newest, "rb") as f2:
        sent, received = f1.read(), f2.read()

    if sent != received:
        print(f"[FAIL] Byte mismatch: {len(sent)} sent vs {len(received)} received.")
        return False

    print(f"[PASS] Reassembled image byte-identical to the edge payload ({len(sent)} bytes).")
    return True


def test_true_negative():
    banner("TEST 2: TRUE NEGATIVE (WILDLIFE) - LOCAL CACHE, RADIO SILENT")

    for path in (COMPRESSED_PAYLOAD, MOCK_SD_CARD_DEER):
        if os.path.exists(path):
            os.remove(path)

    edge = run([EDGE_NODE_SCRIPT, "--image", MOCK_DEER_IMG, "--allow-mock"])

    # Exit 2 must NOT be read as a clean negative - that conflation was the old bug
    if edge.returncode == EXIT_ERROR:
        print("[FAIL] Edge node errored (exit 2) rather than classifying.")
        return False
    if edge.returncode != EXIT_NO_THREAT:
        print(f"[FAIL] Expected exit {EXIT_NO_THREAT} (no threat), got {edge.returncode}.")
        return False

    print("[PASS] Edge node classified wildlife as no-threat (exit 1).")

    if not os.path.exists(MOCK_SD_CARD_DEER):
        print("[FAIL] Full-resolution frame was not cached to the SD card.")
        return False
    print("[PASS] Full-resolution frame cached to the mock SD card.")

    if os.path.exists(COMPRESSED_PAYLOAD):
        print("[FAIL] A transmission payload was generated for wildlife!")
        return False
    print("[PASS] No transmission payload generated - battery and airtime preserved.")
    return True


def main():
    banner("AP-NET LORA CAMERA TRAP - UDP LOOPBACK EMULATION")

    results = [("True positive (human)", test_true_positive()),
               ("True negative (wildlife)", test_true_negative())]

    banner("RESULTS")
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")

    return 0 if all(p for _, p in results) else 1


if __name__ == "__main__":
    sys.exit(main())
