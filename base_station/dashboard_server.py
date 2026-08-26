"""
AP-NET base station: web dashboard + embedded LoRa receiver.

Serves the ranger dashboard, streams live alerts over Server-Sent Events, and runs the
UDP receiver in a background thread (so only one of this and lora_receiver_emulator.py
may hold port 5005).
"""

import os
import re
import sys
import hmac
import json
import time
import queue
import random
import shutil
import socket
import secrets
import base64
import argparse
import threading
import http.server
import socketserver
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import (STATIC_DIR, RECEIVED_IMAGES_DIR, SENSOR_CONFIG, INCIDENT_STORE,
                   ALERT_METADATA, MOCK_IMAGES_DIR, BASE_STATION_HOST, BASE_STATION_PORT,
                   SENDER_HOST, SENDER_PORT, DASHBOARD_PORT, ensure_dir)
import incident_store
from receiver_runtime import run_receiver, make_socket

# Camera is considered online this long after its last heartbeat.
# Field value is 3 h 10 m (heartbeat every 3 h); shortened for interactive demos.
CAMERA_ONLINE_WINDOW_S = 15.0

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

# --- Shared state ------------------------------------------------------------
active_clients = set()
clients_lock = threading.Lock()

active_cameras = {}
active_cameras_lock = threading.Lock()

# HTTP Basic Auth credentials as (username, password), or None to disable auth entirely.
# None is only safe when bound to loopback, where only local processes can connect at all
# (FIX_PLAN.md C1). main() sets this before the server starts serving requests.
AUTH_CREDENTIALS = None

# /simulate-alert rate limiting and image-count cap (FIX_PLAN.md C3). Without this, a
# scripted client (or a runaway UI double-click loop) can call it as fast as the disk
# allows, generating unbounded simulated_*.png files.
SIMULATE_MIN_INTERVAL_S = 1.0
SIMULATE_MAX_IMAGES = 50
_last_simulate_time = 0.0
_simulate_lock = threading.Lock()


def broadcast_event(event_data):
    """Push an event to every connected SSE client."""
    with clients_lock:
        if active_clients:
            print(f"[SERVER] Broadcasting to {len(active_clients)} client(s).")
        for client_queue in active_clients:
            client_queue.put(event_data)


def mark_camera_online(node_id):
    with active_cameras_lock:
        active_cameras[node_id] = time.time()


def online_cameras():
    now = time.time()
    with active_cameras_lock:
        return [node for node, seen in active_cameras.items()
                if now - seen < CAMERA_ONLINE_WINDOW_S]


