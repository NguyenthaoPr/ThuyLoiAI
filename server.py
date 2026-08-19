import os
import re
import asyncio
import random
import tempfile
import time
import hashlib
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai


# ============================================================
# THỦY LỢI AI - SERVER.PY
# BẢN NÂNG CẤP ỔN ĐỊNH - GIỮ NGUYÊN KIẾN TRÚC HIỆN TẠI
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

# ----------------------------
# BẢO VỆ HỆ THỐNG
# ----------------------------
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "2")))
REQUEST_TIMEOUT = max(15, int(os.getenv("REQUEST_TIMEOUT", "45")))
QUEUE_TIMEOUT = max(5, int(os.getenv("QUEUE_TIMEOUT", "20")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "2")))
MAX_QUESTION_LENGTH = max(100, int(os.getenv("MAX_QUESTION_LENGTH", "2000")))

# Upload: tránh một file quá lớn làm cạn RAM server.
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "25")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_OPERATION_TIMEOUT = max(
    30, int(os.getenv("UPLOAD_OPERATION_TIMEOUT", "300"))
)

# ----------------------------
# CACHE ỔN ĐỊNH TRONG PROCESS
# Giữ cache hiện tại nhưng nâng từ 200 -> 1000.
# Đây vẫn là RAM cache; tầng lưu bền vững sẽ làm ở bước sau.
# ----------------------------
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
CACHE_TTL = max(60, int(os.getenv("CACHE_TTL", "3600")))
CACHE_MAX_ENTRIES = max(100, int(os.getenv("CACHE_MAX_ENTRIES", "1000")))

_answer_cache = OrderedDict()
_cache_lock = asyncio.Lock()

# Chống nhiều request cùng lúc hỏi đúng một câu MISS.
_inflight = {}
_inflight_lock = asyncio.Lock()

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của
Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời dựa trên kho hồ sơ, tài liệu, quy định, quy trình và
dữ liệu đã được đưa vào Gemini File Search.

NGUYÊN TẮC BẮT BUỘC:
1. Ưu tiên thông tin trong kho hồ sơ THỦY LỢI AI.
2. Không tự bịa số liệu, điều khoản, tên/số văn bản, ngày tháng,
   thông số kỹ thuật hoặc quy trình vận hành.
3. Nếu không tìm thấy đủ căn cứ, nói rõ:
   "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."
4. Tổng hợp, phân tích rõ ràng khi tài liệu có thông tin liên quan.
5. Khi có thể xác định nguồn, nêu tên tài liệu hoặc nguồn.
6. Với pháp luật, ưu tiên văn bản có trong kho hồ sơ.
7. Nếu có nhiều tài liệu, so sánh và chỉ ra điểm khác nhau.
8. Không biến suy đoán thành kết luận chính thức.
9. Trả lời bằng tiếng Việt.
10. Ưu tiên ngắn gọn, chính xác, dễ hiểu, có căn cứ và phù hợp
    nghiệp vụ Thủy lợi.
