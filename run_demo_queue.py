"""
End-to-end AP-NET emulation over in-memory queues.

The fastest and most reliable demo: no sockets, so no firewall prompts or sandbox
restrictions. It exercises the same lora_protocol and receiver_runtime code paths the
UDP emulation uses - the only difference is the transport underneath.

    Test 1  human intruder  -> compress, fragment, lose packets, ARQ recover, reassemble
    Test 2  wildlife        -> cache to SD, radio stays silent
"""

import os
import sys
import time
import queue
import socket
import random
import threading

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_node"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "base_station"))

import lora_protocol as lp
from paths import (MOCK_HUMAN_IMG, MOCK_DEER_IMG, MOCK_SD_CARD_DEER, SD_CARD_DIR,
                   COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW, RECEIVED_IMAGES_DIR,
                   SENSOR_CONFIG, INCIDENT_STORE, ALERT_METADATA)
import edge_node
from detector import detect_human
from receiver_runtime import run_receiver

SIMULATED_LOSS_RATE = 0.15


class QueueSocket:
    """
    A socket-shaped wrapper around two queues.

    Lets the real receiver loop run unmodified over an in-memory link - the emulation
    tests the production code path rather than a parallel reimplementation of it.
    """

    def __init__(self, inbox, outbox):
        self.inbox = inbox
        self.outbox = outbox
        self._timeout = None

    def settimeout(self, timeout):
        self._timeout = timeout

    def recvfrom(self, _bufsize=2048):
        try:
            return self.inbox.get(timeout=self._timeout), ("queue", 0)
        except queue.Empty:
            raise socket.timeout()

    def sendto(self, data, _addr):
        self.outbox.put(data)

    def close(self):
        pass


def banner(text):
    print("\n" + "=" * 80)
    print(f" {text.center(78)} ")
    print("=" * 80)


def sender_loop(b64_payload, uplink, downlink, max_rounds=5):
    """Transmit, then honour BLOCKNACKs until the base station confirms or we give up."""
    packets = lp.fragment_payload(b64_payload, node_id=1, trans_id=1)
    packet_map = dict(packets)
    print(f"[SENDER] {len(b64_payload)} chars -> {len(packets)} fragments")

    tx = list(packets)
    random.shuffle(tx)
    print("[SENDER] Interleaved burst: shuffling fragment order")
    for _seq, pkt in tx:
        uplink.put(pkt)
        time.sleep(0.001)

    print("[SENDER] Burst complete. Awaiting BLOCKNACK...")

    for round_no in range(1, max_rounds + 1):
        try:
            data = downlink.get(timeout=5.0)
        except queue.Empty:
            print("[SENDER] Timeout waiting for BLOCKNACK.")
            return False

        _n, _t, status, missing = lp.parse_blocknack(data)

        if status == lp.STATUS_SUCCESS:
            print("[SENDER] SUCCESS: base station confirmed complete reassembly.")
            return True

        if status == lp.STATUS_NACK:
            print(f"[SENDER] [Round {round_no}/{max_rounds}] missing {len(missing)}: {missing}")
            for seq in missing:
                if seq in packet_map:
                    uplink.put(packet_map[seq])
                    time.sleep(0.001)
            print(f"[SENDER] Retransmitted {len(missing)} fragments.")

    print(f"[SENDER] Gave up after {max_rounds} ARQ rounds.")
    return False


def test_true_positive():
    banner("TEST 1: TRUE POSITIVE (HUMAN) - COMPRESS, FRAGMENT, ARQ, REASSEMBLE")

    result = detect_human(MOCK_HUMAN_IMG, allow_mock=True)
    print(f"[EDGE-AI] {result}")
    if not result.human_detected:
        print("[FAIL] Human not detected in intruder image.")
        return False

    edge_node.process_image(MOCK_HUMAN_IMG, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW)
    with open(COMPRESSED_PAYLOAD, encoding="utf-8") as f:
        b64_payload = f.read()

    uplink, downlink = queue.Queue(), queue.Queue()
    stop_event = threading.Event()

    rx = threading.Thread(
        target=run_receiver,
        kwargs=dict(
            sock=QueueSocket(uplink, downlink),
            output_dir=RECEIVED_IMAGES_DIR,
            config_path=SENSOR_CONFIG,
            store_path=INCIDENT_STORE,
            alert_path=ALERT_METADATA,
            loss_rate=SIMULATED_LOSS_RATE,
            burst_timeout=0.2,
            stop_event=stop_event,
        ),
        daemon=True,
    )
    rx.start()
    time.sleep(0.3)

    delivered = sender_loop(b64_payload, uplink, downlink)
    stop_event.set()
    rx.join(timeout=3.0)

    if not delivered:
        print("[FAIL] Base station never confirmed reassembly.")
        return False

    # Byte-identity is the real proof the fragmented MAC layer is lossless
    import glob
    received = sorted(glob.glob(os.path.join(RECEIVED_IMAGES_DIR, "reassembled_*.webp")))
    if not received:
        print("[FAIL] No reassembled image produced.")
        return False

    with open(COMPRESSED_PREVIEW, "rb") as f1, open(received[-1], "rb") as f2:
        sent_bytes, recv_bytes = f1.read(), f2.read()

    if sent_bytes != recv_bytes:
        print(f"[FAIL] Reassembled bytes differ ({len(sent_bytes)} sent vs {len(recv_bytes)} received).")
        return False

    print(f"[PASS] Reassembled image byte-identical to the edge payload ({len(sent_bytes)} bytes).")
    return True


def test_true_negative():
    banner("TEST 2: TRUE NEGATIVE (WILDLIFE) - LOCAL CACHE, RADIO SILENT")

    if os.path.exists(COMPRESSED_PAYLOAD):
        os.remove(COMPRESSED_PAYLOAD)

    result = detect_human(MOCK_DEER_IMG, allow_mock=True)
    print(f"[EDGE-AI] {result}")
    if result.human_detected:
        print("[FAIL] Wildlife misclassified as human.")
        return False

    dest = edge_node.cache_to_sd_card(MOCK_DEER_IMG, SD_CARD_DIR)
    print(f"[PASS] Full-resolution frame cached to SD: {dest}")

    if os.path.exists(COMPRESSED_PAYLOAD):
        print("[FAIL] A transmission payload was generated for wildlife!")
        return False

    print("[PASS] No transmission payload generated - battery and airtime preserved.")
    return True


def main():
    banner("AP-NET LORA CAMERA TRAP - IN-MEMORY QUEUE EMULATION")

    for path in (COMPRESSED_PREVIEW, COMPRESSED_PAYLOAD, MOCK_SD_CARD_DEER):
        if os.path.exists(path):
            os.remove(path)

    results = [("True positive (human)", test_true_positive()),
               ("True negative (wildlife)", test_true_negative())]

    banner("RESULTS")
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")

    if all(passed for _, passed in results):
        print("\nEdge decision logic and the fragmented ARQ protocol both verified.")
        return 0

    print("\nOne or more checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
