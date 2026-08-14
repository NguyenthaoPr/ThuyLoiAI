# server.py
# THỦY LỢI AI
# FastAPI + Gemini Interactions API + Gemini File Search
# Bản ổn định + tiết kiệm chi phí
#
# Các điểm đã sửa:
# 1. Sửa lỗi NameError: result is not defined.
# 2. Lấy câu trả lời trực tiếp từ interaction.output_text.
# 3. Có fallback đọc interaction.steps.
# 4. Không retry 429 để tránh nhân quota.
# 5. Có cooldown khi Gemini báo 429.
# 6. Cache câu hỏi giống nhau trong RAM để giảm số lần gọi Gemini.
# 7. Chỉ 1 request Gemini tại một thời điểm trên mỗi instance Render.
# 8. Có /documents để xem số tài liệu.
# 9. Có /upload và /documents/{name} để quản lý tài liệu.
# 10. Có /health, /diagnostics, /metrics để kiểm tra hệ thống.

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

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    from google import genai
    GOOGLE_GENAI_IMPORT_ERROR = None
except Exception as exc:
    genai = None
    GOOGLE_GENAI_IMPORT_ERROR = repr(exc)


# ============================================================
# CẤU HÌNH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE", ""
).strip()

# Có thể đổi trên Render Environment Variables.
# Gemini 3.6 Flash là model được tài liệu Google hiện dùng cho
# ví dụ File Search / Interactions API.
# Gemini 3.1 Flash-Lite: tiết kiệm chi phí và hỗ trợ File Search.
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.1-flash-lite"
).strip()

# File Search lấy một số chunk phù hợp nhất.
# 3 là điểm cân bằng tốt giữa độ chính xác và chi phí.
FILE_SEARCH_TOP_K = max(
    1,
    min(8, int(os.getenv("FILE_SEARCH_TOP_K", "3")))
)

# Giới hạn câu trả lời để tránh output quá dài.
MAX_OUTPUT_TOKENS = max(
    128,
    min(2048, int(os.getenv("MAX_OUTPUT_TOKENS", "700")))
)

# Gemini 3 hỗ trợ thinking_level.
# minimal giúp giảm chi phí và thời gian với câu hỏi tra cứu.
THINKING_LEVEL = os.getenv(
    "THINKING_LEVEL",
    "minimal"
).strip().lower()

if THINKING_LEVEL not in {"minimal", "low", "medium", "high"}:
    THINKING_LEVEL = "minimal"

APP_VERSION = "23.0-flash-lite-cost"

# Chỉ cho một request Gemini chạy cùng lúc trên một instance.
# Đây là chủ ý để hạn chế 429 khi nhiều người cùng truy cập.
MAX_CONCURRENT = max(
    1,
    int(os.getenv("MAX_CONCURRENT", "1"))
)

REQUEST_TIMEOUT = max(
    20,
    int(os.getenv("REQUEST_TIMEOUT", "60"))
)

# Không retry 429.
# Với lỗi mạng 5xx/timeout, mặc định chỉ thử thêm 1 lần.
MAX_RETRIES = max(
    1,
    min(2, int(os.getenv("MAX_RETRIES", "1")))
)

MAX_UPLOAD_SIZE_MB = max(
    1,
    int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
)

# Cache câu hỏi giống nhau.
# 1 giờ = nếu 20 người hỏi cùng câu thì chỉ gọi Gemini một lần.
CACHE_TTL = max(
    60,
    int(os.getenv("CACHE_TTL", "3600"))
)

CACHE_SIZE = max(
    20,
    int(os.getenv("CACHE_SIZE", "500"))
)


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI — trợ lý AI chuyên ngành Thủy lợi
của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

NGUỒN DỮ LIỆU BẮT BUỘC
- Ưu tiên tuyệt đối thông tin được truy xuất từ Gemini File Search Store.
- Chỉ sử dụng thông tin ngoài kho khi câu hỏi không phải câu hỏi
  tra cứu hồ sơ và việc đó thực sự cần thiết.
