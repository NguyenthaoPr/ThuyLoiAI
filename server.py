# server.py
# THỦY LỢI AI - FastAPI + Gemini Interactions API + File Search
# Version 20.2 (Production & Performance Optimized)

import os
import re
import time
import asyncio
import logging
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from collections import OrderedDict
from typing import Any, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# ============================================================
# GOOGLE GENAI IMPORT
# ============================================================

try:
    from google import genai
    GOOGLE_GENAI_IMPORT_ERROR = None
except Exception as exc:
    genai = None
    GOOGLE_GENAI_IMPORT_ERROR = repr(exc)

# ============================================================
# APP CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

APP_VERSION = "20.4-stable"

# Concurrency control & Limits
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "1")))
REQUEST_TIMEOUT = max(20, int(os.getenv("REQUEST_TIMEOUT", "60")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "1")))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))  # Giới hạn 50MB

# RAM Cache settings
CACHE_TTL = max(60, int(os.getenv("CACHE_TTL", "1800")))
CACHE_SIZE = max(20, int(os.getenv("CACHE_SIZE", "200")))

# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI — trợ lý AI chuyên ngành Thủy lợi của
Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

NGUỒN DỮ LIỆU:
- Ưu tiên tuyệt đối thông tin được truy xuất từ Gemini File Search Store.
- Không tự bịa số liệu, tên công trình, thông số kỹ thuật, quy trình,
  quy định, số hiệu văn bản, ngày tháng hoặc kết luận.
- Nếu tài liệu trong kho không đủ căn cứ, phải nói rõ:
  "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."

QUY TẮC TRẢ LỜI:
1. Trả lời trực tiếp đúng câu hỏi.
2. Nếu câu hỏi liên quan một công trình, ưu tiên tài liệu đúng công trình.
3. Nếu cần đối chiếu nhiều tài liệu, tổng hợp thông tin từ các tài liệu
   được File Search truy xuất.
4. Giữ nguyên số liệu và đơn vị trong hồ sơ.
5. Với câu hỏi về vị trí, thông số, số lượng máy, công suất, diện tích,
   cao trình, lưu lượng... phải ưu tiên số liệu trong hồ sơ.
