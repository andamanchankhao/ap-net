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
import math
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

# Planning.md 7.1 suggests 128x128 of the whole frame. On a 1920x1080 capture that
# leaves a person too few pixels to recognise, and squashing 16:9 into a square narrows
# them further. Cropping to the detection box first spends about the same bytes on the
# person instead of the background: ~1.5 KB / 12 fragments at 256 px, measured on a
# 1920x1080 frame (a leafy forest scene compresses worse than that).
TARGET_LONG_SIDE = 256     # longest edge of the image that goes on air
CROP_PADDING = 0.10        # context kept around the person box, per side
WEBP_QUALITY = 60


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


def person_crop_box(boxes, width, height, padding=CROP_PADDING):
    """
    One region covering every person box, padded and clamped to the frame.

    Returns (left, top, right, bottom), or None when there is nothing to crop to - the
    mock detector reports no boxes, and neither does a manually forced alert.
    """
    if not boxes:
        return None

    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    pad_x, pad_y = (x2 - x1) * padding, (y2 - y1) * padding

    left, top = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
    right, bottom = min(width, int(math.ceil(x2 + pad_x))), min(height, int(math.ceil(y2 + pad_y)))
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right, bottom


def fit_long_side(size, long_side=TARGET_LONG_SIDE):
    """
    Scale (w, h) so the longer edge is `long_side`, keeping the aspect ratio.

    Never enlarges: a distant person's small crop gains no detail from upscaling, only
    bytes on air.
    """
    w, h = size
    scale = min(1.0, long_side / max(w, h))
    return max(1, round(w * scale)), max(1, round(h * scale))


def process_image(image_path, output_payload_path, output_preview_path, boxes=None):
    """
    Crop to the detected people, downscale, drop chrominance, compress to WebP, encode Base64.

    `boxes` are the detector's (x1, y1, x2, y2, score) tuples in source-image pixels.
    Without any, the whole frame is sent instead, still at its own aspect ratio.

    Note on grayscale (FIX_PLAN.md E2): WebP has no grayscale mode, so Pillow re-encodes
    the "L" image as RGB. The flat chroma planes still compress to near nothing, so the
    size win is real, but the decoded image comes back as RGB - the base station converts
    it back to "L" before saving its preview.
    """
    with Image.open(image_path) as img:
        region = person_crop_box(boxes, img.width, img.height)
        source = img.crop(region) if region else img
        size = fit_long_side(source.size)

        what = f"person crop {source.width}x{source.height}" if region else "full frame"
        print(f"[EDGE-VISION] Minimising: {what} -> {size[0]}x{size[1]}, "
              f"grayscale, WebP q{WEBP_QUALITY}...")

        img_gray = source.resize(size, Image.Resampling.LANCZOS).convert("L")

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
            process_image(args.image, args.output_payload, args.output_preview, result.boxes)
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
