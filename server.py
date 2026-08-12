import os
import asyncio
import random
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Google GenAI SDK chính thức
try:
    from google import genai
    GOOGLE_GENAI_IMPORT_ERROR = None
except Exception as exc:
    genai = None
    GOOGLE_GENAI_IMPORT_ERROR = repr(exc)

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "2")))
REQUEST_TIMEOUT = max(15, int(os.getenv("REQUEST_TIMEOUT", "90")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "3")))

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

Hãy trả lời chủ yếu dựa trên kho Gemini File Search được cấu hình cho ứng dụng.

Quy tắc:
- Ưu tiên tuyệt đối tài liệu trong kho.
- Không tự bịa số liệu, điều khoản, số hiệu văn bản, ngày tháng, thông số kỹ thuật.
- Nếu kho không có đủ căn cứ, nói rõ: "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."
- Nếu có nguồn/citation, nêu nguồn khi phù hợp.
- Trả lời bằng tiếng Việt.
- Với quy trình, trình bày theo từng bước.
- Với số liệu, giữ nguyên đơn vị và số liệu của tài liệu.
- Không biến suy đoán thành kết luận chính thức.
"""

logger = logging.getLogger("thuyloiai")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    logger.info("====================================")
    logger.info("KHỞI ĐỘNG THỦY LỢI AI V18")
    logger.info("====================================")
    logger.info("Google GenAI import: %s", "OK" if genai else f"LỖI: {GOOGLE_GENAI_IMPORT_ERROR}")
    logger.info("GEMINI_API_KEY: %s", "ĐÃ CẤU HÌNH" if GEMINI_API_KEY else "THIẾU")
    logger.info("GEMINI_FILE_SEARCH_STORE: %s", GEMINI_FILE_SEARCH_STORE or "THIẾU")
    logger.info("MODEL: %s", GEMINI_MODEL)

    if genai and GEMINI_API_KEY:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info("Gemini client: ĐÃ KHỞI TẠO")
        except Exception as exc:
            gemini_client = None
            logger.exception("Gemini client: LỖI KHỞI TẠO: %r", exc)
    else:
        gemini_client = None

    yield

    gemini_client = None
    logger.info("THỦY LỢI AI ĐÃ DỪNG")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="18.0",
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
    question: str = Field(default="", max_length=20000)


def clean_store_name(value: str) -> str:
    """Cho phép nhập cả fileSearchStores/xxx hoặc chỉ xxx."""
    value = (value or "").strip()
    return value


def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()

    permanent = [
        "400", "401", "403",
        "bad request", "unauthenticated",
        "permission denied", "invalid argument",
        "api key", "not found",
    ]
    if any(x in text for x in permanent):
        return False

    retryable = [
        "429", "500", "502", "503", "504",
        "rate limit", "resource exhausted",
        "unavailable", "timeout", "deadline",
        "temporarily", "internal", "connection",
        "reset", "server error",
    ]
    return any(x in text for x in retryable)


def call_gemini(question: str):
    if gemini_client is None:
        raise RuntimeError("Gemini client chưa được khởi tạo.")

    store = clean_store_name(GEMINI_FILE_SEARCH_STORE)
    if not store:
        raise RuntimeError("GEMINI_FILE_SEARCH_STORE chưa được cấu hình.")

    # Đây là cú pháp File Search chính thức của Google GenAI SDK.
    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[
            {
                "type": "file_search",
                "file_search_store_names": [store],
            }
        ],
    )


def extract_answer_and_sources(result):
    answer = (getattr(result, "output_text", None) or "").strip()
    sources = []

    # API/SDK hiện tại có thể cung cấp citations trong steps.
    for step in (getattr(result, "steps", None) or []):
        if getattr(step, "type", None) != "model_output":
            continue

        for block in (getattr(step, "content", None) or []):
            if getattr(block, "type", None) == "text":
                if not answer:
                    answer = (getattr(block, "text", "") or "").strip()

                for annotation in (getattr(block, "annotations", None) or []):
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

    if not answer:
        raise RuntimeError("Gemini không trả về nội dung.")

    return answer, sources


async def ask_gemini(question: str):
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            async with request_semaphore:
                result = await asyncio.wait_for(
                    asyncio.to_thread(call_gemini, question),
                    timeout=REQUEST_TIMEOUT,
                )

            return extract_answer_and_sources(result)

        except Exception as exc:
            last_error = exc
            retryable = is_retryable_error(exc)

            logger.warning(
                "Gemini attempt %s/%s | retryable=%s | %r",
                attempt + 1,
                MAX_RETRIES,
                retryable,
                exc,
            )

            if not retryable or attempt >= MAX_RETRIES - 1:
                break

            delay = min(15, 2 ** attempt) + random.uniform(0, 0.5)
            logger.info("Tự thử lại sau %.1f giây...", delay)
            await asyncio.sleep(delay)

    raise last_error or RuntimeError("Gemini không thể xử lý câu hỏi.")


def error_response(message: str, status_code: int = 200):
    # Trả JSON ổn định để frontend cũ/new đều có thể đọc.
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "ok": False,
            "answer": message,
            "reply": message,
            "message": message,
            "engine": "Gemini File Search",
        },
    )


@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": "V18",
        "engine": "Gemini File Search",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": "V18",
        "google_genai_imported": genai is not None,
        "google_genai_import_error": GOOGLE_GENAI_IMPORT_ERROR,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_client_ready": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_store": GEMINI_FILE_SEARCH_STORE,
        "model": GEMINI_MODEL,
    }


@app.get("/diagnostics")
async def diagnostics():
    return {
        "service": "THỦY LỢI AI",
        "server_version": "V18",
        "google_genai_imported": genai is not None,
        "google_genai_import_error": GOOGLE_GENAI_IMPORT_ERROR,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_client_ready": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_store": GEMINI_FILE_SEARCH_STORE,
        "model": GEMINI_MODEL,
        "endpoints": ["/", "/health", "/diagnostics", "/ask", "/api/ask"],
    }


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": "V18",
        "ask_endpoints": ["/ask", "/api/ask"],
    }


async def process_question(data: Question):
    question = (data.question or "").strip()

    logger.info("CÂU HỎI: %s", question)

    if not question:
        return error_response("Vui lòng nhập câu hỏi.")

    if not GEMINI_API_KEY:
        return error_response("Render chưa có GEMINI_API_KEY.")

    if gemini_client is None:
        return error_response(
            "Gemini client chưa sẵn sàng. Kiểm tra google-genai và GEMINI_API_KEY."
        )

    if not GEMINI_FILE_SEARCH_STORE:
        return error_response("Render chưa có GEMINI_FILE_SEARCH_STORE.")

    try:
        logger.info("ĐANG GỬI CÂU HỎI GEMINI FILE SEARCH...")
        answer, sources = await ask_gemini(question)

        logger.info("ĐÃ NHẬN CÂU TRẢ LỜI GEMINI")

        payload = {
            "status": "ok",
            "ok": True,
            "answer": answer,
            "reply": answer,
            "message": answer,
            "engine": "Gemini File Search",
            "version": "V18",
        }

        if sources:
            payload["sources"] = sources

        return payload

    except Exception as exc:
        logger.exception("GEMINI KHÔNG TRẢ LỜI: %r", exc)

        # Không trả 503 để frontend cũ không bị treo/hiển thị lỗi chung.
        return error_response(
            "THỦY LỢI AI chưa lấy được câu trả lời từ kho Gemini File Search. "
            "Hệ thống đã tự thử lại. Hãy xem /diagnostics và Render Logs để kiểm tra.",
            status_code=200,
        )


@app.post("/ask")
async def ask(data: Question):
    return await process_question(data)


# Alias để tương thích với frontend đang gọi /api/ask.
@app.post("/api/ask")
async def api_ask(data: Question):
    return await process_question(data)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