- Với câu hỏi về công trình, nhân sự, số liệu, thông số kỹ thuật,
  diện tích, vị trí, máy bơm, công suất, lưu lượng, quy trình,
  quy định, văn bản, ngày tháng hoặc số hiệu:
  PHẢI ưu tiên hồ sơ trong File Search Store.

QUY TẮC CHỐNG BỊA DỮ LIỆU
1. Không tự bịa số liệu.
2. Không tự bịa tên công trình.
3. Không tự bịa số hiệu văn bản.
4. Không tự bịa ngày tháng.
5. Không tự bịa thông số kỹ thuật.
6. Không biến suy đoán thành kết luận.
7. Nếu kho không có đủ căn cứ, trả lời:
   "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."
8. Nếu có nhiều hồ sơ liên quan, đối chiếu và tổng hợp.
9. Giữ nguyên số liệu và đơn vị theo hồ sơ.
10. Nếu tìm thấy nguồn, nêu tên tài liệu ở cuối câu trả lời.

CÁCH TRẢ LỜI
- Trả lời trực tiếp câu hỏi.
- Tiếng Việt.
- Rõ ràng, ngắn gọn nhưng đủ ý.
- Dễ đọc trên điện thoại.
- Với câu hỏi số liệu: ưu tiên đưa con số ngay đầu câu trả lời.
- Với câu hỏi nhiều ý: dùng gạch đầu dòng.
- Với quy trình: trình bày từng bước.
- Không nói "tôi nghĩ" nếu hồ sơ đã có căn cứ.
"""


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("thuyloiai")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# ============================================================
# GLOBAL STATE
# ============================================================

gemini_client: Any = None

request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

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

# key -> (created_time, payload)
_cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = asyncio.Lock()

_quota_cooldown_until = 0.0
_quota_lock = asyncio.Lock()


# ============================================================
# EXCEPTIONS
# ============================================================

class GeminiRateLimitError(Exception):
    """Gemini 429 / quota exceeded."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class GeminiTimeoutError(Exception):
    """Gemini request timeout."""


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_store_name(value: str) -> str:
    value = (value or "").strip()

    if not value:
        return ""

    if value.startswith("fileSearchStores/"):
        return value

    return f"fileSearchStores/{value}"


def normalize_question(question: str) -> str:
    return " ".join(
        (question or "").strip().lower().split()
    )


async def increment_metric(
    key: str,
    amount: int = 1
):
    async with metrics_lock:
        if key in metrics:
            metrics[key] += amount


# ============================================================
# CACHE
# ============================================================

async def cache_get(
    question: str
) -> Optional[dict[str, Any]]:

    key = normalize_question(question)

    if not key:
        return None

    async with _cache_lock:

        item = _cache.get(key)

        if not item:
            return None

        created_at, payload = item

        if time.time() - created_at > CACHE_TTL:
            _cache.pop(key, None)
            return None

        _cache.move_to_end(key)

        await increment_metric("cache_hits")

        result = dict(payload)
        result["cached"] = True

        return result


async def cache_put(
    question: str,
    payload: dict[str, Any]
):

    key = normalize_question(question)

    if not key:
        return

    async with _cache_lock:

        now = time.time()

        # Xóa cache hết hạn.
        expired = [
            k
            for k, (created, _) in _cache.items()
            if now - created > CACHE_TTL
        ]

        for k in expired:
            _cache.pop(k, None)

        _cache[key] = (
            now,
            dict(payload)
        )

        _cache.move_to_end(key)

        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


async def cache_clear():

    async with _cache_lock:
        _cache.clear()

    logger.info("CACHE: ĐÃ XÓA")


# ============================================================
# QUOTA COOLDOWN
# ============================================================

async def get_quota_cooldown() -> float:

    async with _quota_lock:

        return max(
            0.0,
            _quota_cooldown_until - time.time()
        )


