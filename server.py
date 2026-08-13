# ============================================================
# THỦY LỢI AI - SERVER 3.2 STABLE
# FastAPI + Gemini File Search + Interactions API
# ============================================================

import os
import time
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google import genai


# ============================================================
# 1. CẤU HÌNH
# ============================================================

APP_NAME = "THỦY LỢI AI"
VERSION = "3.2.0"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Store hiện tại của anh trên Gemini
FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE",
    "fileSearchStores/thuy-loi-ai-kho-ho-so-rfjt7wu5sehf"
).strip()

# Model hiện tại
MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# Các định dạng cho phép
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".html",
    ".xml",
    ".ppt",
    ".pptx",
}


# ============================================================
# 2. LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("thuyloiai")


# ============================================================
# 3. FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Trợ lý AI chuyên ngành Thủy lợi sử dụng Gemini File Search",
)


# ============================================================
# 4. CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 5. GEMINI CLIENT
# ============================================================

client = None

if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Gemini client: OK")
    except Exception as exc:
        logger.exception("Không thể khởi tạo Gemini client: %s", exc)
else:
    logger.error("THIẾU GEMINI_API_KEY")


# ============================================================
# 6. MODEL REQUEST
# ============================================================

class AskRequest(BaseModel):
    question: str


# ============================================================
# 7. HÀM KIỂM TRA CLIENT
# ============================================================

def _require_client():

    if client is None:

        raise RuntimeError(
            "Gemini chưa được cấu hình. "
            "Kiểm tra biến môi trường GEMINI_API_KEY trên Render."
        )


# ============================================================
# 8. LẤY DANH SÁCH DOCUMENT
# ============================================================

def _document_list():

    _require_client()

    documents = []

    try:

        pager = client.file_search_stores.documents.list(
            parent=FILE_SEARCH_STORE
        )

        for doc in pager:

            documents.append(doc)

        return documents

    except Exception as exc:

        logger.exception(
            "Không thể đọc documents từ Store %s: %s",
            FILE_SEARCH_STORE,
            exc,
        )

        raise


# ============================================================
# 9. TRÍCH XUẤT TEXT TỪ INTERACTION
# ============================================================

def _extract_answer(interaction):

    # Cách mới: output_text
    try:

        text = getattr(interaction, "output_text", None)

        if text:
            return str(text).strip()

    except Exception:
        pass


    # Duyệt outputs
    try:

        outputs = getattr(interaction, "outputs", None)

        if outputs:

            texts = []

            for output in outputs:

                output_type = getattr(output, "type", "")

                if output_type == "text":

                    text = getattr(output, "text", None)

                    if text:
                        texts.append(str(text))

            if texts:

                return "\n".join(texts).strip()

    except Exception:
        pass


    # Dự phòng cho một số phiên bản SDK
    try:

        steps = getattr(interaction, "steps", None)

        if steps:

            texts = []

            for step in steps:

                content = getattr(step, "content", None)

                if not content:
                    continue

                for block in content:

                    block_type = getattr(block, "type", "")

                    if block_type == "text":

                        text = getattr(block, "text", None)

                        if text:
                            texts.append(str(text))

            if texts:

                return "\n".join(texts).strip()

    except Exception:
        pass


    return ""


# ============================================================
# 10. TRÍCH XUẤT NGUỒN
# ============================================================

def _extract_sources(interaction):

    sources = []

    def add_source(name):

        if not name:
            return

        name = str(name).strip()

        if name and name not in sources:
            sources.append(name)


    # --------------------------------------------------------
    # outputs
    # --------------------------------------------------------

    try:

        outputs = getattr(interaction, "outputs", None)

        if outputs:

            for output in outputs:

                annotations = getattr(
                    output,
                    "annotations",
                    None
                )

                if not annotations:
                    continue

                for annotation in annotations:

                    annotation_type = getattr(
                        annotation,
                        "type",
                        ""
                    )

                    if annotation_type != "file_citation":
                        continue

                    # Các tên field có thể khác nhau theo SDK
                    for field in (
                        "title",
                        "display_name",
                        "file_name",
                        "name",
                    ):

                        value = getattr(
                            annotation,
                            field,
                            None
                        )

                        if value:
                            add_source(value)

    except Exception as exc:

        logger.warning(
            "Không đọc được citation: %s",
            exc,
        )


    # --------------------------------------------------------
    # steps
    # --------------------------------------------------------

    try:

        steps = getattr(interaction, "steps", None)

        if steps:

            for step in steps:

                content = getattr(
                    step,
                    "content",
                    None
                )

                if not content:
                    continue

                for block in content:

                    annotations = getattr(
                        block,
                        "annotations",
                        None
                    )

                    if not annotations:
                        continue

                    for annotation in annotations:

                        annotation_type = getattr(
                            annotation,
                            "type",
                            ""
                        )

                        if annotation_type != "file_citation":
                            continue

                        for field in (
                            "title",
                            "display_name",
                            "file_name",
                            "name",
                        ):

                            value = getattr(
                                annotation,
                                field,
                                None
                            )

                            if value:
                                add_source(value)

    except Exception:
        pass


    return sources


