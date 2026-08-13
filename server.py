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


# ============================================================
# THUY LOI AI - SERVER
# Gemini Interactions API + File Search
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Có thể để trống để hệ thống tự tìm Store theo tên.
GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE", ""
).strip()

GEMINI_FILE_SEARCH_DISPLAY_NAME = os.getenv(
    "GEMINI_FILE_SEARCH_DISPLAY_NAME",
    "THUY LOI AI - KHO HO SO"
).strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

MAX_CONCURRENT = max(
    1, int(os.getenv("MAX_CONCURRENT", "3"))
)

REQUEST_TIMEOUT = max(
    10, int(os.getenv("REQUEST_TIMEOUT", "60"))
)

MAX_RETRIES = max(
    1, int(os.getenv("MAX_RETRIES", "3"))
)


SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi.

MỤC TIÊU:
Trả lời câu hỏi dựa ưu tiên trên kho hồ sơ, tài liệu,
quy định, quy trình và dữ liệu được cung cấp qua Gemini File Search.

NGUYÊN TẮC BẮT BUỘC:

1. Ưu tiên tuyệt đối thông tin trong kho hồ sơ THỦY LỢI AI.
2. Không tự bịa số liệu, điều khoản, tên văn bản, số văn bản,
   ngày tháng, thông số kỹ thuật hoặc quy trình vận hành.
3. Nếu kho không có đủ căn cứ, phải nói rõ:
   "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."
4. Không biến suy đoán thành kết luận chính thức.
5. Khi có căn cứ, nêu nguồn tài liệu nếu có thể xác định.
6. Với câu hỏi pháp luật, ưu tiên văn bản có trong kho.
7. Nếu có nhiều tài liệu liên quan, tổng hợp và chỉ ra điểm khác nhau.
8. Với số liệu, giữ nguyên số liệu và đơn vị theo tài liệu.
9. Với quy trình, trình bày theo từng bước nếu phù hợp.
10. Trả lời bằng tiếng Việt.
11. Trả lời ngắn gọn, rõ ràng, chính xác và phù hợp nghiệp vụ Thủy lợi.
12. Không khẳng định "100% chính xác". Nếu thiếu căn cứ,
    phải nói rõ thiếu căn cứ.