async def set_quota_cooldown(
    seconds: Optional[float]
):

    global _quota_cooldown_until

    if not seconds:
        seconds = 30.0

    seconds = max(
        5.0,
        min(float(seconds), 300.0)
    )

    async with _quota_lock:

        _quota_cooldown_until = max(
            _quota_cooldown_until,
            time.time() + seconds
        )


# ============================================================
# ERROR HELPERS
# ============================================================

def is_429(error: Exception) -> bool:

    text = str(error).lower()

    return any(
        token in text
        for token in (
            "429",
            "too_many_requests",
            "too many requests",
            "resource_exhausted",
            "quota exceeded",
            "rate limit",
            "rate_limit",
        )
    )


def is_retryable_non429(
    error: Exception
) -> bool:

    text = str(error).lower()

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

    if any(token in text for token in permanent):
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
        "server error",
    )

    return any(
        token in text
        for token in retryable
    )


def extract_retry_after(
    error: Exception
) -> Optional[float]:

    text = str(error)

    patterns = (
        r"retry in\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retry after\s+([0-9]+(?:\.[0-9]+)?)s",
        r"retryDelay['\"]?\s*[:=]\s*['\"]?([0-9]+(?:\.[0-9]+)?)s",
    )

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            try:
                return max(
                    1.0,
                    float(match.group(1))
                )
            except ValueError:
                pass

    return None


def error_response(
    message: str,
    status_code: int = 200,
    status: str = "error",
    extra: Optional[dict[str, Any]] = None,
):

    payload = {
        "status": status,
        "ok": False,
        "success": False,
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
        content=payload
    )


# ============================================================
# MIME TYPES
# ============================================================

def mime_type_for(
    filename: str
) -> Optional[str]:

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

        ".docx":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",

        ".xls":
            "application/vnd.ms-excel",

        ".xlsx":
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

        ".ppt":
            "application/vnd.ms-powerpoint",

        ".pptx":
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }

    return mapping.get(ext)


# ============================================================
# EXTRACT ANSWER + SOURCES
# ============================================================

def extract_answer_and_sources(
    interaction: Any
) -> tuple[str, list[dict[str, Any]]]:

    # --------------------------------------------------------
    # CÁCH 1 — output_text
    # Đây là cách chính thức và đơn giản nhất.
    # --------------------------------------------------------

    answer = (
        getattr(
            interaction,
            "output_text",
            None
        )
        or ""
    ).strip()

    sources: list[dict[str, Any]] = []

    # --------------------------------------------------------
    # CÁCH 2 — đọc steps
    # Dùng để lấy citation / fallback.
    # --------------------------------------------------------

    steps = (
        getattr(
            interaction,
            "steps",
            None
        )
        or []
    )

    for step in steps:

        if getattr(
            step,
            "type",
            None
        ) != "model_output":
            continue

        contents = (
            getattr(
                step,
                "content",
                None
            )
            or []
        )

        for block in contents:

            block_type = getattr(
                block,
                "type",
                None
            )

            # Fallback text.
            if block_type == "text":

                text = (
                    getattr(
                        block,
                        "text",
                        None
                    )
                    or ""
                ).strip()

                if not answer and text:
                    answer = text

                # Citation.
                annotations = (
                    getattr(
                        block,
                        "annotations",
                        None
                    )
                    or []
                )

                for annotation in annotations:

                    if getattr(
                        annotation,
                        "type",
                        None
                    ) != "file_citation":
                        continue

                    item: dict[str, Any] = {}

                    file_name = getattr(
                        annotation,
                        "file_name",
                        None
                    )

                    source = getattr(
                        annotation,
                        "source",
                        None
                    )

                    page_number = getattr(
                        annotation,
                        "page_number",
                        None
                    )

                    if file_name:
                        item["file_name"] = str(
                            file_name
                        )

                    if source:
                        item["source"] = str(
                            source
                        )

                    if page_number is not None:
                        item["page_number"] = page_number

                    if (
                        item
                        and item not in sources
                    ):
                        sources.append(item)

    if not answer:

        raise RuntimeError(
            "Gemini trả về nhưng không có nội dung văn bản."
        )

    return answer, sources


