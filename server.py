import os
import time
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from google import genai
from google.genai import types


# ============================================================
# THỦY LỢI AI 3.0
# Kiến trúc tối giản:
# Browser -> FastAPI -> Gemini File Search Store -> Gemini
# ============================================================

APP_VERSION = "3.0.0"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("Thiếu biến môi trường GEMINI_API_KEY")

if not FILE_SEARCH_STORE:
    raise RuntimeError("Thiếu biến môi trường GEMINI_FILE_SEARCH_STORE")

# Chuẩn hóa tên store nếu người dùng chỉ nhập ID.
if not FILE_SEARCH_STORE.startswith("fileSearchStores/"):
    FILE_SEARCH_STORE = f"fileSearchStores/{FILE_SEARCH_STORE}"

client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


def _document_list():
    """Lấy danh sách tài liệu trong File Search Store."""
    return list(client.file_search_stores.documents.list(parent=FILE_SEARCH_STORE))


def _serialize_document(doc):
    """Chuyển object SDK thành JSON đơn giản."""
    result = {
        "name": getattr(doc, "name", None),
        "display_name": getattr(doc, "display_name", None),
    }

    # Một số phiên bản SDK dùng displayName / state / createTime...
    for attr in ("state", "create_time", "update_time", "mime_type"):
        value = getattr(doc, attr, None)
        if value is not None:
            result[attr] = str(value)

    return result


def _extract_citations(response):
    """Lấy thông tin nguồn nếu Gemini trả về grounding metadata."""
    citations = []

    try:
        candidate = response.candidates[0]
        metadata = getattr(candidate, "grounding_metadata", None)

        if not metadata:
            return citations

        chunks = getattr(metadata, "grounding_chunks", None) or []

        for chunk in chunks:
            retrieved = getattr(chunk, "retrieved_context", None)
            if not retrieved:
                continue

            item = {
                "title": getattr(retrieved, "title", None),
                "text": getattr(retrieved, "text", None),
                "uri": getattr(retrieved, "uri", None),
            }

            # Bỏ các trường rỗng
            item = {k: v for k, v in item.items() if v}
            if item:
                citations.append(item)

    except Exception:
        # Citation chỉ là phần bổ sung, không làm hỏng câu trả lời.
        pass

    return citations


@app.get("/", include_in_schema=False)
def home():
    """Mở giao diện index.html nếu file tồn tại."""
    index_file = Path(__file__).with_name("index.html")

    if index_file.exists():
        return FileResponse(index_file)

    return {
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "status": "online",
        "engine": "Gemini File Search",
    }


@app.get("/health")
def health():
    """Kiểm tra nhanh cấu hình và kết nối File Search."""
    try:
        documents = _document_list()

        return {
            "status": "ok",
            "service": "THỦY LỢI AI",
            "version": APP_VERSION,
            "engine": "Gemini File Search",
            "gemini_configured": bool(GEMINI_API_KEY),
            "file_search_configured": bool(FILE_SEARCH_STORE),
            "file_search_store": FILE_SEARCH_STORE,
            "file_search_ready": True,
            "documents": len(documents),
            "model": MODEL,
        }

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "service": "THỦY LỢI AI",
                "version": APP_VERSION,
                "error": str(e),
                "file_search_store": FILE_SEARCH_STORE,
            },
        )


@app.get("/api")
def api_info():
    return {
        "service": "THỦY LỢI AI",
        "version": APP_VERSION,
        "model": MODEL,
        "store": FILE_SEARCH_STORE,
        "endpoints": {
            "ask": "POST /ask",
            "upload": "POST /upload",
            "documents": "GET /documents",
            "stores": "GET /stores",
            "health": "GET /health",
        },
    }


@app.get("/stores")
def stores():
    """Liệt kê các File Search Store mà API key nhìn thấy."""
    try:
        result = []

        for store in client.file_search_stores.list():
            result.append(
                {
                    "name": getattr(store, "name", None),
                    "display_name": getattr(store, "display_name", None),
                    "active": getattr(store, "active", None),
                }
            )

        return {
            "success": True,
            "configured_store": FILE_SEARCH_STORE,
            "count": len(result),
            "stores": result,
        }

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"success": False, "error": str(e)},
        )


