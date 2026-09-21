"""
Standalone base-station receiver.

All protocol and reassembly logic lives in receiver_runtime / lora_protocol; this file is
just the CLI wrapper. Note that dashboard_server.py runs the same receiver internally, so
only one of the two may hold UDP 5005 at a time.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import (RECEIVED_IMAGES_DIR, SENSOR_CONFIG, INCIDENT_STORE, ALERT_METADATA,
                   BASE_STATION_HOST, BASE_STATION_PORT)
from receiver_runtime import run_receiver, make_socket


def parse_args():
    parser = argparse.ArgumentParser(description="LoRa Base Station Receiver Emulator")
    parser.add_argument("--host", default=BASE_STATION_HOST,
                        help="Address to listen on. 0.0.0.0 accepts a field node on another "
                             "machine (e.g. a Pi 5 on the LAN).")
    parser.add_argument("--port", type=int, default=BASE_STATION_PORT, help="Port to listen on")
    parser.add_argument("--loss-rate", type=float, default=0.15, help="Simulated packet loss (0.0-1.0)")
    parser.add_argument("--burst-timeout", type=float, default=0.5, help="Idle seconds that end a burst")
    parser.add_argument("--output-dir", default=RECEIVED_IMAGES_DIR, help="Where to save reassembled images")
    return parser.parse_args()


def main_logic(args):
    sock = make_socket(args.host, args.port)
    if sock is None:
        return 1

    print(f"Base Station Receiver listening on {args.host}:{args.port}")
    try:
        run_receiver(
            sock=sock,
            output_dir=args.output_dir,
            config_path=SENSOR_CONFIG,
            store_path=INCIDENT_STORE,
            alert_path=ALERT_METADATA,
            loss_rate=args.loss_rate,
            burst_timeout=args.burst_timeout,
        )
    except KeyboardInterrupt:
        print("\nReceiver stopped by user.")
    finally:
        sock.close()
    return 0


def main():
    return main_logic(parse_args())


if __name__ == "__main__":
    sys.exit(main())
