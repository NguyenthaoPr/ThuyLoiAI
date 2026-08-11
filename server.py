import os
import asyncio
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
notebooklm_context = None

# Khóa để tránh nhiều request cùng lúc tạo nhiều kết nối
connection_lock = asyncio.Lock()


# =========================================================
# KẾT NỐI NOTEBOOKLM
# =========================================================

async def connect_notebooklm():

    global notebooklm_client
    global notebooklm_context

    async with connection_lock:

        # Nếu đã có kết nối thì dùng lại
        if notebooklm_client is not None:
            return notebooklm_client

        print("====================================")
        print("ĐANG KẾT NỐI NOTEBOOKLM...")
        print("====================================")

        try:

            # -------------------------------------------------
            # Tạo context manager
            # -------------------------------------------------

            context = NotebookLMClient.from_storage(
                timeout=180,
                keepalive=300,
                chat_timeout=180
            )

            # -------------------------------------------------
            # Trường hợp API trả về async context manager
            # -------------------------------------------------

            if hasattr(context, "__aenter__"):

                client = await context.__aenter__()

                notebooklm_context = context
                notebooklm_client = client

            # -------------------------------------------------
            # Trường hợp API trả về coroutine
            # -------------------------------------------------

            else:

                client = await context

                # Nếu kết quả vẫn là context manager
                if hasattr(client, "__aenter__"):

                    notebooklm_context = client
                    notebooklm_client = await client.__aenter__()

                else:

                    notebooklm_context = None
                    notebooklm_client = client

            # -------------------------------------------------
            # Thành công
            # -------------------------------------------------

            print("====================================")
            print("ĐÃ KẾT NỐI NOTEBOOKLM")
            print("Notebook ID:", NOTEBOOK_ID)
            print("====================================")

            return notebooklm_client

        except Exception as e:

            notebooklm_client = None
            notebooklm_context = None

            print("====================================")
            print("KẾT NỐI NOTEBOOKLM THẤT BẠI")
            print(repr(e))
            print("====================================")

            return None


# =========================================================
# ĐÓNG KẾT NỐI
# =========================================================

async def close_notebooklm():

    global notebooklm_client
    global notebooklm_context

    print("ĐANG ĐÓNG KẾT NỐI NOTEBOOKLM...")

    try:

        if notebooklm_context is not None:

            await notebooklm_context.__aexit__(
                None,
                None,
                None
            )

    except Exception as e:

        print("LỖI ĐÓNG NOTEBOOKLM:")
        print(repr(e))

    finally:

        notebooklm_client = None
        notebooklm_context = None

        print("ĐÃ ĐÓNG NOTEBOOKLM")


# =========================================================
# KẾT NỐI LẠI
# =========================================================

async def reconnect_notebooklm():

    print("====================================")
    print("ĐANG KẾT NỐI LẠI NOTEBOOKLM...")
    print("====================================")

    await close_notebooklm()

    return await connect_notebooklm()


# =========================================================
# FASTAPI LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    print()
    print("====================================")
    print("       KHỞI ĐỘNG THỦY LỢI AI")
    print("====================================")

    # -----------------------------------------------------
    # Kết nối NotebookLM
    # -----------------------------------------------------

    client = await connect_notebooklm()

    if client is None:

        print("⚠️ NOTEBOOKLM CHƯA KẾT NỐI")
        print("Server vẫn tiếp tục hoạt động.")

    else:

        print("✅ THỦY LỢI AI SẴN SÀNG")

    yield

    # -----------------------------------------------------
    # Shutdown
    # -----------------------------------------------------

    await close_notebooklm()


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
        "message": (
            "Server đang hoạt động "
            "nhưng chưa tìm thấy index.html"
        )
    }


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
async def health():

    connected = notebooklm_client is not None

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "notebooklm": connected,
        "notebook_id": NOTEBOOK_ID
    }


# =========================================================
# API INFO
# =========================================================

@app.get("/api")
async def api_info():

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "message": "API đang hoạt động",
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
    # Kiểm tra câu hỏi
    # -----------------------------------------------------

    if not question:

        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi."
        }


    # -----------------------------------------------------
    # Nếu chưa có kết nối → tự kết nối
    # -----------------------------------------------------

    client = notebooklm_client

    if client is None:

        print("NOTEBOOKLM CHƯA KẾT NỐI")
        print("ĐANG TỰ ĐỘNG KẾT NỐI...")

        client = await connect_notebooklm()

        if client is None:

            return {
                "status": "error",
                "answer": (
                    "THỦY LỢI AI chưa kết nối được "
                    "kho dữ liệu NotebookLM.\n\n"
                    "Vui lòng thử lại sau ít phút."
                )
            }


    # -----------------------------------------------------
    # Gửi câu hỏi
    # -----------------------------------------------------

    try:

        print("ĐANG GỬI CÂU HỎI NOTEBOOKLM...")

        result = await client.chat.ask(
            NOTEBOOK_ID,
            question
        )

        print("ĐÃ NHẬN CÂU TRẢ LỜI")

        return {
            "status": "ok",
            "answer": result.answer
        }


    # -----------------------------------------------------
    # Nếu phiên kết nối lỗi → kết nối lại 1 lần
    # -----------------------------------------------------

    except Exception as first_error:

        print("====================================")
        print("LỖI LẦN 1")
        print(repr(first_error))
        print("====================================")

        print("ĐANG THỬ KẾT NỐI LẠI...")


        # -------------------------------------------------
        # Kết nối lại
        # -------------------------------------------------

        client = await reconnect_notebooklm()


        if client is None:

            return {
                "status": "error",
                "answer": (
                    "Không thể kết nối NotebookLM.\n\n"
                    "Máy chủ đang thử khôi phục phiên "
                    "xác thực. Vui lòng thử lại sau."
                )
            }


        # -------------------------------------------------
        # Thử lại câu hỏi
        # -------------------------------------------------

        try:

            print("ĐANG GỬI LẠI CÂU HỎI...")

            result = await client.chat.ask(
                NOTEBOOK_ID,
                question
            )

            print("ĐÃ NHẬN CÂU TRẢ LỜI SAU KHI KẾT NỐI LẠI")

            return {
                "status": "ok",
                "answer": result.answer
            }


        except Exception as second_error:

            print("====================================")
            print("LỖI NOTEBOOKLM SAU KHI KẾT NỐI LẠI")
            print(repr(second_error))
            print("====================================")

            return {
                "status": "error",
                "answer": (
                    "Không lấy được câu trả lời từ "
                    "NotebookLM.\n\n"
                    "Chi tiết lỗi: "
                    + str(second_error)
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
