import os
import asyncio
import random
import tempfile
import time
import hashlib
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File
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
    version="4.0",
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
        "cache_enabled": CACHE_ENABLED,
        "cache_ttl": CACHE_TTL,
        "cache_max_entries": CACHE_MAX_ENTRIES,
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
            "clear_cache": "/cache",
        },
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


def make_cache_key(question: str) -> str:
    """
    Key phụ thuộc model + File Search Store + phiên bản cache.
    Đổi model/store/CACHE_VERSION sẽ tự tạo cache key mới.
    """
    raw = (
        f"{CACHE_VERSION}|{GEMINI_MODEL}|"
        f"{store_name()}|{normalize_question(question)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_cached_answer(question: str):
    """Lấy câu trả lời còn hạn từ cache."""
    global cache_hits, cache_misses

    if not CACHE_ENABLED:
        return None

    key = make_cache_key(question)
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


async def set_cached_answer(question: str, answer: str, sources):
    """Lưu một câu trả lời thành công vào cache."""
    if not CACHE_ENABLED:
        return

    key = make_cache_key(question)

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


async def ask_gemini_with_retry(question: str):
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            async with request_semaphore:
                result = await asyncio.wait_for(
                    asyncio.to_thread(call_gemini, question),
                    timeout=REQUEST_TIMEOUT,
                )

            return extract_answer_and_sources(result)

        except Exception as e:
            last_error = e
            print("LỖI GEMINI:", repr(e))

            retryable = is_retryable_error(e)
            print("RETRYABLE:", retryable)

            if not retryable or attempt >= MAX_RETRIES - 1:
                break

            delay = min(8, 2 ** attempt) + random.uniform(0, 0.5)
            print(
                f"THỬ LẠI LẦN {attempt + 2}/{MAX_RETRIES} "
                f"SAU {delay:.1f} GIÂY..."
            )
            await asyncio.sleep(delay)

    raise last_error or RuntimeError("Gemini không thể xử lý câu hỏi.")


@app.post("/ask")
async def ask(data: Question):
    question = data.question.strip()

    print("=" * 60)
    print("CÂU HỎI:", question)
    print("=" * 60)

    if not question:
        return {"status": "error", "answer": "Vui lòng nhập câu hỏi."}

    if not GEMINI_API_KEY:
        return {
            "status": "error",
            "answer": "THỦY LỢI AI chưa được cấu hình Gemini API.",
        }

    if gemini_client is None:
        return {
            "status": "error",
            "answer": "THỦY LỢI AI chưa kết nối được Gemini API. Vui lòng thử lại sau.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "status": "error",
            "answer": "THỦY LỢI AI chưa có kho dữ liệu Gemini File Search.",
        }

    try:
        # ---------------------------------------------------------
        # CACHE: trả ngay nếu đã có câu trả lời còn hạn
        # ---------------------------------------------------------
        cached = await get_cached_answer(question)

        if cached is not None:
            print("CACHE HIT - TRẢ CÂU TRẢ LỜI TỪ CACHE")

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

        answer, sources = await ask_gemini_with_retry(question)

        print("ĐÃ NHẬN CÂU TRẢ LỜI GEMINI")

        # Chỉ cache câu trả lời thành công.
        await set_cached_answer(question, answer, sources)

        response = {
            "status": "ok",
            "answer": answer,
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
            "cached": False,
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
                "từ kho dữ liệu Gemini. Hệ thống đã tự thử lại. "
                "Vui lòng thử lại sau ít giây."
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


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
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

        def do_upload():
            operation = (
                gemini_client.file_search_stores.upload_to_file_search_store(
                    file=temp_path,
                    file_search_store_name=store_name(),
                    config={"display_name": file.filename},
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
