"""
AP-NET edge node: AI decision + extreme data minimisation.

Exit codes (FIX_PLAN.md B4 - these used to be ambiguous):

    0  human detected      -> payload written, caller should transmit
    1  no threat           -> image cached to local SD, no transmission
    2  error               -> could not complete; caller must NOT treat as "no threat"

Previously both "no threat" and "crashed" exited 1, so run_demo.sh cheerfully reported
a hard failure as a successful negative classification.
"""

import os
import sys
import argparse
import base64
from io import BytesIO

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import SD_CARD_DIR, COMPRESSED_PAYLOAD, COMPRESSED_PREVIEW, ensure_parent, ensure_dir
from detector import detect_human, DEFAULT_THRESHOLD

EXIT_HUMAN_DETECTED = 0
EXIT_NO_THREAT = 1
EXIT_ERROR = 2

TARGET_SIZE = (128, 128)   # Planning.md 7.1
WEBP_QUALITY = 80


def parse_args():
    parser = argparse.ArgumentParser(description="Edge Node AI & Vision Pipeline")
    parser.add_argument("--image", required=True, help="Path to the captured image")
    parser.add_argument("--model", default="", help="Model path (default: auto-discover in edge_node/models/)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Person confidence threshold (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--sd-card", default=SD_CARD_DIR, help="Mock SD card directory")
    parser.add_argument("--output-payload", default=COMPRESSED_PAYLOAD, help="Where to write the Base64 payload")
    parser.add_argument("--output-preview", default=COMPRESSED_PREVIEW, help="Where to write the WebP preview")
    parser.add_argument("--allow-mock", action="store_true",
                        help="Permit the filename-heuristic fallback when no model is installed (demos only)")
    return parser.parse_args()


def process_image(image_path, output_payload_path, output_preview_path):
    """
    Downscale to 128x128, drop chrominance, compress to WebP, encode Base64.

    Note on grayscale (FIX_PLAN.md E2): WebP has no grayscale mode, so Pillow re-encodes
    the "L" image as RGB. The flat chroma planes still compress to near nothing, so the
    size win is real, but the decoded image comes back as RGB - the base station converts
    it back to "L" before saving its preview.
    """
    print(f"[EDGE-VISION] Minimising: {TARGET_SIZE[0]}x{TARGET_SIZE[1]}, grayscale, WebP q{WEBP_QUALITY}...")

    with Image.open(image_path) as img:
        img_gray = img.resize(TARGET_SIZE, Image.Resampling.LANCZOS).convert("L")

        buffer = BytesIO()
        img_gray.save(buffer, format="WebP", quality=WEBP_QUALITY)
        webp_data = buffer.getvalue()

    # Create directories BEFORE writing (FIX_PLAN.md A2 - this used to be the other way round)
    ensure_parent(output_preview_path)
    with open(output_preview_path, "wb") as f:
        f.write(webp_data)
    print(f"[EDGE-VISION] Preview written: {output_preview_path} ({len(webp_data)} bytes)")

    b64_data = base64.b64encode(webp_data).decode("utf-8")

    ensure_parent(output_payload_path)
    with open(output_payload_path, "w", encoding="utf-8") as f:
        f.write(b64_data)
    print(f"[EDGE-VISION] Base64 payload written: {output_payload_path} ({len(b64_data)} chars)")

    return len(webp_data), len(b64_data)


def cache_to_sd_card(image_path, sd_card_dir):
    """Store the full-resolution frame locally for later ecological study."""
    ensure_dir(sd_card_dir)
    dest_path = os.path.join(sd_card_dir, os.path.basename(image_path))
    with Image.open(image_path) as img:
        img.save(dest_path)
    return dest_path


def main():
    args = parse_args()

    if not os.path.exists(args.image):
        print(f"[EDGE] ERROR: image not found: {args.image}", file=sys.stderr)
        return EXIT_ERROR

    try:
        result = detect_human(args.image, args.model, args.threshold, allow_mock=args.allow_mock)
    except RuntimeError as e:
        print(f"[EDGE] ERROR: {e}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:
        print(f"[EDGE] ERROR: inference failed: {e}", file=sys.stderr)
        return EXIT_ERROR

    backend_note = f" via {result.backend}" + (f" ({result.note})" if result.note else "")

    if result.human_detected:
        print(f"[EDGE] ALERT: human detected at {result.confidence:.2%} confidence{backend_note}")
        for x1, y1, x2, y2, score in result.boxes:
            print(f"         person {score:.2f} at ({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})")
        try:
            process_image(args.image, args.output_payload, args.output_preview)
        except Exception as e:
            print(f"[EDGE] ERROR: compression failed: {e}", file=sys.stderr)
            return EXIT_ERROR
        print("[EDGE] Ready for transmission.")
        return EXIT_HUMAN_DETECTED

    print(f"[EDGE] No threat (person confidence {result.confidence:.2%}{backend_note})")
    try:
        dest = cache_to_sd_card(args.image, args.sd_card)
    except Exception as e:
        print(f"[EDGE] ERROR: SD cache failed: {e}", file=sys.stderr)
        return EXIT_ERROR

    print(f"[EDGE] Full-resolution frame cached to SD: {dest}")
    print("[EDGE] Radio stays silent. Returning to standby.")
    return EXIT_NO_THREAT


if __name__ == "__main__":
    sys.exit(main())
