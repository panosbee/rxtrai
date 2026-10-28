"""
RxTrail — OCR Engine: queue-based wrapper around Tesseract OCR for Greek prescriptions.

Uses the Tesseract CLI directly (subprocess) — no pytesseract/PIL dependency.
Runs OCR in a background thread to avoid blocking the API event loop.
"""
import asyncio
import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rxtrail.ocr")

# Tesseract binary — auto-detect from known install locations or PATH
_TESSERACT_PATHS = [
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",  # 32-bit, has Greek
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",         # 64-bit
    "tesseract",  # fallback: rely on PATH (Linux)
]

_tesseract_cmd: Optional[str] = None
_executor = ThreadPoolExecutor(max_workers=2)
_ocr_queue: asyncio.Queue = asyncio.Queue(maxsize=20)

_OCR_TIMEOUT_SECONDS = 180


def _find_tesseract() -> str:
    """Locate the Tesseract binary on the system."""
    global _tesseract_cmd
    if _tesseract_cmd:
        return _tesseract_cmd

    for path in _TESSERACT_PATHS:
        if shutil.which(path) or Path(path).exists():
            _tesseract_cmd = path
            logger.info(f"Tesseract found at: {path}")
            return path

    raise RuntimeError(
        "Tesseract OCR not found. Install it from https://github.com/UB-Mannheim/tesseract/wiki"
    )


def _run_tesseract(image_path: str, output_type: str = "stdout") -> str:
    """Run tesseract CLI, return its stdout.

    output_type: "stdout" (κείμενο) ή "tsv" (TSV — μπαίνει ως configfile ΤΕΛΕΥΤΑΙΟ
    arg, αλλιώς ο tesseract το μπερδεύει με options και αποτυγχάνει).
    """
    cmd = [_find_tesseract(), image_path, "stdout", "-l", "ell+eng", "--psm", "6"]
    if output_type == "tsv":
        cmd.append("tsv")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_OCR_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Tesseract failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _run_ocr_sync(image_path: str) -> str:
    """Synchronous OCR using Tesseract with Greek + English language support."""
    return _run_tesseract(image_path, "stdout").strip()


def _parse_tsv_lines(tsv: str) -> list[dict]:
    """Parse tesseract TSV output into lines with average confidence."""
    rows = [line.split("\t") for line in tsv.splitlines()]
    if len(rows) < 2:
        return []
    header = rows[0]
    try:
        idx = {name: i for i, name in enumerate(header)}
    except Exception:
        return []
    if "text" not in idx:
        return []

    grouped: dict = {}
    for r in rows[1:]:
        if len(r) <= idx["text"]:
            continue
        text = r[idx["text"]].strip()
        if not text:
            continue
        key = (
            r[idx.get("block_num", 0)] if idx.get("block_num", 0) < len(r) else "0",
            r[idx.get("par_num", 0)] if idx.get("par_num", 0) < len(r) else "0",
            r[idx.get("line_num", 0)] if idx.get("line_num", 0) < len(r) else "0",
        )
        conf_raw = r[idx["conf"]] if idx["conf"] < len(r) else ""
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = 0.0
        if key not in grouped:
            grouped[key] = {"words": [], "confs": []}
        grouped[key]["words"].append(text)
        grouped[key]["confs"].append(conf)

    lines = []
    for key in sorted(grouped.keys()):
        g = grouped[key]
        avg = (sum(g["confs"]) / len(g["confs"])) / 100.0 if g["confs"] else 0.0
        lines.append({
            "text": " ".join(g["words"]),
            "confidence": round(avg, 3),
        })
    return lines


def _run_ocr_detailed_sync(image_path: str) -> dict:
    """Single-pass OCR returning full text + per-line confidence (via TSV)."""
    tsv = _run_tesseract(image_path, "tsv")
    lines = _parse_tsv_lines(tsv)
    full_text = "\n".join(l["text"] for l in lines).strip()
    avg_conf = round(sum(l["confidence"] for l in lines) / len(lines), 3) if lines else None
    return {"lines": lines, "full_text": full_text, "confidence": avg_conf}


# ── Async wrappers ──────────────────────────────────────────────────────────

async def run_ocr(image_path: str) -> str:
    """Run OCR on an image file. Returns extracted text."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_ocr_sync, image_path)


async def run_ocr_detailed(image_path: str) -> dict:
    """Run OCR (single pass) and return {lines, full_text, confidence}."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run_ocr_detailed_sync, image_path)


# ── Queue-based async OCR (for background processing) ────────────────────────

class OCRJob:
    """Represents a queued OCR job."""
    def __init__(self, job_id: str, image_path: str):
        self.job_id = job_id
        self.image_path = image_path
        self.status = "PENDING"  # PENDING → PROCESSING → DONE / FAILED
        self.result_text: Optional[str] = None
        self.confidence: Optional[float] = None
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "text": self.result_text,
            "confidence": self.confidence,
            "error": self.error,
        }


# In-memory job store (replace with Redis or DB for production)
_jobs: dict[str, OCRJob] = {}


async def submit_ocr_job(job_id: str, image_path: str) -> OCRJob:
    """Submit an OCR job to the background queue."""
    job = OCRJob(job_id=job_id, image_path=image_path)
    _jobs[job_id] = job
    await _ocr_queue.put(job)
    return job


async def get_ocr_job(job_id: str) -> Optional[OCRJob]:
    """Get the current status of an OCR job."""
    return _jobs.get(job_id)


async def _ocr_worker():
    """Background worker: consumes jobs from the queue and runs OCR (single pass)."""
    logger.info("OCR worker started")
    while True:
        try:
            job = await _ocr_queue.get()
            job.status = "PROCESSING"
            logger.info(f"OCR job {job.job_id}: processing {job.image_path}")

            try:
                result = await run_ocr_detailed(job.image_path)
                job.result_text = result["full_text"]
                job.confidence = result.get("confidence")
                job.status = "DONE"
                logger.info(f"OCR job {job.job_id}: done ({len(job.result_text or '')} chars)")
            except Exception as e:
                job.status = "FAILED"
                job.error = str(e)
                logger.error(f"OCR job {job.job_id}: failed - {e}")

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("OCR worker error")


_worker_task = None


def start_ocr_worker():
    """Start the background OCR worker."""
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.ensure_future(_ocr_worker())


def stop_ocr_worker():
    """Stop the background OCR worker."""
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
    _executor.shutdown(wait=False)