"""


gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
ACTIVE_FILE_SEARCH_STORE = ""


def clean_store_name(value: Any) -> str:
    """Chuẩn hóa Store ID, loại bỏ khoảng trắng và dấu quote thừa."""
    if value is None:
        return ""

    value = str(value).strip()

    if len(value) >= 2:
        if (
            (value[0] == '"' and value[-1] == '"')
            or
            (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1].strip()

    return value


def get_store_display_name(store: Any) -> str:
    return str(
        getattr(store, "display_name", "") or ""
    ).strip()


def get_store_name(store: Any) -> str:
    return clean_store_name(
        getattr(store, "name", "")
    )


def find_file_search_store() -> str:
    """
    Xác định Store theo thứ tự:
    1. GEMINI_FILE_SEARCH_STORE nếu đã cấu hình và tồn tại.
    2. Store có display_name đúng tên cấu hình.
    3. Nếu chỉ có đúng một Store thì dùng Store đó.
    """
    if gemini_client is None:
        return ""

    configured = clean_store_name(
        GEMINI_FILE_SEARCH_STORE
    )

    stores = list(
        gemini_client.file_search_stores.list()
    )

    # 1. Ưu tiên Store ID cấu hình.
    if configured:
        for store in stores:
            store_name = get_store_name(store)
            if store_name == configured:
                return store_name

        # Thử get trực tiếp nếu Store không xuất hiện trong list.
        try:
            store = gemini_client.file_search_stores.get(
                name=configured
            )
            store_name = get_store_name(store)
            if store_name:
                return store_name
        except Exception as e:
            print(
                "STORE CẤU HÌNH KHÔNG TRUY CẬP ĐƯỢC:",
                repr(e)
            )

    # 2. Tìm theo Display Name.
    wanted = (
        GEMINI_FILE_SEARCH_DISPLAY_NAME
        .strip()
        .casefold()
    )

    if wanted:
        for store in stores:
            display_name = (
                get_store_display_name(store)
                .casefold()
            )

            if display_name == wanted:
                return get_store_name(store)

    # 3. Nếu chỉ có một Store thì dùng Store đó.
    if len(stores) == 1:
        only_store = get_store_name(stores[0])
        if only_store:
            return only_store

    return ""


def list_file_search_stores() -> list[dict]:
    if gemini_client is None:
        raise RuntimeError(
            "Gemini API chưa được kết nối."
        )

    result = []

    for store in gemini_client.file_search_stores.list():
        name = get_store_name(store)
        display_name = get_store_display_name(store)

        result.append({
            "name": name,
            "display_name": display_name,
            "active": (
                name == ACTIVE_FILE_SEARCH_STORE
            ),
        })

    return result


def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()

    permanent = [
        "400",
        "401",
        "403",
        "bad request",
        "unauthenticated",
        "permission denied",
        "api key",
        "invalid argument",
        "invalid_request",
        "not found",
        "not_found",
        "resource does not exist",
    ]

    if any(item in text for item in permanent):
        return False

    retryable = [
        "408",
        "409",
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

    return any(
        item in text
        for item in retryable
    )


def call_gemini(question: str):
    if gemini_client is None:
        raise RuntimeError(
            "Gemini API chưa được kết nối."
        )

    store_name = (
        ACTIVE_FILE_SEARCH_STORE
        or find_file_search_store()
    )

    if not store_name:
        raise RuntimeError(
            "Không tìm thấy Gemini File Search Store. "
            "Hãy kiểm tra GEMINI_FILE_SEARCH_STORE "
            "hoặc GEMINI_FILE_SEARCH_DISPLAY_NAME."
        )

    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[
            {
                "type": "file_search",
                "file_search_store_names": [
                    store_name
                ],
            }
        ],
    )


def extract_answer_and_sources(result):
    answer = (
        getattr(result, "output_text", None)
        or ""
    ).strip()

    sources = []

    for step in (
        getattr(result, "steps", [])
        or []
    ):
        if getattr(step, "type", None) != "model_output":
            continue

        for block in (
            getattr(step, "content", [])
            or []
        ):
            if (
                not answer
                and getattr(block, "type", None) == "text"
            ):
                answer += (
                    getattr(block, "text", "")
                    or ""
                )

            for annotation in (
                getattr(block, "annotations", [])
                or []
            ):
                if (
                    getattr(annotation, "type", None)
                    != "file_citation"
                ):
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

                custom_metadata = getattr(
                    annotation,
                    "custom_metadata",
                    None
                )

                if file_name:
                    item["file_name"] = str(file_name)

                if source:
                    item["source"] = str(source)

                if custom_metadata:
                    item["custom_metadata"] = str(
                        custom_metadata
                    )

                if item and item not in sources:
                    sources.append(item)

    answer = answer.strip()

    if not answer:
        raise RuntimeError(
            "Gemini không trả về nội dung."
        )

    return answer, sources


async def ask_gemini_with_retry(question: str):
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

            return extract_answer_and_sources(result)

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
                min(10, 2 ** attempt)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client
    global ACTIVE_FILE_SEARCH_STORE

    print("=" * 60)
    print("              KHỞI ĐỘNG THỦY LỢI AI")
    print("=" * 60)

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
            print(
                "GEMINI API: LỖI KHỞI TẠO:",
                repr(e)
            )

    if gemini_client is not None:
        try:
            ACTIVE_FILE_SEARCH_STORE = find_file_search_store()

            if ACTIVE_FILE_SEARCH_STORE:
                print(
                    "FILE SEARCH STORE:",
                    ACTIVE_FILE_SEARCH_STORE
                )
            else:
                print(
                    "FILE SEARCH STORE: KHÔNG TÌM THẤY"
                )

        except Exception as e:
            ACTIVE_FILE_SEARCH_STORE = ""
            print(
                "FILE SEARCH STORE: LỖI KIỂM TRA:",
                repr(e)
            )
    else:
        ACTIVE_FILE_SEARCH_STORE = ""

    print(
        "STORE CẤU HÌNH:",
        GEMINI_FILE_SEARCH_STORE or "(tự tìm)"
    )
    print(
        "STORE DISPLAY NAME:",
        GEMINI_FILE_SEARCH_DISPLAY_NAME
    )
    print("MODEL:", GEMINI_MODEL)
    print("MAX CONCURRENT:", MAX_CONCURRENT)
    print("REQUEST TIMEOUT:", REQUEST_TIMEOUT)
    print("MAX RETRIES:", MAX_RETRIES)
    print("=" * 60)

    yield

    gemini_client = None
    ACTIVE_FILE_SEARCH_STORE = ""
    print("THỦY LỢI AI ĐÃ DỪNG")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="4.0",
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
        "version": "4.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_connected": (
            gemini_client is not None
        ),
        "file_search_configured": bool(
            GEMINI_FILE_SEARCH_STORE
        ),
        "file_search_store": (
            ACTIVE_FILE_SEARCH_STORE
        ),
        "file_search_ready": bool(
            ACTIVE_FILE_SEARCH_STORE
        ),
        "model": GEMINI_MODEL,
        "max_concurrent": MAX_CONCURRENT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }


@app.get("/stores")
async def stores():
    try:
        items = list_file_search_stores()

        return {
            "success": True,
            "configured_store": clean_store_name(
                GEMINI_FILE_SEARCH_STORE
            ),
            "auto_display_name": (
                GEMINI_FILE_SEARCH_DISPLAY_NAME
            ),
            "active_store": ACTIVE_FILE_SEARCH_STORE,
            "count": len(items),
            "stores": items,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


@app.get("/documents")
async def documents():
    try:
        store_name = (
            ACTIVE_FILE_SEARCH_STORE
            or find_file_search_store()
        )

        if not store_name:
            return {
                "success": False,
                "error": (
                    "Chưa xác định được "
                    "File Search Store."
                ),
            }

        docs = []

        for doc in (
            gemini_client
            .file_search_stores
            .documents
            .list(parent=store_name)
        ):
            docs.append({
                "name": str(
                    getattr(doc, "name", "")
                    or ""
                ),
                "display_name": str(
                    getattr(
                        doc,
                        "display_name",
                        ""
                    )
                    or ""
                ),
            })

        return {
            "success": True,
            "store": store_name,
            "count": len(docs),
            "documents": docs,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": (
            "Gemini Interactions API + File Search"
        ),
        "version": "4.0",
        "endpoints": {
            "home": "/",
            "health": "/health",
            "stores": "/stores",
            "documents": "/documents",
            "ask": "/ask",
            "docs": "/docs",
        },
    }


@app.post("/ask")
async def ask(data: Question):
    global ACTIVE_FILE_SEARCH_STORE

    question = (
        data.question
        if data.question
        else ""
    ).strip()

    print("=" * 60)
    print("CÂU HỎI:", question)
    print("=" * 60)

    if not question:
        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi.",
        }

    if not GEMINI_API_KEY:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa được cấu hình "
                "GEMINI_API_KEY."
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

    if not ACTIVE_FILE_SEARCH_STORE:
        try:
            ACTIVE_FILE_SEARCH_STORE = (
                find_file_search_store()
            )
        except Exception as e:
            print(
                "LỖI TÌM STORE:",
                repr(e)
            )

    if not ACTIVE_FILE_SEARCH_STORE:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa tìm thấy kho "
                "Gemini File Search. "
                "Vui lòng kiểm tra Store."
            ),
        }

    try:
        print(
            "STORE:",
            ACTIVE_FILE_SEARCH_STORE
        )

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
            "engine": (
                "Gemini Interactions API + "
                "File Search"
            ),
        }

        if sources:
            response["sources"] = sources

        return response

    except Exception as e:
        print(
            "GEMINI KHÔNG TRẢ LỜI:",
            repr(e)
        )

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI tạm thời chưa lấy "
                "được câu trả lời từ kho dữ liệu "
                "Gemini. Hệ thống đã tự thử lại. "
                "Vui lòng thử lại sau ít giây."
            ),
            "engine": (
                "Gemini Interactions API + "
                "File Search"
            ),
        }


if __name__ == "__main__":
    import uvicorn

    port = int(
        os.getenv("PORT", "10000")
    )

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
    )