# ============================================================
# 11. KIỂM TRA LỖI CÓ NÊN RETRY KHÔNG
# ============================================================

def _is_retryable_error(exc):

    message = str(exc).lower()

    retry_words = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "unavailable",
        "internal error",
        "deadline",
        "connection reset",
        "connection aborted",
    ]

    return any(
        word in message
        for word in retry_words
    )


# ============================================================
# 12. HỎI GEMINI FILE SEARCH
# ============================================================

def _ask_gemini(question):

    _require_client()

    # Prompt bắt buộc ưu tiên hồ sơ
    prompt = f"""
Bạn là THỦY LỢI AI, trợ lý chuyên ngành quản lý, vận hành
và khai thác công trình thủy lợi.

NGUYÊN TẮC TRẢ LỜI:

1. Ưu tiên tuyệt đối thông tin được tìm thấy trong
   File Search Store của THỦY LỢI AI.

2. Không tự bịa số liệu, tên công trình, thông số kỹ thuật,
   vị trí, số lượng máy, diện tích hoặc nội dung hồ sơ.

3. Nếu hồ sơ có thông tin thì trả lời chính xác theo hồ sơ.

4. Nếu thông tin không có trong hồ sơ, phải nói rõ:
   "Hồ sơ hiện có chưa cung cấp thông tin này."

5. Khi có thể, nêu tên tài liệu nguồn ở cuối câu trả lời.

6. Trả lời bằng tiếng Việt, rõ ràng, ngắn gọn nhưng đầy đủ.

7. Nếu câu hỏi hỏi về số liệu, ưu tiên trả lời trực tiếp
   bằng con số và đơn vị.

CÂU HỎI NGƯỜI DÙNG:

{question}
""".strip()


    # --------------------------------------------------------
    # RETRY TỐI ĐA 3 LẦN
    # --------------------------------------------------------

    last_error = None

    for attempt in range(1, 4):

        try:

            logger.info(
                "Gemini File Search lần %s/3 | %s",
                attempt,
                question,
            )

            interaction = client.interactions.create(

                model=MODEL,

                input=prompt,

                tools=[
                    {
                        "type": "file_search",
                        "file_search_store_names": [
                            FILE_SEARCH_STORE
                        ],
                    }
                ],
            )


            answer = _extract_answer(interaction)

            sources = _extract_sources(interaction)


            if not answer:

                raise RuntimeError(
                    "Gemini trả về Interaction nhưng không có nội dung."
                )


            logger.info(
                "Gemini trả lời thành công lần %s",
                attempt,
            )


            return {
                "answer": answer,
                "sources": sources,
                "interaction_id": getattr(
                    interaction,
                    "id",
                    None
                ),
            }


        except Exception as exc:

            last_error = exc

            logger.exception(
                "Lỗi Gemini lần %s/3: %s",
                attempt,
                exc,
            )

            # Không retry lỗi cấu hình / permission / invalid
            if not _is_retryable_error(exc):

                break

            if attempt < 3:

                wait_seconds = attempt * 2

                time.sleep(wait_seconds)


    raise RuntimeError(
        f"Gemini File Search thất bại: {last_error}"
    )


# ============================================================
# 13. HOME
# ============================================================

@app.get("/")
def home():

    # Nếu có index.html trong thư mục hiện tại
    index_file = Path("index.html")

    if index_file.exists():

        return FileResponse(
            index_file,
            media_type="text/html"
        )

    return {
        "success": True,
        "service": APP_NAME,
        "version": VERSION,
        "status": "online",
    }


# ============================================================
# 14. HEALTH
# ============================================================

