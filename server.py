from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from notebooklm import NotebookLMClient


# ==========================================
# CẤU HÌNH THỦY LỢI AI
# ==========================================

NOTEBOOK_ID = "dea8fcc6-bc21-432c-b3f6-0619b619b5d2"


# ==========================================
# KẾT NỐI NOTEBOOKLM
# ==========================================

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


# ==========================================
# FASTAPI
# ==========================================

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    lifespan=lifespan
)


# ==========================================
# CORS
# ==========================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "https://nguyenthaopr.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],

    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ==========================================
# DỮ LIỆU CÂU HỎI
# ==========================================

class Question(BaseModel):

    question: str


# ==========================================
# KIỂM TRA SERVER
# ==========================================

@app.get("/")
async def home():

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "message": "Server đang hoạt động"
    }


# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/health")
async def health():

    client = getattr(
        app.state,
        "notebooklm",
        None
    )

    return {
        "status": "ok",
        "notebooklm": client is not None
    }


# ==========================================
# HỎI NOTEBOOKLM
# ==========================================

@app.post("/ask")
async def ask(data: Question):

    question = data.question.strip()

    print("====================================")
    print("CÂU HỎI:", question)
    print("====================================")

    if not question:

        return {
            "answer": "Vui lòng nhập câu hỏi."
        }


    client = getattr(
        app.state,
        "notebooklm",
        None
    )


    if client is None:

        return {
            "answer":
                "THỦY LỢI AI chưa kết nối được NotebookLM."
        }


    try:

        result = await client.chat.ask(
            NOTEBOOK_ID,
            question
        )


        print("ĐÃ NHẬN CÂU TRẢ LỜI")


        return {
            "answer": result.answer
        }


    except Exception as e:

        print("LỖI NOTEBOOKLM:")
        print(repr(e))


        return {
            "answer":
                "Không lấy được câu trả lời từ NotebookLM.\n\n"
                + str(e)
        }