# ============================================================
# USAGE
# ============================================================

def extract_usage(
    interaction: Any
) -> Optional[dict[str, Any]]:

    """
    QUAN TRỌNG:
    Hàm này nhận interaction trực tiếp.
    Không còn lỗi:
        NameError: name 'result' is not defined
    """

    usage = getattr(
        interaction,
        "usage",
        None
    )

    if usage is None:
        return None

    keys = (
        "total_input_tokens",
        "total_output_tokens",
        "total_thought_tokens",
        "total_tool_use_tokens",
        "total_cached_tokens",
        "total_tokens",
    )

    data = {}

    for key in keys:

        value = getattr(
            usage,
            key,
            None
        )

        if value is not None:
            data[key] = value

    return data or None


# ============================================================
# GEMINI INTERACTIONS API
# ============================================================

async def call_gemini(
    question: str
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
            "GEMINI_FILE_SEARCH_STORE chưa được cấu hình."
        )

    # Nếu đang trong cooldown do 429,
    # KHÔNG gửi thêm request.
    cooldown = await get_quota_cooldown()

    if cooldown > 0:

        raise GeminiRateLimitError(
            (
                "Gemini đang bị giới hạn tạm thời. "
                f"Vui lòng thử lại sau {cooldown:.0f} giây."
            ),
            retry_after=cooldown
        )

    generation_config = {
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "thinking_level": THINKING_LEVEL,
        "thinking_summaries": "none",
    }

    # Interactions API chính thức hỗ trợ File Search.
    interaction = await gemini_client.aio.interactions.create(

        model=GEMINI_MODEL,

        system_instruction=SYSTEM_PROMPT,

        input=question,

        # Không cần lưu conversation server-side.
        # Giảm dữ liệu lưu và phù hợp với chatbot tra cứu độc lập.
        store=False,

        generation_config=generation_config,

        tools=[
            {
                "type": "file_search",

                "file_search_store_names": [
                    store
                ],

                "top_k": FILE_SEARCH_TOP_K,
            }
        ],
    )

    return interaction


