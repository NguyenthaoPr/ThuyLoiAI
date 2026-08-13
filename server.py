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
    version="2.1.0"
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# ENVIRONMENT
# =========================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE")


# =========================================================
# GEMINI CLIENT
# =========================================================

client = None

if GEMINI_API_KEY:
    client = genai.Client(
        api_key=GEMINI_API_KEY
    )


# =========================================================
# REQUEST
# =========================================================

class QueryRequest(BaseModel):
    question: str


# =========================================================
# SYSTEM INSTRUCTION
# =========================================================

SYSTEM_INSTRUCTION = """
Bạn là THỦY LỢI AI,
trợ lý chuyên gia về lĩnh vực Thủy lợi.

NHIỆM VỤ:
Trả lời câu hỏi dựa trên Kho dữ liệu Thủy lợi
được cung cấp thông qua Gemini File Search.

NGUYÊN TẮC:

1. Ưu tiên tuyệt đối thông tin trong Kho dữ liệu.

2. Không tự bịa số liệu, tên công trình,
   quy định hoặc thông tin kỹ thuật.

3. Nếu không tìm thấy căn cứ trong Kho dữ liệu,
   phải nói:

   "Hệ thống chưa tìm thấy thông tin này
   trong Kho dữ liệu Thủy lợi."

4. Khi có nguồn, cố gắng nêu:
   - Tên tài liệu
   - Điều/Khoản nếu có
   - Trang nếu có

5. Trả lời bằng tiếng Việt.

6. Đối với hồ sơ công trình,
   ưu tiên thông số và tài liệu chính thức.

7. Không sử dụng NotebookLM.

8. Trả lời ngắn gọn, rõ ràng,
   phù hợp với cán bộ kỹ thuật Thủy lợi.
"""


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return {
        "service": "THỦY LỢI AI",
        "version": "2.1.0",
        "status": "online",
        "engine": "Gemini File Search"
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "gemini": bool(GEMINI_API_KEY),
        "file_search": bool(FILE_SEARCH_STORE)
    }


# =========================================================
# ASK
# =========================================================

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

    if client is None:
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

    # -----------------------------------------------------
    # GỌI GEMINI FILE SEARCH
    # -----------------------------------------------------

    last_error = None

    for attempt in range(3):

        try:

            interaction = client.interactions.create(
                model="gemini-2.5-flash",
                input=question,
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[
                    {
                        "type": "file_search",
                        "file_search_store_names": [
                            FILE_SEARCH_STORE
                        ]
                    }
                ]
            )

            answer = getattr(
                interaction,
                "text",
                None
            )

            if not answer:
                answer = str(interaction)

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
        "error": last_error or
                 "Gemini API không phản hồi"
    }