11. Với quy trình, có thể trình bày theo từng bước.
12. Với số liệu, giữ nguyên đơn vị và số liệu theo tài liệu.
"""


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    print("=" * 60)
    print("KHỞI ĐỘNG THỦY LỢI AI - BẢN 5.1 STABLE")
    print("=" * 60)
    print("KIỂM TRA GEMINI...")

    if not GEMINI_API_KEY:
        print("GEMINI API: CHƯA CÓ API KEY")
    else:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            print("GEMINI API: ĐÃ KHỞI TẠO CLIENT")
        except Exception as e:
            gemini_client = None
            print("GEMINI API: LỖI KHỞI TẠO:", repr(e))

    print(
        "FILE SEARCH STORE:",
        GEMINI_FILE_SEARCH_STORE or "CHƯA CẤU HÌNH",
    )
    print("MODEL:", GEMINI_MODEL)
    print("MAX CONCURRENT:", MAX_CONCURRENT)
    print("QUEUE TIMEOUT:", QUEUE_TIMEOUT)
    print("REQUEST TIMEOUT:", REQUEST_TIMEOUT)
    print("MAX RETRIES:", MAX_RETRIES)
    print("CACHE ENABLED:", CACHE_ENABLED)
    print("CACHE TTL:", CACHE_TTL)
    print("CACHE MAX ENTRIES:", CACHE_MAX_ENTRIES)
    print("MAX UPLOAD MB:", MAX_UPLOAD_MB)
    print("=" * 60)

    yield

    gemini_client = None
    print("THỦY LỢI AI ĐÃ DỪNG")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="5.1",
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
    question: str


# ============================================================
# BASIC HELPERS
# ============================================================

def require_gemini():
    if gemini_client is None:
        raise RuntimeError("Gemini API chưa được kết nối.")
    if not GEMINI_FILE_SEARCH_STORE:
        raise RuntimeError(
            "Gemini File Search Store chưa được cấu hình."
        )


def store_name():
    return GEMINI_FILE_SEARCH_STORE.strip()


def normalize_question(text: str) -> str:
    """
    Chuẩn hóa câu hỏi để cache nhận diện tốt hơn:
    - bỏ khoảng trắng thừa
    - lowercase
    - bỏ khoảng trắng quanh dấu câu
    """
    value = (text or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([?.!,;:])", r"\1", value)
    return value


# ============================================================
# CACHE
# ============================================================

async def get_cached_answer(question: str):
    if not CACHE_ENABLED:
        return None

    key = normalize_question(question)

    async with _cache_lock:
        item = _answer_cache.get(key)

        if not item:
            return None

        age = time.time() - item["created_at"]

        if age > CACHE_TTL:
            _answer_cache.pop(key, None)
            return None

        # LRU: câu vừa dùng được đưa lên đầu.
        _answer_cache.move_to_end(key, last=False)

        return {
            "answer": item["answer"],
            "sources": item.get("sources", []),
            "created_at": item["created_at"],
            "age_seconds": round(age, 1),
        }


async def set_cached_answer(question: str, answer: str, sources=None):
    if not CACHE_ENABLED:
        return

    key = normalize_question(question)

    # Không lưu câu trả lời rỗng hoặc quá lớn.
    if not key or not answer:
        return

    async with _cache_lock:
        _answer_cache[key] = {
            "answer": answer,
            "sources": sources or [],
            "created_at": time.time(),
        }

        _answer_cache.move_to_end(key, last=False)

        while len(_answer_cache) > CACHE_MAX_ENTRIES:
            _answer_cache.popitem(last=True)


async def clear_answer_cache():
    async with _cache_lock:
        _answer_cache.clear()

    async with _inflight_lock:
        _inflight.clear()


async def cache_info():
    async with _cache_lock:
        now = time.time()
        valid = 0
        expired = 0

        for item in _answer_cache.values():
            if now - item["created_at"] <= CACHE_TTL:
                valid += 1
            else:
                expired += 1

        return {
            "enabled": CACHE_ENABLED,
            "count": len(_answer_cache),
            "valid_count": valid,
            "expired_count": expired,
            "max_entries": CACHE_MAX_ENTRIES,
            "ttl_seconds": CACHE_TTL,
        }


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "version": "5.1",
    }


# ============================================================
# HEALTH
# QUAN TRỌNG:
# /health KHÔNG GỌI GEMINI FILE SEARCH.
# Vì frontend có thể gọi nhiều lần và health phải thật nhẹ.
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_connected": gemini_client is not None,
        "gemini_client_ready": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "max_concurrent": MAX_CONCURRENT,
        "queue_timeout": QUEUE_TIMEOUT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
        "max_question_length": MAX_QUESTION_LENGTH,
        "cache_enabled": CACHE_ENABLED,
        "cache_ttl": CACHE_TTL,
        "cache_max_entries": CACHE_MAX_ENTRIES,
    }


# ============================================================
# HEALTH DEEP
# Chỉ dùng khi cần kiểm tra thực tế File Search Store.
# Không dùng liên tục từ frontend.
# ============================================================

@app.get("/health/deep")
async def health_deep():
    result = {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "gemini_connected": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_ready": False,
        "documents_count": 0,
    }

    if gemini_client is None:
        result["status"] = "error"
        result["message"] = "Gemini API chưa kết nối."
        return result

    if not GEMINI_FILE_SEARCH_STORE:
        result["status"] = "error"
        result["message"] = "Chưa cấu hình File Search Store."
        return result

    try:
        documents = await asyncio.wait_for(
            asyncio.to_thread(list_documents_sync),
            timeout=30,
        )

        result["file_search_ready"] = True
        result["documents_count"] = len(documents)
        result["message"] = "File Search Store hoạt động."
        return result

    except asyncio.TimeoutError:
        result["status"] = "error"
        result["message"] = "Kiểm tra File Search Store quá thời gian."
        return result

    except Exception as e:
        print("HEALTH DEEP ERROR:", repr(e))
        result["status"] = "error"
        result["message"] = "Không truy cập được File Search Store."
        result["error"] = str(e)
        return result


# ============================================================
# API INFO
# ============================================================

@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "version": "5.1",
        "endpoints": {
            "home": "/",
            "health": "/health",
            "health_deep": "/health/deep",
            "ask": "/ask",
            "stores": "/stores",
            "documents": "/documents",
            "pdf_documents": "/documents/pdf",
            "delete_pdf": "/documents/pdf",
            "upload": "/upload",
            "cache": "/cache",
            "clear_cache": "/cache",
        },
        "protection": {
            "queue": True,
            "retry": True,
            "cache": True,
            "cache_stampede": True,
        },
    }


# ============================================================
# CACHE API
# Giữ cấu trúc API, chỉ bổ sung để kiểm tra cache.
# ============================================================

@app.get("/cache")
async def get_cache():
    return {
        "success": True,
        **(await cache_info()),
    }


@app.delete("/cache")
async def clear_cache():
    await clear_answer_cache()

    return {
        "success": True,
        "message": "Đã xóa toàn bộ cache câu hỏi.",
    }


# ============================================================
# GEMINI RETRY
# ============================================================

def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()

    permanent = [
        "400",
        "401",
        "403",
        "bad request",
        "unauthenticated",
        "permission denied",
        "api key",
        "invalid argument",
        "not found",
    ]

    if any(x in text for x in permanent):
        return False

    retryable = [
        "408",
        "409",
        "429",
        "500",
        "502",
        "503",
        "504",
        "rate limit",
        "resource exhausted",
        "unavailable",
        "timeout",
        "deadline",
        "temporarily",
        "internal",
        "connection",
        "reset",
        "server error",
    ]

    # Timeout luôn cho phép retry tối đa theo MAX_RETRIES.
    return any(x in text for x in retryable)


def call_gemini(question: str):
    require_gemini()

    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[{
            "type": "file_search",
            "file_search_store_names": [store_name()],
        }],
    )


# ============================================================
# ANSWER + SOURCES
# ============================================================

def extract_answer_and_sources(result):
    answer = (getattr(result, "output_text", None) or "").strip()
    sources = []

    for step in (getattr(result, "steps", []) or []):
        if getattr(step, "type", None) != "model_output":
            continue

        for block in (getattr(step, "content", []) or []):
            if not answer and getattr(block, "type", None) == "text":
                answer += getattr(block, "text", "") or ""

            for annotation in (getattr(block, "annotations", []) or []):
                if getattr(annotation, "type", None) != "file_citation":
                    continue

                item = {}

                file_name = getattr(annotation, "file_name", None)
                source = getattr(annotation, "source", None)

                if file_name:
                    item["file_name"] = str(file_name)

                if source:
                    item["source"] = str(source)

                if item and item not in sources:
                    sources.append(item)

    answer = answer.strip()

    if not answer:
        raise RuntimeError("Gemini không trả về nội dung.")

    return answer, sources


# ============================================================
# GEMINI CALL WITH QUEUE + TIMEOUT + RETRY
# ============================================================

async def _gemini_once(question: str):
    """
    Một lượt gọi Gemini.
    Queue timeout tách riêng với request timeout.
    """

    try:
        await asyncio.wait_for(
            request_semaphore.acquire(),
            timeout=QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            "Hệ thống đang có nhiều yêu cầu. Hàng đợi đã quá thời gian chờ."
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(call_gemini, question),
            timeout=REQUEST_TIMEOUT,
        )
        return extract_answer_and_sources(result)

    finally:
        request_semaphore.release()


async def ask_gemini_with_retry(question: str):
    last_error = None

    for attempt in range(MAX_RETRIES):
        started = time.monotonic()

        try:
            answer, sources = await _gemini_once(question)

            elapsed = time.monotonic() - started

            print(
                f"GEMINI SUCCESS attempt={attempt + 1}/{MAX_RETRIES} "
                f"time={elapsed:.1f}s"
            )

            return answer, sources

        except Exception as e:
            last_error = e

            elapsed = time.monotonic() - started

            print(
                f"GEMINI ERROR attempt={attempt + 1}/{MAX_RETRIES} "
                f"time={elapsed:.1f}s error={repr(e)}"
            )

            retryable = is_retryable_error(e)
            print("RETRYABLE:", retryable)

            if not retryable or attempt >= MAX_RETRIES - 1:
                break

            delay = min(6, 2 ** attempt) + random.uniform(0.2, 0.8)

            print(
                f"THỬ LẠI LẦN {attempt + 2}/{MAX_RETRIES} "
                f"SAU {delay:.1f} GIÂY..."
            )

            await asyncio.sleep(delay)

    raise last_error or RuntimeError(
        "Gemini không thể xử lý câu hỏi."
    )


# ============================================================
# SINGLE-FLIGHT / CACHE STAMPEDE PROTECTION
# ============================================================

async def ask_with_singleflight(question: str):
    key = normalize_question(question)

    # Kiểm tra cache lần đầu.
    cached = await get_cached_answer(question)

    if cached:
        print(
            "CACHE HIT -",
            f"age={cached['age_seconds']}s",
        )
        return cached["answer"], cached["sources"], True

    # Nếu một request khác đang xử lý cùng câu hỏi,
    # chờ đúng request đó thay vì gọi Gemini lần nữa.
    async with _inflight_lock:
        future = _inflight.get(key)

        if future is None:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            _inflight[key] = future
            is_owner = True
        else:
            is_owner = False

    if not is_owner:
        print("CACHE STAMPEDE PROTECTION - CHỜ REQUEST ĐANG XỬ LÝ")

        try:
            answer, sources = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=REQUEST_TIMEOUT + QUEUE_TIMEOUT + 15,
            )
            return answer, sources, False

        except Exception:
            # Request gốc thất bại; request hiện tại sẽ tự xử lý lại.
            async with _inflight_lock:
                if _inflight.get(key) is future:
                    _inflight.pop(key, None)

            # Tiếp tục xuống xử lý Gemini.
            return await ask_with_singleflight(question)

    try:
        print("CACHE MISS - ĐANG GỬI CÂU HỎI GEMINI...")

        answer, sources = await ask_gemini_with_retry(question)

        # Chỉ cache câu trả lời thành công.
        await set_cached_answer(
            question,
            answer,
            sources,
        )

        if not future.done():
            future.set_result((answer, sources))

        print("CACHE SAVED - CÂU TRẢ LỜI ĐÃ ĐƯỢC LƯU")

        return answer, sources, False

    except Exception as e:
        if not future.done():
            future.set_exception(e)

        raise

    finally:
        async with _inflight_lock:
            if _inflight.get(key) is future:
                _inflight.pop(key, None)


# ============================================================
# ASK
# ============================================================

@app.post("/ask")
async def ask(data: Question):
    question = (data.question or "").strip()

    print("=" * 60)
    print("CÂU HỎI:", question)
    print("=" * 60)

    if not question:
        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi.",
        }

    if len(question) > MAX_QUESTION_LENGTH:
        return {
            "status": "error",
            "answer": (
                f"Câu hỏi quá dài. Vui lòng nhập tối đa "
                f"{MAX_QUESTION_LENGTH} ký tự."
            ),
        }

    # CACHE phải được kiểm tra trước khi yêu cầu Gemini.
    cached = await get_cached_answer(question)

    if cached:
        print("CACHE HIT - TRẢ CÂU TRẢ LỜI TỪ CACHE")

        response = {
            "status": "ok",
            "answer": cached["answer"],
            "engine": "Local Cache",
            "model": GEMINI_MODEL,
            "cache": True,
        }

        if cached["sources"]:
            response["sources"] = cached["sources"]

        return response

    if not GEMINI_API_KEY:
        return {
            "status": "error",
            "answer": "THỦY LỢI AI chưa được cấu hình Gemini API.",
        }

    if gemini_client is None:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa kết nối được Gemini API. "
                "Vui lòng thử lại sau."
            ),
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa có kho dữ liệu "
                "Gemini File Search."
            ),
        }

    try:
        answer, sources, was_cache = await ask_with_singleflight(question)

        response = {
            "status": "ok",
            "answer": answer,
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
            "cache": False,
        }

        if sources:
            response["sources"] = sources

        return response

    except Exception as e:
        print("GEMINI KHÔNG TRẢ LỜI:", repr(e))

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI tạm thời chưa lấy được câu trả lời "
                "từ kho dữ liệu Gemini. Hệ thống đã tự kiểm tra "
                "và thử lại. Vui lòng thử lại sau ít giây."
            ),
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
            "cache": False,
        }


# ============================================================
# STORE / DOCUMENT HELPERS
# ============================================================

def serialize_store(store):
    return {
        "name": str(getattr(store, "name", "") or ""),
        "display_name": str(
            getattr(store, "display_name", None)
            or getattr(store, "displayName", None)
            or ""
        ),
    }


def serialize_document(doc):
    name = str(getattr(doc, "name", "") or "")

    display_name = str(
        getattr(doc, "display_name", None)
        or getattr(doc, "displayName", None)
        or ""
    )

    state = str(getattr(doc, "state", "") or "")

    mime_type = str(
        getattr(doc, "mime_type", None)
        or getattr(doc, "mimeType", None)
        or ""
    )

    return {
        "name": name,
        "display_name": display_name,
        "mime_type": mime_type,
        "state": state,
    }


def list_documents_sync():
    require_gemini()

    documents = []

    pager = gemini_client.file_search_stores.documents.list(
        parent=store_name(),
        config={"page_size": 20},
    )

    for doc in pager:
        documents.append(serialize_document(doc))

    return documents


# ============================================================
# STORES
# ============================================================

@app.get("/stores")
async def list_stores():
    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa được kết nối.",
        }

    try:
        stores = []

        def load():
            for s in gemini_client.file_search_stores.list():
                stores.append(serialize_store(s))

        await asyncio.to_thread(load)

        return {
            "success": True,
            "count": len(stores),
            "stores": stores,
        }

    except Exception as e:
        print("STORE LIST ERROR:", repr(e))

        return {
            "success": False,
            "error": str(e),
        }


# ============================================================
# DOCUMENTS
# ============================================================

@app.get("/documents")
async def list_documents():
    if gemini_client is None:
        return {
            "success": False,
            "store": store_name(),
            "count": 0,
            "documents": [],
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "store": "",
            "count": 0,
            "documents": [],
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        documents = await asyncio.to_thread(list_documents_sync)

        return {
            "success": True,
            "store": store_name(),
            "count": len(documents),
            "documents": documents,
        }

    except Exception as e:
        print("DOCUMENT LIST ERROR:", repr(e))

        return {
            "success": False,
            "store": store_name(),
            "count": 0,
            "documents": [],
            "error": str(e),
        }


def is_pdf_document(doc):
    name = (doc.get("display_name") or "").strip().lower()
    mime = (doc.get("mime_type") or "").strip().lower()
    resource_name = (doc.get("name") or "").strip().lower()

    return (
        name.endswith(".pdf")
        or mime == "application/pdf"
        or ".pdf" in resource_name
    )


# ============================================================
# PDF LIST
# ============================================================

@app.get("/documents/pdf")
async def list_pdf_documents():
    """Chỉ liệt kê PDF; KHÔNG XÓA."""

    if gemini_client is None:
        return {
            "success": False,
            "count": 0,
            "documents": [],
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "count": 0,
            "documents": [],
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        documents = await asyncio.to_thread(list_documents_sync)
        pdfs = [doc for doc in documents if is_pdf_document(doc)]

        return {
            "success": True,
            "store": store_name(),
            "count": len(pdfs),
            "documents": pdfs,
            "message": "Chỉ liệt kê PDF. Chưa xóa tài liệu nào.",
        }

    except Exception as e:
        print("PDF LIST ERROR:", repr(e))

        return {
            "success": False,
            "count": 0,
            "documents": [],
            "error": str(e),
        }


# ============================================================
# DELETE PDF
# ============================================================

def delete_pdf_documents_sync():
    require_gemini()

    documents = list_documents_sync()
    pdfs = [doc for doc in documents if is_pdf_document(doc)]

    deleted = []
    failed = []

    for doc in pdfs:
        try:
            gemini_client.file_search_stores.documents.delete(
                name=doc["name"],
                config={"force": True},
            )

            deleted.append(doc)

        except Exception as e:
            print(
                "PDF DELETE ERROR:",
                doc["name"],
                repr(e),
            )

            failed.append({
                "document": doc,
                "error": str(e),
            })

    return deleted, failed


@app.delete("/documents/pdf")
async def delete_pdf_documents():
    """
    XÓA TẤT CẢ DOCUMENT PDF trong File Search Store hiện tại.
    Chỉ xóa PDF; không xóa Word, Excel, TXT...
    """

    if gemini_client is None:
        return {
            "success": False,
            "deleted_count": 0,
            "failed_count": 0,
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "deleted_count": 0,
            "failed_count": 0,
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        deleted, failed = await asyncio.to_thread(
            delete_pdf_documents_sync
        )

        # Nội dung kho thay đổi -> xóa cache trả lời cũ.
        await clear_answer_cache()

        return {
            "success": len(failed) == 0,
            "store": store_name(),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "deleted": deleted,
            "failed": failed,
            "message": (
                f"Đã xóa {len(deleted)} PDF. "
                f"Còn lỗi: {len(failed)}. "
                f"Cache câu trả lời đã được làm mới."
            ),
        }

    except Exception as e:
        print("PDF DELETE ALL ERROR:", repr(e))

        return {
            "success": False,
            "deleted_count": 0,
            "failed_count": 0,
            "error": str(e),
        }


# ============================================================
# UPLOAD
# Giữ nguyên endpoint /upload.
# Bổ sung:
# - giới hạn dung lượng
# - timeout operation
# - không busy-loop liên tục
# - upload thành công -> xóa cache cũ
# ============================================================

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    if not file.filename:
        return {
            "success": False,
            "error": "Chưa chọn file.",
        }

    suffix = Path(file.filename).suffix
    temp_path = None

    try:
        content = await file.read()

        if len(content) > MAX_UPLOAD_BYTES:
            return {
                "success": False,
                "filename": file.filename,
                "error": (
                    f"File quá lớn. Kích thước tối đa "
                    f"{MAX_UPLOAD_MB} MB."
                ),
            }

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp:
            temp.write(content)
            temp_path = temp.name

        def do_upload():
            operation = (
                gemini_client
                .file_search_stores
                .upload_to_file_search_store(
                    file=temp_path,
                    file_search_store_name=store_name(),
                    config={
                        "display_name": file.filename
                    },
                )
            )

            started = time.monotonic()

            while not operation.done:
                if (
                    time.monotonic() - started
                    > UPLOAD_OPERATION_TIMEOUT
                ):
                    raise TimeoutError(
                        "Gemini upload quá thời gian chờ."
                    )

                time.sleep(0.5)
                operation = gemini_client.operations.get(
                    operation
                )

            return operation

        operation = await asyncio.to_thread(do_upload)

        # Kho tài liệu đã thay đổi:
        # không được giữ câu trả lời cache cũ.
        await clear_answer_cache()

        return {
            "success": True,
            "filename": file.filename,
            "store": store_name(),
            "message": (
                "Đã đưa file vào Gemini File Search Store. "
                "Cache câu trả lời đã được làm mới."
            ),
            "operation": str(operation),
        }

    except Exception as e:
        print("UPLOAD ERROR:", repr(e))

        return {
            "success": False,
            "filename": file.filename,
            "error": str(e),
        }

    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass

        try:
            await file.close()
        except Exception:
            pass

# ============================================================
# IMAGE UPLOAD - BƯỚC 13B-1A
# Chỉ nhận ảnh + kiểm tra + tạo SHA-256.
# KHÔNG gọi Gemini.
# KHÔNG đưa ảnh vào File Search.
# ============================================================

@app.post("/image-upload")
async def image_upload(file: UploadFile = File(...)):
    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    max_image_bytes = 10 * 1024 * 1024  # 10 MB

    filename = Path(file.filename or "image").name
    content_type = (file.content_type or "").lower().strip()

    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP."
        )

    content = await file.read()

    if len(content) > max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail="Ảnh vượt quá giới hạn 10 MB."
        )

    image_hash = hashlib.sha256(content).hexdigest()

 print(
    "IMAGE RECEIVED | %s | %.2f KB | %s | SHA256=%s"
    % (
        filename,
        len(content) / 1024,
        content_type,
        image_hash,
    )
)

 return {
        "success": True,
        "status": "received",
        "filename": filename,
        "mime_type": content_type,
        "size_bytes": len(content),
        "image_hash": image_hash,
    }
# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
    )
