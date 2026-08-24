from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List

import requests

from resophy.core.base_paper import Paper
from resophy.core.paper_store import paper_store

PaperList = List[Paper]
CategoryPath = List[str]


@dataclass
class TranslationDependencies:
    translation_tasks: Dict[str, Dict[str, Any]]
    translation_tasks_lock: threading.Lock
    get_categories: Callable[[], dict]
    get_category_path: Callable[[dict, str], CategoryPath | None]
    get_papers_in_category: Callable[[str, CategoryPath], PaperList]
    save_paper_metadata: Callable[[str, Paper], None]


def _write_translation_log(
    pdf_dir: str, base_name: str, log_lines: List[str]
) -> str | None:
    """Persist babeldoc output for both success and failure paths"""
    log_file = os.path.join(pdf_dir, f"{base_name}.translate.log")
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
    except Exception as e:  # noqa: BLE001
        print(f"Failed to save log file: {e}")
        return None
    return log_file


_FAILURE_PATTERNS = [
    ("scannedpdferror", "scanned PDF and OCR workaround did not take effect"),
    ("contains no paragraphs", "no extractable text (scanned PDF without text layer)"),
    ("total tokens: 0", "LLM service unreachable (0 tokens consumed)"),
    ("api key", "invalid LLM API key"),
    ("unauthorized", "LLM service authentication failed"),
    ("401", "LLM service authentication failed"),
    ("does not exist", "model name mismatch"),
    ("404", "model not found"),
    ("connection error", "cannot connect to LLM service"),
    ("apiconnectionerror", "cannot connect to LLM service"),
    ("max retries exceeded", "cannot connect to LLM service"),
]


def _detect_failure_reason(log_lines: List[str]) -> str:
    text = "\n".join(log_lines).lower()
    for pattern, reason in _FAILURE_PATTERNS:
        if pattern in text:
            return reason
    return ""


def _build_failure_message(message: str, reason: str, log_file: str | None) -> str:
    parts = [message]
    if reason:
        parts.append(reason)
    if log_file:
        parts.append(f"see {log_file}")
    return ": ".join(parts)


_OCR_DPI = 200
_OCR_MIN_SCORE = 0.5
_OCR_SERVICE_TIMEOUT = 120
_OCR_SERVICE_WORKERS = 6
_OCR_SERVICE_ATTEMPTS = 3
_OCR_SERVICE_RETRY_DELAY = 1.0
_OCR_SERVICE_HEALTH_TIMEOUT = 5
_SCAN_SAMPLE_LIMIT = 6


class OcrServiceError(RuntimeError):
    """Raised when the remote OCR service fails after retries"""


def _service_healthy(service_url: str) -> bool:
    """Probe the OCR microservice health endpoint"""
    try:
        response = requests.get(
            f"{service_url.rstrip('/')}/health",
            timeout=_OCR_SERVICE_HEALTH_TIMEOUT,
        )
        return response.ok
    except Exception:  # noqa: BLE001
        return False


def _sample_page_indices(page_count: int, limit: int = _SCAN_SAMPLE_LIMIT) -> List[int]:
    if page_count <= limit:
        return list(range(page_count))
    step = page_count / limit
    return sorted({int(i * step) for i in range(limit)})


def _needs_text_layer(pdf_path: str) -> bool:
    """Detect image-only scanned PDFs by sampling pages for extractable text"""
    try:
        import fitz

        doc = fitz.open(pdf_path)
    except Exception as e:  # noqa: BLE001
        print(f"Cannot open PDF for scan check: {e}")
        return False
    with doc:
        indices = _sample_page_indices(doc.page_count)
        if not indices:
            return False
        has_image = False
        for idx in indices:
            page = doc.load_page(idx)
            if page.get_text().strip():
                return False
            if page.get_images(full=True):
                has_image = True
        return has_image


def _write_text_boxes(page, boxes, font, scale) -> int:
    """Write OCR boxes as an invisible text layer, return characters written"""
    import fitz

    written = 0
    for box, text, score in boxes:
        if score < _OCR_MIN_SCORE or not text.strip():
            continue
        xs = [point[0] * scale for point in box]
        ys = [point[1] * scale for point in box]
        rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
        rect &= page.rect
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            continue
        unit_width = max(font.text_length(text, fontsize=10) / 10, 0.1)
        fontsize = min(rect.height * 0.9, rect.width / unit_width)
        baseline = fitz.Point(rect.x0, rect.y1 - rect.height * 0.15)
        page.insert_text(
            baseline,
            text,
            fontname="china-s",
            fontsize=fontsize,
            render_mode=3,
        )
        written += len(text)
    return written


