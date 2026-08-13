# server.py
# THỦY LỢI AI - FastAPI + Gemini Interactions API + File Search
# Version 20.0

import os
import re
import time
import asyncio
import logging
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# ============================================================
# GOOGLE GENAI
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

APP_VERSION = "20.0"

# Mặc định chỉ 1 truy vấn Gemini tại một thời điểm để ổn định
# với quota thấp. Có thể tăng lên 2 khi đã có quota phù hợp.
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "1")))

REQUEST_TIMEOUT = max(
    20,
    int(os.getenv("REQUEST_TIMEOUT", "60")),
)

# Chỉ retry lỗi mạng/5xx. KHÔNG retry 429 quota.
MAX_RETRIES = max(
    1,
    int(os.getenv("MAX_RETRIES", "2")),
)

# Cache câu hỏi thành công trong RAM.
CACHE_TTL = max(
    60,
    int(os.getenv("CACHE_TTL", "1800")),
)
CACHE_SIZE = max(
    20,
    int(os.getenv("CACHE_SIZE", "200")),
)

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

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# ============================================================
# METRICS / CACHE
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

_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = asyncio.Lock()


def normalize_question(question: str) -> str:
    return " ".join((question or "").strip().lower().split())


async def cache_get(question: str):
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

        metrics["cache_hits"] += 1

        result = dict(value)
        result["cached"] = True
        return result


async def cache_put(question: str, value: dict[str, Any]):
    key = normalize_question(question)

    if not key:
        return

    async with _cache_lock:
        _cache[key] = (
            time.time(),
            dict(value),
        )

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
    logger.info("KHOI DONG THUY LOI AI %s", APP_VERSION)
    logger.info("=" * 60)

    logger.info(
        "Google GenAI: %s",
        "OK" if genai else f"LOI: {GOOGLE_GENAI_IMPORT_ERROR}",
    )

    logger.info(
        "GEMINI_API_KEY: %s",
        "DA CAU HINH" if GEMINI_API_KEY else "THIEU",
    )

    logger.info(
        "GEMINI_FILE_SEARCH_STORE: %s",
        GEMINI_FILE_SEARCH_STORE or "THIEU",
    )

    logger.info(
        "GEMINI_MODEL: %s",
        GEMINI_MODEL,
    )

    logger.info(
        "MAX_CONCURRENT: %s",
        MAX_CONCURRENT,
    )

    logger.info(
        "REQUEST_TIMEOUT: %ss",
        REQUEST_TIMEOUT,
    )

    if genai and GEMINI_API_KEY:
        try:
            gemini_client = genai.Client(
                api_key=GEMINI_API_KEY
            )

            logger.info(
                "Gemini client: OK"
            )

        except Exception as exc:
            gemini_client = None

            logger.exception(
                "Gemini client INIT ERROR: %r",
                exc,
            )
    else:
        gemini_client = None

    yield

    try:
        if gemini_client is not None:
            await gemini_client.aio.aclose()
    except Exception:
        pass

    gemini_client = None

    logger.info(
        "THUY LOI AI DA DUNG"
    )


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
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
# MODELS
# ============================================================

class Question(BaseModel):
    question: str = Field(
        default="",
        max_length=20000,
    )


# ============================================================
# HELPERS
# ============================================================

def clean_store_name(value: str) -> str:
    return (value or "").strip()


