import os
import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None


# ============================================================
# THỦY LỢI AI
# Gemini API + Gemini File Search
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE", ""
).strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

MAX_CONCURRENT = max(
    1,
    int(os.getenv("MAX_CONCURRENT", "3"))
)

REQUEST_TIMEOUT = max(
    10,
    int(os.getenv("REQUEST_TIMEOUT", "60"))
)

MAX_RETRIES = max(
    1,
    int(os.getenv("MAX_RETRIES", "3"))
)

RETRY_BASE_DELAY = max(
    0.5,
    float(os.getenv("RETRY_BASE_DELAY", "1.5"))
)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("thuyloiai")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="THỦY LỢI AI",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# GEMINI CLIENT
# ============================================================

request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

gemini_client = None

if genai is not None and GEMINI_API_KEY:

    try:

        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )

        logger.info(
            "GEMINI API: ĐÃ KẾT NỐI"
        )

    except Exception:

        logger.exception(
            "GEMINI API: KẾT NỐI THẤT BẠI"
        )

        gemini_client = None

else:

    logger.warning(
        "GEMINI API: CHƯA CẤU HÌNH GEMINI_API_KEY"
    )


logger.info(
    "FILE SEARCH STORE: %s",
    GEMINI_FILE_SEARCH_STORE or "(chưa cấu hình)"
)

logger.info(
    "MODEL: %s",
    GEMINI_MODEL
)

logger.info(
    "MAX CONCURRENT: %s",
    MAX_CONCURRENT
)

logger.info(
    "REQUEST TIMEOUT: %ss",
    REQUEST_TIMEOUT
)

logger.info(
    "MAX RETRIES: %s",
    MAX_RETRIES
)


# ============================================================
# MODEL
# ============================================================

class Question(BaseModel):

    question: str


# ============================================================
# UTILS
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# FILE CITATION
# ============================================================

def annotation_to_source(
    annotation: Any
) -> Optional[dict]:

    if annotation is None:
        return None

    annotation_type = clean_text(
        getattr(annotation, "type", "")
    )

    if (
        annotation_type
        and annotation_type != "file_citation"
    ):
        return None

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

    if file_name:

        item["file_name"] = clean_text(
            file_name
        )

    if source:

        item["source"] = clean_text(
            source
        )

    custom_metadata = getattr(
        annotation,
        "custom_metadata",
        None
    )

    if custom_metadata:

        metadata = {}

        for md in custom_metadata:

            key = getattr(
                md,
                "key",
                None
            )

            if not key:
                continue

            if getattr(
                md,
                "string_value",
                None
            ) is not None:

                metadata[
                    clean_text(key)
                ] = clean_text(
                    md.string_value
                )

            elif getattr(
                md,
                "numeric_value",
                None
            ) is not None:

                metadata[
                    clean_text(key)
                ] = md.numeric_value

            elif getattr(
                md,
                "bool_value",
                None
            ) is not None:

                metadata[
                    clean_text(key)
                ] = md.bool_value

        if metadata:

            item["metadata"] = metadata

    return item or None


# ============================================================
# PARSE INTERACTIONS API
# ============================================================

def extract_interaction_output(
    interaction: Any
):

    answer_parts = []

    sources = []

    output_text = getattr(
        interaction,
        "output_text",
        None
    )

    if output_text:

        answer_parts.append(
            clean_text(output_text)
        )

    for step in (
        getattr(interaction, "steps", None)
        or []
    ):

        step_type = clean_text(
            getattr(step, "type", "")
        )

        if step_type != "model_output":
            continue

        for content_block in (
            getattr(step, "content", None)
            or []
        ):

            block_type = clean_text(
                getattr(
                    content_block,
                    "type",
                    ""
                )
            )

            if block_type != "text":
                continue

            text = getattr(
                content_block,
                "text",
                None
            )

            if text:

                text = clean_text(text)

                if (
                    text
                    and text not in answer_parts
                ):

                    answer_parts.append(text)

            for annotation in (
                getattr(
                    content_block,
                    "annotations",
                    None
                )
                or []
            ):

                source = annotation_to_source(
                    annotation
                )

                if (
                    source
                    and source not in sources
                ):

                    sources.append(source)

    answer = "\n\n".join(
        p for p in answer_parts
        if p
    ).strip()

    if not answer:

        raise RuntimeError(
            "Gemini không trả về nội dung."
        )

    return answer, sources


# ============================================================
# FALLBACK: GENERATE CONTENT
# ============================================================

