import os
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from notebooklm import NotebookLMClient


# ============================================================
# THỦY LỢI AI - NOTEBOOKLM BACKEND
# ============================================================

APP_NAME = "THỦY LỢI AI"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

NOTEBOOK_ID = os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "").strip()

MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "180"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

notebook_client: Optional[NotebookLMClient] = None
notebook_connected = False


# ============================================================
# LOG
# ============================================================

def log(message: str):
    print(f"[THỦY LỢI AI] {message}", flush=True)


# ============================================================
# KẾT NỐI NOTEBOOKLM
# ============================================================

async def connect_notebooklm():
    global notebook_client
    global notebook_connected

    try:
        log("=" * 60)
        log("KHỞI ĐỘNG THỦY LỢI AI")
        log("=" * 60)

        if not NOTEBOOK_ID:
            log("❌ Chưa có NOTEBOOKLM_NOTEBOOK")
            notebook_connected = False
            return False

        if not AUTH_JSON:
            log("❌ Chưa có NOTEBOOKLM_AUTH_JSON")
            notebook_connected = False
            return False

        log(f"NOTEBOOK: {NOTEBOOK_ID}")

        # notebooklm-py tự đọc NOTEBOOKLM_AUTH_JSON
        notebook_client = NotebookLMClient.from_storage(
            timeout=REQUEST_TIMEOUT,
            chat_timeout=REQUEST_TIMEOUT,
            rate_limit_max_retries=2,
            server_error_max_retries=2,
            max_concurrent_rpcs=MAX_CONCURRENT,
        )

        notebook_client = await notebook_client.__aenter__()

        # Kiểm tra Notebook có tồn tại
        notebooks = await notebook_client.notebooks.list()

        found = False

        for notebook in notebooks:
            if str(notebook.id) == NOTEBOOK_ID:
                found = True
                log(f"✅ Đã tìm thấy Notebook: {notebook.title}")
                break

        if not found:
            log("❌ Không tìm thấy NotebookLM_NOTEBOOK")
            notebook_connected = False
            return False

        notebook_connected = True

        log("✅ NOTEBOOKLM ĐÃ KẾT NỐI")
        log(f"MAX CONCURRENT: {MAX_CONCURRENT}")
        log(f"REQUEST TIMEOUT: {REQUEST_TIMEOUT}")
        log("=" * 60)

        return True

    except Exception as e:
        notebook_connected = False
        log(f"❌ LỖI KẾT NỐI NOTEBOOKLM: {e}")
        return False


# ============================================================
# ĐÓNG KẾT NỐI
# ============================================================

async def disconnect_notebooklm():
    global notebook_client
    global notebook_connected

    try:
        if notebook_client is not None:
            await notebook_client.__aexit__(None, None, None)

    except Exception as e:
        log(f"Lỗi đóng NotebookLM: {e}")

    notebook_client = None
    notebook_connected = False


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    await connect_notebooklm()

    yield

    await disconnect_notebooklm()


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    description="Trợ lý AI chuyên ngành Thủy lợi sử dụng NotebookLM",
    version="2.0",
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODEL
# ============================================================

class Question(BaseModel):
    question: str


# ============================================================
# TRANG CHỦ
# ============================================================

@app.get("/")
async def home():

    if os.path.exists(INDEX_FILE):
        return FileResponse(
            INDEX_FILE,
            media_type="text/html"
        )

    return {
        "status": "ok",
        "service": APP_NAME,
        "message": "THỦY LỢI AI đang hoạt động",
        "health": "/health",
        "ask": "/ask"
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "service": APP_NAME,
        "engine": "NotebookLM",
        "notebook_configured": bool(NOTEBOOK_ID),
        "auth_configured": bool(AUTH_JSON),
        "notebook_connected": notebook_connected,
        "notebook_id": NOTEBOOK_ID if NOTEBOOK_ID else None,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }


# ============================================================
# KẾT NỐI LẠI NOTEBOOKLM
# ============================================================

