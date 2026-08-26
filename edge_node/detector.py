"""
Human detection for the AP-NET edge node.

Replaces the old filename-substring check (FIX_PLAN.md D3) with real inference.

Backend priority — the first one that can actually load a model wins:

  1. onnxruntime   + yolov8n.onnx     (recommended on Raspberry Pi 5, matches Planning.md 6.2)
  2. ultralytics   + yolov8n.pt       (convenient, heavier: pulls in PyTorch)
  3. tflite        + yolov8n_int8.tflite
  4. mock                             (filename heuristic — DEV ONLY, warns loudly)

The mock backend is still available so the emulation demos run on a machine with no
model installed, but it now announces itself instead of silently pretending to be an AI.

Getting a model onto the Pi:

    pip install ultralytics
    yolo export model=yolov8n.pt format=onnx imgsz=640
    mv yolov8n.onnx edge_node/models/

COCO class 0 is "person"; that is the only class this system acts on.
"""

import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import MODELS_DIR

PERSON_CLASS_ID = 0
DEFAULT_THRESHOLD = 0.70   # Planning.md 6.3
DEFAULT_IMGSZ = 640


class DetectionResult:
    """Outcome of one inference pass."""

    def __init__(self, human_detected, confidence, backend, boxes=None, note=""):
        self.human_detected = human_detected
        self.confidence = confidence
        self.backend = backend
        self.boxes = boxes or []      # [(x1, y1, x2, y2, score), ...] in source-image pixels
        self.note = note

    @property
    def is_mock(self):
        return self.backend == "mock"

    def __iter__(self):
        """Backwards compatibility: `human, conf = detect_human(...)` still works."""
        return iter((self.human_detected, self.confidence))

    def __repr__(self):
        return (f"DetectionResult(human={self.human_detected}, conf={self.confidence:.3f}, "
                f"backend={self.backend!r}, boxes={len(self.boxes)})")


# =============================================================================
# Model discovery
# =============================================================================
def find_model(explicit_path=""):
    """
    Resolve a model file. Explicit path wins; otherwise scan edge_node/models/
    for a known extension. Returns "" when nothing is available.
    """
    if explicit_path:
        return explicit_path if os.path.exists(explicit_path) else ""

    for pattern in ("*.onnx", "*.tflite", "*.pt"):
        found = sorted(glob.glob(os.path.join(MODELS_DIR, pattern)))
        if found:
            return found[0]
    return ""


# =============================================================================
# Pre-processing
# =============================================================================
def _letterbox(img, target=DEFAULT_IMGSZ):
    """
    Resize preserving aspect ratio and pad to a square with grey bars.

    Plain .resize() distorts the subject and measurably costs accuracy, which matters
    when a single frame decides whether a ranger team is dispatched.

    Returns (padded_image, scale, pad_x, pad_y) so boxes can be mapped back.
    """
    from PIL import Image

    src_w, src_h = img.size
    scale = min(target / src_w, target / src_h)
    new_w, new_h = int(round(src_w * scale)), int(round(src_h * scale))

    resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (target, target), (114, 114, 114))
    pad_x, pad_y = (target - new_w) // 2, (target - new_h) // 2
    canvas.paste(resized, (pad_x, pad_y))
    return canvas, scale, pad_x, pad_y


def _prepare_tensor(image_path, target, dtype_is_float, imgsz=DEFAULT_IMGSZ):
    """
    Load an image and produce an NCHW batch plus the letterbox geometry.

    `.convert("RGB")` is mandatory: grayscale night shots and RGBA PNGs would
    otherwise yield a 2- or 4-channel array and blow up the interpreter.
    """
    import numpy as np
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    src_w, src_h = img.size
    padded, scale, pad_x, pad_y = _letterbox(img, imgsz)

    arr = np.array(padded)
    if dtype_is_float:
        arr = arr.astype(np.float32) / 255.0
    else:
        arr = arr.astype(np.uint8)            # INT8/UINT8 quantized models take raw pixels

    arr = np.transpose(arr, (2, 0, 1))        # HWC -> CHW
    arr = np.expand_dims(arr, axis=0)         # -> NCHW
    return arr, (scale, pad_x, pad_y, src_w, src_h)


