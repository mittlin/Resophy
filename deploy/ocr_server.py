"""Standalone OCR microservice for Resophy scanned-PDF translation.

Runs RapidOCR (ONNX Runtime, CPU) behind a tiny HTTP API so translation
clients can offload page recognition to a more powerful machine.

Deploy on the LLM/parsing server (isolated or shared venv):

    pip install fastapi "uvicorn[standard]" rapidocr_onnxruntime
    uvicorn ocr_server:app --host 0.0.0.0 --port 6003

API:
    GET  /health -> {"status": "ok"}
    POST /ocr    -> multipart file=<image bytes>
                    {"results": [{"box": [[x, y], ...], "text": str, "score": float}]}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from rapidocr_onnxruntime import RapidOCR

app = FastAPI(title="Resophy OCR Service", version="1.0")
_ocr = RapidOCR()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