@app.get("/health")
def health():

    result = {
        "success": True,
        "service": APP_NAME,
        "version": VERSION,
        "model": MODEL,
        "store": FILE_SEARCH_STORE,
        "gemini": False,
        "documents": 0,
    }


    if client is None:

        result["success"] = False
        result["error"] = "GEMINI_API_KEY chưa được cấu hình."

        return JSONResponse(
            status_code=503,
            content=result
        )


    result["gemini"] = True


    try:

        docs = _document_list()

        result["documents"] = len(docs)

        result["status"] = "ready"

        return result


    except Exception as exc:

        result["success"] = False
        result["status"] = "store_error"
        result["error"] = str(exc)

        return JSONResponse(
            status_code=503,
            content=result
        )


# ============================================================
# 15. API INFO
# ============================================================

@app.get("/api")
def api_info():

    return {
        "success": True,
        "service": APP_NAME,
        "version": VERSION,
        "model": MODEL,
        "store": FILE_SEARCH_STORE,

        "endpoints": {
            "home": "/",
            "health": "/health",
            "stores": "/stores",
            "documents": "/documents",
            "upload": "/upload",
            "ask": "/ask",
            "docs": "/docs",
        },
    }


# ============================================================
# 16. LIST STORE
# ============================================================

@app.get("/stores")
def stores():

    _require_client()

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
            })


        return {
            "success": True,
            "configured_store": FILE_SEARCH_STORE,
            "count": len(result),
            "stores": result,
        }


    except Exception as exc:

        logger.exception(
            "Lỗi đọc stores: %s",
            exc
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            }
        )


# ============================================================
# 17. LIST DOCUMENTS
# ============================================================

@app.get("/documents")
def documents():

    try:

        docs = _document_list()

        result = []

        for doc in docs:

            result.append({
                "name": getattr(
                    doc,
                    "name",
                    None
                ),

                "display_name": getattr(
                    doc,
                    "display_name",
                    None
                ),

                "state": str(
                    getattr(
                        doc,
                        "state",
                        None
                    )
                ),

                "mime_type": getattr(
                    doc,
                    "mime_type",
                    None
                ),

                "create_time": str(
                    getattr(
                        doc,
                        "create_time",
                        None
                    )
                ),

                "update_time": str(
                    getattr(
                        doc,
                        "update_time",
                        None
                    )
                ),
            })


        return {
            "success": True,
            "store": FILE_SEARCH_STORE,
            "count": len(result),
            "documents": result,
        }


    except Exception as exc:

        logger.exception(
            "Lỗi documents: %s",
            exc
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            }
        )


# ============================================================
# 18. UPLOAD TÀI LIỆU
# ============================================================

@app.post("/upload")
async def upload(file: UploadFile = File(...)):

    _require_client()

    filename = file.filename or "unknown"

    extension = Path(filename).suffix.lower()


    # --------------------------------------------------------
    # KIỂM TRA ĐỊNH DẠNG
    # --------------------------------------------------------

    if extension not in ALLOWED_EXTENSIONS:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": (
                    f"Định dạng {extension or 'không xác định'} "
                    "chưa được hỗ trợ."
                ),
            },
        )


    # --------------------------------------------------------
    # ĐỌC FILE
    # --------------------------------------------------------

    try:

        content = await file.read()

    except Exception as exc:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": f"Không đọc được file: {exc}",
            },
        )


    if not content:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "File rỗng.",
            },
        )


    if len(content) > MAX_FILE_SIZE:

        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "error": "File vượt quá giới hạn 100 MB.",
            },
        )


    temp_path = None


    try:

        # ----------------------------------------------------
        # TẠO FILE TẠM
        # ----------------------------------------------------

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=extension
        ) as temp_file:

            temp_file.write(content)

            temp_path = temp_file.name


        logger.info(
            "Upload: %s | %.2f MB",
            filename,
            len(content) / 1024 / 1024,
        )


        # ----------------------------------------------------
        # MIME TYPE
        # ----------------------------------------------------

        mime_type = (
            file.content_type
            or "application/octet-stream"
        )


        # ----------------------------------------------------
        # 1. UPLOAD VÀO GEMINI FILE API
        # ----------------------------------------------------

        uploaded_file = client.files.upload(

            file=temp_path,

            config={
                "display_name": filename,
                "mime_type": mime_type,
            },
        )


        logger.info(
            "Gemini File upload OK: %s",
            getattr(
                uploaded_file,
                "name",
                None
            ),
        )


        # ----------------------------------------------------
        # 2. IMPORT VÀO FILE SEARCH STORE
        # ----------------------------------------------------

        operation = client.file_search_stores.import_file(

            file_search_store_name=FILE_SEARCH_STORE,

            file_name=uploaded_file.name,
        )


        # ----------------------------------------------------
        # 3. CHỜ INDEX
        # ----------------------------------------------------

        started = time.time()

        timeout_seconds = 300

        while not operation.done:

            if time.time() - started > timeout_seconds:

                raise TimeoutError(
                    "Quá thời gian chờ Gemini index tài liệu."
                )

            time.sleep(3)

            operation = client.operations.get(
                operation
            )


        logger.info(
            "Import vào File Search Store hoàn tất: %s",
            filename,
        )


        return {
            "success": True,
            "message": "Tải tài liệu thành công.",
            "filename": filename,
            "store": FILE_SEARCH_STORE,
            "file": getattr(
                uploaded_file,
                "name",
                None
            ),
            "status": "indexed",
        }


    except Exception as exc:

        logger.exception(
            "UPLOAD ERROR: %s",
            exc,
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "filename": filename,
                "error": str(exc),
            },
        )


    finally:

        # ----------------------------------------------------
        # XÓA FILE TẠM TRÊN RENDER
        # ----------------------------------------------------

        if temp_path:

            try:

                os.remove(temp_path)

            except Exception:

                pass