def _ocr_page_via_service(png_bytes: bytes, service_url: str) -> list:
    """Send one rendered page to the OCR microservice and return its boxes"""
    response = requests.post(
        f"{service_url.rstrip('/')}/ocr",
        files={"file": ("page.png", png_bytes, "image/png")},
        timeout=_OCR_SERVICE_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json().get("results", [])
    return [(item["box"], item["text"], float(item["score"])) for item in payload]


def _ocr_page_with_retry(png_bytes: bytes, service_url: str, page_no: int) -> list:
    """OCR one page through the service, retrying transient failures"""
    last_exc: Exception | None = None
    for attempt in range(1, _OCR_SERVICE_ATTEMPTS + 1):
        try:
            return _ocr_page_via_service(png_bytes, service_url)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _OCR_SERVICE_ATTEMPTS:
                time.sleep(_OCR_SERVICE_RETRY_DELAY)
    raise OcrServiceError(
        f"page {page_no}: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _build_ocr_pdf(
    pdf_path: str,
    progress_cb: Callable[[str], None],
    service_url: str = "",
) -> str:
    """Build an invisible OCR text layer via remote service; fall back to local
    CPU OCR only when the service is unreachable at start"""
    url = service_url.strip()
    if url:
        if _service_healthy(url):
            return _build_ocr_pdf_service(pdf_path, progress_cb, url)
        progress_cb(f"OCR service {url} unreachable, using local CPU OCR")
    return _build_ocr_pdf_local(pdf_path, progress_cb)


def _build_ocr_pdf_local(pdf_path: str, progress_cb: Callable[[str], None]) -> str:
    """Run local CPU OCR and write an invisible text layer so babeldoc can translate"""
    import fitz
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    font = fitz.Font("china-s")
    scale = 72 / _OCR_DPI
    out_path = os.path.splitext(pdf_path)[0] + ".ocr.pdf"

    doc = fitz.open(pdf_path)
    try:
        total = doc.page_count
        ocr_char_count = 0
        for idx in range(total):
            page = doc.load_page(idx)
            pix = page.get_pixmap(dpi=_OCR_DPI)
            result, _ = ocr(pix.tobytes("png"))
            boxes = []
            if result:
                boxes = [(box, text, float(score)) for box, text, score in result]
            ocr_char_count += _write_text_boxes(page, boxes, font, scale)
            if (idx + 1) % 10 == 0 or idx + 1 == total:
                progress_cb(f"OCR progress: {idx + 1}/{total} pages")
        progress_cb(f"OCR finished, extracted {ocr_char_count} characters")
        doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()
    return out_path


def _build_ocr_pdf_service(
    pdf_path: str,
    progress_cb: Callable[[str], None],
    service_url: str,
) -> str:
    """OCR pages through the HTTP microservice; abort on unrecoverable failures"""
    import fitz

    font = fitz.Font("china-s")
    scale = 72 / _OCR_DPI
    out_path = os.path.splitext(pdf_path)[0] + ".ocr.pdf"

    doc = fitz.open(pdf_path)
    executor = ThreadPoolExecutor(max_workers=_OCR_SERVICE_WORKERS)
    try:
        total = doc.page_count
        ocr_char_count = 0
        inflight: deque = deque()
        next_idx = 0

        def submit(idx):
            pix = doc.load_page(idx).get_pixmap(dpi=_OCR_DPI)
            future = executor.submit(
                _ocr_page_with_retry, pix.tobytes("png"), service_url, idx + 1
            )
            inflight.append((future, idx))

        prefetch = min(_OCR_SERVICE_WORKERS * 2, total)
        while next_idx < prefetch:
            submit(next_idx)
            next_idx += 1
        while inflight:
            future, idx = inflight.popleft()
            boxes = future.result()
            ocr_char_count += _write_text_boxes(
                doc.load_page(idx), boxes, font, scale
            )
            if (idx + 1) % 10 == 0 or idx + 1 == total:
                progress_cb(f"OCR progress: {idx + 1}/{total} pages")
            if next_idx < total:
                submit(next_idx)
                next_idx += 1
        progress_cb(f"OCR finished, extracted {ocr_char_count} characters")
        doc.save(out_path, garbage=3, deflate=True)
    except OcrServiceError:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    finally:
        doc.close()
    return out_path


def translate_paper_task(
    task_id: str,
    paper_id: str,
    pdf_path: str,
    pdf_dir: str,
    pdf_filename: str,
    openai_model: str,
    openai_base_url: str,
    openai_api_key: str,
    deps: TranslationDependencies,
    ocr_service_url: str = "",
) -> None:
    """Background translation tasks"""
    start_time = datetime.now()  # Recording start time
    with deps.translation_tasks_lock:
        task_info = deps.translation_tasks[task_id]
        task_info["status"] = "running"
        log_lines = task_info["logs"]
        log_lock = task_info["log_lock"]
        process = None

    def read_output(pipe, label):
        """Read subprocess output in real time"""
        try:
            for line in iter(pipe.readline, ""):
                if line:
                    line = line.rstrip()
                    print(f"[{label}] {line}")
                    with log_lock:
                        log_lines.append(f"[{label}] {line}")
        except Exception as e:  # noqa: BLE001
            print(f"Error while reading output: {e}")
        finally:
            pipe.close()

    original_cwd = os.getcwd()
    try:
        os.chdir(pdf_dir)

        target_pdf_filename = pdf_filename
        ocr_applied = False
        source_pdf_path = os.path.join(pdf_dir, pdf_filename)
        if _needs_text_layer(source_pdf_path):

            def _ocr_progress(message: str) -> None:
                print(message)
                with log_lock:
                    log_lines.append(f"[Resophy] {message}")

            ocr_path = _build_ocr_pdf(source_pdf_path, _ocr_progress, ocr_service_url)
            target_pdf_filename = os.path.basename(ocr_path)
            ocr_applied = True

        cmd = [
            "babeldoc",
            "--openai",
            "--openai-model",
            openai_model,
            "--openai-base-url",
            openai_base_url,
            "--openai-api-key",
            openai_api_key,
            "--auto-enable-ocr-workaround",
            "--files",
            target_pdf_filename,
        ]

        print(f"Execute translation command: {' '.join(cmd)}")
        print(f"working directory: {pdf_dir}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        with deps.translation_tasks_lock:
            deps.translation_tasks[task_id]["process"] = process

        stdout_thread = threading.Thread(
            target=read_output, args=(process.stdout, "STDOUT")
        )
        stderr_thread = threading.Thread(
            target=read_output, args=(process.stderr, "STDERR")
        )
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        return_code = process.wait(timeout=7200)

        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        with deps.translation_tasks_lock:
            if return_code == 0:
                base_name = os.path.splitext(pdf_filename)[0]
                zero_tokens = any(
                    "total tokens: 0" in line.lower() for line in log_lines
                )
                if ocr_applied and not zero_tokens:
                    produced_dual = os.path.join(
                        pdf_dir, f"{base_name}.ocr.zh.dual.pdf"
                    )
                    produced_mono = os.path.join(
                        pdf_dir, f"{base_name}.ocr.zh.mono.pdf"
                    )
                    ocr_input_path = os.path.join(pdf_dir, f"{base_name}.ocr.pdf")
                    if os.path.exists(produced_dual):
                        os.replace(
                            produced_dual,
                            os.path.join(pdf_dir, f"{base_name}.zh.dual.pdf"),
                        )
                    if os.path.exists(produced_mono):
                        os.remove(produced_mono)
                    if os.path.exists(ocr_input_path):
                        os.remove(ocr_input_path)

                dual_file = os.path.join(pdf_dir, f"{base_name}.zh.dual.pdf")
                mono_file = os.path.join(pdf_dir, f"{base_name}.zh.mono.pdf")

                if os.path.exists(dual_file) and not zero_tokens:
                    if os.path.exists(mono_file):
                        os.remove(mono_file)

                    # First try from paper_store Find papers in (supports _ReadingListTemp Table of contents)
                    entry = paper_store.get_entry(paper_id)
                    if entry:
                        paper = entry.paper
                        paper.mark_chinese_version(dual_file)
                        target_path = paper.file_path or pdf_path
                        if target_path:
                            deps.save_paper_metadata(target_path, paper)
                    else:
                        # if paper_store Not found in , use recursive search of classification tree
                        categories = deps.get_categories()

                        def search_and_update_paper(node):
                            category_path = deps.get_category_path(categories, node["id"])
                            if category_path:
                                papers = deps.get_papers_in_category(
                                    node["id"], category_path
                                )
                                for paper in papers:
                                    if paper.id == paper_id:
                                        paper.mark_chinese_version(dual_file)
                                        target_path = paper.file_path or pdf_path
                                        if target_path:
                                            deps.save_paper_metadata(target_path, paper)
                                        return True
                            if "children" in node:
                                for child in node["children"]:
                                    if search_and_update_paper(child):
                                        return True
                            return False

                        for child in categories.get("children", []):
                            if search_and_update_paper(child):
                                break

                    log_file = _write_translation_log(pdf_dir, base_name, log_lines)

                    end_time = datetime.now()
                    translation_duration = int((end_time - start_time).total_seconds())

                    # First try from paper_store Find papers in (supports _ReadingListTemp Table of contents)
                    entry = paper_store.get_entry(paper_id)
                    if entry:
                        paper = entry.paper
                        paper.translation_time = max(
                            getattr(paper, "translation_time", 0),
                            translation_duration,
                        )
                        path = paper.file_path
                        if path and os.path.exists(path):
                            deps.save_paper_metadata(path, paper)
                    else:
                        # if paper_store Not found in , use recursive search of classification tree
                        categories = deps.get_categories()

                        def search_and_update_time(node):
                            category_path = deps.get_category_path(categories, node["id"])
                            if category_path:
                                papers = deps.get_papers_in_category(
                                    node["id"], category_path
                                )
                                for paper in papers:
                                    if paper.id == paper_id:
                                        paper.translation_time = max(
                                            getattr(paper, "translation_time", 0),
                                            translation_duration,
                                        )
                                        path = paper.file_path
                                        if path and os.path.exists(path):
                                            deps.save_paper_metadata(path, paper)
                                        return True
                            if "children" in node:
                                for child in node["children"]:
                                    if search_and_update_time(child):
                                        return True
                            return False

                        for child in categories.get("children", []):
                            if search_and_update_time(child):
                                break

                    deps.translation_tasks[task_id]["status"] = "completed"
                    deps.translation_tasks[task_id]["result"] = {
                        "success": True,
                        "chinese_version_path": dual_file,
                        "log_file": log_file,
                    }
                else:
                    base_name = os.path.splitext(pdf_filename)[0]
                    log_file = _write_translation_log(pdf_dir, base_name, log_lines)
                    reason = _detect_failure_reason(log_lines)
                    deps.translation_tasks[task_id]["status"] = "failed"
                    deps.translation_tasks[task_id]["result"] = {
                        "success": False,
                        "error": _build_failure_message(
                            "Translation file not generated", reason, log_file
                        ),
                    }
            else:
                base_name = os.path.splitext(pdf_filename)[0]
                log_file = _write_translation_log(pdf_dir, base_name, log_lines)
                reason = _detect_failure_reason(log_lines)
                deps.translation_tasks[task_id]["status"] = "failed"
                deps.translation_tasks[task_id]["result"] = {
                    "success": False,
                    "error": _build_failure_message(
                        f"Translation failed (exit code: {return_code})",
                        reason,
                        log_file,
                    ),
                }

    except OcrServiceError as e:
        print(f"OCR service error: {e}")
        with log_lock:
            log_lines.append(f"[Resophy] OCR service error: {e}")
        base_name = os.path.splitext(pdf_filename)[0]
        log_file = _write_translation_log(pdf_dir, base_name, log_lines)
        with deps.translation_tasks_lock:
            deps.translation_tasks[task_id]["status"] = "failed"
            deps.translation_tasks[task_id]["result"] = {
                "success": False,
                "error": (
                    f"OCR服务异常（{e}），本次未回落本地；"
                    f"请检查OCR服务器 {ocr_service_url} 后重新发起翻译"
                ),
            }
    except subprocess.TimeoutExpired:
        log_file = _write_translation_log(
            pdf_dir, os.path.splitext(pdf_filename)[0], log_lines
        )
        with deps.translation_tasks_lock:
            deps.translation_tasks[task_id]["status"] = "failed"
            deps.translation_tasks[task_id]["result"] = {
                "success": False,
                "error": _build_failure_message("Translation timeout", "", log_file),
            }
        if process:
            process.kill()
    except Exception as e:  # noqa: BLE001
        print(f"An error occurred during translation: {str(e)}")
        import traceback

        traceback.print_exc()
        with deps.translation_tasks_lock:
            deps.translation_tasks[task_id]["status"] = "failed"
            deps.translation_tasks[task_id]["result"] = {
                "success": False,
                "error": f"Translation failed: {str(e)}",
            }
        if process:
            process.kill()
    finally:
        os.chdir(original_cwd)
