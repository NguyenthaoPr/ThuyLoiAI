import os
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai


# =========================================================
# THỦY LỢI AI V2
# Gemini File Search
# =========================================================

app = FastAPI(
    title="THỦY LỢI AI",
    version="2.0.0"
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# ENVIRONMENT
# ---------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE")


# ---------------------------------------------------------
# GEMINI CLIENT
# ---------------------------------------------------------

client = None

if GEMINI_API_KEY:
    client = genai.Client(
        api_key=GEMINI_API_KEY
    )


# ---------------------------------------------------------
# REQUEST
# ---------------------------------------------------------

class QueryRequest(BaseModel):
    question: str


# ---------------------------------------------------------
# SYSTEM INSTRUCTION
# ---------------------------------------------------------

SYSTEM_INSTRUCTION = """
Bạn là THỦY LỢI AI, trợ lý chuyên ngành Thủy lợi.

NGUYÊN TẮC:

1. Chỉ sử dụng thông tin tìm được trong Kho dữ liệu
   THỦY LỢI AI thông qua File Search.

2. Không được tự bịa số liệu.

3. Nếu không tìm thấy thông tin phù hợp,
   phải nói rõ:
   "Hệ thống chưa tìm thấy thông tin này
   trong Kho dữ liệu Thủy lợi."

4. Khi trả lời phải cố gắng nêu:
   - Tên tài liệu
   - Nội dung liên quan
   - Trang nếu hệ thống cung cấp

5. Trả lời bằng tiếng Việt.

6. Đối với thông tin kỹ thuật:
   ưu tiên thông tin trực tiếp từ hồ sơ,
   quy trình và tài liệu chính thức.

7. Không sử dụng NotebookLM.
"""


# ---------------------------------------------------------
# HOME
# ---------------------------------------------------------

@app.get("/")
def home():
    return {
        "service": "THỦY LỢI AI",
        "version": "2.0.0",
        "status": "online",
        "engine": "Gemini File Search"
    }


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():

    return {
        "status": "ok",
        "gemini": bool(GEMINI_API_KEY),
        "file_search": bool(FILE_SEARCH_STORE)
    }


# ---------------------------------------------------------
# ASK
# ---------------------------------------------------------

@app.post("/ask")
def ask(request: QueryRequest):

    if not GEMINI_API_KEY:
        return {
            "success": False,
            "error": "Chưa cấu hình GEMINI_API_KEY"
        }

    if not FILE_SEARCH_STORE:
        return {
            "success": False,
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE"
        }

    if not client:
        return {
            "success": False,
            "error": "Gemini Client chưa khởi tạo"
        }

    question = request.question.strip()

    if not question:
        return {
            "success": False,
            "error": "Câu hỏi trống"
        }

    prompt = f"""
{SYSTEM_INSTRUCTION}

CÂU HỎI NGƯỜI DÙNG:

{question}
"""

    # -----------------------------------------------------
    # RETRY
    # -----------------------------------------------------

    last_error = None

    for attempt in range(3):

        try:

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "system_instruction": SYSTEM_INSTRUCTION,
                    "tools": [
                        {
                            "file_search": {
                                "file_search_store_names": [
                                    FILE_SEARCH_STORE
                                ]
                            }
                        }
                    ]
                }
            )

            answer = response.text or ""

            return {
                "success": True,
                "answer": answer,
                "engine": "Gemini File Search"
            }

        except Exception as e:

            last_error = str(e)

            if attempt < 2:
                time.sleep(2 ** attempt)

    return {
        "success": False,
        "error": last_error or "Gemini API không phản hồi"
    }