# =============================================================================
# Post-processing
# =============================================================================
def _decode_yolov8(raw, geometry, threshold):
    """
    Decode a YOLOv8 head into person boxes.

    YOLOv8 is anchor-free and has no objectness channel: output is
    (1, 4 + num_classes, num_anchors), rows 0-3 being cx, cy, w, h in letterbox pixels.
    """
    import numpy as np

    scale, pad_x, pad_y, src_w, src_h = geometry

    pred = np.squeeze(raw)                       # (84, 8400) or (8400, 84)
    if pred.ndim != 2:
        return 0.0, []
    if pred.shape[0] < pred.shape[1]:            # (84, 8400) -> (8400, 84)
        pred = pred.transpose()

    if pred.shape[1] <= 4:
        return 0.0, []

    scores = pred[:, 4 + PERSON_CLASS_ID]
    best = float(scores.max()) if scores.size else 0.0

    keep = scores >= threshold
    boxes = []
    for cx, cy, w, h, score in zip(pred[keep, 0], pred[keep, 1],
                                   pred[keep, 2], pred[keep, 3], scores[keep]):
        # letterbox pixels -> source-image pixels
        x1 = (cx - w / 2 - pad_x) / scale
        y1 = (cy - h / 2 - pad_y) / scale
        x2 = (cx + w / 2 - pad_x) / scale
        y2 = (cy + h / 2 - pad_y) / scale
        boxes.append((max(0.0, x1), max(0.0, y1),
                      min(float(src_w), x2), min(float(src_h), y2), float(score)))

    return best, _nms(boxes, iou_threshold=0.45)


def _nms(boxes, iou_threshold=0.45):
    """Greedy non-maximum suppression on (x1, y1, x2, y2, score) tuples."""
    if not boxes:
        return []

    boxes = sorted(boxes, key=lambda b: b[4], reverse=True)
    kept = []
    for cand in boxes:
        overlaps = False
        for k in kept:
            ix1, iy1 = max(cand[0], k[0]), max(cand[1], k[1])
            ix2, iy2 = min(cand[2], k[2]), min(cand[3], k[3])
            iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
            inter = iw * ih
            if inter <= 0:
                continue
            area_c = (cand[2] - cand[0]) * (cand[3] - cand[1])
            area_k = (k[2] - k[0]) * (k[3] - k[1])
            union = area_c + area_k - inter
            if union > 0 and inter / union > iou_threshold:
                overlaps = True
                break
        if not overlaps:
            kept.append(cand)
    return kept


# =============================================================================
# Backends
# =============================================================================
def _run_onnx(image_path, model_path, threshold):
    import onnxruntime as ort

    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    inp = session.get_inputs()[0]

    imgsz = DEFAULT_IMGSZ
    shape = inp.shape
    if len(shape) == 4 and isinstance(shape[2], int) and shape[2] > 0:
        imgsz = shape[2]

    dtype_is_float = "float" in str(inp.type).lower()
    tensor, geometry = _prepare_tensor(image_path, imgsz, dtype_is_float, imgsz)

    raw = session.run(None, {inp.name: tensor})[0]
    confidence, boxes = _decode_yolov8(raw, geometry, threshold)
    return DetectionResult(confidence >= threshold, confidence, "onnxruntime", boxes)


def _run_ultralytics(image_path, model_path, threshold):
    from ultralytics import YOLO

    model = YOLO(model_path)
    results = model.predict(image_path, conf=threshold, classes=[PERSON_CLASS_ID],
                            imgsz=DEFAULT_IMGSZ, verbose=False)

    confidence, boxes = 0.0, []
    for res in results:
        if res.boxes is None:
            continue
        for box in res.boxes:
            score = float(box.conf[0])
            confidence = max(confidence, score)
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
            boxes.append((x1, y1, x2, y2, score))

    return DetectionResult(confidence >= threshold, confidence, "ultralytics", boxes)