def safe_filename_component(text, fallback="node"):
    """
    Reduce arbitrary text to something safe to embed in a filename.

    _handle_simulate() builds a filename from the client-supplied node_id. Without this,
    a request like {"node_id": "../../../../tmp/pwned"} would make os.path.join produce a
    path outside RECEIVED_IMAGES_DIR entirely, and shutil.copy would write the seed image
    there - a path-traversal write primitive, found while adding the C3 rate limit below.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_")
    return cleaned[:64] or fallback


def prune_simulated_images(output_dir, pattern="simulated_*.png", keep=SIMULATE_MAX_IMAGES):
    """
    Delete the oldest simulated_*.png files beyond `keep` (FIX_PLAN.md C3). Only ever
    touches files matching this prefix, so real received transmissions are untouched.
    """
    import glob

    matches = glob.glob(os.path.join(output_dir, pattern))
    if len(matches) <= keep:
        return

    matches.sort(key=os.path.getmtime)
    for path in matches[:-keep]:
        try:
            os.remove(path)
        except OSError as e:
            print(f"[SERVER] Warning: could not prune {path}: {e}")


# =============================================================================
# HTTP handler
# =============================================================================
class DashboardHTTPHandler(http.server.BaseHTTPRequestHandler):

    server_version = "APNET/1.0"

    # --- response helpers ---
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, content_type, status=200, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def log_message(self, fmt, *args):
        pass   # the receiver and watcher already narrate; keep the console readable

    # --- auth ---
    def _authorized(self):
        """
        Check HTTP Basic Auth against AUTH_CREDENTIALS.

        AUTH_CREDENTIALS is None when the server is loopback-only (main() only leaves
        auth disabled in that case), so this always returns True in that mode - only
        local processes can reach the socket at all.

        Uses hmac.compare_digest for both fields so a timing attack cannot narrow down
        the password one byte at a time.
        """
        if AUTH_CREDENTIALS is None:
            return True

        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False

        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            return False

        expected_user, expected_pass = AUTH_CREDENTIALS
        return (hmac.compare_digest(user, expected_user) and
                hmac.compare_digest(password, expected_pass))

    def _require_auth(self):
        """
        Send a 401 challenge if unauthenticated. Returns whether the request may proceed.

        The browser handles the rest natively: it prompts for credentials once per
        origin+realm and then attaches them to every subsequent request automatically -
        including images, fetch() calls and the EventSource /events stream - so nothing
        in app.js needs to change for this to work end to end.
        """
        if self._authorized():
            return True

        body = b"Authentication required."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="AP-NET Base Station"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    # --- GET ---
    def do_GET(self):
        if not self._require_auth():
            return

        path = self.path.split("?")[0]

        if path == "/events":
            return self._handle_sse()
        if path == "/latest-alert":
            return self._handle_latest_alert()
        if path == "/incidents":
            return self._handle_incidents()
        if path == "/active-cameras":
            return self._send_json({"active_nodes": online_cameras()})
        if path == "/sensor-config":
            return self._serve_file(SENSOR_CONFIG, "application/json; charset=utf-8")
        if path.startswith("/received_images/"):
            return self._handle_received_image(path)
        return self._serve_static(path)

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        client_queue = queue.Queue()
        with clients_lock:
            active_clients.add(client_queue)
            print(f"[SERVER] SSE client connected ({len(active_clients)} total).")

        try:
            while True:
                try:
                    event = client_queue.get(timeout=2.0)
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")   # keep-alive, also detects disconnects
                self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            with clients_lock:
                active_clients.discard(client_queue)
                print(f"[SERVER] SSE client disconnected ({len(active_clients)} remain).")

    def _handle_latest_alert(self):
        record = incident_store.latest(INCIDENT_STORE)
        if record:
            return self._send_json(record)
        if os.path.exists(ALERT_METADATA):
            return self._serve_file(ALERT_METADATA, "application/json; charset=utf-8")
        return self._send_json({"error": "No alert received yet"}, status=404)

    def _handle_incidents(self):
        """
        Full incident history from the server (FIX_PLAN.md B2).

        The dashboard used to keep this only in localStorage, so the record vanished
        when the browser was cleared and was invisible from any other machine.
        """
        try:
            records = incident_store.load_all(INCIDENT_STORE)
            return self._send_json({
                "incidents": records,
                "stats": incident_store.stats(INCIDENT_STORE),
            })
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _handle_received_image(self, path):
        filename = os.path.basename(path[len("/received_images/"):])
        return self._serve_file(os.path.join(RECEIVED_IMAGES_DIR, filename))

    def _serve_static(self, path):
        name = path.lstrip("/")
        if name in ("", "index.html"):
            return self._serve_file(os.path.join(STATIC_DIR, "index.html"))
        return self._serve_file(os.path.join(STATIC_DIR, os.path.basename(name)))

    def _serve_file(self, file_path, content_type=None):
        if not os.path.isfile(file_path):
            return self._send_bytes(b"Not found", "text/plain; charset=utf-8", status=404)

        if content_type is None:
            ext = os.path.splitext(file_path)[1].lower()
            content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

        try:
            with open(file_path, "rb") as f:
                body = f.read()
        except OSError as e:
            return self._send_bytes(f"Read error: {e}".encode(), "text/plain; charset=utf-8", status=500)

        self._send_bytes(body, content_type)

    # --- POST ---
    def do_POST(self):
        if not self._require_auth():
            return

        path = self.path.split("?")[0]

        if path == "/sensor-config":
            return self._handle_save_config()
        if path == "/resolve-alert":
            return self._handle_resolve()
        if path == "/simulate-alert":
            return self._handle_simulate()
        return self._send_json({"error": "Endpoint not found"}, status=404)

    def _handle_save_config(self):
        config = self._read_json_body()
        if config is None or not isinstance(config, dict):
            return self._send_json({"error": "Invalid JSON body"}, status=400)

        try:
            with open(SENSOR_CONFIG, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except OSError as e:
            return self._send_json({"error": str(e)}, status=500)

        print("[SERVER] Sensor configuration updated; broadcasting.")
        broadcast_event({"event_type": "CONFIG_UPDATE", "sensors": config})
        return self._send_json({"success": True})

    def _handle_resolve(self):
        """
        Mark ONE incident resolved, identified by log_id.

        This endpoint used to ignore its body entirely and always rewrite the newest
        alert, so resolving an old incident silently resolved the wrong one
        (FIX_PLAN.md B3).
        """
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "Invalid JSON body"}, status=400)

        log_id = body.get("log_id")
        if not log_id:
            latest = incident_store.latest(INCIDENT_STORE)
            if not latest:
                return self._send_json({"error": "No incident to resolve"}, status=404)
            log_id = latest.get("log_id")

        record = incident_store.update_action(INCIDENT_STORE, log_id, "PATROL_RESOLVED")
        if record is None:
            return self._send_json({"error": f"Unknown log_id: {log_id}"}, status=404)

        # Keep alert_metadata.json in step when the resolved incident is the active one
        try:
            if os.path.exists(ALERT_METADATA):
                with open(ALERT_METADATA, "r", encoding="utf-8") as f:
                    active = json.load(f)
                if active.get("log_id") == log_id:
                    active["action"] = "PATROL_RESOLVED"
                    with open(ALERT_METADATA, "w", encoding="utf-8") as f:
                        json.dump(active, f, indent=2)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[SERVER] Warning: could not sync alert_metadata.json: {e}")

        print(f"[SERVER] Incident resolved: {log_id}")
        broadcast_event({"event_type": "INCIDENT_RESOLVED", "log_id": log_id})
        return self._send_json({"success": True, "incident": record})

    def _handle_simulate(self):
        global _last_simulate_time

        # Rate limit (FIX_PLAN.md C3): without this, a scripted client - or an
        # authenticated-but-compromised session - can call this endpoint as fast as the
        # disk allows and flood received_images/ with simulated_*.png files.
        with _simulate_lock:
            now = time.time()
            elapsed = now - _last_simulate_time
            if elapsed < SIMULATE_MIN_INTERVAL_S:
                retry_after = SIMULATE_MIN_INTERVAL_S - elapsed
                self.send_response(429)
                self.send_header("Retry-After", f"{retry_after:.1f}")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                body = json.dumps({"error": "Too many requests. Slow down."}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            _last_simulate_time = now

        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "Invalid JSON body"}, status=400)

        node_name = body.get("node_id", "Camera-Trap-01")

        lat, lng = 15.606848, 99.318075
        loc_name = "Huai Kha Khaeng Wildlife Sanctuary - HQ"
        try:
            if os.path.exists(SENSOR_CONFIG):
                with open(SENSOR_CONFIG, "r", encoding="utf-8") as f:
                    config = json.load(f)
                entry = config.get(node_name, {})
                lat = entry.get("latitude", lat)
                lng = entry.get("longitude", lng)
                loc_name = entry.get("name", loc_name)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[SERVER] Warning: could not read sensor config: {e}")

        # Sanitize before it becomes part of a filename - an unsanitized node_id like
        # "../../../../tmp/pwned" would let os.path.join escape RECEIVED_IMAGES_DIR
        # entirely and write the seed image anywhere the server process can reach.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        node_slug = safe_filename_component(node_name)
        png_name = f"simulated_{node_slug}_{stamp}.png"
        dest = os.path.join(RECEIVED_IMAGES_DIR, png_name)
        seed = os.path.join(RECEIVED_IMAGES_DIR, "seed_human.png")

        try:
            ensure_dir(RECEIVED_IMAGES_DIR)
            if os.path.exists(seed):
                shutil.copy(seed, dest)
            else:
                from PIL import Image
                Image.new("L", (128, 128), color=128).save(dest)
            prune_simulated_images(RECEIVED_IMAGES_DIR)
        except Exception as e:
            return self._send_json({"error": f"Could not stage image: {e}"}, status=500)

        metadata = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "node_id": node_name,
            "latitude": lat,
            "longitude": lng,
            "location_name": loc_name,
            "threat_type": "HUMAN_INTRUDER",
            "confidence": round(0.85 + random.uniform(-0.05, 0.10), 2),
            "battery_voltage": round(3.6 + random.uniform(0.0, 0.5), 2),
            "battery_percent": random.randint(70, 98),
            "rssi": random.randint(-108, -70),
            "snr": round(random.uniform(-11.5, -3.0), 1),
            "image_name": png_name,
            "action": "PATROL_DISPATCHED",
            "simulated": True,
        }

        record = incident_store.append(INCIDENT_STORE, metadata)
        with open(ALERT_METADATA, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)

        mark_camera_online(node_name)
        print(f"[SERVER] [SIM] {node_name} at {lat}, {lng} ({loc_name})")
        broadcast_event(record)
        return self._send_json({"success": True, "metadata": record})


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exctype, _value, _tb = sys.exc_info()
        if exctype in (ConnectionResetError, BrokenPipeError):
            return   # browser refresh, not worth a traceback
        super().handle_error(request, client_address)


# =============================================================================
# Background receiver
# =============================================================================
def lora_receiver_thread_func(stop_event, loss_rate):
    sock = make_socket(BASE_STATION_HOST, BASE_STATION_PORT, log=lambda m: print(f"[LORA] {m}"))
    if sock is None:
        print("[LORA] Receiver disabled. Dashboard will still serve simulated alerts.")
        return

    def on_alert(record):
        broadcast_event(record)
        mark_camera_online(record.get("node_id", "Camera-Trap-01"))

    def on_heartbeat(node_id):
        mark_camera_online(node_id)
        broadcast_event({"event_type": "HEARTBEAT", "node_id": node_id, "timestamp": time.time()})

    try:
        run_receiver(
            sock=sock,
            output_dir=RECEIVED_IMAGES_DIR,
            config_path=SENSOR_CONFIG,
            store_path=INCIDENT_STORE,
            alert_path=ALERT_METADATA,
            sender_addr=(SENDER_HOST, SENDER_PORT),
            loss_rate=loss_rate,
            on_alert=on_alert,
            on_heartbeat=on_heartbeat,
            log=lambda m: print(f"[LORA] {m}"),
            stop_event=stop_event,
        )
    finally:
        sock.close()


# =============================================================================
# Startup
# =============================================================================
def generate_seed_images():
    """Produce the 128x128 grayscale seed used by /simulate-alert."""
    from PIL import Image

    ensure_dir(RECEIVED_IMAGES_DIR)
    seed_path = os.path.join(RECEIVED_IMAGES_DIR, "seed_human.png")
    if os.path.exists(seed_path):
        return

    source = os.path.join(MOCK_IMAGES_DIR, "human_intruder_01.jpg")
    if not os.path.exists(source):
        print(f"[SERVER] Warning: seed source missing: {source}")
        return

    try:
        with Image.open(source) as img:
            img.resize((128, 128), Image.Resampling.LANCZOS).convert("L").save(seed_path, "PNG")
        print(f"[SERVER] Generated seed image: {seed_path}")
    except Exception as e:
        print(f"[SERVER] Failed to generate seed image: {e}")


def reset_received_images():
    """
    Wipe received_images/ - only ever on an explicit --reset.

    This used to run unconditionally at every startup, destroying the entire image
    archive while the browser's localStorage kept referencing the deleted files, so the
    incident history came back with every image broken (FIX_PLAN.md B1).
    """
    if not os.path.isdir(RECEIVED_IMAGES_DIR):
        ensure_dir(RECEIVED_IMAGES_DIR)
        return

    archive = os.path.join(RECEIVED_IMAGES_DIR, "archive",
                           datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(archive, exist_ok=True)

    moved = 0
    for name in os.listdir(RECEIVED_IMAGES_DIR):
        src = os.path.join(RECEIVED_IMAGES_DIR, name)
        if name == "archive" or not os.path.isfile(src):
            continue
        shutil.move(src, os.path.join(archive, name))
        moved += 1

    print(f"[SERVER] --reset: archived {moved} file(s) to {archive}")


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def parse_args():
    parser = argparse.ArgumentParser(description="AP-NET base station dashboard server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. 127.0.0.1 (default) keeps the dashboard "
                             "local-only. Pass 0.0.0.0 to view it from another machine "
                             "on the LAN (e.g. a Raspberry Pi field node reaching this "
                             "as its base station) - doing so requires a password; see "
                             "--user/--password.")
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    parser.add_argument("--loss-rate", type=float, default=0.15,
                        help="Simulated LoRa packet loss (0.0 disables)")
    parser.add_argument("--reset", action="store_true",
                        help="Archive everything currently in received_images/ before starting")
    parser.add_argument("--no-receiver", action="store_true",
                        help="Do not start the UDP receiver (frees port 5005)")
    parser.add_argument("--user", default="ranger",
                        help="Basic Auth username when bound off loopback (default: ranger)")
    parser.add_argument("--password", default="",
                        help="Basic Auth password when bound off loopback. "
                             "Auto-generated and printed at startup if omitted.")
    parser.add_argument("--no-auth", action="store_true",
                        help="DANGEROUS: disable auth even when bound off loopback. "
                             "Only for a trusted, isolated test network.")
    return parser.parse_args()


def configure_auth(args):
    """
    Decide whether Basic Auth is required and set the module-level credentials.

    Loopback-only binding needs no auth: only processes on this machine can open the
    socket at all. Anything else defaults to requiring a password (FIX_PLAN.md C1) -
    the dashboard used to bind 0.0.0.0 with no authentication whatsoever, so anyone on
    the same Wi-Fi could read live ranger response locations or forge alerts.

    Returns the password actually in effect, or None when auth is disabled.
    """
    global AUTH_CREDENTIALS

    if args.host in LOOPBACK_HOSTS:
        AUTH_CREDENTIALS = None
        return None

    if args.no_auth:
        AUTH_CREDENTIALS = None
        return None

    password = args.password or secrets.token_urlsafe(12)
    AUTH_CREDENTIALS = (args.user, password)
    return password


def main():
    args = parse_args()

    ensure_dir(STATIC_DIR)
    ensure_dir(RECEIVED_IMAGES_DIR)

    if args.reset:
        reset_received_images()

    generate_seed_images()

    password = configure_auth(args)

    stop_event = threading.Event()
    if not args.no_receiver:
        threading.Thread(target=lora_receiver_thread_func,
                         args=(stop_event, args.loss_rate), daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), DashboardHTTPHandler)

    existing = len(incident_store.load_all(INCIDENT_STORE))
    display_host = "localhost" if args.host in ("0.0.0.0", "") else args.host

    print("=" * 80)
    print("              ANTI-POACHING BASE STATION DASHBOARD".center(80))
    print("=" * 80)
    print(f"  Dashboard   : http://{display_host}:{args.port}")
    if args.host not in LOOPBACK_HOSTS:
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
            print(f"  On the LAN  : http://{lan_ip}:{args.port}")
        except OSError:
            pass
        if password:
            print("  AUTH        : Basic Auth required (the browser will prompt).")
            print(f"                user     : {args.user}")
            print(f"                password : {password}")
            if not args.password:
                print("                (auto-generated - pass --password to set a fixed one)")
        else:
            print("  AUTH        : *** DISABLED (--no-auth) — anyone on this network can")
            print("                read alerts, move camera traps, or forge intrusions. ***")
    print(f"  LoRa RX     : {'disabled' if args.no_receiver else f'{BASE_STATION_HOST}:{BASE_STATION_PORT}'}")
    print(f"  Incidents   : {existing} in history")
    print("  Ctrl+C to stop.")
    print("=" * 80)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down...")
    finally:
        stop_event.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