# ============================================================
# 19. ASK
# ============================================================

@app.post("/ask")
def ask(request: AskRequest):

    question = (
        request.question or ""
    ).strip()


    if not question:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Vui lòng nhập câu hỏi.",
            },
        )


    # --------------------------------------------------------
    # KIỂM TRA STORE
    # --------------------------------------------------------

    try:

        docs = _document_list()

    except Exception as exc:

        logger.exception(
            "Không kiểm tra được Store: %s",
            exc,
        )

        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "status": "store_error",
                "answer": (
                    "THỦY LỢI AI chưa kết nối được "
                    "với kho dữ liệu Gemini."
                ),
                "error": str(exc),
            },
        )


    if len(docs) == 0:

        return {
            "success": False,
            "status": "no_documents",

            "answer": (
                "Kho dữ liệu THỦY LỢI AI hiện chưa có tài liệu. "
                "Hãy tải tài liệu lên trước."
            ),

            "store": FILE_SEARCH_STORE,
        }


    # --------------------------------------------------------
    # GỌI GEMINI
    # --------------------------------------------------------

    try:

        result = _ask_gemini(question)


        answer = result["answer"]

        sources = result["sources"]


        # ----------------------------------------------------
        # THÊM NGUỒN HỒ SƠ
        # ----------------------------------------------------

        if sources:

            answer += "\n\n📚 Nguồn hồ sơ:\n"

            for source in sources:

                answer += f"• {source}\n"


        return {
            "success": True,
            "status": "ok",
            "question": question,
            "answer": answer,
            "sources": sources,
            "store": FILE_SEARCH_STORE,
            "model": MODEL,
            "documents": len(docs),
            "interaction_id": result[
                "interaction_id"
            ],
        }


    except Exception as exc:

        logger.exception(
            "ASK ERROR: %s",
            exc,
        )


        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "status": "gemini_error",

                "answer": (
                    "THỦY LỢI AI tạm thời chưa lấy được "
                    "câu trả lời từ kho dữ liệu Gemini. "
                    "Vui lòng thử lại sau ít giây."
                ),

                "error": str(exc),

                "store": FILE_SEARCH_STORE,

                "model": MODEL,
            },
        )


# ============================================================
# 20. XÓA DOCUMENT
# ============================================================

@app.delete("/documents")
def delete_document(name: str):

    _require_client()

    if not name:

        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Thiếu tên document.",
            },
        )


    try:

        client.file_search_stores.documents.delete(
            name=name,
            config={
                "force": True
            },
        )


        return {
            "success": True,
            "message": "Đã xóa tài liệu.",
            "document": name,
        }


    except Exception as exc:

        logger.exception(
            "DELETE ERROR: %s",
            exc,
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
            },
        )


# ============================================================
# 21. STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():

    logger.info("=" * 60)
    logger.info("%s - VERSION %s", APP_NAME, VERSION)
    logger.info("=" * 60)

    logger.info(
        "MODEL: %s",
        MODEL
    )

    logger.info(
        "STORE: %s",
        FILE_SEARCH_STORE
    )

    logger.info(
        "GEMINI API: %s",
        "OK" if GEMINI_API_KEY else "THIEU KEY"
    )

    if client:

        try:

            docs = _document_list()

            logger.info(
                "DOCUMENTS: %s",
                len(docs)
            )

        except Exception as exc:

            logger.error(
                "STORE CHECK ERROR: %s",
                exc
            )

    logger.info("=" * 60)


# ============================================================
# 22. CHẠY LOCAL
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        ),
        reload=False,
    )
