import os
import json
import asyncio
import shutil
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from notebooklm import NotebookLMClient


# =========================================================
# THỦY LỢI AI - CẤU HÌNH
# =========================================================

NOTEBOOK_ID = os.environ.get(
    "NOTEBOOKLM_NOTEBOOK",
    "e9719e1a-bd4a-45c9-a296-02136d6beb0e"
)

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

# Thư mục writable trên Render
RUNTIME_DIR = Path("/tmp/thuyloiai_notebooklm")
PROFILE_DIR = RUNTIME_DIR / "profiles" / "default"
STORAGE_FILE = PROFILE_DIR / "storage_state.json"

# Khóa để tránh nhiều request cùng lúc reconnect
RECONNECT_LOCK = asyncio.Lock()


# =========================================================
# CHUẨN BỊ STORAGE NOTEBOOKLM
# =========================================================

def prepare_storage():
    """
    Lấy NOTEBOOKLM_AUTH_JSON từ Render Environment
    và tạo storage_state.json ở thư mục writable.
    """

    auth_json = os.environ.get("NOTEBOOKLM_AUTH_JSON")

    if not auth_json:
        raise RuntimeError(
            "Thiếu biến môi trường NOTEBOOKLM_AUTH_JSON"
        )

    PROFILE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    try:
        data = json.loads(auth_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"NOTEBOOKLM_AUTH_JSON không phải JSON hợp lệ: {e}"
        )

    if "cookies" not in data:
        raise RuntimeError(
            "NOTEBOOKLM_AUTH_JSON không chứa trường cookies"
        )

    # Chỉ tạo lại file nếu chưa tồn tại.
    # Nhờ vậy cookie được NotebookLM xoay trong runtime
    # sẽ không bị ghi đè ở mỗi request.
    if not STORAGE_FILE.exists():

        STORAGE_FILE.write_text(
            json.dumps(
                data,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        print("ĐÃ TẠO STORAGE RUNTIME")

    # Ép notebooklm dùng storage runtime này
    os.environ["NOTEBOOKLM_HOME"] = str(RUNTIME_DIR)

    return STORAGE_FILE


# =========================================================
# TẠO CLIENT NOTEBOOKLM
# =========================================================

async def create_notebook_client():

    storage = prepare_storage()

    print("Đang tạo kết nối NotebookLM...")

    client = NotebookLMClient.from_storage(
        path=str(storage),

        # Timeout tổng thể
        timeout=180,

        # Giữ session sống
        keepalive=600,

        # Cho phép retry lỗi mạng / server
        server_error_max_retries=3,

        # Retry khi bị rate limit
        rate_limit_max_retries=3,

        # Giới hạn vừa phải cho Render Free
        max_concurrent_rpcs=8,
    )

    return client


# =========================================================
# KẾT NỐI NOTEBOOKLM
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    print("====================================")
    print("       KHỞI ĐỘNG THỦY LỢI AI")
    print("====================================")

    app.state.notebooklm = None

    try:

        client = await create_notebook_client().__aenter__()

        app.state.notebooklm = client

        print("ĐÃ KẾT NỐI NOTEBOOKLM")
        print("Notebook ID:", NOTEBOOK_ID)
        print("Storage:", STORAGE_FILE)

    except Exception as e:

        print("====================================")
        print("LỖI KẾT NỐI NOTEBOOKLM")
        print("====================================")
        print(repr(e))

        app.state.notebooklm = None

    yield

    # -----------------------------------------------------
    # Đóng client khi Render shutdown
    # -----------------------------------------------------

    client = getattr(
        app.state,
        "notebooklm",
        None
    )

    if client is not None:

        try:
            await client.__aexit__(
                None,
                None,
                None
            )
        except Exception as e:
            print(
                "Lỗi khi đóng NotebookLM:",
                repr(e)
            )


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
# API INFO
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
# KẾT NỐI LẠI NOTEBOOKLM
# =========================================================

async def reconnect_notebooklm():

    async with RECONNECT_LOCK:

        current = getattr(
            app.state,
            "notebooklm",
            None
        )

        if current is not None:

            try:
                await current.notebooks.list()

                print(
                    "Kết nối NotebookLM hiện tại vẫn hoạt động."
                )

                return current

            except Exception:
                pass

        print("ĐANG KẾT NỐI LẠI NOTEBOOKLM...")

        old_client = getattr(
            app.state,
            "notebooklm",
            None
        )

        if old_client is not None:

            try:
                await old_client.__aexit__(
                    None,
                    None,
                    None
                )
            except Exception:
                pass

        try:

            client = await create_notebook_client().__aenter__()

            app.state.notebooklm = client

            print(
                "ĐÃ KẾT NỐI LẠI NOTEBOOKLM"
            )

            return client

        except Exception as e:

            print(
                "KẾT NỐI LẠI THẤT BẠI:",
                repr(e)
            )

            app.state.notebooklm = None

            return None


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
    # Lấy client
    # -----------------------------------------------------

    client = getattr(
        app.state,
        "notebooklm",
        None
    )

    # Nếu chưa có client → kết nối
    if client is None:

        client = await reconnect_notebooklm()

    if client is None:

        return {
            "status": "error",
            "answer": (
                "⚠️ THỦY LỢI AI chưa kết nối được "
                "kho dữ liệu NotebookLM.\n\n"
                "Vui lòng thử lại sau ít phút."
            )
        }

    # -----------------------------------------------------
    # Gửi câu hỏi
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

        error_text = str(e)

        print("====================================")
        print("LỖI NOTEBOOKLM")
        print("====================================")
        print(repr(e))

        # -------------------------------------------------
        # Nếu lỗi xác thực / kết nối:
        # kết nối lại 1 lần rồi thử lại câu hỏi
        # -------------------------------------------------

        auth_error_keywords = [
            "unauthenticated",
            "authentication",
            "401",
            "403",
            "expired",
            "invalid",
            "rejected",
            "token"
        ]

        is_auth_error = any(
            word.lower() in error_text.lower()
            for word in auth_error_keywords
        )

        if is_auth_error:

            print(
                "PHÁT HIỆN LỖI XÁC THỰC."
            )

            new_client = await reconnect_notebooklm()

            if new_client is not None:

                try:

                    result = await new_client.chat.ask(
                        NOTEBOOK_ID,
                        question
                    )

                    print(
                        "ĐÃ THỬ LẠI THÀNH CÔNG"
                    )

                    return {
                        "status": "ok",
                        "answer": result.answer
                    }

                except Exception as retry_error:

                    print(
                        "THỬ LẠI VẪN THẤT BẠI:",
                        repr(retry_error)
                    )

        # -------------------------------------------------
        # Trả lỗi thân thiện cho người dùng
        # -------------------------------------------------

        return {
            "status": "error",
            "answer": (
                "⚠️ Chưa lấy được câu trả lời từ "
                "kho dữ liệu THỦY LỢI.\n\n"
                "Hệ thống đang tự kết nối lại. "
                "Vui lòng thử lại sau ít phút."
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
