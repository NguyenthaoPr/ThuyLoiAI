import os
import asyncio
import random
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai


# =========================================================
# THỦY LỢI AI - SERVER
# Gemini 3.1 Flash-Lite + Gemini File Search
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE", ""
).strip()

# Render Environment GEMINI_MODEL sẽ được ưu tiên.
# Nếu chưa có thì dùng model tiết kiệm này.
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.1-flash-lite"
).strip()

MAX_CONCURRENT = max(
    1, int(os.getenv("MAX_CONCURRENT", "2"))
)

REQUEST_TIMEOUT = max(
    10, int(os.getenv("REQUEST_TIMEOUT", "60"))
)

MAX_RETRIES = max(
    1, int(os.getenv("MAX_RETRIES", "2"))
)


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi
của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời dựa trên kho hồ sơ, tài liệu, quy định,
quy trình và dữ liệu đã được đưa vào Gemini File Search.

NGUYÊN TẮC BẮT BUỘC:

1. Ưu tiên thông tin trong kho hồ sơ THỦY LỢI AI.

2. Không tự bịa số liệu, điều khoản, tên văn bản,
số văn bản, ngày tháng, thông số kỹ thuật hoặc
quy trình vận hành.

3. Nếu không tìm thấy đủ căn cứ trong kho, nói rõ:
"Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."

4. Khi tài liệu có nhiều thông tin liên quan,
hãy tổng hợp và phân tích rõ ràng.

5. Khi xác định được nguồn, nêu tên tài liệu.

6. Với câu hỏi pháp luật, ưu tiên văn bản có trong kho.

7. Nếu có nhiều tài liệu liên quan,
hãy so sánh và chỉ ra điểm khác nhau.

8. Không biến suy đoán thành kết luận chính thức.

9. Trả lời bằng tiếng Việt.

10. Ưu tiên chính xác, ngắn gọn, dễ hiểu,
có căn cứ và phù hợp nghiệp vụ Thủy lợi.

11. Với quy trình, có thể trình bày theo từng bước.

12. Với số liệu, giữ nguyên số liệu và đơn vị
theo tài liệu.

13. Không nói rằng bạn đã đọc toàn bộ kho.
Chỉ sử dụng căn cứ mà File Search cung cấp
cho câu hỏi hiện tại.

