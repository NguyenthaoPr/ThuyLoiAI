import os
import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from notebooklm import NotebookLMClient


# ============================================================
# CẤU HÌNH
# ============================================================

APP_NAME = "THỦY LỢI AI"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

NOTEBOOK_ID = os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "").strip()

MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "180"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

notebook_client = None


# ============================================================
# LOG
# ============================================================

def log(text):
    print(f"[THUY LOI AI] {text}", flush=True)


# ============================================================
# KẾT NỐI NOTEBOOKLM
# ============================================================

async def create_notebook_client():

    global notebook_client

    if not AUTH_JSON:
        raise RuntimeError(
            "Thiếu biến NOTEBOOKLM_AUTH_JSON"
        )

    if not NOTEBOOK_ID:
        raise RuntimeError(
            "Thiếu biến NOTEBOOKLM_NOTEBOOK"
        )

    log("Đang kết nối NotebookLM...")

    client = NotebookLMClient.from_storage(
        timeout=30,
        chat_timeout=REQUEST_TIMEOUT,
    )

    notebook_client = await client.__aenter__()

    log("NotebookLM client đã khởi tạo.")

    # Kiểm tra Notebook
    notebooks = await notebook_client.notebooks.list()

    found = False
    notebook_title = ""

    for notebook in notebooks:

        if str(notebook.id) == NOTEBOOK_ID:

            found = True
            notebook_title = getattr(
                notebook,
                "title",
                ""
            )

            break

    if not found:

        await notebook_client.__aexit__(
            None,
            None,
            None
        )

        notebook_client = None

        raise RuntimeError(
            "Không tìm thấy NOTEBOOKLM_NOTEBOOK."
        )

    log(
        f"NotebookLM OK: {notebook_title}"
    )

    return True


# ============================================================
# ĐÓNG NOTEBOOKLM
# ============================================================

async def close_notebook_client():

    global notebook_client

    if notebook_client is not None:

        try:

            await notebook_client.__aexit__(
                None,
                None,
                None
            )

        except Exception as e:

            log(
                f"Lỗi đóng NotebookLM: {e}"
            )

    notebook_client = None


# ============================================================
# KHỞI ĐỘNG
# ============================================================

@asynccontextmanager
async def lifespan(app):

    log("=" * 60)
    log("KHỞI ĐỘNG THỦY LỢI AI")
    log("=" * 60)

    log(
        f"NOTEBOOK ID: "
        f"{NOTEBOOK_ID if NOTEBOOK_ID else 'CHƯA CÓ'}"
    )

    log(
        "AUTH JSON: "
        f"{'CÓ' if AUTH_JSON else 'KHÔNG CÓ'}"
    )

    try:

        await create_notebook_client()

        log("✅ NOTEBOOKLM ĐÃ SẴN SÀNG")

    except Exception as e:

        log(
            f"❌ KHÔNG KẾT NỐI ĐƯỢC NOTEBOOKLM: {e}"
        )

        notebook_client = None

    yield

    await close_notebook_client()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version="3.0",
    lifespan=lifespan
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
        "engine": "NotebookLM",
        "health": "/health",
        "ask": "/ask"
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {

        "status": "ok",

        "service": APP_NAME,

        "engine": "NotebookLM",

        "notebook_configured":
            bool(NOTEBOOK_ID),

        "auth_configured":
            bool(AUTH_JSON),

        "notebook_connected":
            notebook_client is not None,

        "notebook_id":
            NOTEBOOK_ID if NOTEBOOK_ID else None,

        "max_concurrent":
            MAX_CONCURRENT,

        "request_timeout":
            REQUEST_TIMEOUT,

        "max_retries":
            MAX_RETRIES
    }


# ============================================================
# RECONNECT
# ============================================================

@app.get("/reconnect")
async def reconnect():

    global notebook_client

    try:

        await close_notebook_client()

        await create_notebook_client()

        return {
            "status": "ok",
            "message":
                "Đã kết nối lại NotebookLM."
        }

    except Exception as e:

        notebook_client = None

        return {
            "status": "error",
            "message": str(e)
        }


# ============================================================
# HỎI NOTEBOOKLM
# ============================================================

async def ask_notebooklm(question):

    global notebook_client

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            # Nếu mất kết nối → tự kết nối lại
            if notebook_client is None:

                await create_notebook_client()

            log(
                f"Gửi câu hỏi lần "
                f"{attempt}/{MAX_RETRIES}"
            )

            async with request_semaphore:

                result = await asyncio.wait_for(

                    notebook_client.chat.ask(
                        NOTEBOOK_ID,
                        question
                    ),

                    timeout=REQUEST_TIMEOUT
                )

            answer = getattr(
                result,
                "answer",
                None
            )

            if not answer:

                raise RuntimeError(
                    "NotebookLM không trả về câu trả lời."
                )

            return answer.strip()

        except Exception as e:

            last_error = e

            log(
                f"⚠️ Lỗi lần {attempt}: {e}"
            )

            # Hủy client lỗi
            try:

                await close_notebook_client()

            except Exception:
                pass

            if attempt < MAX_RETRIES:

                wait_time = attempt * 2

                log(
                    f"Chờ {wait_time} giây..."
                )

                await asyncio.sleep(
                    wait_time
                )

    raise RuntimeError(
        f"NotebookLM thất bại sau "
        f"{MAX_RETRIES} lần: {last_error}"
    )


# ============================================================
# API ASK
# ============================================================

@app.post("/ask")
async def ask(data: Question):

    question = data.question.strip()

    if not question:

        return {
            "status": "error",
            "answer":
                "Vui lòng nhập câu hỏi."
        }

    log("=" * 60)
    log(f"CÂU HỎI: {question}")
    log("=" * 60)

    start = time.time()

    try:

        answer = await ask_notebooklm(
            question
        )

        elapsed = round(
            time.time() - start,
            2
        )

        log(
            f"✅ HOÀN THÀNH: {elapsed}s"
        )

        return {

            "status": "ok",

            "answer": answer,

            "engine": "NotebookLM",

            "elapsed": elapsed
        }

    except Exception as e:

        log(
            f"❌ KHÔNG LẤY ĐƯỢC TRẢ LỜI: {e}"
        )

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI tạm thời "
                "chưa kết nối được NotebookLM. "
                "Hệ thống đã tự thử khôi phục.",

            "engine": "NotebookLM"
        }


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():

    return {

        "service": APP_NAME,

        "engine": "NotebookLM",

        "connected":
            notebook_client is not None,

        "notebook":
            NOTEBOOK_ID,

        "ready":
            notebook_client is not None
            and bool(NOTEBOOK_ID)
    }


# ============================================================
# NOTEBOOK
# ============================================================

@app.get("/notebook")
async def notebook():

    if notebook_client is None:

        return {
            "status": "error",
            "message":
                "NotebookLM chưa kết nối."
        }

    try:

        notebooks = (
            await notebook_client.notebooks.list()
        )

        data = []

        for nb in notebooks:

            data.append({

                "id": str(nb.id),

                "title":
                    getattr(
                        nb,
                        "title",
                        ""
                    )
            })

        return {

            "status": "ok",

            "active_notebook":
                NOTEBOOK_ID,

            "notebooks":
                data
        }

    except Exception as e:

        return {

            "status": "error",

            "message": str(e)
        }


# ============================================================
# SERVER
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
        workers=1
    )
