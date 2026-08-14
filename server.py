import os
import asyncio
import random
import re
import tempfile
import time
import hashlib
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "2")))
REQUEST_TIMEOUT = max(10, int(os.getenv("REQUEST_TIMEOUT", "60")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "2")))

# BẢO VỆ HÀNG ĐỢI
QUEUE_TIMEOUT = max(5, int(os.getenv("QUEUE_TIMEOUT", "30")))
MAX_QUESTION_LENGTH = max(100, int(os.getenv("MAX_QUESTION_LENGTH", "4000")))

# CACHE CÂU HỎI
# Cache nằm trong RAM của Render instance hiện tại.
# Chỉ cache câu trả lời thành công.
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").strip().lower() not in {
    "0", "false", "no", "off"
}
CACHE_TTL = max(60, int(os.getenv("CACHE_TTL", "3600")))
CACHE_MAX_ENTRIES = max(10, int(os.getenv("CACHE_MAX_ENTRIES", "200")))

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời dựa trên kho hồ sơ, tài liệu, quy định, quy trình và dữ liệu
đã được đưa vào Gemini File Search.

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
10. Ưu tiên ngắn gọn, chính xác, dễ hiểu, có căn cứ và phù hợp nghiệp vụ Thủy lợi.
11. Với quy trình, có thể trình bày theo từng bước.
12. Với số liệu, giữ nguyên đơn vị và số liệu theo tài liệu.
"""

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# LRU cache đơn giản cho một Render instance.
answer_cache = OrderedDict()
cache_hits = 0
cache_misses = 0
cache_lock = asyncio.Lock()
CACHE_VERSION = "v1"

# Chống nhiều người cùng gọi Gemini cho cùng một câu hỏi tại cùng thời điểm.
inflight_locks = {}
inflight_locks_guard = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    print("=" * 60)
    print("KHỞI ĐỘNG THỦY LỢI AI")
    print("=" * 60)
    print("KIỂM TRA GEMINI...")

    if not GEMINI_API_KEY:
        print("GEMINI API: CHƯA CÓ API KEY")
    else:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            print("GEMINI API: ĐÃ KẾT NỐI")
        except Exception as e:
            gemini_client = None
            print("GEMINI API: LỖI KHỞI TẠO:", repr(e))

    print("FILE SEARCH STORE:", GEMINI_FILE_SEARCH_STORE or "CHƯA CẤU HÌNH")
    print("MODEL:", GEMINI_MODEL)
    print("MAX CONCURRENT:", MAX_CONCURRENT)
    print("REQUEST TIMEOUT:", REQUEST_TIMEOUT)
    print("MAX RETRIES:", MAX_RETRIES)
    print("=" * 60)

    yield

    gemini_client = None
    print("THỦY LỢI AI ĐÃ DỪNG")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str

    # Bộ lọc metadata tùy chọn, ví dụ:
    # loai="quy_trinh"
    # cong_trinh="tu_cau"
    # nam=2026
    # Chỉ áp dụng khi frontend/admin truyền rõ bộ lọc.
    metadata_filter: str | None = None


def require_gemini():
    if gemini_client is None:
        raise RuntimeError("Gemini API chưa được kết nối.")
    if not GEMINI_FILE_SEARCH_STORE:
        raise RuntimeError("Gemini File Search Store chưa được cấu hình.")


def store_name():
    return GEMINI_FILE_SEARCH_STORE.strip()


@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
    }


@app.get("/health")
async def health():
    """
    Health check thực tế của THỦY LỢI AI.

    Kiểm tra:
    1. GEMINI_API_KEY
    2. Gemini client
    3. Gemini File Search Store
    4. Khả năng thực tế truy cập và đọc Store
    """

    result = {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,

        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_connected": gemini_client is not None,
        # Giữ tên này để frontend kiểm tra trạng thái thống nhất.
        "gemini_client_ready": gemini_client is not None,

        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_ready": False,
        "documents_count": 0,

        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
        "queue_timeout": QUEUE_TIMEOUT,
        "max_question_length": MAX_QUESTION_LENGTH,
        "cache_enabled": CACHE_ENABLED,
        "cache_ttl": CACHE_TTL,
        "cache_max_entries": CACHE_MAX_ENTRIES,
        "metadata_filter_supported": True,
    }

    # 1. Kiểm tra API key
    if not GEMINI_API_KEY:
        result["status"] = "error"
        result["message"] = "Chưa cấu hình GEMINI_API_KEY."
        return result

    # 2. Kiểm tra Gemini client
    if gemini_client is None:
        result["status"] = "error"
        result["message"] = "Gemini client chưa được khởi tạo."
        return result

    # 3. Kiểm tra File Search Store
    if not GEMINI_FILE_SEARCH_STORE:
        result["status"] = "error"
        result["message"] = "Chưa cấu hình GEMINI_FILE_SEARCH_STORE."
        return result

    # 4. Kiểm tra thực tế Store bằng cách đọc danh sách tài liệu.
    try:
        documents = await asyncio.to_thread(list_documents_sync)
        result["file_search_ready"] = True
        result["documents_count"] = len(documents)

    except Exception as e:
        print("HEALTH FILE SEARCH ERROR:", repr(e))
        result["status"] = "error"
        result["message"] = (
            "Gemini đã kết nối nhưng không truy cập được "
            "File Search Store."
        )
        result["health_error"] = str(e)
        return result

    result["message"] = "THỦY LỢI AI hoạt động bình thường."
    return result


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "endpoints": {
            "home": "/",
            "health": "/health",
            "ask": "/ask",
            "stores": "/stores",
            "documents": "/documents",
            "pdf_documents": "/documents/pdf",
            "delete_pdf": "/documents/pdf",
            "upload": "/upload",
            "cache": "/cache",
            "metadata": "/metadata",
            "clear_cache": "/cache",
            "protection": "queue + retry + cache stampede",
            "metadata_filter": "optional on /ask",
        },
    }


@app.get("/metadata")
async def metadata_info():
    """Thông tin về metadata/filter đang được hỗ trợ."""
    return {
        "success": True,
        "supported_fields": [
            "loai",
            "cong_trinh",
            "nam",
            "don_vi",
            "hieu_luc",
            "ten_file",
        ],
        "examples": [
            'loai="quy_trinh"',
            'cong_trinh="tu_cau"',
            'nam=2026',
            'loai="quy_trinh" AND cong_trinh="tu_cau"',
        ],
        "note": (
            "Metadata chỉ được gắn cho tài liệu khi tài liệu được upload/import "
            "với custom_metadata. 33 tài liệu hiện có không tự được gắn lại."
        ),
    }


@app.get("/cache")
async def get_cache():
    """Xem trạng thái cache câu hỏi."""
    return {
        "success": True,
        **(await cache_info()),
    }


@app.delete("/cache")
async def clear_cache():
    """Xóa toàn bộ cache câu hỏi."""
    await clear_answer_cache()
    return {
        "success": True,
        "message": "Đã xóa toàn bộ cache câu hỏi.",
        **(await cache_info()),
    }


def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()

    permanent = [
        "400", "401", "403", "bad request",
        "unauthenticated", "permission denied",
        "api key", "invalid argument", "not found",
    ]
    if any(x in text for x in permanent):
        return False

    retryable = [
        "429", "500", "502", "503", "504",
        "rate limit", "resource exhausted", "unavailable",
        "timeout", "deadline", "temporarily", "internal",
        "connection", "reset", "server error",
    ]
    return any(x in text for x in retryable)


def normalize_question(question: str) -> str:
    """Chuẩn hóa câu hỏi để khác biệt khoảng trắng không tạo cache mới."""
    return " ".join(str(question or "").strip().lower().split())


def make_cache_key(
    question: str,
    metadata_filter: str | None = None,
) -> str:
    """
    Key phụ thuộc model + File Search Store + metadata filter + câu hỏi.
    Hai câu hỏi giống nhau nhưng dùng bộ lọc khác nhau không dùng chung cache.
    """
    normalized_filter = " ".join(
        str(metadata_filter or "").strip().split()
    )

    raw = (
        f"{CACHE_VERSION}|{GEMINI_MODEL}|"
        f"{store_name()}|{normalized_filter}|"
        f"{normalize_question(question)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_inflight_lock(key: str):
    """Lấy lock riêng cho từng câu hỏi."""
    async with inflight_locks_guard:
        lock = inflight_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            inflight_locks[key] = lock
        return lock


async def get_cached_answer(question: str, metadata_filter: str | None = None):
    """Lấy câu trả lời còn hạn từ cache."""
    global cache_hits, cache_misses

    if not CACHE_ENABLED:
        return None

    key = make_cache_key(question, metadata_filter)
    now = time.monotonic()

    async with cache_lock:
        item = answer_cache.get(key)

        if item is None:
            cache_misses += 1
            return None

        if now - item.get("created_at", 0) > CACHE_TTL:
            answer_cache.pop(key, None)
            cache_misses += 1
            return None

        answer_cache.move_to_end(key)
        cache_hits += 1

        return {
            "answer": item["answer"],
            "sources": list(item.get("sources", [])),
        }


async def set_cached_answer(question: str, answer: str, sources, metadata_filter: str | None = None):
    """Lưu một câu trả lời thành công vào cache."""
    if not CACHE_ENABLED:
        return

    key = make_cache_key(question, metadata_filter)

    async with cache_lock:
        answer_cache[key] = {
            "created_at": time.monotonic(),
            "answer": answer,
            "sources": list(sources or []),
        }
        answer_cache.move_to_end(key)

        while len(answer_cache) > CACHE_MAX_ENTRIES:
            answer_cache.popitem(last=False)


async def clear_answer_cache():
    """Xóa toàn bộ cache khi kho tài liệu thay đổi."""
    async with cache_lock:
        answer_cache.clear()


async def cache_info():
    async with cache_lock:
        return {
            "enabled": CACHE_ENABLED,
            "entries": len(answer_cache),
            "max_entries": CACHE_MAX_ENTRIES,
            "ttl_seconds": CACHE_TTL,
            "hits": cache_hits,
            "misses": cache_misses,
        }


def call_gemini(question: str, metadata_filter: str | None = None):
    require_gemini()

    file_search_tool = {
        "type": "file_search",
        "file_search_store_names": [store_name()],
    }

    if metadata_filter:
        file_search_tool["metadata_filter"] = metadata_filter

    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[file_search_tool],
    )


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

                # Gemini có thể trả custom metadata trong citation.
                custom_metadata = getattr(annotation, "custom_metadata", None)

                if custom_metadata:
                    try:
                        item["metadata"] = custom_metadata
                    except Exception:
                        pass

                if item and item not in sources:
                    sources.append(item)

    answer = answer.strip()

    if not answer:
        raise RuntimeError("Gemini không trả về nội dung.")

    return answer, sources


async def ask_gemini_with_retry(
    question: str,
    metadata_filter: str | None = None,
):
    """
    Gọi Gemini có kiểm soát tải.
    - Chờ slot tối đa QUEUE_TIMEOUT giây.
    - Không giữ slot trong lúc chờ retry.
    - Retry lỗi tạm thời bằng exponential backoff + jitter.
    """
    last_error = None

    for attempt in range(MAX_RETRIES):
        acquired = False

        try:
            try:
                await asyncio.wait_for(
                    request_semaphore.acquire(),
                    timeout=QUEUE_TIMEOUT,
                )
                acquired = True
            except asyncio.TimeoutError:
                raise RuntimeError(
                    "Hệ thống đang có nhiều yêu cầu. "
                    "Vui lòng thử lại sau ít giây."
                )

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        call_gemini,
                        question,
                        metadata_filter,
                    ),
                    timeout=REQUEST_TIMEOUT,
                )
            finally:
                if acquired:
                    request_semaphore.release()
                    acquired = False

            return extract_answer_and_sources(result)

        except Exception as e:
            if acquired:
                request_semaphore.release()

            last_error = e

            print(
                f"GEMINI ERROR attempt={attempt + 1}/{MAX_RETRIES}: "
                f"{repr(e)}"
            )

            retryable = is_retryable_error(e)
            print("RETRYABLE:", retryable)

            if not retryable or attempt >= MAX_RETRIES - 1:
                break

            delay = min(8.0, 2.0 ** attempt) + random.uniform(0.2, 0.8)

            print(
                f"THỬ LẠI LẦN {attempt + 2}/{MAX_RETRIES} "
                f"SAU {delay:.1f} GIÂY..."
            )

            await asyncio.sleep(delay)

    raise last_error or RuntimeError(
        "Gemini không thể xử lý câu hỏi."
    )


@app.post("/ask")
async def ask(data: Question):
    question = data.question.strip()
    metadata_filter = (
        data.metadata_filter.strip()
        if data.metadata_filter
        else None
    )

    if metadata_filter and not metadata_filter_is_safe(metadata_filter):
        return {
            "status": "error",
            "answer": "Bộ lọc tài liệu không hợp lệ hoặc quá dài.",
        }

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
                f"Câu hỏi quá dài. Vui lòng giới hạn "
                f"trong {MAX_QUESTION_LENGTH} ký tự."
            ),
        }

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
                "THỦY LỢI AI chưa có kho dữ liệu Gemini File Search."
            ),
        }

    try:
        # 1. Kiểm tra cache trước.
        cached = await get_cached_answer(question, metadata_filter)

        if cached is not None:
            print("CACHE HIT - TRẢ CÂU TRẢ LỜI TỪ CACHE")

            response = {
                "status": "ok",
                "answer": cached["answer"],
                "engine": "Gemini File Search",
                "model": GEMINI_MODEL,
                "cached": True,
                "metadata_filter": metadata_filter,
            }

            if cached["sources"]:
                response["sources"] = cached["sources"]

            return response

        # 2. Chống cache stampede:
        # cùng một câu hỏi tại cùng thời điểm chỉ một request gọi Gemini.
        key = make_cache_key(question, metadata_filter)
        question_lock = await get_inflight_lock(key)

        async with question_lock:
            # Request này có thể đã chờ một request khác xử lý xong.
            # Kiểm tra cache lại trước khi gọi Gemini.
            cached = await get_cached_answer(question, metadata_filter)

            if cached is not None:
                print("CACHE HIT SAU KHI CHỜ LOCK")

                response = {
                    "status": "ok",
                    "answer": cached["answer"],
                    "engine": "Gemini File Search",
                    "model": GEMINI_MODEL,
                    "cached": True,
                }

                if cached["sources"]:
                    response["sources"] = cached["sources"]

                return response

            print("CACHE MISS - ĐANG GỬI CÂU HỎI GEMINI...")

            answer, sources = await ask_gemini_with_retry(question, metadata_filter)

            print("ĐÃ NHẬN CÂU TRẢ LỜI GEMINI")

            await set_cached_answer(question, answer, sources, metadata_filter)

            response = {
                "status": "ok",
                "answer": answer,
                "engine": "Gemini File Search",
                "model": GEMINI_MODEL,
                "cached": False,
                "metadata_filter": metadata_filter,
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
                "từ kho dữ liệu Gemini. Hệ thống đã tự kiểm tra và "
                "thử lại khi có thể. Vui lòng thử lại sau ít giây."
            ),
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
        }


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

    # Gemini giới hạn tối đa 20 Document mỗi trang.
    # SDK pager sẽ tự đi qua các trang còn lại.
    pager = gemini_client.file_search_stores.documents.list(
        parent=store_name(),
        config={"page_size": 20},
    )

    for doc in pager:
        documents.append(serialize_document(doc))

    return documents


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
        return {"success": False, "error": str(e)}


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
            print("PDF DELETE ERROR:", doc["name"], repr(e))
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
        deleted, failed = await asyncio.to_thread(delete_pdf_documents_sync)

        if deleted:
            await clear_answer_cache()

        return {
            "success": len(failed) == 0,
            "store": store_name(),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "deleted": deleted,
            "failed": failed,
            "cache_cleared": bool(deleted),
            "message": (
                f"Đã xóa {len(deleted)} PDF. "
                f"Còn lỗi: {len(failed)}."
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


def clean_metadata_key(value: str) -> str:
    """Chuẩn hóa key metadata theo dạng chữ/số/gạch dưới."""
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_]", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:64]


def clean_metadata_value(value: str) -> str:
    """Chuẩn hóa giá trị metadata để tránh chuỗi quá dài."""
    return " ".join((value or "").strip().split())[:256]


def build_custom_metadata(
    filename: str,
    loai: str = "",
    cong_trinh: str = "",
    nam: str = "",
    don_vi: str = "",
    hieu_luc: str = "",
):
    """
    Tạo custom_metadata cho Gemini File Search.

    Các trường đều tùy chọn. Nếu frontend chưa gửi metadata,
    hệ thống vẫn upload bình thường.
    """
    items = []

    def add_string(key: str, value: str):
        value = clean_metadata_value(value)
        if value:
            items.append({
                "key": clean_metadata_key(key),
                "string_value": value,
            })

    def add_numeric(key: str, value: str):
        value = (value or "").strip()
        if not value:
            return
        try:
            number = int(value)
        except ValueError:
            return
        items.append({
            "key": clean_metadata_key(key),
            "numeric_value": number,
        })

    # Metadata người quản trị truyền vào.
    add_string("loai", loai)
    add_string("cong_trinh", cong_trinh)
    add_numeric("nam", nam)
    add_string("don_vi", don_vi)
    add_string("hieu_luc", hieu_luc)

    # Luôn lưu tên file để hỗ trợ truy vết.
    add_string("ten_file", filename)

    return items


def metadata_filter_is_safe(metadata_filter: str | None) -> bool:
    """
    Kiểm tra sơ bộ metadata_filter.
    Đây không phải bộ phân tích cú pháp đầy đủ của AIP-160;
    mục tiêu là chặn ký tự điều khiển và chuỗi quá dài.
    """
    if not metadata_filter:
        return True

    value = metadata_filter.strip()

    if len(value) > 500:
        return False

    forbidden = [";", "\\n", "\\r", "\\x00"]
    return not any(item in value for item in forbidden)


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    loai: str = Form(""),
    cong_trinh: str = Form(""),
    nam: str = Form(""),
    don_vi: str = Form(""),
    hieu_luc: str = Form(""),
):
    """Upload một file vào File Search Store hiện tại."""
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

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp:
            temp.write(content)
            temp_path = temp.name

        custom_metadata = build_custom_metadata(
            filename=file.filename,
            loai=loai,
            cong_trinh=cong_trinh,
            nam=nam,
            don_vi=don_vi,
            hieu_luc=hieu_luc,
        )

        upload_config = {
            "display_name": file.filename,
        }

        if custom_metadata:
            upload_config["custom_metadata"] = custom_metadata

        def do_upload():
            operation = (
                gemini_client.file_search_stores.upload_to_file_search_store(
                    file=temp_path,
                    file_search_store_name=store_name(),
                    config=upload_config,
                )
            )

            while not operation.done:
                operation = gemini_client.operations.get(operation)

            return operation

        operation = await asyncio.to_thread(do_upload)

        # Tài liệu đã thay đổi -> cache cũ có thể không còn đúng.
        await clear_answer_cache()

        return {
            "success": True,
            "filename": file.filename,
            "store": store_name(),
            "message": "Đã đưa file vào Gemini File Search Store.",
            "operation": str(operation),
            "custom_metadata": custom_metadata,
            "cache_cleared": True,
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
    )