@app.get("/reconnect")
async def reconnect():

    global notebook_client

    try:

        await disconnect_notebooklm()

        success = await connect_notebooklm()

        if success:
            return {
                "status": "ok",
                "message": "Đã kết nối lại NotebookLM"
            }

        return {
            "status": "error",
            "message": "Không thể kết nối lại NotebookLM"
        }

    except Exception as e:

        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================
# HỎI NOTEBOOKLM
# ============================================================

async def ask_notebooklm(question: str):

    if not notebook_connected or notebook_client is None:

        raise RuntimeError(
            "THỦY LỢI AI chưa kết nối NotebookLM."
        )

    last_error = None

    for attempt in range(MAX_RETRIES):

        try:

            log(f"CÂU HỎI: {question}")
            log(f"THỬ LẦN {attempt + 1}/{MAX_RETRIES}")

            async with request_semaphore:

                result = await asyncio.wait_for(
                    notebook_client.chat.ask(
                        NOTEBOOK_ID,
                        question
                    ),
                    timeout=REQUEST_TIMEOUT
                )

            # AskResult thường có .answer
            answer = getattr(result, "answer", None)

            if answer is None:
                answer = getattr(result, "text", None)

            if answer is None:
                answer = str(result)

            answer = str(answer).strip()

            if not answer:
                raise RuntimeError(
                    "NotebookLM không trả về nội dung."
                )

            log("✅ ĐÃ NHẬN CÂU TRẢ LỜI")

            return answer

        except Exception as e:

            last_error = e

            log(
                f"⚠️ LỖI NOTEBOOKLM "
                f"(lần {attempt + 1}): {e}"
            )

            if attempt < MAX_RETRIES - 1:

                wait_time = 2 ** attempt

                log(
                    f"Chờ {wait_time} giây rồi thử lại..."
                )

                await asyncio.sleep(wait_time)

    raise RuntimeError(
        f"NotebookLM không trả lời sau "
        f"{MAX_RETRIES} lần: {last_error}"
    )


# ============================================================
# API HỎI ĐÁP
# ============================================================

@app.post("/ask")
async def ask(data: Question):

    question = data.question.strip()

    log("=" * 60)
    log(f"NHẬN CÂU HỎI: {question}")
    log("=" * 60)

    if not question:

        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi."
        }

    if not notebook_connected:

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa kết nối được NotebookLM. "
                "Hệ thống đang chờ khôi phục kết nối."
            ),
            "engine": "NotebookLM"
        }

    start_time = time.time()

    try:

        answer = await ask_notebooklm(question)

        elapsed = round(
            time.time() - start_time,
            2
        )

        return {
            "status": "ok",
            "answer": answer,
            "engine": "NotebookLM",
            "elapsed": elapsed
        }

    except Exception as e:

        log(f"❌ LỖI: {e}")

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI tạm thời chưa lấy được "
                "câu trả lời từ NotebookLM. "
                "Hệ thống đã tự thử kết nối lại."
            ),
            "engine": "NotebookLM",
            "error": str(e)
        }


# ============================================================
# API KIỂM TRA NOTEBOOK
# ============================================================

@app.get("/notebook")
async def notebook_info():

    if not notebook_connected or notebook_client is None:

        return {
            "status": "error",
            "message": "NotebookLM chưa kết nối."
        }

    try:

        notebooks = await notebook_client.notebooks.list()

        result = []

        for notebook in notebooks:

            result.append({
                "id": str(notebook.id),
                "title": getattr(
                    notebook,
                    "title",
                    ""
                )
            })

        return {
            "status": "ok",
            "active_notebook": NOTEBOOK_ID,
            "notebooks": result
        }

    except Exception as e:

        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================
# API ROOT STATUS
# ============================================================

@app.get("/status")
async def status():

    return {
        "service": APP_NAME,
        "engine": "NotebookLM",
        "connected": notebook_connected,
        "notebook": NOTEBOOK_ID,
        "ready": (
            notebook_connected
            and notebook_client is not None
        )
    }


# ============================================================
# CHẠY SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv("PORT", "10000")
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        workers=1
    )