def error_response(
    message: str,
    status_code: int = 200,
    status: str = "error",
    extra: dict[str, Any] | None = None,
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

    return JSONResponse(
        status_code=status_code,
        content=payload,
    )


def is_429(error: Exception) -> bool:
    text = str(error).lower()

    return (
        "429" in text
        or "too_many_requests" in text
        or "resource_exhausted" in text
        or "quota exceeded" in text
        or "rate limit" in text
    )


def is_retryable_non429(error: Exception) -> bool:
    text = str(error).lower()

    if is_429(error):
        return False

    permanent = (
        "400",
        "401",
        "403",
        "404",
        "invalid argument",
        "bad request",
        "unauthenticated",
        "permission denied",
        "permission_denied",
        "not found",
        "api key",
    )

    if any(x in text for x in permanent):
        return False

    retryable = (
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "deadline",
        "temporarily",
        "internal",
        "connection",
        "reset",
        "unavailable",
    )

    return any(
        x in text
        for x in retryable
    )


def extract_retry_after(
    error: Exception,
) -> float | None:

    text = str(error)

    patterns = [
        r"retry in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry after\s+([0-9]+(?:\.[0-9]+)?)s",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            try:
                return max(
                    1.0,
                    float(match.group(1)),
                )
            except ValueError:
                pass

    return None


def mime_type_for(
    filename: str,
) -> str | None:

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
        ".docx": (
            "application/"
            "vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        ".xls": "application/vnd.ms-excel",
        ".xlsx": (
            "application/"
            "vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": (
            "application/"
            "vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
    }

    return mapping.get(ext)


def extract_answer_and_sources(result):

    answer = (
        getattr(
            result,
            "output_text",
            None,
        )
        or ""
    ).strip()

    sources: list[dict[str, Any]] = []

    for step in (
        getattr(
            result,
            "steps",
            None,
        )
        or []
    ):

        if getattr(
            step,
            "type",
            None,
        ) != "model_output":
            continue

        for block in (
            getattr(
                step,
                "content",
                None,
            )
            or []
        ):

            if getattr(
                block,
                "type",
                None,
            ) != "text":
                continue

            text = (
                getattr(
                    block,
                    "text",
                    None,
                )
                or ""
            ).strip()

            if not answer and text:
                answer = text

            for annotation in (
                getattr(
                    block,
                    "annotations",
                    None,
                )
                or []
            ):

                if getattr(
                    annotation,
                    "type",
                    None,
                ) != "file_citation":
                    continue

                item: dict[str, Any] = {}

                file_name = getattr(
                    annotation,
                    "file_name",
                    None,
                )

                source = getattr(
                    annotation,
                    "source",
                    None,
                )

                page_number = getattr(
                    annotation,
                    "page_number",
                    None,
                )

                if file_name:
                    item["file_name"] = str(
                        file_name
                    )

                if source:
                    item["source"] = str(
                        source
                    )

                if page_number:
                    item["page_number"] = (
                        page_number
                    )

                if (
                    item
                    and item not in sources
                ):
                    sources.append(item)

    if not answer:
        raise RuntimeError(
            "Gemini không trả về nội dung văn bản."
        )

    return answer, sources


# ============================================================
# GEMINI FILE SEARCH
# ============================================================

async def call_gemini(
    question: str,
):

    if gemini_client is None:
        raise RuntimeError(
            "Gemini client chưa được khởi tạo."
        )

    store = clean_store_name(
        GEMINI_FILE_SEARCH_STORE
    )

    if not store:
        raise RuntimeError(
            "GEMINI_FILE_SEARCH_STORE "
            "chưa được cấu hình."
        )

    # Cú pháp chính thức hiện tại:
    # Interactions API + File Search.
    return await (
        gemini_client
        .aio
        .interactions
        .create(
            model=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
            input=question,
            tools=[
                {
                    "type": "file_search",
                    "file_search_store_names": [
                        store
                    ],
                }
            ],
        )
    )


async def ask_gemini(
    question: str,
):

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            async with request_semaphore:

                result = await asyncio.wait_for(
                    call_gemini(question),
                    timeout=REQUEST_TIMEOUT,
                )

            return extract_answer_and_sources(
                result
            )

        except Exception as exc:

            last_error = exc

            # KHÔNG retry 429.
            if is_429(exc):

                metrics[
                    "requests_429"
                ] += 1

                logger.error(
                    "GEMINI 429 - KHONG RETRY: %s",
                    exc,
                )

                raise

            retryable = (
                is_retryable_non429(
                    exc
                )
            )

            logger.warning(
                "Gemini attempt %s/%s | "
                "retryable=%s | %r",
                attempt,
                MAX_RETRIES,
                retryable,
                exc,
            )

            if (
                not retryable
                or attempt >= MAX_RETRIES
            ):
                break

            delay = 2.0 * attempt

            logger.info(
                "Thu lai sau %.1f giay...",
                delay,
            )

            await asyncio.sleep(
                delay
            )

    raise (
        last_error
        or RuntimeError(
            "Gemini không thể xử lý câu hỏi."
        )
    )


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def home():

    if INDEX_FILE.exists():

        return FileResponse(
            INDEX_FILE,
            media_type="text/html",
        )

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
        "google_genai_imported": (
            genai is not None
        ),
        "google_genai_import_error": (
            GOOGLE_GENAI_IMPORT_ERROR
        ),
        "gemini_configured": bool(
            GEMINI_API_KEY
        ),
        "gemini_client_ready": (
            gemini_client is not None
        ),
        "file_search_configured": bool(
            GEMINI_FILE_SEARCH_STORE
        ),
        "file_search_store": (
            GEMINI_FILE_SEARCH_STORE
        ),
        "model": GEMINI_MODEL,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
    }


@app.get("/diagnostics")
async def diagnostics():

    return {
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "google_genai_imported": (
            genai is not None
        ),
        "google_genai_import_error": (
            GOOGLE_GENAI_IMPORT_ERROR
        ),
        "gemini_configured": bool(
            GEMINI_API_KEY
        ),
        "gemini_client_ready": (
            gemini_client is not None
        ),
        "file_search_configured": bool(
            GEMINI_FILE_SEARCH_STORE
        ),
        "file_search_store": (
            GEMINI_FILE_SEARCH_STORE
        ),
        "model": GEMINI_MODEL,
        "configuration": {
            "max_concurrent": MAX_CONCURRENT,
            "request_timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
            "cache_ttl": CACHE_TTL,
            "cache_size": CACHE_SIZE,
        },
        "metrics": dict(metrics),
    }


@app.get("/metrics")
async def get_metrics():
    return dict(metrics)


@app.get("/api")
async def api_info():

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "engine": (
            "Gemini Interactions API + "
            "File Search"
        ),
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


# ============================================================
# STORES
# ============================================================

@app.get("/stores")
async def stores():

    if gemini_client is None:

        return {
            "success": False,
            "configured_store": (
                GEMINI_FILE_SEARCH_STORE
            ),
            "stores": [],
            "error": (
                "Gemini client chưa sẵn sàng."
            ),
        }

    try:

        items = []

        async for store in (
            gemini_client
            .aio
            .file_search_stores
            .list()
        ):

            items.append(
                {
                    "name": getattr(
                        store,
                        "name",
                        None,
                    ),
                    "display_name": getattr(
                        store,
                        "display_name",
                        None,
                    ),
                    "active": getattr(
                        store,
                        "active",
                        None,
                    ),
                }
            )

        return {
            "success": True,
            "configured_store": (
                GEMINI_FILE_SEARCH_STORE
            ),
            "count": len(items),
            "stores": items,
        }

    except Exception as exc:

        logger.exception(
            "/stores ERROR"
        )

        return {
            "success": False,
            "configured_store": (
                GEMINI_FILE_SEARCH_STORE
            ),
            "error": str(exc),
        }


# ============================================================
# DOCUMENTS
# ============================================================

@app.get("/documents")
async def documents():

    if gemini_client is None:

        return {
            "success": False,
            "store": (
                GEMINI_FILE_SEARCH_STORE
            ),
            "count": 0,
            "documents": [],
            "error": (
                "Gemini client chưa sẵn sàng."
            ),
        }

    store = clean_store_name(
        GEMINI_FILE_SEARCH_STORE
    )

    if not store:

        return {
            "success": False,
            "store": "",
            "count": 0,
            "documents": [],
            "error": (
                "Chưa cấu hình "
                "GEMINI_FILE_SEARCH_STORE."
            ),
        }

    try:

        docs = []

        async for doc in (
            gemini_client
            .aio
            .file_search_stores
            .documents
            .list(
                parent=store
            )
        ):

            docs.append(
                {
                    "name": str(
                        getattr(
                            doc,
                            "name",
                            "",
                        )
                        or ""
                    ),
                    "display_name": str(
                        getattr(
                            doc,
                            "display_name",
                            "",
                        )
                        or ""
                    ),
                    "state": str(
                        getattr(
                            doc,
                            "state",
                            "",
                        )
                        or ""
                    ),
                }
            )

        return {
            "success": True,
            "store": store,
            "count": len(docs),
            "documents": docs,
        }

    except Exception as exc:

        logger.exception(
            "/documents ERROR"
        )

        return {
            "success": False,
            "store": store,
            "count": 0,
            "documents": [],
            "error": str(exc),
        }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/upload")
async def upload(
    file: UploadFile = File(...)
):

    metrics["uploads_total"] += 1

    if gemini_client is None:

        return error_response(
            "Gemini client chưa sẵn sàng.",
            status_code=503,
        )

    store = clean_store_name(
        GEMINI_FILE_SEARCH_STORE
    )

    if not store:

        return error_response(
            "Render chưa có "
            "GEMINI_FILE_SEARCH_STORE.",
            status_code=503,
        )

    filename = Path(
        file.filename
        or "document"
    ).name

    mime_type = (
        mime_type_for(filename)
        or file.content_type
        or "application/octet-stream"
    )

    temp_path = None

    try:

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=Path(
                filename
            ).suffix,
        ) as tmp:

            temp_path = tmp.name

            while True:

                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                tmp.write(chunk)

        file_size = os.path.getsize(
            temp_path
        )

        logger.info(
            "UPLOAD | %s | %.2f MB | %s",
            filename,
            file_size / 1024 / 1024,
            mime_type,
        )

        # Dùng Files API trước rồi import vào
        # File Search Store. Cách này hỗ trợ Office/
        # Excel và tránh một số lỗi MIME trực tiếp.
        uploaded = await asyncio.wait_for(
            gemini_client
            .aio
            .files
            .upload(
                file=temp_path,
                config={
                    "display_name": filename,
                    "mime_type": mime_type,
                },
            ),
            timeout=REQUEST_TIMEOUT,
        )

        logger.info(
            "FILE UPLOAD OK | %s",
            getattr(
                uploaded,
                "name",
                None,
            ),
        )

        operation = await asyncio.wait_for(
            gemini_client
            .aio
            .file_search_stores
            .import_file(
                file_search_store_name=store,
                file_name=uploaded.name,
            ),
            timeout=REQUEST_TIMEOUT,
        )

        while not getattr(
            operation,
            "done",
            False,
        ):

            await asyncio.sleep(3)

            operation = await asyncio.wait_for(
                gemini_client
                .aio
                .operations
                .get(
                    operation
                ),
                timeout=REQUEST_TIMEOUT,
            )

        metrics[
            "uploads_success"
        ] += 1

        return {
            "success": True,
            "status": "ok",
            "message": (
                "Đã đưa tài liệu vào "
                "kho THỦY LỢI AI."
            ),
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": file_size,
            "store": store,
            "file": getattr(
                uploaded,
                "name",
                None,
            ),
            "operation": getattr(
                operation,
                "name",
                None,
            ),
        }

    except Exception as exc:

        logger.exception(
            "UPLOAD ERROR | %s | %r",
            filename,
            exc,
        )

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

        if temp_path:

            try:
                os.remove(
                    temp_path
                )
            except OSError:
                pass


# ============================================================
# ASK
# ============================================================

async def process_question(
    data: Question
):

    question = (
        data.question or ""
    ).strip()

    metrics[
        "requests_total"
    ] += 1

    logger.info(
        "CÂU HỎI: %s",
        question[:300],
    )

    if not question:

        return error_response(
            "Vui lòng nhập câu hỏi."
        )

    if not GEMINI_API_KEY:

        return error_response(
            "Render chưa có GEMINI_API_KEY.",
            status_code=503,
        )

    if gemini_client is None:

        return error_response(
            "Gemini client chưa sẵn sàng. "
            "Kiểm tra google-genai và "
            "GEMINI_API_KEY.",
            status_code=503,
        )

    if not GEMINI_FILE_SEARCH_STORE:

        return error_response(
            "Render chưa có "
            "GEMINI_FILE_SEARCH_STORE.",
            status_code=503,
        )

    # Không gọi Gemini nếu câu hỏi giống hệt
    # một câu đã trả lời thành công trong cache.
    cached = await cache_get(
        question
    )

    if cached:
        return cached

    try:

        answer, sources = (
            await ask_gemini(
                question
            )
        )

        payload = {
            "status": "ok",
            "ok": True,
            "answer": answer,
            "reply": answer,
            "message": answer,
            "engine": (
                "Gemini File Search"
            ),
            "version": APP_VERSION,
            "model": GEMINI_MODEL,
            "cached": False,
        }

        if sources:
            payload[
                "sources"
            ] = sources

        await cache_put(
            question,
            payload,
        )

        metrics[
            "requests_success"
        ] += 1

        logger.info(
            "ASK SUCCESS | sources=%s",
            len(sources),
        )

        return payload

    except Exception as exc:

        logger.exception(
            "ASK ERROR: %r",
            exc,
        )

        # ----------------------------------------------------
        # 429 QUOTA
        # ----------------------------------------------------

        if is_429(exc):

            retry_after = (
                extract_retry_after(
                    exc
                )
            )

            metrics[
                "requests_429"
            ] += 1

            message = (
                "⚠️ Gemini đang đạt giới hạn "
                "truy cập của dự án. "
                "Kho tài liệu THỦY LỢI AI "
                "vẫn còn nguyên và không bị mất. "
                "Vui lòng thử lại sau ít phút."
            )

            if retry_after:

                message += (
                    " Gemini yêu cầu chờ khoảng "
                    f"{retry_after:.0f} giây."
                )

            # Giữ HTTP 200 để frontend hiện
            # đúng thông báo thay vì báo lỗi mạng.
            return error_response(
                message,
                status_code=200,
                status="rate_limited",
                extra={
                    "retryable": True,
                    "retry_after": (
                        retry_after
                    ),
                },
            )

        metrics[
            "requests_error"
        ] += 1

        text = str(exc)

        if (
            "403" in text
            or "permission" in text.lower()
        ):

            message = (
                "THỦY LỢI AI không có quyền "
                "truy cập kho Gemini File Search. "
                "Hãy kiểm tra GEMINI_API_KEY "
                "và GEMINI_FILE_SEARCH_STORE."
            )

        elif (
            "404" in text
            or "not found" in text.lower()
        ):

            message = (
                "Không tìm thấy kho Gemini "
                "File Search. Hãy kiểm tra "
                "GEMINI_FILE_SEARCH_STORE."
            )

        elif (
            "timeout" in text.lower()
            or "deadline" in text.lower()
        ):

            message = (
                "Gemini phản hồi quá lâu. "
                "Vui lòng thử lại sau ít giây."
            )

        else:

            message = (
                "THỦY LỢI AI tạm thời chưa lấy "
                "được câu trả lời từ kho Gemini "
                "File Search. Vui lòng thử lại."
            )

        return error_response(
            message,
            status_code=200,
            status="gemini_error",
        )


@app.post("/ask")
async def ask(
    data: Question
):
    return await process_question(
        data
    )


# Tương thích với frontend cũ
# đang gọi /api/ask.
@app.post("/api/ask")
async def api_ask(
    data: Question
):
    return await process_question(
        data
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
    )
