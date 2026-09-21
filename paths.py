"""
Single source of truth for every filesystem path in AP-NET.

Every path is derived from this file's own location, so the project can be moved,
copied to a Raspberry Pi, or checked out anywhere without editing a single line.

Scripts inside subdirectories bootstrap onto this module with:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from paths import EDGE_DIR, MOCK_IMAGES_DIR   # etc.
"""

import os

# --- Roots -------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

EDGE_DIR = os.path.join(PROJECT_ROOT, "edge_node")
BASE_STATION_DIR = os.path.join(PROJECT_ROOT, "base_station")
MOCK_IMAGES_DIR = os.path.join(PROJECT_ROOT, "mock_images")

# --- Edge node ---------------------------------------------------------------
SD_CARD_DIR = os.path.join(EDGE_DIR, "local_sd_card")
CAPTURE_DIR = os.path.join(EDGE_DIR, "captures")
COMPRESSED_PAYLOAD = os.path.join(EDGE_DIR, "compressed_payload.txt")
COMPRESSED_PREVIEW = os.path.join(EDGE_DIR, "compressed_preview.webp")
MODELS_DIR = os.path.join(EDGE_DIR, "models")

# --- Base station ------------------------------------------------------------
STATIC_DIR = os.path.join(BASE_STATION_DIR, "dashboard_static")
RECEIVED_IMAGES_DIR = os.path.join(BASE_STATION_DIR, "received_images")
SENSOR_CONFIG = os.path.join(BASE_STATION_DIR, "sensor_config.json")
ALERT_METADATA = os.path.join(RECEIVED_IMAGES_DIR, "alert_metadata.json")
INCIDENT_STORE = os.path.join(RECEIVED_IMAGES_DIR, "incidents.jsonl")

# --- Mock assets -------------------------------------------------------------
MOCK_HUMAN_IMG = os.path.join(MOCK_IMAGES_DIR, "human_intruder_01.jpg")
MOCK_DEER_IMG = os.path.join(MOCK_IMAGES_DIR, "wildlife_deer_01.jpg")
MOCK_SD_CARD_DEER = os.path.join(SD_CARD_DIR, "wildlife_deer_01.jpg")

# --- Network defaults --------------------------------------------------------
BASE_STATION_HOST = "127.0.0.1"
BASE_STATION_PORT = 5005
SENDER_PORT = 5006
DASHBOARD_PORT = 8080


def ensure_parent(path):
    """Create the parent directory of `path` if it does not exist. Returns `path`."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def ensure_dir(path):
    """Create directory `path` if it does not exist. Returns `path`."""
    os.makedirs(path, exist_ok=True)
    return path


def bootstrap():
    """
    Put PROJECT_ROOT on sys.path. Call from scripts that live in subdirectories
    so sibling packages (edge_node, base_station) import cleanly.
    """
    import sys
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)
    return PROJECT_ROOT
