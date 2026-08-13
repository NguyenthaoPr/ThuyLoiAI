import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai


# ============================================================
# THỦY LỢI AI V2
# Gemini Interactions API + File Search
# ============================================================

app = FastAPI(
    title="THỦY LỢI AI",
    version="3.0.0"
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
# GEMINI
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE")

if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    client = None


# ============================================================
# REQUEST
# ============================================================

class QueryRequest(BaseModel):
    question: str


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
Bạn là TRỢ LÝ THỦY LỢI AI.

Bạn là trợ lý chuyên ngành Thủy lợi, hỗ trợ cán bộ quản lý,
kỹ sư và người sử dụng tra cứu tài liệu chuyên ngành.

NGUYÊN TẮC:

1. Ưu tiên tuyệt đối thông tin trong Kho dữ liệu Thủy lợi
   được cung cấp thông qua File Search.

2. Không tự bịa số liệu, điều khoản, quy trình hoặc thông số
   kỹ thuật.

3. Nếu Kho dữ liệu không có thông tin phù hợp:
   nói rõ:
   "Chưa tìm thấy thông tin phù hợp trong Kho dữ liệu Thủy lợi."

4. Khi có nguồn tài liệu, phải cố gắng nêu:
   - Tên tài liệu
   - Điều/Khoản nếu có
   - Trang nếu có
   - Nội dung căn cứ

5. Trả lời bằng tiếng Việt.

6. Trả lời ngắn gọn, rõ ràng, có cấu trúc.

7. Đối với thông tin kỹ thuật hoặc pháp lý quan trọng,
   không được suy đoán thay cho tài liệu nguồn.

8. Nếu câu hỏi liên quan đến một công trình cụ thể,
   ưu tiên dữ liệu của chính công trình đó.
"""


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    return {
        "service": "THỦY LỢI AI",
        "version": "3.0.0",
        "status": "online",
        "engine": "Gemini Interactions API",
        "file_search": bool(FILE_SEARCH_STORE)
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "gemini": bool(GEMINI_API_KEY),
        "file_search": bool(FILE_SEARCH_STORE)
    }


# ============================================================
# ASK AI
# ============================================================

@app.post("/ask")
def ask_ai(request: QueryRequest):

    if not client:
        raise HTTPException(
            status_code=500,
            detail="Chưa cấu hình GEMINI_API_KEY trên Render."
        )

    if not FILE_SEARCH_STORE:
        raise HTTPException(
            status_code=500,
            detail="Chưa cấu hình GEMINI_FILE_SEARCH_STORE trên Render."
        )

    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Câu hỏi không được để trống."
        )

    try:

        interaction = client.interactions.create(

            # Model Gemini mới
            model="gemini-3.6-flash",

            # Hướng dẫn hệ thống
            system_instruction=SYSTEM_INSTRUCTION,

            # Câu hỏi
            input=question,

            # Kho tài liệu Thủy lợi
            tools=[
                {
                    "type": "file_search",
                    "file_search_store_names": [
                        FILE_SEARCH_STORE
                    ],
                    "top_k": 5
                }
            ]
        )

        answer = ""

        citations = []

        # ====================================================
        # ĐỌC KẾT QUẢ
        # ====================================================

        for step in interaction.steps:

            if getattr(step, "type", None) != "model_output":
                continue

            for block in getattr(step, "content", []):

                if getattr(block, "type", None) != "text":
                    continue

                # Nội dung trả lời
                text = getattr(block, "text", "")

                if text:
                    answer += text

                # Citation
                annotations = getattr(
                    block,
                    "annotations",
                    []
                )

                for annotation in annotations:

                    if getattr(
                        annotation,
                        "type",
                        None
                    ) == "file_citation":

                        citation = {
                            "file_name": getattr(
                                annotation,
                                "file_name",
                                None
                            ),
                            "page": getattr(
                                annotation,
                                "page_number",
                                None
                            )
                        }

                        citations.append(citation)

        # ====================================================
        # FALLBACK
        # ====================================================

        if not answer:

            output_text = getattr(
                interaction,
                "output_text",
                None
            )

            if output_text:
                answer = output_text

        if not answer:

            answer = (
                "Hệ thống chưa nhận được câu trả lời "
                "từ Gemini."
            )

        # ====================================================
        # RESPONSE
        # ====================================================

        return {
            "success": True,
            "answer": answer,
            "citations": citations
        }

    except Exception as e:

        print("GEMINI ERROR:", repr(e))

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