def _run_tflite(image_path, model_path, threshold):
    try:
        import tflite_runtime.interpreter as tflite
        interpreter = tflite.Interpreter(model_path=model_path)
    except ImportError:
        import tensorflow as tf
        interpreter = tf.lite.Interpreter(model_path=model_path)

    import numpy as np

    interpreter.allocate_tensors()
    inp = interpreter.get_input_details()[0]
    out = interpreter.get_output_details()[0]

    imgsz = int(inp["shape"][1]) if len(inp["shape"]) == 4 else DEFAULT_IMGSZ
    dtype_is_float = np.issubdtype(inp["dtype"], np.floating)
    tensor, geometry = _prepare_tensor(image_path, imgsz, dtype_is_float, imgsz)

    # TFLite YOLO exports are usually NHWC, unlike ONNX
    if len(inp["shape"]) == 4 and int(inp["shape"][3]) in (1, 3):
        tensor = np.transpose(tensor, (0, 2, 3, 1))

    interpreter.set_tensor(inp["index"], tensor.astype(inp["dtype"]))
    interpreter.invoke()
    raw = interpreter.get_tensor(out["index"])

    # De-quantize INT8/UINT8 output before decoding
    scale_zp = out.get("quantization", (0.0, 0))
    if scale_zp and scale_zp[0]:
        raw = (raw.astype(np.float32) - scale_zp[1]) * scale_zp[0]

    confidence, boxes = _decode_yolov8(raw, geometry, threshold)
    return DetectionResult(confidence >= threshold, confidence, "tflite", boxes)


def _run_mock(image_path):
    """
    Development fallback: decide from the filename.

    Kept only so the emulation demos run on a machine with no model. It reports
    backend="mock" so callers can refuse to treat the verdict as real evidence.
    """
    name = os.path.basename(image_path).lower()
    is_human = any(tag in name for tag in ("human", "poacher", "intruder"))
    return DetectionResult(
        is_human,
        0.89 if is_human else 0.12,
        "mock",
        note="filename heuristic - NOT a real detection",
    )


# =============================================================================
# Public entry point
# =============================================================================
def detect_human(image_path, model_path="", threshold=DEFAULT_THRESHOLD, allow_mock=True):
    """
    Decide whether `image_path` contains a person.

    Returns a DetectionResult. Raises RuntimeError when no model is available
    and allow_mock is False, so field deployments cannot silently fall back to
    the filename heuristic.
    """
    resolved = find_model(model_path)

    if resolved:
        ext = os.path.splitext(resolved)[1].lower()
        runners = {".onnx": _run_onnx, ".pt": _run_ultralytics, ".tflite": _run_tflite}
        runner = runners.get(ext)
        if runner:
            try:
                return runner(image_path, resolved, threshold)
            except ImportError as e:
                print(f"[DETECTOR] Runtime for {ext} not installed ({e}).")
            except Exception as e:
                print(f"[DETECTOR] Inference failed on {os.path.basename(resolved)}: {e}")
        else:
            print(f"[DETECTOR] Unsupported model extension: {ext}")
    else:
        print("[DETECTOR] No model found in edge_node/models/.")

    if not allow_mock:
        raise RuntimeError(
            "No usable detection model. Export one with:\n"
            "    yolo export model=yolov8n.pt format=onnx imgsz=640\n"
            "then place it in edge_node/models/ (or pass --allow-mock for demos)."
        )

    print("[DETECTOR] *** FALLING BACK TO MOCK (filename heuristic) — not a real detection ***")
    return _run_mock(image_path)


def describe_backends():
    """Report which inference runtimes are importable. Used by --check."""
    status = {}
    for name, module in (("onnxruntime", "onnxruntime"),
                         ("ultralytics", "ultralytics"),
                         ("tflite_runtime", "tflite_runtime"),
                         ("tensorflow", "tensorflow")):
        try:
            __import__(module)
            status[name] = True
        except ImportError:
            status[name] = False
    return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test the AP-NET human detector")
    parser.add_argument("--image", help="Image to run inference on")
    parser.add_argument("--model", default="", help="Model path (default: auto-discover)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--check", action="store_true", help="Report available backends and exit")
    args = parser.parse_args()

    if args.check:
        print(f"Model directory : {MODELS_DIR}")
        print(f"Discovered model: {find_model() or '(none)'}")
        for name, ok in describe_backends().items():
            print(f"  {'OK     ' if ok else 'missing'}  {name}")
        sys.exit(0)

    if not args.image:
        parser.error("--image is required unless --check is given")

    result = detect_human(args.image, args.model, args.threshold)
    print(result)
    for x1, y1, x2, y2, score in result.boxes:
        print(f"  person {score:.3f} at ({x1:.0f}, {y1:.0f}) - ({x2:.0f}, {y2:.0f})")