def extract_generate_content_output(
    response: Any
):

    answer = clean_text(
        getattr(
            response,
            "text",
            None
        )
    )

    sources = []

    for candidate in (
        getattr(
            response,
            "candidates",
            None
        )
        or []
    ):

        metadata = getattr(
            candidate,
            "grounding_metadata",
            None
        )

        if metadata is None:

            metadata = getattr(
                candidate,
                "groundingMetadata",
                None
            )

        if metadata is None:
            continue

        chunks = getattr(
            metadata,
            "grounding_chunks",
            None
        )

        if chunks is None:

            chunks = getattr(
                metadata,
                "groundingChunks",
                None
            )

        for chunk in chunks or []:

            retrieved = getattr(
                chunk,
                "retrieved_context",
                None
            )

            if retrieved is None:

                retrieved = getattr(
                    chunk,
                    "retrievedContext",
                    None
                )

            if retrieved is None:
                continue

            item = {}

            title = getattr(
                retrieved,
                "title",
                None
            )

            uri = getattr(
                retrieved,
                "uri",
                None
            )

            if title:

                item["file_name"] = clean_text(
                    title
                )

            if uri:

                item["source"] = clean_text(
                    uri
                )

            if (
                item
                and item not in sources
            ):

                sources.append(item)

    if not answer:

        raise RuntimeError(
            "Gemini không trả về nội dung."
        )

    return answer, sources


# ============================================================
# GEMINI - INTERACTIONS API
# ============================================================

def call_interactions(
    question: str
):

    if gemini_client is None:

        raise RuntimeError(
            "Gemini client chưa được khởi tạo."
        )

    interaction = (
        gemini_client.interactions.create(

            model=GEMINI_MODEL,

            input=question,

            system_instruction=(
                "Bạn là THỦY LỢI AI, trợ lý "
                "chuyên môn về thủy lợi của "
                "Chi nhánh Thủy lợi Vu Gia - Thu Bồn.\n\n"

                "Ưu tiên tuyệt đối thông tin "
                "tìm được trong kho hồ sơ được "
                "cung cấp.\n\n"

                "Trả lời bằng tiếng Việt, "
                "rõ ràng, chính xác, có cấu trúc.\n\n"

                "Nếu câu hỏi có thông tin cụ thể "
                "trong hồ sơ, hãy nêu đúng số liệu, "
                "tên văn bản, thời gian hoặc địa điểm "
                "nếu có.\n\n"

                "Không tự bịa thông tin.\n\n"

                "Nếu kho hồ sơ không có đủ căn cứ, "
                "hãy nói rõ là chưa tìm thấy căn cứ "
                "trong kho dữ liệu."
            ),

            tools=[
                {
                    "type": "file_search",

                    "file_search_store_names": [
                        GEMINI_FILE_SEARCH_STORE
                    ],
                }
            ],
        )
    )

    return extract_interaction_output(
        interaction
    )


# ============================================================
# FALLBACK - GENERATE CONTENT
# ============================================================

def call_generate_content(
    question: str
):

    if gemini_client is None:

        raise RuntimeError(
            "Gemini client chưa được khởi tạo."
        )

    if types is None:

        raise RuntimeError(
            "google.genai.types chưa khả dụng."
        )

    response = (
        gemini_client.models.generate_content(

            model=GEMINI_MODEL,

            contents=question,

            config=types.GenerateContentConfig(

                system_instruction=(
                    "Bạn là THỦY LỢI AI, trợ lý "
                    "chuyên môn về thủy lợi của "
                    "Chi nhánh Thủy lợi Vu Gia - Thu Bồn. "
                    "Ưu tiên thông tin trong kho hồ sơ. "
                    "Trả lời bằng tiếng Việt, chính xác, "
                    "không tự bịa. "
                    "Nếu không có căn cứ trong kho, "
                    "nói rõ điều đó."
                ),

                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[
                                GEMINI_FILE_SEARCH_STORE
                            ]
                        )
                    )
                ],
            ),
        )
    )

    return extract_generate_content_output(
        response
    )


# ============================================================
# GEMINI MASTER
# ============================================================

def call_gemini(
    question: str
):

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "THỦY LỢI AI chưa được cấu hình GEMINI_API_KEY."
        )

    if gemini_client is None:

        raise RuntimeError(
            "THỦY LỢI AI chưa kết nối được Gemini API."
        )

    if not GEMINI_FILE_SEARCH_STORE:

        raise RuntimeError(
            "THỦY LỢI AI chưa có GEMINI_FILE_SEARCH_STORE."
        )

    # --------------------------------------------------------
    # ƯU TIÊN INTERACTIONS API
    # --------------------------------------------------------

    try:

        logger.info(
            "GEMINI: DÙNG INTERACTIONS API"
        )

        return call_interactions(
            question
        )

    except Exception as interaction_error:

        logger.warning(
            "Interactions API lỗi."
        )

        logger.warning(
            "Chi tiết: %s",
            repr(interaction_error)
        )

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    logger.info(
        "GEMINI: CHUYỂN SANG GENERATE CONTENT"
    )

    return call_generate_content(
        question
    )


# ============================================================
# RETRY
# ============================================================