6. Không biến suy đoán thành kết luận chính thức.
7. Trả lời bằng tiếng Việt, rõ ràng, dễ đọc trên điện thoại.
8. Nếu có nguồn tài liệu, nêu tên tài liệu ở cuối câu trả lời.
"""

logger = logging.getLogger("thuyloiai")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

gemini_client: Any = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# ============================================================
# METRICS & CACHE MANAGEMENT
# ============================================================

metrics = {
    "requests_total": 0,
    "requests_success": 0,
    "requests_429": 0,
    "requests_error": 0,
    "cache_hits": 0,
    "uploads_total": 0,
    "uploads_success": 0,
}
metrics_lock = asyncio.Lock()

_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = asyncio.Lock()

_quota_cooldown_until = 0.0
_quota_lock = asyncio.Lock()

async def get_quota_cooldown() -> float:
    async with _quota_lock:
        return max(0.0, _quota_cooldown_until - time.time())

async def set_quota_cooldown(seconds: Optional[float]):
    global _quota_cooldown_until
    if not seconds:
        return
    async with _quota_lock:
        _quota_cooldown_until = max(
            _quota_cooldown_until,
            time.time() + max(1.0, seconds),
        )


async def increment_metric(key: str, amount: int = 1):
    async with metrics_lock:
        if key in metrics:
            metrics[key] += amount


def normalize_question(question: str) -> str:
    return " ".join((question or "").strip().lower().split())


async def cache_get(question: str) -> Optional[dict[str, Any]]:
    key = normalize_question(question)
    if not key:
        return None

    async with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None

        created_at, value = item
        if time.time() - created_at > CACHE_TTL:
            _cache.pop(key, None)
            return None

        _cache.move_to_end(key)
        await increment_metric("cache_hits")

        result = dict(value)
        result["cached"] = True
        return result


async def cache_put(question: str, value: dict[str, Any]):
    key = normalize_question(question)
    if not key:
        return

    async with _cache_lock:
        now = time.time()
        expired_keys = [k for k, (t, _) in _cache.items() if now - t > CACHE_TTL]
        for k in expired_keys:
            _cache.pop(k, None)

        _cache[key] = (now, dict(value))
        _cache.move_to_end(key)

        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    logger.info("=" * 60)
    logger.info("KHỞI ĐỘNG THỦY LỢI AI v%s", APP_VERSION)
    logger.info("=" * 60)
    logger.info("Google GenAI: %s", "OK" if genai else f"LỖI: {GOOGLE_GENAI_IMPORT_ERROR}")
    logger.info("GEMINI_API_KEY: %s", "ĐÃ CẤU HÌNH" if GEMINI_API_KEY else "THIẾU")
    logger.info("GEMINI_FILE_SEARCH_STORE: %s", GEMINI_FILE_SEARCH_STORE or "THIẾU")
    logger.info("GEMINI_MODEL: %s", GEMINI_MODEL)
    logger.info("MAX_CONCURRENT: %s | TIMEOUT: %ss", MAX_CONCURRENT, REQUEST_TIMEOUT)

    if genai and GEMINI_API_KEY:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info("Gemini client: ĐÃ SẴN SÀNG")
        except Exception as exc:
            gemini_client = None
            logger.exception("Lỗi khởi tạo Gemini client: %r", exc)
    else:
        gemini_client = None

    yield

    try:
        if gemini_client is not None and hasattr(gemini_client, "aio"):
            await gemini_client.aio.aclose()
    except Exception as exc:
        logger.warning("Error closing Gemini Client: %r", exc)

    gemini_client = None
    logger.info("THỦY LỢI AI ĐÃ DỪNG HOẠT ĐỘNG")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi Vu Gia - Thu Bồn",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELS & HELPERS
# ============================================================

class Question(BaseModel):
    question: str = Field(default="", max_length=20000)


def clean_store_name(value: str) -> str:
    return (value or "").strip()


def error_response(
    message: str,
    status_code: int = 200,
    status: str = "error",
    extra: Optional[dict[str, Any]] = None,
):
    payload = {
        "status": status,
        "ok": False,
        "answer": message,
        "reply": message,
        "message": message,
        "engine": "Gemini File Search",
        "version": APP_VERSION,
    }
    if extra:
        payload.update(extra)

    return JSONResponse(status_code=status_code, content=payload)


def is_429(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        x in text
        for x in [
            "429",
            "too_many_requests",
            "resource_exhausted",
            "quota exceeded",
            "rate limit",
        ]
    )


def is_retryable_non429(error: Exception) -> bool:
    text = str(error).lower()
    if is_429(error):
        return False

    permanent = (
        "400", "401", "403", "404", "invalid argument",
        "bad request", "unauthenticated", "permission denied",
        "permission_denied", "not found", "api key"
    )
    if any(x in text for x in permanent):
        return False

    retryable = (
        "500", "502", "503", "504", "timeout", "deadline",
        "temporarily", "internal", "connection", "reset", "unavailable"
    )
    return any(x in text for x in retryable)


def extract_retry_after(error: Exception) -> Optional[float]:
    text = str(error)
    patterns = [
        r"retry in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry after\s+([0-9]+(?:\.[0-9]+)?)s",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return max(1.0, float(match.group(1)))
            except ValueError:
                pass
    return None


def mime_type_for(filename: str) -> Optional[str]:
    ext = Path(filename).suffix.lower()
    mapping = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".html": "text/html",
        ".htm": "text/html",
        ".json": "application/json",
        ".xml": "application/xml",
        ".rtf": "text/rtf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return mapping.get(ext)


def extract_answer_and_sources(result: Any) -> tuple[str, list[dict[str, Any]]]:
    answer = (getattr(result, "output_text", None) or "").strip()
    sources: list[dict[str, Any]] = []

    steps = getattr(result, "steps", None) or []
    for step in steps:
        if getattr(step, "type", None) != "model_output":
            continue

        contents = getattr(step, "content", None) or []
        for block in contents:
            if getattr(block, "type", None) != "text":
                continue

            text = (getattr(block, "text", None) or "").strip()
            if not answer and text:
                answer = text

            annotations = getattr(block, "annotations", None) or []
            for annotation in annotations:
                if getattr(annotation, "type", None) != "file_citation":
                    continue

                item: dict[str, Any] = {}
                file_name = getattr(annotation, "file_name", None)
                source = getattr(annotation, "source", None)
                page_number = getattr(annotation, "page_number", None)

                if file_name:
                    item["file_name"] = str(file_name)
                if source:
                    item["source"] = str(source)
                if page_number:
                    item["page_number"] = page_number

                if item and item not in sources:
                    sources.append(item)

    if not answer:
        raise RuntimeError("Gemini không trả về nội dung văn bản.")

    return answer, sources


# ============================================================
# GEMINI CALLS
# ============================================================

async def call_gemini(question: str):
    if gemini_client is None:
        raise RuntimeError("Gemini client chưa được khởi tạo.")

    store = clean_store_name(GEMINI_FILE_SEARCH_STORE)
    if not store:
        raise RuntimeError("GEMINI_FILE_SEARCH_STORE chưa được cấu hình.")

    cooldown = await get_quota_cooldown()
    if cooldown > 0:
        exc = RuntimeError(
            f"Gemini đang bị giới hạn tạm thời. Vui lòng thử lại sau {cooldown:.0f} giây."
        )
        setattr(exc, "retry_after", cooldown)
        raise exc

    return await gemini_client.aio.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[{
            "type": "file_search",
            "file_search_store_names": [store],
        }],
    )


async def ask_gemini(question: str) -> tuple[str, list[dict[str, Any]]]:
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with request_semaphore:
                result = await asyncio.wait_for(
                    call_gemini(question),
                    timeout=REQUEST_TIMEOUT,
                )
            return extract_answer_and_sources(result)

        except Exception as exc:
            last_error = exc

            if is_429(exc):
                retry_after = extract_retry_after(exc)
                await set_quota_cooldown(retry_after or 30.0)
                await increment_metric("requests_429")
                logger.error(
                    "GEMINI 429 - KHÔNG RETRY để tránh nhân quota: %s", exc
                )
                raise

            retryable = is_retryable_non429(exc)
            logger.warning(
                "Gemini thử lần %s/%s | retryable=%s | Lỗi: %r",
                attempt, MAX_RETRIES, retryable, exc
            )

            if not retryable or attempt >= MAX_RETRIES:
                break

            await asyncio.sleep(min(3.0 * attempt, 6.0))

    raise last_error or RuntimeError("Gemini không thể xử lý câu hỏi.")


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "engine": "Gemini File Search",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "google_genai_imported": genai is not None,
        "google_genai_import_error": GOOGLE_GENAI_IMPORT_ERROR,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_client_ready": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_store": GEMINI_FILE_SEARCH_STORE,
        "model": GEMINI_MODEL,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
    }


@app.get("/diagnostics")
async def diagnostics():
    async with metrics_lock:
        current_metrics = dict(metrics)

    return {
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "google_genai_imported": genai is not None,
        "google_genai_import_error": GOOGLE_GENAI_IMPORT_ERROR,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_client_ready": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_store": GEMINI_FILE_SEARCH_STORE,
        "model": GEMINI_MODEL,
        "configuration": {
            "max_concurrent": MAX_CONCURRENT,
            "request_timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
            "cache_ttl": CACHE_TTL,
            "cache_size": CACHE_SIZE,
            "max_upload_size_mb": MAX_UPLOAD_SIZE_MB,
        },
        "metrics": current_metrics,
    }


@app.get("/metrics")
async def get_metrics():
    async with metrics_lock:
        return dict(metrics)


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "engine": "Gemini Interactions API + File Search",
        "endpoints": {
            "home": "/",
            "health": "/health",
            "diagnostics": "/diagnostics",
            "metrics": "/metrics",
            "stores": "/stores",
            "documents": "/documents",
            "upload": "/upload",
            "ask": "/ask",
            "api_ask": "/api/ask",
            "docs": "/docs",
        },
    }


@app.get("/stores")
async def stores():
    if gemini_client is None:
        return {
            "success": False,
            "configured_store": GEMINI_FILE_SEARCH_STORE,
            "stores": [],
            "error": "Gemini client chưa sẵn sàng.",
        }

    try:
        items = []
        pager = await gemini_client.aio.file_search_stores.list(
            config={"page_size": 20}
        )
        async for store in pager:
            items.append({
                "name": getattr(store, "name", None),
                "display_name": getattr(store, "display_name", None),
                "active": getattr(store, "active", None),
            })

        return {
            "success": True,
            "configured_store": GEMINI_FILE_SEARCH_STORE,
            "count": len(items),
            "stores": items,
        }
    except Exception as exc:
        logger.exception("Lỗi GET /stores")
        return {
            "success": False,
            "configured_store": GEMINI_FILE_SEARCH_STORE,
            "error": str(exc),
        }


@app.get("/documents")
async def documents():
    if gemini_client is None:
        return {
            "success": False,
            "store": GEMINI_FILE_SEARCH_STORE,
            "count": 0,
            "documents": [],
            "error": "Gemini client chưa sẵn sàng.",
        }

    store = clean_store_name(GEMINI_FILE_SEARCH_STORE)
    if not store:
        return {
            "success": False,
            "store": "",
            "count": 0,
            "documents": [],
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        docs = []
        pager = await gemini_client.aio.file_search_stores.documents.list(
            parent=store,
            config={"page_size": 20},
        )
        async for doc in pager:
            docs.append({
                "name": str(getattr(doc, "name", "") or ""),
                "display_name": str(getattr(doc, "display_name", "") or ""),
                "state": str(getattr(doc, "state", "") or ""),
            })

        return {
            "success": True,
            "store": store,
            "count": len(docs),
            "documents": docs,
        }
    except Exception as exc:
        logger.exception("Lỗi GET /documents")
        return {
            "success": False,
            "store": store,
            "count": 0,
            "documents": [],
            "error": str(exc),
        }


# ============================================================
# UPLOAD HANDLER
# ============================================================

def _write_temp_file(file_obj, temp_path: str, max_bytes: int):
    total_written = 0
    with open(temp_path, "wb") as tmp:
        while chunk := file_obj.file.read(1024 * 1024):
            total_written += len(chunk)
            if total_written > max_bytes:
                raise ValueError(f"Dung lượng file vượt quá giới hạn {max_bytes // (1024 * 1024)}MB.")
            tmp.write(chunk)


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    await increment_metric("uploads_total")

    if gemini_client is None:
        return error_response("Gemini client chưa sẵn sàng.", status_code=503)

    store = clean_store_name(GEMINI_FILE_SEARCH_STORE)
    if not store:
        return error_response("Render chưa có GEMINI_FILE_SEARCH_STORE.", status_code=503)

    filename = Path(file.filename or "document").name
    mime_type = mime_type_for(filename) or file.content_type or "application/octet-stream"
    temp_path = None
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    try:
        suffix = Path(filename).suffix
        temp_file_obj = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp_file_obj.name
        temp_file_obj.close()

        # Kiểm tra kích thước trong khi ghi
        await asyncio.to_thread(_write_temp_file, file, temp_path, max_bytes)
        file_size = os.path.getsize(temp_path)

        logger.info("UPLOAD | %s | %.2f MB | %s", filename, file_size / 1024 / 1024, mime_type)

        async with request_semaphore:
            uploaded = await asyncio.wait_for(
                gemini_client.aio.files.upload(
                    file=temp_path,
                    config={
                        "display_name": filename,
                        "mime_type": mime_type,
                    },
                ),
                timeout=REQUEST_TIMEOUT,
            )

            logger.info("FILE UPLOAD OK | %s", getattr(uploaded, "name", None))

            operation = await asyncio.wait_for(
                gemini_client.aio.file_search_stores.import_file(
                    file_search_store_name=store,
                    file_name=uploaded.name,
                ),
                timeout=REQUEST_TIMEOUT,
            )

            polling_attempts = 0
            max_polling_attempts = 40  # Tối đa 2 phút

            while not getattr(operation, "done", False):
                polling_attempts += 1
                if polling_attempts > max_polling_attempts:
                    raise TimeoutError("Quá thời gian xử lý đưa tài liệu vào Store.")

                await asyncio.sleep(3)
                operation = await asyncio.wait_for(
                    gemini_client.aio.operations.get(operation),
                    timeout=REQUEST_TIMEOUT,
                )

        await increment_metric("uploads_success")

        return {
            "success": True,
            "status": "ok",
            "message": "Đã đưa tài liệu vào kho THỦY LỢI AI.",
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": file_size,
            "store": store,
            "file": getattr(uploaded, "name", None),
            "operation": getattr(operation, "name", None),
        }

    except ValueError as val_err:
        logger.warning("UPLOAD REJECTED | %s | %s", filename, str(val_err))
        return error_response(str(val_err), status_code=400)

    except Exception as exc:
        logger.exception("UPLOAD ERROR | %s | %r", filename, exc)
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "status": "upload_error",
                "filename": filename,
                "error": str(exc),
            },
        )
    finally:
        try:
            await file.close()
        except Exception:
            pass

        if temp_path and os.path.exists(temp_path):
            try:
                await asyncio.to_thread(os.remove, temp_path)
            except OSError:
                pass


# ============================================================
# ASK HANDLER
# ============================================================

async def process_question(data: Question):
    question = (data.question or "").strip()
    await increment_metric("requests_total")

    logger.info("CÂU HỎI: %s", question[:300])

    if not question:
        return error_response("Vui lòng nhập câu hỏi.")

    if not GEMINI_API_KEY:
        return error_response("Render chưa có GEMINI_API_KEY.", status_code=503)

    if gemini_client is None:
        return error_response(
            "Gemini client chưa sẵn sàng. Kiểm tra google-genai và GEMINI_API_KEY.",
            status_code=503,
        )

    if not GEMINI_FILE_SEARCH_STORE:
        return error_response("Render chưa có GEMINI_FILE_SEARCH_STORE.", status_code=503)

    # Đọc từ RAM Cache
    cached = await cache_get(question)
    if cached:
        return cached

    try:
        answer, sources = await ask_gemini(question)

        payload = {
            "status": "ok",
            "ok": True,
            "answer": answer,
            "reply": answer,
            "message": answer,
            "engine": "Gemini File Search",
            "version": APP_VERSION,
            "model": GEMINI_MODEL,
            "cached": False,
        }

        if sources:
            payload["sources"] = sources

        await cache_put(question, payload)
        await increment_metric("requests_success")

        logger.info("ASK SUCCESS | Trích dẫn nguồn: %s", len(sources))
        return payload

    except Exception as exc:
        logger.exception("ASK ERROR: %r", exc)

        if is_429(exc):
            retry_after = extract_retry_after(exc)
            await increment_metric("requests_429")

            message = (
                "⚠️ Gemini đang đạt giới hạn truy cập của dự án. "
                "Kho tài liệu THỦY LỢI AI vẫn còn nguyên và không bị mất. "
                "Vui lòng thử lại sau ít phút."
            )
            if retry_after:
                message += f" Gemini yêu cầu chờ khoảng {retry_after:.0f} giây."

            return error_response(
                message,
                status_code=200,
                status="rate_limited",
                extra={"retryable": True, "retry_after": retry_after},
            )

        await increment_metric("requests_error")
        text = str(exc)

        if "403" in text or "permission" in text.lower():
            message = (
                "THỦY LỢI AI không có quyền truy cập kho Gemini File Search. "
                "Hãy kiểm tra GEMINI_API_KEY và GEMINI_FILE_SEARCH_STORE."
            )
        elif "404" in text or "not found" in text.lower():
            message = (
                "Không tìm thấy kho Gemini File Search. "
                "Hãy kiểm tra GEMINI_FILE_SEARCH_STORE."
            )
        elif "timeout" in text.lower() or "deadline" in text.lower():
            message = "Gemini phản hồi quá lâu. Vui lòng thử lại sau ít giây."
        else:
            message = (
                "THỦY LỢI AI tạm thời chưa lấy được câu trả lời từ kho "
                "Gemini File Search. Vui lòng thử lại."
            )

        return error_response(message, status_code=200, status="gemini_error")


@app.post("/ask")
async def ask(data: Question):
    return await process_question(data)


@app.post("/api/ask")
async def api_ask(data: Question):
    return await process_question(data)


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
