"""
LoRa transmitter emulator: fragmentation + BLOCKNACK-driven selective retransmission.

Wire format and fragmentation now come from lora_protocol (FIX_PLAN.md D1).
"""

import os
import sys
import socket
import argparse
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lora_protocol as lp
from paths import COMPRESSED_PAYLOAD, BASE_STATION_HOST, BASE_STATION_PORT, SENDER_PORT

# A real trap must not transmit forever: LoRa airtime is capped at a 10% duty cycle
# (Planning.md 9) and every retry costs battery. The old `while True:` had no ceiling
# at all (FIX_PLAN.md B6).
MAX_ARQ_ROUNDS = 5

# Generous enough to ride out a base-station disk hiccup without giving up on a
# transmission that already succeeded over the air.
ACK_TIMEOUT_S = 10.0

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 3


def parse_args():
    parser = argparse.ArgumentParser(description="LoRa Sender Emulator (Point-to-Point MAC)")
    parser.add_argument("--payload", default=COMPRESSED_PAYLOAD, help="Base64 payload file")
    parser.add_argument("--port", type=int, default=SENDER_PORT, help="Local port for BLOCKNACKs")
    parser.add_argument("--dest-port", type=int, default=BASE_STATION_PORT, help="Base station port")
    parser.add_argument("--node-id", type=int, default=1, help="Hardware node ID")
    parser.add_argument("--shuffle", action="store_true", help="Send out of order (interleaved burst)")
    parser.add_argument("--delay", type=float, default=0.01, help="Inter-packet delay in seconds")
    parser.add_argument("--max-rounds", type=int, default=MAX_ARQ_ROUNDS, help="ARQ retransmission ceiling")
    parser.add_argument("--ack-timeout", type=float, default=ACK_TIMEOUT_S,
                        help="Seconds to wait for a BLOCKNACK or SUCCESS frame")
    return parser.parse_args()


def main_logic(args):
    if not os.path.exists(args.payload):
        print(f"[SENDER] ERROR: payload not found: {args.payload}", file=sys.stderr)
        print("[SENDER] Run edge_node.py first to generate it.", file=sys.stderr)
        return EXIT_ERROR

    with open(args.payload, "r", encoding="utf-8") as f:
        payload = f.read().strip()

    if not payload:
        print(f"[SENDER] ERROR: payload file is empty: {args.payload}", file=sys.stderr)
        return EXIT_ERROR

    packets = lp.fragment_payload(payload, node_id=args.node_id, trans_id=1)
    packet_map = dict(packets)

    print(f"[SENDER] Payload {len(payload)} chars -> {len(packets)} fragments "
          f"({lp.HEADER_SIZE + lp.MAX_FRAGMENT_SIZE} B per frame)")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", args.port))
    except OSError as e:
        print(f"[SENDER] ERROR: cannot bind port {args.port}: {e}", file=sys.stderr)
        return EXIT_ERROR

    sock.settimeout(args.ack_timeout)
    dest = (BASE_STATION_HOST, args.dest_port)

    try:
        return _transmit(sock, dest, packets, packet_map, args)
    finally:
        sock.close()


def _transmit(sock, dest, packets, packet_map, args):
    tx_queue = list(packets)
    if args.shuffle:
        print("[SENDER] Interleaved burst: shuffling fragment order")
        random.shuffle(tx_queue)

    print(f"[SENDER] Transmitting {len(tx_queue)} fragments to {dest[0]}:{dest[1]}...")
    for _seq, pkt in tx_queue:
        sock.sendto(pkt, dest)
        time.sleep(args.delay)

    print("[SENDER] Burst complete. Awaiting BLOCKNACK...")

    for round_no in range(1, args.max_rounds + 1):
        try:
            data, _addr = sock.recvfrom(1024)
        except socket.timeout:
            print(f"[SENDER] ERROR: no control frame after {args.ack_timeout:g} s "
                  f"(round {round_no}). Aborting.", file=sys.stderr)
            return EXIT_ERROR

        _node, _trans, status, missing = lp.parse_blocknack(data)

        if status is None:
            print("[SENDER] Malformed control frame ignored.")
            continue

        if status == lp.STATUS_SUCCESS:
            print("[SENDER] SUCCESS: base station confirmed complete reassembly.")
            return EXIT_OK

        if status == lp.STATUS_NACK:
            print(f"[SENDER] [Round {round_no}/{args.max_rounds}] "
                  f"BLOCKNACK: {len(missing)} fragments missing {missing}")
            sent = 0
            for seq in missing:
                pkt = packet_map.get(seq)
                if pkt:
                    sock.sendto(pkt, dest)
                    sent += 1
                    time.sleep(args.delay)
            print(f"[SENDER] Retransmitted {sent} fragments. Awaiting confirmation...")

    print(f"[SENDER] ERROR: gave up after {args.max_rounds} ARQ rounds. "
          f"Preserving battery and duty-cycle budget.", file=sys.stderr)
    return EXIT_INCOMPLETE


def main():
    return main_logic(parse_args())


if __name__ == "__main__":
    sys.exit(main())
