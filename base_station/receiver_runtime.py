"""
Shared base-station receive loop.

lora_receiver_emulator.py and the background thread inside dashboard_server.py used to
carry two near-identical copies of this logic that had already drifted apart - different
output filenames, only one handling heartbeats, only one setting SO_REUSEADDR
(FIX_PLAN.md D1). Both now call run_receiver().
"""

import os
import sys
import json
import random
import socket
from io import BytesIO
from datetime import datetime, timezone

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lora_protocol as lp
import incident_store

DEFAULT_LAT, DEFAULT_LNG = 15.606848, 99.318075
DEFAULT_LOCATION = "Huai Kha Khaeng Wildlife Sanctuary - HQ"


# =============================================================================
# Helpers
# =============================================================================
def resolve_node_location(node_name, config_path, log=print):
    """
    Map a node ID to coordinates via sensor_config.json.

    This is the project's "virtualized GPS" (Planning.md 10.2): traps sit at fixed,
    pre-surveyed positions, so the base station resolves location from the node ID
    instead of the trap burning battery on a GPS module.
    """
    lat, lng, name = DEFAULT_LAT, DEFAULT_LNG, DEFAULT_LOCATION

    if not os.path.exists(config_path):
        return lat, lng, name

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        log(f"[RECEIVER] Warning: could not read sensor_config.json: {e}")
        return lat, lng, name

    entry = config.get(node_name)
    if entry:
        lat = entry.get("latitude", lat)
        lng = entry.get("longitude", lng)
        name = entry.get("name", name)
        log(f"[RECEIVER] Resolved {node_name} -> {lat}, {lng} ({name})")

    return lat, lng, name


def save_reassembled_image(webp_bytes, output_dir):
    """
    Write the received WebP plus a PNG preview. Returns (webp_filename, png_filename).

    The preview is converted back to "L" before saving: WebP has no grayscale mode, so the
    decoded image comes back as RGB and a naive PNG save triples the file size for three
    identical channels (FIX_PLAN.md E2).
    """
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

    webp_filename = f"reassembled_{stamp}.webp"
    png_filename = f"reassembled_preview_{stamp}.png"

    with open(os.path.join(output_dir, webp_filename), "wb") as f:
        f.write(webp_bytes)

    with Image.open(BytesIO(webp_bytes)) as img:
        img.convert("L").save(os.path.join(output_dir, png_filename), "PNG")

    return webp_filename, png_filename


def build_metadata(node_id, png_filename, config_path, log=print, rssi=-102, snr=-8.5,
                   confidence=0.89, battery_voltage=3.82, battery_percent=82):
    node_name = f"Camera-Trap-{node_id if node_id else 1:02d}"
    lat, lng, loc_name = resolve_node_location(node_name, config_path, log)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "node_id": node_name,
        "latitude": lat,
        "longitude": lng,
        "location_name": loc_name,
        "threat_type": "HUMAN_INTRUDER",
        "confidence": confidence,
        "battery_voltage": battery_voltage,
        "battery_percent": battery_percent,
        "rssi": rssi,
        "snr": snr,
        "image_name": png_filename,
        "action": "PATROL_DISPATCHED",
    }