@app.get("/documents")
def documents():
    """Danh sách tài liệu đã được lập chỉ mục trong kho."""
    try:
        docs = _document_list()

        return {
            "success": True,
            "store": FILE_SEARCH_STORE,
            "count": len(docs),
            "documents": [_serialize_document(doc) for doc in docs],
        }

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "store": FILE_SEARCH_STORE,
                "error": str(e),
            },
        )


@app.post("/upload")
def upload(file: UploadFile = File(...)):
    """
    Nhận PDF từ máy người dùng và đưa thẳng vào Gemini File Search Store.

    Không lưu PDF lâu dài trên Render.
    File tạm chỉ dùng trong lúc upload/index.
    """
    filename = Path(file.filename or "document.pdf").name

    if not filename.lower().endswith(".pdf"):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Chỉ nhận file PDF."
            },
        )

    temp_path = None

    try:
        # Lưu file upload thành file tạm để SDK Gemini đọc.
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".pdf",
        ) as tmp:
            temp_path = tmp.name

            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)

        operation = client.file_search_stores.upload_to_file_search_store(
            file=temp_path,
            file_search_store_name=FILE_SEARCH_STORE,
            config={
                "display_name": filename,
            },
        )

        # Chờ Gemini hoàn tất việc chunk -> embedding -> indexing.
        while not operation.done:
            time.sleep(3)
            operation = client.operations.get(operation)

        return {
            "success": True,
            "message": "Đã đưa tài liệu vào kho THỦY LỢI AI.",
            "filename": filename,
            "store": FILE_SEARCH_STORE,
            "operation": getattr(operation, "name", None),
        }

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "filename": filename,
                "error": str(e),
            },
        )

    finally:
        try:
            file.file.close()
        except Exception:
            pass

        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


@app.post("/ask")
def ask(request: AskRequest):
    """Hỏi Gemini và bắt buộc dùng File Search Store làm nguồn dữ liệu."""
    question = (request.question or "").strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Vui lòng nhập câu hỏi."
            },
        )

    try:
        # Kiểm tra kho trước khi hỏi để tránh trả lời khi kho hoàn toàn rỗng.
        docs = _document_list()

        if len(docs) == 0:
            return {
                "success": False,
                "status": "no_documents",
                "answer": (
                    "Kho THỦY LỢI AI hiện chưa có tài liệu. "
                    "Hãy tải PDF lên bằng chức năng /upload trước."
                ),
                "store": FILE_SEARCH_STORE,
            }

        prompt = f"""
Bạn là TRỢ LÝ AI CHUYÊN NGÀNH THỦY LỢI.

Hãy trả lời câu hỏi dựa trước hết và chủ yếu vào các tài liệu
được tìm thấy trong kho hồ sơ THỦY LỢI AI.

Nguyên tắc:
1. Không tự bịa số liệu, điều khoản, tên văn bản hoặc nội dung hồ sơ.
2. Nếu tài liệu không đủ thông tin, nói rõ: "Tài liệu hiện có chưa đủ thông tin để kết luận."
3. Khi có thể, nêu rõ tên tài liệu làm căn cứ.
4. Trả lời bằng tiếng Việt, rõ ràng, ngắn gọn nhưng có chiều sâu chuyên môn.
5. Nếu câu hỏi yêu cầu trích dẫn văn bản, ưu tiên nội dung đúng theo tài liệu.

CÂU HỎI:
{question}
""".strip()

        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[FILE_SEARCH_STORE]
                        )
                    )
                ]
            ),
        )

        answer = getattr(response, "text", None)

        if not answer:
            answer = "Gemini chưa trả về nội dung trả lời."

        return {
            "success": True,
            "status": "ok",
            "answer": answer,
            "engine": "Gemini File Search",
            "model": MODEL,
            "store": FILE_SEARCH_STORE,
            "documents_available": len(docs),
            "citations": _extract_citations(response),
        }

    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "status": "error",
                "answer": "THỦY LỢI AI chưa thể trả lời câu hỏi.",
                "error": str(e),
                "engine": "Gemini File Search",
            },
        )


# Render chạy: uvicorn server:app --host 0.0.0.0 --port $PORT
