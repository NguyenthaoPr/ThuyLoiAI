import os
import asyncio
import random
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "3")))
REQUEST_TIMEOUT = max(10, int(os.getenv("REQUEST_TIMEOUT", "60")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "3")))

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời dựa trên kho hồ sơ, tài liệu, quy định, quy trình và dữ liệu đã được đưa vào Gemini File Search.

NGUYÊN TẮC BẮT BUỘC:
1. Ưu tiên thông tin trong kho hồ sơ THỦY LỢI AI.
2. Không tự bịa số liệu, điều khoản, tên/số văn bản, ngày tháng, thông số kỹ thuật hoặc quy trình vận hành.
3. Nếu không tìm thấy đủ căn cứ, nói rõ: "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client
    print("====================================")
    print("       KHỞI ĐỘNG THỦY LỢI AI")
    print("====================================")
    print("KIỂM TRA GEMINI...")

    if not GEMINI_API_KEY:
        print("GEMINI API: CHƯA CÓ API KEY")
    else:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            print("GEMINI API: ĐÃ KẾT NỐI")
        except Exception as e:
            gemini_client = None
            print("GEMINI API: LỖI KHỞI TẠO", repr(e))

    print("FILE SEARCH STORE:", GEMINI_FILE_SEARCH_STORE or "CHƯA CẤU HÌNH")
    print("MODEL:", GEMINI_MODEL)
    print("MAX CONCURRENT:", MAX_CONCURRENT)
    print("REQUEST TIMEOUT:", REQUEST_TIMEOUT)
    print("MAX RETRIES:", MAX_RETRIES)
    print("====================================")
    yield
    gemini_client = None
    print("THỦY LỢI AI ĐÃ DỪNG")

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="3.0",
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

@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(
            str(INDEX_FILE),
            media_type="text/html"
        )

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "message": "Backend Gemini đang hoạt động nhưng chưa tìm thấy index.html.",
        "health": "/health",
        "ask": "/ask"
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_connected": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "model": GEMINI_MODEL,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }

@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "endpoints": {"home": "/", "health": "/health", "ask": "/ask"},
    }

def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()
    permanent = ["400", "401", "403", "bad request", "unauthenticated", "permission denied", "api key", "invalid argument"]
    if any(x in text for x in permanent):
        return False
    retryable = ["429", "500", "502", "503", "504", "rate limit", "resource exhausted", "unavailable", "timeout", "deadline", "temporarily", "internal", "connection", "reset", "server error"]
    return any(x in text for x in retryable)

def call_gemini(question: str):
    if gemini_client is None:
        raise RuntimeError("Gemini API chưa được kết nối.")
    if not GEMINI_FILE_SEARCH_STORE:
        raise RuntimeError("Gemini File Search Store chưa được cấu hình.")

    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[{
            "type": "file_search",
            "file_search_store_names": [GEMINI_FILE_SEARCH_STORE],
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
                answer += (getattr(block, "text", "") or "")
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
            delay = min(10, 2 ** attempt) + random.uniform(0, 0.5)
            print(f"THỬ LẠI LẦN {attempt + 2}/{MAX_RETRIES} SAU {delay:.1f} GIÂY...")
            await asyncio.sleep(delay)
    raise last_error or RuntimeError("Gemini không thể xử lý câu hỏi.")

@app.post("/ask")
async def ask(data: Question):
    question = data.question.strip()
    print("====================================")
    print("CÂU HỎI:", question)
    print("====================================")

    if not question:
        return {"status": "error", "answer": "Vui lòng nhập câu hỏi."}
    if not GEMINI_API_KEY:
        return {"status": "error", "answer": "THỦY LỢI AI chưa được cấu hình Gemini API."}
    if gemini_client is None:
        return {"status": "error", "answer": "THỦY LỢI AI chưa kết nối được Gemini API. Vui lòng thử lại sau."}
    if not GEMINI_FILE_SEARCH_STORE:
        return {"status": "error", "answer": "THỦY LỢI AI chưa có kho dữ liệu Gemini File Search."}

    try:
        print("ĐANG GỬI CÂU HỎI GEMINI...")
        answer, sources = await ask_gemini_with_retry(question)
        print("ĐÃ NHẬN CÂU TRẢ LỜI GEMINI")
        response = {"status": "ok", "answer": answer, "engine": "Gemini File Search"}
        if sources:
            response["sources"] = sources
        return response
    except Exception as e:
        print("GEMINI KHÔNG TRẢ LỜI:", repr(e))
        return {
            "status": "error",
            "answer": "THỦY LỢI AI tạm thời chưa lấy được câu trả lời từ kho dữ liệu Gemini. Hệ thống đã tự thử lại. Vui lòng thử lại sau ít giây.",
            "engine": "Gemini File Search",
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