def persist_alert(metadata, output_dir, store_path, alert_path):
    """
    Record an alert both ways: append to the durable incident history, and refresh
    alert_metadata.json which the dashboard reads for the currently-active alarm.
    """
    record = incident_store.append(store_path, metadata)
    with open(alert_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return record


# =============================================================================
# Receive loop
# =============================================================================
def run_receiver(sock, output_dir, config_path, store_path, alert_path,
                 sender_addr, loss_rate=0.15, burst_timeout=0.5,
                 on_alert=None, on_heartbeat=None, log=print,
                 stop_event=None, loss_seed=42):
    """
    Drive the reassembly state machine until `stop_event` is set (or forever).

    `on_alert(metadata)` fires after a complete image is reassembled and persisted;
    `on_heartbeat(node_id)` fires for each keep-alive frame.

    Packet loss uses a private random.Random instance rather than random.seed() so it
    stays reproducible without hijacking the process-wide RNG that the dashboard's
    telemetry simulation depends on (FIX_PLAN.md D4).
    """
    loss_rng = random.Random(loss_seed)
    session = lp.ReassemblySession()

    log(f"[RECEIVER] Listening. Simulated packet loss: {loss_rate:.1%}")

    while stop_event is None or not stop_event.is_set():
        sock.settimeout(burst_timeout if session.active else 1.0)

        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            if not session.active:
                continue
            _evaluate_burst(session, sock, sender_addr, output_dir, config_path,
                            store_path, alert_path, on_alert, log)
            continue
        except OSError as e:
            log(f"[RECEIVER] Socket error: {e}")
            break

        # Heartbeats bypass loss simulation: they are status pings, not payload traffic
        if lp.is_heartbeat(data):
            node = lp.parse_heartbeat(data)
            if node and on_heartbeat:
                on_heartbeat(node)
            continue

        if loss_rng.random() < loss_rate:
            header = lp.parse_header(data)
            if header:
                log(f"  [RECEIVER] [LOSS] Dropped fragment seq {header[2]}")
            continue

        status = session.accept(data)
        if status != "accepted":
            log(f"  [RECEIVER] Discarded frame ({status})")
            continue

        received, total = session.progress()
        if received == 1:
            log(f"[RECEIVER] Incoming transmission: expecting {total} fragments")


def _evaluate_burst(session, sock, sender_addr, output_dir, config_path,
                    store_path, alert_path, on_alert, log):
    """End-of-burst handling: reassemble if complete, otherwise request the gaps."""
    received, total = session.progress()
    log(f"[RECEIVER] Burst idle. {received}/{total} fragments held.")

    missing = session.missing()

    if not missing:
        try:
            webp_bytes = session.assemble()
        except ValueError as e:
            log(f"[RECEIVER] Reassembly failed: {e}. Requesting full retransmit.")
            missing = list(range(1, total + 1))
        else:
            node_id, trans_id = session.node_id, session.trans_id

            # Acknowledge on the radio FIRST, then write to disk.
            #
            # Storing the alert touches several files and a single stall - a Google Drive
            # sync, an SD card garbage-collecting, a full filesystem - used to push the
            # SUCCESS frame past the sender's ACK timeout. The trap then burned battery
            # retransmitting an image the base station had already decoded perfectly.
            # Everything needed for the ACK (a complete, decodable payload) is known here,
            # so disk latency no longer sits inside the protocol's timing budget.
            _send(sock, lp.make_blocknack(node_id, trans_id, lp.STATUS_SUCCESS, []), sender_addr, log)
            log(f"[RECEIVER] SUCCESS sent ({len(webp_bytes)} B decoded).")
            session.reset()

            try:
                _webp_name, png_name = save_reassembled_image(webp_bytes, output_dir)
                metadata = build_metadata(node_id, png_name, config_path, log)
                record = persist_alert(metadata, output_dir, store_path, alert_path)
                log(f"[RECEIVER] Image saved -> {png_name}")
                log(f"[RECEIVER] Incident stored: {record['log_id']}\n")
                if on_alert:
                    on_alert(record)
            except Exception as e:
                log(f"[RECEIVER] Error persisting alert (image was received intact): {e}")
            return

    log(f"[RECEIVER] Missing {len(missing)} fragments. Sending BLOCKNACK...")
    _send(sock, lp.make_blocknack(session.node_id, session.trans_id, lp.STATUS_NACK, missing),
          sender_addr, log)


def _send(sock, packet, addr, log):
    try:
        sock.sendto(packet, addr)
    except OSError as e:
        log(f"[RECEIVER] Could not send control frame: {e}")


def make_socket(host, port, log=print):
    """
    Bind a UDP socket with SO_REUSEADDR set.

    Without SO_REUSEADDR a quick restart hit "Errno 48: Address already in use" - the
    exact failure recorded in server.log (FIX_PLAN.md D1).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        log(f"[RECEIVER] Cannot bind {host}:{port}: {e}")
        log("[RECEIVER] Another receiver is probably already running "
            "(dashboard_server.py has one built in).")
        sock.close()
        return None
    return sock
