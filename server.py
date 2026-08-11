import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from notebooklm import NotebookLMClient


# =========================================================
# THỦY LỢI AI
# CẤU HÌNH
# =========================================================

NOTEBOOK_ID = os.environ.get(
    "NOTEBOOKLM_NOTEBOOK",
    "e9719e1a-bd4a-45c9-a296-02136d6beb0e"
)

BASE_DIR = Path(__file__).resolve().parent

INDEX_FILE = BASE_DIR / "index.html"


# =========================================================
# BIẾN TOÀN CỤC
# =========================================================

notebooklm_client = None


# =========================================================
# LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global notebooklm_client

    print()
    print("====================================")
    print("       KHỞI ĐỘNG THỦY LỢI AI")
    print("====================================")

    try:

        print("ĐANG KẾT NỐI NOTEBOOKLM...")

        # =================================================
        # KẾT NỐI NOTEBOOKLM
        # =================================================

        async with NotebookLMClient.from_storage(
            timeout=180,
            keepalive=300,
            chat_timeout=180
        ) as client:

            notebooklm_client = client

            print("====================================")
            print("ĐÃ KẾT NỐI NOTEBOOKLM")
            print("Notebook ID:", NOTEBOOK_ID)
            print("====================================")

            print("THỦY LỢI AI SẴN SÀNG")
            print()

            # =============================================
            # GIỮ KẾT NỐI TRONG SUỐT THỜI GIAN SERVER CHẠY
            # =============================================

            yield

    except Exception as e:

        notebooklm_client = None

        print("====================================")
        print("LỖI KẾT NỐI NOTEBOOKLM")
        print("====================================")

        print(repr(e))

        print("====================================")

        # Server vẫn chạy để /health và giao diện hoạt động

        yield

    finally:

        notebooklm_client = None

        print("====================================")
        print("THỦY LỢI AI ĐÃ DỪNG")
        print("====================================")


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="1.0.0",
    lifespan=lifespan
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "https://nguyenthaopr.github.io",
        "https://thuyloiai.onrender.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],

    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"],
)


# =========================================================
# DỮ LIỆU CÂU HỎI
# =========================================================

class Question(BaseModel):

    question: str


# =========================================================
# TRANG CHỦ
# =========================================================

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
        "message": "Server đang hoạt động"
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "notebooklm": notebooklm_client is not None,
        "notebook_id": NOTEBOOK_ID
    }


# =========================================================
# API
# =========================================================

@app.get("/api")
async def api_info():

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",

        "notebooklm": notebooklm_client is not None,

        "notebook_id": NOTEBOOK_ID,

        "endpoints": {
            "home": "/",
            "health": "/health",
            "ask": "/ask"
        }
    }


# =========================================================
# HỎI NOTEBOOKLM
# =========================================================

@app.post("/ask")
async def ask(data: Question):

    global notebooklm_client

    question = data.question.strip()

    print()
    print("====================================")
    print("CÂU HỎI:", question)
    print("====================================")

    # -----------------------------------------------------
    # KIỂM TRA CÂU HỎI
    # -----------------------------------------------------

    if not question:

        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi."
        }


    # -----------------------------------------------------
    # KIỂM TRA KẾT NỐI
    # -----------------------------------------------------

    if notebooklm_client is None:

        print("NOTEBOOKLM CHƯA KẾT NỐI")

        return {
            "status": "error",

            "answer": (
                "THỦY LỢI AI chưa kết nối được "
                "kho dữ liệu NotebookLM.\n\n"
                "Vui lòng thử lại sau ít phút."
            )
        }


    # -----------------------------------------------------
    # GỬI CÂU HỎI
    # -----------------------------------------------------

    try:

        print("ĐANG GỬI CÂU HỎI NOTEBOOKLM...")

        result = await notebooklm_client.chat.ask(
            NOTEBOOK_ID,
            question
        )

        print("ĐÃ NHẬN CÂU TRẢ LỜI")

        return {
            "status": "ok",
            "answer": result.answer
        }


    except Exception as e:

        print("====================================")
        print("LỖI NOTEBOOKLM")
        print("====================================")

        print(repr(e))

        print("====================================")

        return {
            "status": "error",

            "answer": (
                "Không lấy được câu trả lời từ NotebookLM.\n\n"
                + str(e)
            )
        }


# =========================================================
# CHẠY TRỰC TIẾP
# =========================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port
    )