async def ask_gemini_with_retry(
    question: str
):

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            async with request_semaphore:

                logger.info(
                    "GỬI CÂU HỎI GEMINI "
                    "| attempt=%s/%s "
                    "| %s",
                    attempt,
                    MAX_RETRIES,
                    question
                )

                result = await asyncio.wait_for(

                    asyncio.to_thread(
                        call_gemini,
                        question
                    ),

                    timeout=REQUEST_TIMEOUT
                )

                logger.info(
                    "ĐÃ NHẬN CÂU TRẢ LỜI GEMINI"
                )

                return result

        except Exception as exc:

            last_error = exc

            logger.exception(
                "GEMINI LỖI "
                "| attempt=%s/%s",
                attempt,
                MAX_RETRIES
            )

            if attempt < MAX_RETRIES:

                delay = (
                    RETRY_BASE_DELAY
                    * (2 ** (attempt - 1))
                )

                logger.info(
                    "CHỜ %.1f GIÂY RỒI THỬ LẠI",
                    delay
                )

                await asyncio.sleep(
                    delay
                )

    raise (
        last_error
        or RuntimeError(
            "Gemini không thể xử lý câu hỏi."
        )
    )


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():

    if INDEX_FILE.exists():

        return FileResponse(
            str(INDEX_FILE),
            media_type="text/html"
        )

    return {
        "status": "ok",

        "service": "THỦY LỢI AI",

        "message": (
            "Backend Gemini đang hoạt động "
            "nhưng chưa tìm thấy index.html."
        ),

        "health": "/health",

        "ask": "/ask"
    }


# ============================================================
# HEAD
# ============================================================

@app.head("/")
async def home_head():

    return {}


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {

        "status": "ok",

        "service": "THỦY LỢI AI",

        "engine": "Gemini File Search",

        "gemini_configured": bool(
            GEMINI_API_KEY
        ),

        "gemini_connected": (
            gemini_client is not None
        ),

        "file_search_configured": bool(
            GEMINI_FILE_SEARCH_STORE
        ),

        "model": GEMINI_MODEL,

        "max_concurrent": MAX_CONCURRENT,

        "request_timeout": REQUEST_TIMEOUT,

        "max_retries": MAX_RETRIES
    }


# ============================================================
# ASK
# ============================================================

@app.post("/ask")
async def ask(data: Question):

    question = clean_text(
        data.question
    )

    logger.info(
        "=" * 60
    )

    logger.info(
        "CÂU HỎI: %s",
        question
    )

    logger.info(
        "=" * 60
    )

    # --------------------------------------------------------
    # KIỂM TRA CÂU HỎI
    # --------------------------------------------------------

    if not question:

        return {

            "status": "error",

            "answer":
                "Vui lòng nhập câu hỏi."
        }

    # --------------------------------------------------------
    # KIỂM TRA API KEY
    # --------------------------------------------------------

    if not GEMINI_API_KEY:

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI chưa được cấu hình Gemini API."
        }

    # --------------------------------------------------------
    # KIỂM TRA CLIENT
    # --------------------------------------------------------

    if gemini_client is None:

        return {

            "status": "error",

            "answer": (
                "THỦY LỢI AI chưa kết nối được "
                "Gemini API. Vui lòng thử lại sau."
            )
        }

    # --------------------------------------------------------
    # KIỂM TRA FILE SEARCH STORE
    # --------------------------------------------------------

    if not GEMINI_FILE_SEARCH_STORE:

        return {

            "status": "error",

            "answer": (
                "THỦY LỢI AI chưa có kho dữ liệu "
                "Gemini File Search."
            )
        }

    # --------------------------------------------------------
    # GỌI GEMINI
    # --------------------------------------------------------

    try:

        answer, sources = (
            await ask_gemini_with_retry(
                question
            )
        )

        response = {

            "status": "ok",

            "answer": answer,

            "engine": "Gemini File Search"
        }

        if sources:

            response["sources"] = sources

        return response

    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    except asyncio.TimeoutError:

        return {

            "status": "error",

            "answer": (
                "THỦY LỢI AI xử lý quá lâu. "
                "Hệ thống đã tự thử lại nhưng "
                "chưa nhận được kết quả. "
                "Vui lòng thử lại sau ít giây."
            ),

            "engine": "Gemini File Search"
        }

    # --------------------------------------------------------
    # LỖI KHÁC
    # --------------------------------------------------------

    except Exception as exc:

        logger.exception(
            "GEMINI KHÔNG TRẢ LỜI"
        )

        return {

            "status": "error",

            "answer": (
                "THỦY LỢI AI tạm thời chưa lấy "
                "được câu trả lời từ kho dữ liệu Gemini. "
                "Hệ thống đã tự thử lại. "
                "Vui lòng thử lại sau ít giây."
            ),

            "engine": "Gemini File Search",

            "detail": clean_text(exc)
        }


# ============================================================
# CONFIG - KIỂM TRA
# Không hiển thị API KEY
# ============================================================

@app.get("/config")
async def config():

    return {

        "service": "THỦY LỢI AI",

        "model": GEMINI_MODEL,

        "file_search_store":
            GEMINI_FILE_SEARCH_STORE,

        "gemini_configured":
            bool(GEMINI_API_KEY),

        "gemini_connected":
            gemini_client is not None
    }


# ============================================================
# RUN
# ============================================================

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
        port=port
    )