async def ask_gemini(
    question: str
):

    last_error: Optional[Exception] = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            async with request_semaphore:

                interaction = await asyncio.wait_for(
                    call_gemini(question),
                    timeout=REQUEST_TIMEOUT
                )

            answer, sources = (
                extract_answer_and_sources(
                    interaction
                )
            )

            usage = extract_usage(
                interaction
            )

            return (
                interaction,
                answer,
                sources,
                usage,
            )

        except GeminiRateLimitError:
            raise

        except asyncio.TimeoutError as exc:

            last_error = GeminiTimeoutError(
                "Gemini phản hồi quá thời gian cho phép."
            )

            logger.warning(
                "GEMINI TIMEOUT lần %s/%s",
                attempt,
                MAX_RETRIES
            )

            if attempt >= MAX_RETRIES:
                break

            await asyncio.sleep(
                min(
                    2 * attempt,
                    4
                )
            )

        except Exception as exc:

            last_error = exc

            # 429 tuyệt đối không retry.
            if is_429(exc):

                retry_after = (
                    extract_retry_after(exc)
                    or 30.0
                )

                await set_quota_cooldown(
                    retry_after
                )

                await increment_metric(
                    "requests_429"
                )

                logger.error(
                    "GEMINI 429 - KHÔNG RETRY: %r",
                    exc
                )

                raise GeminiRateLimitError(
                    str(exc),
                    retry_after=retry_after
                )

            retryable = (
                is_retryable_non429(exc)
            )

            logger.warning(
                "GEMINI ERROR lần %s/%s | retryable=%s | %r",
                attempt,
                MAX_RETRIES,
                retryable,
                exc
            )

            if (
                not retryable
                or attempt >= MAX_RETRIES
            ):
                break

            # Chỉ retry lỗi máy chủ/mạng.
            await asyncio.sleep(
                min(
                    2 * attempt,
                    4
                )
            )

    raise (
        last_error
        or RuntimeError(
            "Gemini không thể xử lý câu hỏi."
        )
    )


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global gemini_client

    logger.info("=" * 60)
    logger.info(
        "KHỞI ĐỘNG THỦY LỢI AI %s",
        APP_VERSION
    )
    logger.info("=" * 60)

    logger.info(
        "Google GenAI: %s",
        (
            "OK"
            if genai
            else f"LỖI: {GOOGLE_GENAI_IMPORT_ERROR}"
        )
    )

    logger.info(
        "GEMINI_API_KEY: %s",
        (
            "ĐÃ CẤU HÌNH"
            if GEMINI_API_KEY
            else "THIẾU"
        )
    )

    logger.info(
        "FILE SEARCH STORE: %s",
        (
            GEMINI_FILE_SEARCH_STORE
            if GEMINI_FILE_SEARCH_STORE
            else "THIẾU"
        )
    )

    logger.info(
        "MODEL: %s",
        GEMINI_MODEL
    )

    logger.info(
        "TOP_K: %s",
        FILE_SEARCH_TOP_K
    )

    logger.info(
        "MAX_CONCURRENT: %s",
        MAX_CONCURRENT
    )

    logger.info(
        "TIMEOUT: %ss",
        REQUEST_TIMEOUT
    )

    logger.info(
        "MAX_RETRIES: %s",
        MAX_RETRIES
    )

    logger.info(
        "CACHE: %s entries / %ss",
        CACHE_SIZE,
        CACHE_TTL
    )

    if genai and GEMINI_API_KEY:

        try:

            gemini_client = genai.Client(
                api_key=GEMINI_API_KEY
            )

            logger.info(
                "Gemini client: ĐÃ SẴN SÀNG"
            )

        except Exception as exc:

            gemini_client = None

            logger.exception(
                "Lỗi khởi tạo Gemini client: %r",
                exc
            )

    else:

        gemini_client = None

    yield

    # Đóng async client.
    try:

        if (
            gemini_client is not None
            and hasattr(
                gemini_client,
                "aio"
            )
        ):

            await gemini_client.aio.aclose()

    except Exception as exc:

        logger.warning(
            "Lỗi đóng Gemini client: %r",
            exc
        )

    gemini_client = None

    logger.info(
        "THỦY LỢI AI ĐÃ DỪNG"
    )


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="THỦY LỢI AI",
    description=(
        "Trợ lý AI chuyên ngành Thủy lợi "
        "Vu Gia - Thu Bồn"
    ),
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
# REQUEST MODEL
# ============================================================

class Question(BaseModel):

    question: str = Field(
        default="",
        max_length=20000
    )


# ============================================================
# HOME / HEALTH
# ============================================================

@app.get("/")
async def home():

    if INDEX_FILE.exists():

        return FileResponse(
            INDEX_FILE,
            media_type="text/html"
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
        "engine": "Gemini File Search",
        "google_genai_imported": (
            genai is not None
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
            clean_store_name(
                GEMINI_FILE_SEARCH_STORE
            )
        ),
        "model": GEMINI_MODEL,
        "top_k": FILE_SEARCH_TOP_K,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }


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
# DIAGNOSTICS / METRICS
# ============================================================

@app.get("/diagnostics")
async def diagnostics():

    async with metrics_lock:
        current_metrics = dict(metrics)

    cooldown = await get_quota_cooldown()

    return {
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "gemini_configured": bool(
            GEMINI_API_KEY
        ),
        "gemini_client_ready": (
            gemini_client is not None
        ),
        "file_search_store": (
            clean_store_name(
                GEMINI_FILE_SEARCH_STORE
            )
        ),
        "model": GEMINI_MODEL,
        "configuration": {
            "top_k": FILE_SEARCH_TOP_K,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "thinking_level": THINKING_LEVEL,
            "max_concurrent": MAX_CONCURRENT,
            "request_timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
            "cache_ttl": CACHE_TTL,
            "cache_size": CACHE_SIZE,
        },
        "quota_cooldown_seconds": round(
            cooldown,
            1
        ),
        "metrics": current_metrics,
    }


