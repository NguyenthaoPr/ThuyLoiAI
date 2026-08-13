import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai


# ============================================================
# THỦY LỢI AI
# V3.0
# FastAPI + Gemini Interactions API + File Search
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
# ENVIRONMENT
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE"
)

client = None

if GEMINI_API_KEY:
    client = genai.Client(
        api_key=GEMINI_API_KEY
    )


# ============================================================
# REQUEST MODEL
# ============================================================

class QueryRequest(BaseModel):
    question: str


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
Bạn là TRỢ LÝ THỦY LỢI AI.

Bạn hỗ trợ cán bộ, kỹ sư và người sử dụng
tra cứu và phân tích dữ liệu chuyên ngành Thủy lợi.

NGUYÊN TẮC BẮT BUỘC:

1. Ưu tiên sử dụng thông tin từ Kho dữ liệu
   Thủy lợi thông qua File Search.

2. Không tự bịa số liệu, điều khoản, quy trình,
   thông số kỹ thuật hoặc tên văn bản.

3. Nếu không tìm thấy thông tin phù hợp trong
   Kho dữ liệu, phải nói rõ:

   "Chưa tìm thấy thông tin phù hợp trong
   Kho dữ liệu Thủy lợi."

4. Khi có nguồn, cố gắng nêu:
   - Tên tài liệu
   - Điều/Khoản nếu có
   - Trang nếu có
   - Nội dung căn cứ

5. Trả lời bằng tiếng Việt.

6. Trả lời rõ ràng, ngắn gọn, dễ sử dụng
   trên điện thoại.

7. Với thông tin pháp lý hoặc kỹ thuật quan trọng,
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
        "file_search": bool(
            GEMINI_FILE_SEARCH_STORE
        )
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "gemini": bool(GEMINI_API_KEY),
        "file_search": bool(
            GEMINI_FILE_SEARCH_STORE
        )
    }


# ============================================================
# KIỂM TRA FILE SEARCH STORE
# ============================================================

@app.get("/stores")
def list_stores():

    if not client:

        raise HTTPException(
            status_code=500,
            detail="Chưa cấu hình GEMINI_API_KEY."
        )

    try:

        result = []

        for store in client.file_search_stores.list():

            result.append({
                "name": getattr(
                    store,
                    "name",
                    None
                ),
                "display_name": getattr(
                    store,
                    "display_name",
                    None
                ),
                "active": (
                    getattr(
                        store,
                        "name",
                        None
                    )
                    ==
                    GEMINI_FILE_SEARCH_STORE
                )
            })

        return {
            "success": True,
            "configured_store":
                GEMINI_FILE_SEARCH_STORE,
            "count": len(result),
            "stores": result
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# KIỂM TRA TÀI LIỆU TRONG STORE
# ============================================================

@app.get("/documents")
def list_documents():

    if not client:

        raise HTTPException(
            status_code=500,
            detail="Chưa cấu hình GEMINI_API_KEY."
        )

    if not GEMINI_FILE_SEARCH_STORE:

        raise HTTPException(
            status_code=500,
            detail=(
                "Chưa cấu hình "
                "GEMINI_FILE_SEARCH_STORE."
            )
        )

    try:

        documents = []

        for document in (
            client.file_search_stores.documents.list(
                parent=GEMINI_FILE_SEARCH_STORE
            )
        ):

            documents.append({
                "name": getattr(
                    document,
                    "name",
                    None
                ),
                "display_name": getattr(
                    document,
                    "display_name",
                    None
                )
            })

        return {
            "success": True,
            "store": GEMINI_FILE_SEARCH_STORE,
            "count": len(documents),
            "documents": documents
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ============================================================
# ASK
# ============================================================

@app.post("/ask")
def ask_ai(request: QueryRequest):

    # --------------------------------------------------------
    # Kiểm tra API KEY
    # --------------------------------------------------------

    if not client:

        raise HTTPException(
            status_code=500,
            detail=(
                "Chưa cấu hình GEMINI_API_KEY "
                "trên Render."
            )
        )

    # --------------------------------------------------------
    # Kiểm tra File Search Store
    # --------------------------------------------------------

    if not GEMINI_FILE_SEARCH_STORE:

        raise HTTPException(
            status_code=500,
            detail=(
                "Chưa cấu hình "
                "GEMINI_FILE_SEARCH_STORE "
                "trên Render."
            )
        )

    # --------------------------------------------------------
    # Kiểm tra câu hỏi
    # --------------------------------------------------------

    question = request.question.strip()

    if not question:

        raise HTTPException(
            status_code=400,
            detail="Câu hỏi không được để trống."
        )

    try:

        # ====================================================
        # GEMINI INTERACTIONS
        # ====================================================

        interaction = client.interactions.create(

            model="gemini-3.6-flash",

            system_instruction=
                SYSTEM_INSTRUCTION,

            input=question,

            tools=[
                {
                    "type": "file_search",
                    "file_search_store_names": [
                        GEMINI_FILE_SEARCH_STORE
                    ],
                    "top_k": 5
                }
            ],

            generation_config={
                "temperature": 0.1
            }
        )

        # ====================================================
        # LẤY CÂU TRẢ LỜI
        # ====================================================

        answer = getattr(
            interaction,
            "output_text",
            ""
        )

        citations = []

        # ====================================================
        # LẤY CITATION
        # ====================================================

        for step in getattr(
            interaction,
            "steps",
            []
        ):

            if getattr(
                step,
                "type",
                None
            ) != "model_output":

                continue

            for content in getattr(
                step,
                "content",
                []
            ):

                if getattr(
                    content,
                    "type",
                    None
                ) != "text":

                    continue

                annotations = getattr(
                    content,
                    "annotations",
                    []
                )

                for annotation in annotations:

                    if getattr(
                        annotation,
                        "type",
                        None
                    ) != "file_citation":

                        continue

                    citations.append({
                        "file_name":
                            getattr(
                                annotation,
                                "file_name",
                                None
                            ),

                        "source":
                            getattr(
                                annotation,
                                "source",
                                None
                            )
                    })

        # ====================================================
        # KHÔNG CÓ CÂU TRẢ LỜI
        # ====================================================

        if not answer:

            answer = (
                "Gemini chưa trả về nội dung "
                "câu trả lời."
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

        print(
            "GEMINI ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
