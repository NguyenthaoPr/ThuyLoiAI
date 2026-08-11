from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from notebooklm import NotebookLMClient


# =========================================================
# THỦY LỢI AI - CẤU HÌNH
# =========================================================

NOTEBOOK_ID = "dea8fcc6-bc21-432c-b3f6-0619b619b5d2"

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"


# =========================================================
# KẾT NỐI NOTEBOOKLM
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    print("====================================")
    print("       KHỞI ĐỘNG THỦY LỢI AI")
    print("====================================")

    try:

        async with NotebookLMClient.from_storage(
            timeout=180,
            keepalive=300
        ) as client:

            app.state.notebooklm = client

            print("ĐÃ KẾT NỐI NOTEBOOKLM")
            print("Notebook ID:", NOTEBOOK_ID)

            yield

    except Exception as e:

        print("LỖI KẾT NỐI NOTEBOOKLM:")
        print(repr(e))

        app.state.notebooklm = None

        yield


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
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

    # Nếu có index.html → mở giao diện THỦY LỢI AI
    if INDEX_FILE.exists():

        return FileResponse(
            INDEX_FILE,
            media_type="text/html"
        )

    # Nếu không tìm thấy index.html
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "message": "Server đang hoạt động nhưng chưa tìm thấy index.html"
    }


# =========================================================
# KIỂM TRA SERVER
# =========================================================

@app.get("/health")
async def health():

    client = getattr(
        app.state,
        "notebooklm",
        None
    )

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "notebooklm": client is not None,
        "notebook_id": NOTEBOOK_ID
    }


# =========================================================
# THÔNG TIN API
# =========================================================

@app.get("/api")
async def api_info():

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "message": "API đang hoạt động",
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

    question = data.question.strip()

    print("====================================")
    print("CÂU HỎI:", question)
    print("====================================")


    # -----------------------------------------------------
    # Kiểm tra câu hỏi
    # -----------------------------------------------------

    if not question:

        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi."
        }


    # -----------------------------------------------------
    # Lấy kết nối NotebookLM
    # -----------------------------------------------------

    client = getattr(
        app.state,
        "notebooklm",
        None
    )


    if client is None:

        print("NOTEBOOKLM CHƯA KẾT NỐI")

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa kết nối được NotebookLM.\n\n"
                "Vui lòng thử lại sau."
            )
        }


    # -----------------------------------------------------
    # Gửi câu hỏi tới NotebookLM
    # -----------------------------------------------------

    try:

        result = await client.chat.ask(
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


        return {
            "status": "error",
            "answer": (
                "Không lấy được câu trả lời từ NotebookLM.\n\n"
                + str(e)
            )
        }


# =========================================================
# CHẠY TRỰC TIẾP - KHÔNG BẮT BUỘC KHI DÙNG RENDER
# =========================================================

if __name__ == "__main__":

    import os
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