@app.get("/metrics")
async def get_metrics():

    async with metrics_lock:

        return dict(metrics)


# ============================================================
# STORES
# ============================================================

@app.get("/stores")
async def stores():

    if gemini_client is None:

        return {
            "success": False,
            "configured_store": (
                clean_store_name(
                    GEMINI_FILE_SEARCH_STORE
                )
            ),
            "stores": [],
            "error": (
                "Gemini client chưa sẵn sàng."
            ),
        }

    try:

        items = []

        pager = (
            await gemini_client
            .aio
            .file_search_stores
            .list(
                config={
                    "page_size": 20
                }
            )
        )

        async for store in pager:

            items.append(
                {
                    "name": getattr(
                        store,
                        "name",
                        None
                    ),
                    "display_name": getattr(
                        store,
                        "display_name",
                        None
                    ),
                    "active": getattr(
                        store,
                        "active",
                        None
                    ),
                }
            )

        return {
            "success": True,
            "configured_store": (
                clean_store_name(
                    GEMINI_FILE_SEARCH_STORE
                )
            ),
            "count": len(items),
            "stores": items,
        }

    except Exception as exc:

        logger.exception(
            "GET /stores ERROR"
        )

        return {
            "success": False,
            "configured_store": (
                clean_store_name(
                    GEMINI_FILE_SEARCH_STORE
                )
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
                clean_store_name(
                    GEMINI_FILE_SEARCH_STORE
                )
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

        # QUAN TRỌNG:
        # phải truyền parent=store.
        pager = (
            await gemini_client
            .aio
            .file_search_stores
            .documents
            .list(
                parent=store,
                config={
                    "page_size": 100
                }
            )
        )

        async for doc in pager:

            docs.append(
                {
                    "name": str(
                        getattr(
                            doc,
                            "name",
                            ""
                        )
                        or ""
                    ),
                    "display_name": str(
                        getattr(
                            doc,
                            "display_name",
                            ""
                        )
                        or ""
                    ),
                    "state": str(
                        getattr(
                            doc,
                            "state",
                            ""
                        )
                        or ""
                    ),
                    "mime_type": str(
                        getattr(
                            doc,
                            "mime_type",
                            ""
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
            "GET /documents ERROR"
        )

        return {
            "success": False,
            "store": store,
            "count": 0,
            "documents": [],
            "error": str(exc),
        }


# ============================================================
# DELETE DOCUMENT
# ============================================================

@app.delete(
    "/documents/{document_name:path}"
)
async def delete_document(
    document_name: str
):

    if gemini_client is None:

        return error_response(
            "Gemini client chưa sẵn sàng.",
            status_code=503
        )

    store = clean_store_name(
        GEMINI_FILE_SEARCH_STORE
    )

    if not store:

        return error_response(
            "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
            status_code=503
        )

    if document_name.startswith(
        "fileSearchStores/"
    ):

        full_name = document_name

    else:

        full_name = (
            f"{store}/documents/{document_name}"
        )

    try:

        await (
            gemini_client
            .aio
            .file_search_stores
            .documents
            .delete(
                name=full_name,
                config={
                    "force": True
                }
            )
        )

        # Tài liệu đã thay đổi -> xóa cache
        # để câu trả lời mới không dùng dữ liệu cũ.
        await cache_clear()

        return {
            "success": True,
            "status": "ok",
            "message": (
                "Đã xóa tài liệu khỏi "
                "kho THỦY LỢI AI."
            ),
            "document": full_name,
        }

    except Exception as exc:

        logger.exception(
            "DELETE DOCUMENT ERROR: %r",
            exc
        )

        return error_response(
            f"Không xóa được tài liệu: {exc}",
            status_code=200,
            status="delete_error"
        )


# ============================================================
# UPLOAD
# ============================================================

def _write_temp_file(
    file_obj,
    temp_path: str,
    max_bytes: int
):

    total_written = 0

    with open(
        temp_path,
        "wb"
    ) as tmp:

        while True:

            chunk = file_obj.file.read(
                1024 * 1024
            )

            if not chunk:
                break

            total_written += len(chunk)

            if total_written > max_bytes:

                raise ValueError(
                    "Dung lượng file vượt quá "
                    f"giới hạn {max_bytes // (1024 * 1024)}MB."
                )

            tmp.write(chunk)


@app.post("/upload")
async def upload(
    file: UploadFile = File(...)
):

    await increment_metric(
        "uploads_total"
    )

    if gemini_client is None:

        return error_response(
            "Gemini client chưa sẵn sàng.",
            status_code=503
        )

    store = clean_store_name(
        GEMINI_FILE_SEARCH_STORE
    )

    if not store:

        return error_response(
            "Render chưa có GEMINI_FILE_SEARCH_STORE.",
            status_code=503
        )

    filename = Path(
        file.filename or "document"
    ).name

    mime_type = (
        mime_type_for(filename)
        or file.content_type
        or "application/octet-stream"
    )

    temp_path = None

    max_bytes = (
        MAX_UPLOAD_SIZE_MB
        * 1024
        * 1024
    )

    try:

        suffix = Path(
            filename
        ).suffix

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        )

        temp_path = temp_file.name

        temp_file.close()

        await asyncio.to_thread(
            _write_temp_file,
            file,
            temp_path,
            max_bytes
        )

        file_size = os.path.getsize(
            temp_path
        )

        logger.info(
            "UPLOAD | %s | %.2f MB | %s",
            filename,
            file_size / 1024 / 1024,
            mime_type
        )

        # Chỉ cho một upload/query chạy
        # tại một thời điểm trên instance.
        async with request_semaphore:

            uploaded = await asyncio.wait_for(

                gemini_client
                .aio
                .files
                .upload(

                    file=temp_path,

                    config={
                        "display_name": filename,
                        "mime_type": mime_type,
                    }
                ),

                timeout=REQUEST_TIMEOUT
            )

            logger.info(
                "FILE UPLOAD OK | %s",
                getattr(
                    uploaded,
                    "name",
                    None
                )
            )

            operation = await asyncio.wait_for(

                gemini_client
                .aio
                .file_search_stores
                .import_file(

                    file_search_store_name=store,

                    file_name=uploaded.name
                ),

                timeout=REQUEST_TIMEOUT
            )

            polling_attempts = 0

            # 2 phút.
            max_polling_attempts = 40

            while not getattr(
                operation,
                "done",
                False
            ):

                polling_attempts += 1

                if (
                    polling_attempts
                    > max_polling_attempts
                ):

                    raise TimeoutError(
                        "Quá thời gian xử lý "
                        "đưa tài liệu vào Store."
                    )

                await asyncio.sleep(3)

                operation = await asyncio.wait_for(

                    gemini_client
                    .aio
                    .operations
                    .get(operation),

                    timeout=REQUEST_TIMEOUT
                )

        await increment_metric(
            "uploads_success"
        )

        # Tài liệu mới -> cache cũ có thể sai.
        await cache_clear()

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
                None
            ),
            "operation": getattr(
                operation,
                "name",
                None
            ),
        }

    except ValueError as exc:

        logger.warning(
            "UPLOAD REJECTED | %s | %s",
            filename,
            str(exc)
        )

        return error_response(
            str(exc),
            status_code=400
        )

    except Exception as exc:

        logger.exception(
            "UPLOAD ERROR | %s | %r",
            filename,
            exc
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "status": "upload_error",
                "filename": filename,
                "error": str(exc),
            }
        )

    finally:

        try:
            await file.close()
        except Exception:
            pass

        if (
            temp_path
            and os.path.exists(temp_path)
        ):

            try:

                await asyncio.to_thread(
                    os.remove,
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

    await increment_metric(
        "requests_total"
    )

    logger.info(
        "CÂU HỎI: %s",
        question[:300]
    )

    if not question:

        return error_response(
            "Vui lòng nhập câu hỏi."
        )

    if not GEMINI_API_KEY:

        return error_response(
            "Render chưa có GEMINI_API_KEY.",
            status_code=503
        )

    if gemini_client is None:

        return error_response(
            (
                "Gemini client chưa sẵn sàng. "
                "Kiểm tra google-genai "
                "và GEMINI_API_KEY."
            ),
            status_code=503
        )

    if not GEMINI_FILE_SEARCH_STORE:

        return error_response(
            (
                "Render chưa có "
                "GEMINI_FILE_SEARCH_STORE."
            ),
            status_code=503
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = await cache_get(
        question
    )

    if cached:

        return cached

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    try:

        (
            interaction,
            answer,
            sources,
            usage,
        ) = await ask_gemini(
            question
        )

        # ----------------------------------------------------
        # QUAN TRỌNG:
        # Không còn:
        #     usage = getattr(result, ...)
        #
        # Vì result trước đây không tồn tại.
        # ----------------------------------------------------

        payload = {
            "status": "ok",
            "ok": True,
            "success": True,

            "answer": answer,
            "reply": answer,
            "message": answer,

            "engine": "Gemini File Search",
            "version": APP_VERSION,

            "model": GEMINI_MODEL,

            "cached": False,
        }

        if usage:

            payload["usage"] = usage

        if sources:

            payload["sources"] = sources

        # Lưu cache.
        await cache_put(
            question,
            payload
        )

        await increment_metric(
            "requests_success"
        )

        logger.info(
            "ASK SUCCESS | sources=%s | usage=%s",
            len(sources),
            usage
        )

        return payload

    except GeminiRateLimitError as exc:

        await increment_metric(
            "requests_429"
        )

        retry_after = (
            exc.retry_after
            or 30.0
        )

        message = (
            "⚠️ Gemini đang đạt giới hạn "
            "truy cập của dự án. "
            "Kho tài liệu THỦY LỢI AI "
            "vẫn còn nguyên và không bị mất. "
            f"Vui lòng thử lại sau khoảng "
            f"{retry_after:.0f} giây."
        )

        return error_response(
            message,
            status_code=200,
            status="rate_limited",
            extra={
                "retryable": True,
                "retry_after": retry_after,
            }
        )

    except Exception as exc:

        await increment_metric(
            "requests_error"
        )

        logger.exception(
            "ASK ERROR: %r",
            exc
        )

        text = str(exc)

        lower = text.lower()

        if (
            "403" in text
            or "permission" in lower
        ):

            message = (
                "THỦY LỢI AI không có quyền "
                "truy cập kho Gemini File Search. "
                "Hãy kiểm tra "
                "GEMINI_API_KEY và "
                "GEMINI_FILE_SEARCH_STORE."
            )

        elif (
            "404" in text
            or "not found" in lower
        ):

            message = (
                "Không tìm thấy kho Gemini "
                "File Search. Hãy kiểm tra "
                "GEMINI_FILE_SEARCH_STORE."
            )

        elif (
            "timeout" in lower
            or "deadline" in lower
        ):

            message = (
                "Gemini phản hồi quá lâu. "
                "Vui lòng thử lại sau ít giây."
            )

        else:

            message = (
                "THỦY LỢI AI chưa lấy được "
                "câu trả lời từ kho hồ sơ. "
                "Vui lòng thử lại."
            )

        return error_response(
            message,
            status_code=200,
            status="gemini_error"
        )


@app.post("/ask")
async def ask(
    data: Question
):

    return await process_question(
        data
    )


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
            "10000"
        )
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