14. Nếu câu hỏi hỏi về một công trình, trạm bơm,
hồ chứa, văn bản, người, số liệu hoặc địa điểm cụ thể,
hãy ưu tiên tìm đúng tài liệu liên quan trước khi trả lời.
"""


# =========================================================
# GEMINI CLIENT
# =========================================================

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


# =========================================================
# LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    print("")
    print("==============================================")
    print("             KHỞI ĐỘNG THỦY LỢI AI")
    print("==============================================")
    print("KIỂM TRA GEMINI...")

    if not GEMINI_API_KEY:
        print("GEMINI API: CHƯA CÓ API KEY")
    else:
        try:
            gemini_client = genai.Client(
                api_key=GEMINI_API_KEY
            )
            print("GEMINI API: ĐÃ KẾT NỐI")
        except Exception as e:
            gemini_client = None
            print("GEMINI API: LỖI KHỞI TẠO:", repr(e))

    print(
        "FILE SEARCH STORE:",
        GEMINI_FILE_SEARCH_STORE or "CHƯA CẤU HÌNH"
    )
    print("MODEL:", GEMINI_MODEL)
    print("MAX CONCURRENT:", MAX_CONCURRENT)
    print("REQUEST TIMEOUT:", REQUEST_TIMEOUT)
    print("MAX RETRIES:", MAX_RETRIES)
    print("==============================================")
    print("")

    yield

    gemini_client = None
    print("THỦY LỢI AI ĐÃ DỪNG")


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="4.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str


# =========================================================
# HOME / HEALTH / API
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
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "version": "4.1",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_connected": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_store": GEMINI_FILE_SEARCH_STORE,
        "model": GEMINI_MODEL,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "endpoints": {
            "home": "/",
            "health": "/health",
            "api": "/api",
            "documents": "/documents",
            "documents_pdf_preview": "/documents/pdf/preview",
            "documents_pdf_delete": "/documents/pdf",
            "ask": "/ask",
        },
    }


# =========================================================
# ERROR / RETRY
# =========================================================

def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()

    permanent_errors = [
        "400",
        "401",
        "403",
        "404",
        "bad request",
        "unauthenticated",
        "permission denied",
        "api key",
        "invalid argument",
        "not found",
    ]

    if any(x in text for x in permanent_errors):
        return False

    retryable_errors = [
        "408",
        "429",
        "500",
        "502",
        "503",
        "504",
        "rate limit",
        "resource exhausted",
        "unavailable",
        "timeout",
        "deadline",
        "temporarily",
        "internal",
        "connection",
        "reset",
        "server error",
    ]

    return any(x in text for x in retryable_errors)


# =========================================================
# GEMINI FILE SEARCH
# =========================================================

def call_gemini(question: str):
    if gemini_client is None:
        raise RuntimeError(
            "Gemini API chưa được kết nối."
        )

    if not GEMINI_FILE_SEARCH_STORE:
        raise RuntimeError(
            "Gemini File Search Store chưa được cấu hình."
        )

    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[
            {
                "type": "file_search",
                "file_search_store_names": [
                    GEMINI_FILE_SEARCH_STORE
                ],
            }
        ],
    )


def extract_answer_and_sources(
    result: Any
):
    answer = (
        getattr(result, "output_text", None)
        or ""
    ).strip()

    sources = []

    steps = getattr(
        result,
        "steps",
        []
    ) or []

    for step in steps:
        if getattr(
            step,
            "type",
            None
        ) != "model_output":
            continue

        blocks = getattr(
            step,
            "content",
            []
        ) or []

        for block in blocks:

            if (
                not answer
                and getattr(
                    block,
                    "type",
                    None
                ) == "text"
            ):
                answer += (
                    getattr(
                        block,
                        "text",
                        ""
                    ) or ""
                )

            annotations = getattr(
                block,
                "annotations",
                []
            ) or []

            for annotation in annotations:

                if getattr(
                    annotation,
                    "type",
                    None
                ) != "file_citation":
                    continue

                item = {}

                file_name = getattr(
                    annotation,
                    "file_name",
                    None
                )

                source = getattr(
                    annotation,
                    "source",
                    None
                )

                page_number = getattr(
                    annotation,
                    "page_number",
                    None
                )

                if file_name:
                    item["file_name"] = str(
                        file_name
                    )

                if source:
                    item["source"] = str(
                        source
                    )

                if page_number is not None:
                    item["page_number"] = page_number

                if (
                    item
                    and item not in sources
                ):
                    sources.append(item)

    answer = answer.strip()

    if not answer:
        raise RuntimeError(
            "Gemini không trả về nội dung."
        )

    return answer, sources


async def ask_gemini_with_retry(
    question: str
):
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            async with request_semaphore:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        call_gemini,
                        question
                    ),
                    timeout=REQUEST_TIMEOUT,
                )

            return extract_answer_and_sources(
                result
            )

        except Exception as e:
            last_error = e

            print(
                "LỖI GEMINI:",
                repr(e)
            )

            retryable = is_retryable_error(e)

            print(
                "RETRYABLE:",
                retryable
            )

            if (
                not retryable
                or attempt >= MAX_RETRIES - 1
            ):
                break

            delay = (
                min(8, 2 ** attempt)
                + random.uniform(0, 0.5)
            )

            print(
                f"THỬ LẠI LẦN "
                f"{attempt + 2}/{MAX_RETRIES} "
                f"SAU {delay:.1f} GIÂY..."
            )

            await asyncio.sleep(delay)

    raise (
        last_error
        or RuntimeError(
            "Gemini không thể xử lý câu hỏi."
        )
    )


# =========================================================
# DOCUMENT HELPERS
# =========================================================

def document_display_name(
    document: Any
) -> str:
    """
    Lấy tên hiển thị.
    Nếu display_name rỗng, dùng phần cuối của name.
    """
    display_name = str(
        getattr(
            document,
            "display_name",
            ""
        )
        or ""
    ).strip()

    if display_name:
        return display_name

    name = str(
        getattr(
            document,
            "name",
            ""
        )
        or ""
    ).strip()

    if "/" in name:
        return name.rsplit("/", 1)[-1]

    return name


def is_pdf_document(
    document: Any
) -> bool:
    """
    Xác định PDF dựa trên display_name/name.
    Không phụ thuộc chữ hoa/chữ thường.
    """
    display_name = document_display_name(
        document
    )

    name = str(
        getattr(
            document,
            "name",
            ""
        )
        or ""
    )

    return (
        display_name.lower().endswith(".pdf")
        or name.lower().endswith(".pdf")
    )


def serialize_document(
    document: Any
) -> dict:
    return {
        "name": str(
            getattr(
                document,
                "name",
                ""
            )
            or ""
        ),
        "display_name": document_display_name(
            document
        ),
        "state": str(
            getattr(
                document,
                "state",
                ""
            )
            or ""
        ),
        "is_pdf": is_pdf_document(
            document
        ),
    }


def list_all_documents_sync():
    """
    Lấy toàn bộ Document trong Store.
    page_size=20 theo giới hạn API.
    Client sync được chạy trong thread để không block
    FastAPI event loop.
    """
    if gemini_client is None:
        raise RuntimeError(
            "Gemini API chưa được kết nối."
        )

    if not GEMINI_FILE_SEARCH_STORE:
        raise RuntimeError(
            "Chưa cấu hình GEMINI_FILE_SEARCH_STORE."
        )

    pager = (
        gemini_client
        .file_search_stores
        .documents
        .list(
            parent=GEMINI_FILE_SEARCH_STORE,
            config={
                "page_size": 20
            }
        )
    )

    documents = []

    for document in pager:
        documents.append(document)

    return documents


async def list_all_documents():
    return await asyncio.to_thread(
        list_all_documents_sync
    )


# =========================================================
# GET /documents
# XEM TOÀN BỘ KHO
# =========================================================

@app.get("/documents")
async def documents():

    try:
        docs = await list_all_documents()

        result = [
            serialize_document(doc)
            for doc in docs
        ]

        return {
            "success": True,
            "store": GEMINI_FILE_SEARCH_STORE,
            "count": len(result),
            "pdf_count": sum(
                1
                for item in result
                if item["is_pdf"]
            ),
            "documents": result,
        }

    except Exception as e:
        print(
            "LỖI LIST DOCUMENTS:",
            repr(e)
        )

        return {
            "success": False,
            "store": GEMINI_FILE_SEARCH_STORE,
            "count": 0,
            "pdf_count": 0,
            "documents": [],
            "error": str(e),
        }


# =========================================================
# GET /documents/pdf/preview
# CHỈ XEM PDF SẼ BỊ XÓA - KHÔNG XÓA
# =========================================================

@app.get("/documents/pdf/preview")
async def preview_pdf_documents():

    try:
        docs = await list_all_documents()

        pdfs = [
            serialize_document(doc)
            for doc in docs
            if is_pdf_document(doc)
        ]

        return {
            "success": True,
            "store": GEMINI_FILE_SEARCH_STORE,
            "pdf_count": len(pdfs),
            "pdf_documents": pdfs,
            "message": (
                "Đây là danh sách PDF sẽ bị xóa. "
                "Chưa có tài liệu nào bị xóa."
            ),
        }

    except Exception as e:
        print(
            "LỖI PREVIEW PDF:",
            repr(e)
        )

        return {
            "success": False,
            "store": GEMINI_FILE_SEARCH_STORE,
            "pdf_count": 0,
            "pdf_documents": [],
            "error": str(e),
        }


# =========================================================
# DELETE /documents/pdf
# XÓA TOÀN BỘ PDF - GIỮ FILE KHÁC
# =========================================================

def delete_documents_sync(
    documents: list[Any]
):
    deleted = []
    kept = []
    errors = []

    for document in documents:

        display_name = document_display_name(
            document
        )

        name = str(
            getattr(
                document,
                "name",
                ""
            )
            or ""
        ).strip()

        if not is_pdf_document(document):
            kept.append(display_name)
            continue

        if not name:
            errors.append({
                "file": display_name,
                "error": "Document không có name."
            })
            continue

        try:

            # force=True để xóa Document và các chunk
            # đã được lập chỉ mục trong File Search.
            gemini_client \
                .file_search_stores \
                .documents \
                .delete(
                    name=name,
                    config={
                        "force": True
                    }
                )

            deleted.append({
                "name": name,
                "display_name": display_name,
            })

            print(
                "ĐÃ XÓA PDF:",
                display_name
            )

        except Exception as e:

            errors.append({
                "file": display_name,
                "name": name,
                "error": str(e),
            })

            print(
                "LỖI XÓA PDF:",
                display_name,
                repr(e)
            )

    return deleted, kept, errors


@app.delete("/documents/pdf")
async def delete_pdf_documents():

    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa kết nối."
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "error": (
                "Chưa cấu hình "
                "GEMINI_FILE_SEARCH_STORE."
            )
        }

    try:

        # Quan trọng:
        # Lấy danh sách trước, sau đó mới xóa.
        # Không xóa ngay trong lúc đang duyệt pager,
        # tránh bỏ sót tài liệu khi danh sách thay đổi.
        docs = await list_all_documents()

        pdf_docs = [
            doc
            for doc in docs
            if is_pdf_document(doc)
        ]

        non_pdf_docs = [
            doc
            for doc in docs
            if not is_pdf_document(doc)
        ]

        if not pdf_docs:
            return {
                "success": True,
                "message": (
                    "Không tìm thấy file PDF "
                    "trong kho."
                ),
                "store": GEMINI_FILE_SEARCH_STORE,
                "deleted_count": 0,
                "deleted": [],
                "kept_count": len(non_pdf_docs),
                "kept": [
                    document_display_name(doc)
                    for doc in non_pdf_docs
                ],
                "error_count": 0,
                "errors": [],
            }

        async with request_semaphore:

            deleted, kept, errors = (
                await asyncio.to_thread(
                    delete_documents_sync,
                    docs
                )
            )

        return {
            "success": len(errors) == 0,
            "store": GEMINI_FILE_SEARCH_STORE,
            "deleted_count": len(deleted),
            "deleted": deleted,
            "kept_count": len(kept),
            "kept": kept,
            "error_count": len(errors),
            "errors": errors,
            "message": (
                f"Đã xử lý {len(docs)} tài liệu. "
                f"Đã xóa {len(deleted)} PDF. "
                f"Giữ lại {len(kept)} tài liệu không phải PDF."
            ),
        }

    except Exception as e:

        print(
            "LỖI XÓA PDF:",
            repr(e)
        )

        return {
            "success": False,
            "store": GEMINI_FILE_SEARCH_STORE,
            "deleted_count": 0,
            "deleted": [],
            "error_count": 1,
            "errors": [
                {
                    "error": str(e)
                }
            ],
        }


# =========================================================
# POST /ask
# =========================================================

@app.post("/ask")
async def ask(data: Question):

    question = (
        data.question
        or ""
    ).strip()

    print("")
    print("==============================================")
    print("CÂU HỎI:", question)
    print("MODEL:", GEMINI_MODEL)
    print("==============================================")

    if not question:
        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi.",
        }

    if not GEMINI_API_KEY:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa được "
                "cấu hình Gemini API."
            ),
        }

    if gemini_client is None:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa kết nối được "
                "Gemini API. Vui lòng thử lại sau."
            ),
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa có kho dữ liệu "
                "Gemini File Search."
            ),
        }

    try:

        print(
            "ĐANG GỬI CÂU HỎI GEMINI..."
        )

        answer, sources = (
            await ask_gemini_with_retry(
                question
            )
        )

        print(
            "ĐÃ NHẬN CÂU TRẢ LỜI GEMINI"
        )

        response = {
            "status": "ok",
            "answer": answer,
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
        }

        if sources:
            response["sources"] = sources

        return response

    except asyncio.TimeoutError:

        print("GEMINI TIMEOUT")

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI xử lý quá lâu. "
                "Vui lòng thử lại."
            ),
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
        }

    except Exception as e:

        print(
            "GEMINI KHÔNG TRẢ LỜI:",
            repr(e)
        )

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI tạm thời chưa lấy được "
                "câu trả lời từ kho dữ liệu Gemini. "
                "Vui lòng thử lại sau ít giây."
            ),
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
        }


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
    )
