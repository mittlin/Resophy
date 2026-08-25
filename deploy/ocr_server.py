"""Standalone OCR + document-layout microservice for Resophy PDF translation.

Runs RapidOCR (ONNX Runtime, CPU) and an optional DocLayout-YOLO ONNX model
behind a tiny HTTP API so translation clients can offload page recognition
and layout analysis to a more powerful machine.

Deploy on the LLM/parsing server (isolated or shared venv):

    pip install fastapi "uvicorn[standard]" rapidocr_onnxruntime
    uvicorn ocr_server:app --host 0.0.0.0 --port 6003

The layout model is optional. Point LAYOUT_MODEL_PATH at
doclayout_yolo_docstructbench_imgsz1024.onnx (default:
<this dir>/models/doclayout_yolo_docstructbench_imgsz1024.onnx).
When present, /health reports "layout": true and babeldoc clients may pass
--rpc-doclayout3 to offload layout inference.

API:
    GET  /health   -> {"status": "ok", "layout": bool}
    POST /ocr      -> multipart file=<image bytes>
                      {"results": [{"box": [[x, y], ...], "text": str, "score": float}]}
    POST /analyze  -> multipart file=<JPEG bytes>  (babeldoc rpc_doclayout3 protocol)
                      {"boxes": [{"coords": [x1, y1, x2, y2], "label": str,
                                  "ocr_match_score": float}]}
"""

from __future__ import annotations

import ast
import logging
import os
import threading
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger("ocr_server")

app = FastAPI(title="Resophy OCR Service", version="1.1")
# Lower box_thresh + wider unclip recover faint/thin lines the defaults miss on
# dense scanned standards (measured: missed lines 11 -> 3 across 3 pages, and
# ~30% faster detection).
_ocr = RapidOCR(det_box_thresh=0.3, det_unclip_ratio=2.5)

_LAYOUT_MODEL_FILE = "doclayout_yolo_docstructbench_imgsz1024.onnx"
_LAYOUT_CONF_THRESHOLD = 0.25

_layout_session = None
_layout_stride = 32
_layout_names: dict[int, str] = {}
_layout_semaphore = threading.Semaphore(2)


def _load_layout_model() -> None:
    """Load the DocLayout-YOLO ONNX model if available (optional feature)."""
    global _layout_session, _layout_stride, _layout_names
    try:
        import onnxruntime as ort

        default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", _LAYOUT_MODEL_FILE)
        model_path = os.environ.get("LAYOUT_MODEL_PATH") or default_path
        if not os.path.exists(model_path):
            logger.warning("Layout model not found at %s; /analyze disabled", model_path)
            return

        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        meta = session.get_modelmeta().custom_metadata_map
        _layout_stride = int(ast.literal_eval(meta.get("stride", "32")))
        _layout_names = {int(k): str(v) for k, v in ast.literal_eval(meta.get("names", "{}")).items()}
        _layout_session = session
        logger.info("Layout model loaded from %s (stride=%s)", model_path, _layout_stride)
    except Exception:  # noqa: BLE001
        _layout_session = None
        logger.exception("Failed to load layout model; /analyze disabled")


_load_layout_model()


def _resize_and_pad(image: Any, new_shape: tuple[int, int]) -> Any:
    """Replicate babeldoc OnnxModel.resize_and_pad_image preprocessing."""
    import cv2

    h, w = image.shape[:2]
    new_h, new_w = new_shape
    ratio = min(new_h / h, new_w / w)
    resized_h, resized_w = int(round(h * ratio)), int(round(w * ratio))
    image = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    pad_w = (new_w - resized_w) % _layout_stride
    pad_h = (new_h - resized_h) % _layout_stride
    top, bottom = pad_h // 2, pad_h - pad_h // 2
    left, right = pad_w // 2, pad_w - pad_w // 2
    return cv2.copyMakeBorder(
        image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
    )


def _analyze(data: bytes) -> list[dict[str, Any]]:
    """Run DocLayout-YOLO inference on a JPEG image (babeldoc-compatible)."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid image data")

    orig_h, orig_w = image.shape[:2]
    pix = _resize_and_pad(image, (1024, 1024))
    pad_h, pad_w = pix.shape[:2]
    batch = np.transpose(pix, (2, 0, 1))[None].astype(np.float32) / 255.0

    input_name = _layout_session.get_inputs()[0].name
    with _layout_semaphore:
        preds = _layout_session.run(None, {input_name: batch})[0][0]

    keep = preds[preds[..., 4] > _LAYOUT_CONF_THRESHOLD]
    gain = min(pad_h / orig_h, pad_w / orig_w)
    pad_x = round((pad_w - orig_w * gain) / 2 - 0.1)
    pad_y = round((pad_h - orig_h * gain) / 2 - 0.1)

    boxes: list[dict[str, Any]] = []
    for row in keep:
        x1, y1, x2, y2, conf, cls_id = row[:6]
        boxes.append(
            {
                "coords": [
                    float((x1 - pad_x) / gain),
                    float((y1 - pad_y) / gain),
                    float((x2 - pad_x) / gain),
                    float((y2 - pad_y) / gain),
                ],
                "label": _layout_names.get(int(cls_id), str(int(cls_id))),
                "ocr_match_score": float(conf),
            }
        )
    return boxes


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "layout": _layout_session is not None}


@app.post("/ocr")
def ocr_endpoint(file: UploadFile = File(...)) -> dict[str, Any]:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        result, _ = _ocr(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}") from exc

    items: list[dict[str, Any]] = []
    if result:
        for box, text, score in result:
            items.append(
                {
                    "box": [[float(point[0]), float(point[1])] for point in box],
                    "text": text,
                    "score": float(score),
                }
            )
    return {"results": items}


@app.post("/analyze")
def analyze_endpoint(file: UploadFile = File(...)) -> dict[str, Any]:
    if _layout_session is None:
        raise HTTPException(status_code=503, detail="layout model not loaded")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        return {"boxes": _analyze(data)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"layout analysis failed: {exc}") from exc
