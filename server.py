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
# CẤU HÌNH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE", ""
).strip()

# Model mặc định tiết kiệm chi phí.
# Có thể ghi đè bằng Environment Variable trên Render.
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.1-flash-lite"
).strip()

MAX_CONCURRENT = max(
    1,
    int(os.getenv("MAX_CONCURRENT", "2"))
)

REQUEST_TIMEOUT = max(
    10,
    int(os.getenv("REQUEST_TIMEOUT", "60"))
)

MAX_RETRIES = max(
    1,
    int(os.getenv("MAX_RETRIES", "2"))
)


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi
của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời câu hỏi dựa trên kho hồ sơ, tài liệu, quy định,
quy trình và dữ liệu đã được đưa vào Gemini File Search.

NGUYÊN TẮC BẮT BUỘC:

1. Ưu tiên tuyệt đối thông tin tìm được trong kho hồ sơ
   THỦY LỢI AI.

2. Không tự bịa số liệu, điều khoản, tên văn bản,
   số văn bản, ngày tháng, thông số kỹ thuật,
   diện tích, công suất hoặc quy trình vận hành.

3. Nếu kho tài liệu không có đủ căn cứ để trả lời,
   nói rõ:
   "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."

4. Khi tìm thấy tài liệu liên quan, phải tổng hợp
   thông tin chính xác và dễ hiểu.

5. Khi có thể xác định nguồn, nêu tên tài liệu nguồn.

6. Với câu hỏi pháp luật, quy định, quy trình:
   ưu tiên văn bản có trong kho hồ sơ.

7. Nếu có nhiều tài liệu liên quan:
   tổng hợp và chỉ ra điểm giống, khác hoặc thay đổi
   nếu tài liệu cho phép xác định.

8. Không biến suy đoán thành kết luận chính thức.

9. Trả lời bằng tiếng Việt.

10. Ưu tiên:
    - chính xác
    - ngắn gọn
    - dễ hiểu
    - có căn cứ
    - phù hợp nghiệp vụ Thủy lợi.

11. Với quy trình:
    có thể trình bày thành từng bước.

12. Với số liệu:
    giữ nguyên số liệu và đơn vị theo tài liệu.

13. Nếu câu hỏi yêu cầu một thông tin cụ thể như:
    tên người, chức vụ, trạm bơm, công trình,
    diện tích, số lượng, ngày tháng, thông số...
    hãy tìm trực tiếp trong kho trước khi trả lời.

14. Nếu không tìm thấy thông tin trong kho,
    không được suy đoán từ kiến thức chung.

15. Không nói rằng bạn đã đọc toàn bộ kho tài liệu.
    Chỉ trả lời dựa trên các nội dung File Search thực sự
    truy xuất được cho câu hỏi hiện tại.
"""


# ============================================================
# GEMINI CLIENT
# ============================================================

gemini_client = None

request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global gemini_client

    print("")
    print("==============================================")
    print("          KHỞI ĐỘNG THỦY LỢI AI")
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

            print(
                "GEMINI API: LỖI KHỞI TẠO",
                repr(e)
            )

    print(
        "FILE SEARCH STORE:",
        GEMINI_FILE_SEARCH_STORE
        if GEMINI_FILE_SEARCH_STORE
        else "CHƯA CẤU HÌNH"
    )

    print("MODEL:", GEMINI_MODEL)

    print(
        "MAX CONCURRENT:",
        MAX_CONCURRENT
    )

    print(
        "REQUEST TIMEOUT:",
        REQUEST_TIMEOUT
    )

    print(
        "MAX RETRIES:",
        MAX_RETRIES
    )

    print("==============================================")
    print("")

    yield

    gemini_client = None

    print("THỦY LỢI AI ĐÃ DỪNG")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="4.0",
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DATA MODEL
# ============================================================

class Question(BaseModel):
    question: str


# ============================================================
# HOME
# ============================================================

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
    }


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

        "max_retries": MAX_RETRIES,
    }


# ============================================================
# API INFO
# ============================================================

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

            "ask": "/ask",

        },
    }


# ============================================================
# KIỂM TRA LỖI CÓ THỂ RETRY
# ============================================================

def is_retryable_error(
    error: Exception
) -> bool:

    text = str(error).lower()

    permanent_errors = [

        "400",

        "401",

        "403",

        "bad request",

        "unauthenticated",

        "permission denied",

        "api key",

        "invalid argument",

        "not found",

    ]

    for item in permanent_errors:

        if item in text:

            return False

    retryable_errors = [

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

    for item in retryable_errors:

        if item in text:

            return True

    return False


# ============================================================
# GỌI GEMINI FILE SEARCH
# ============================================================

def call_gemini(
    question: str
):

    if gemini_client is None:

        raise RuntimeError(
            "Gemini API chưa được kết nối."
        )

    if not GEMINI_FILE_SEARCH_STORE:

        raise RuntimeError(
            "Gemini File Search Store chưa được cấu hình."
        )

    result = gemini_client.interactions.create(

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

    return result


# ============================================================
# TRÍCH XUẤT CÂU TRẢ LỜI + NGUỒN
# ============================================================

def extract_answer_and_sources(
    result: Any
):

    answer_parts = []

    sources = []

    # --------------------------------------------------------
    # output_text
    # --------------------------------------------------------

    output_text = getattr(
        result,
        "output_text",
        None
    )

    if output_text:

        answer_parts.append(
            str(output_text).strip()
        )

    # --------------------------------------------------------
    # steps
    # --------------------------------------------------------

    steps = getattr(
        result,
        "steps",
        None
    )

    if steps is None:

        steps = []

    for step in steps:

        step_type = getattr(
            step,
            "type",
            None
        )

        if step_type != "model_output":

            continue

        content_list = getattr(
            step,
            "content",
            None
        )

        if content_list is None:

            continue

        for block in content_list:

            block_type = getattr(
                block,
                "type",
                None
            )

            # ------------------------------------------------
            # TEXT
            # ------------------------------------------------

            if block_type == "text":

                text = getattr(
                    block,
                    "text",
                    None
                )

                if text:

                    text = str(
                        text
                    ).strip()

                    if text:

                        if text not in answer_parts:

                            answer_parts.append(
                                text
                            )

            # ------------------------------------------------
            # ANNOTATIONS
            # ------------------------------------------------

            annotations = getattr(
                block,
                "annotations",
                None
            )

            if annotations is None:

                continue

            for annotation in annotations:

                annotation_type = getattr(
                    annotation,
                    "type",
                    None
                )

                if annotation_type != "file_citation":

                    continue

                source_item = {}

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

                document_uri = getattr(
                    annotation,
                    "document_uri",
                    None
                )

                if file_name:

                    source_item[
                        "file_name"
                    ] = str(file_name)

                if source:

                    source_item[
                        "source"
                    ] = str(source)

                if page_number is not None:

                    source_item[
                        "page_number"
                    ] = page_number

                if document_uri:

                    source_item[
                        "document_uri"
                    ] = str(document_uri)

                if source_item:

                    if source_item not in sources:

                        sources.append(
                            source_item
                        )

    # --------------------------------------------------------
    # GỘP ANSWER
    # --------------------------------------------------------

    answer = "\n".join(

        part
        for part in answer_parts
        if part

    ).strip()

    if not answer:

        raise RuntimeError(
            "Gemini không trả về nội dung."
        )

    return answer, sources


# ============================================================
# GỌI GEMINI CÓ RETRY
# ============================================================

async def ask_gemini_with_retry(
    question: str
):

    last_error = None

    for attempt in range(
        MAX_RETRIES
    ):

        try:

            async with request_semaphore:

                result = await asyncio.wait_for(

                    asyncio.to_thread(

                        call_gemini,

                        question

                    ),

                    timeout=REQUEST_TIMEOUT,

                )

            answer, sources = (
                extract_answer_and_sources(
                    result
                )
            )

            return answer, sources

        except Exception as e:

            last_error = e

            print(
                "LỖI GEMINI:",
                repr(e)
            )

            retryable = (
                is_retryable_error(e)
            )

            print(
                "RETRYABLE:",
                retryable
            )

            if (
                not retryable
                or attempt >= MAX_RETRIES - 1
            ):

                break

            delay = min(
                8,
                2 ** attempt
            ) + random.uniform(
                0,
                0.5
            )

            print(
                f"THỬ LẠI "
                f"LẦN {attempt + 2}/"
                f"{MAX_RETRIES} "
                f"SAU {delay:.1f} GIÂY..."
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
# ASK
# ============================================================

@app.post("/ask")
async def ask(
    data: Question
):

    question = (
        data.question
        or ""
    ).strip()

    print("")
    print("==============================================")
    print("CÂU HỎI:", question)
    print("MODEL:", GEMINI_MODEL)
    print("==============================================")


    # --------------------------------------------------------
    # KIỂM TRA CÂU HỎI
    # --------------------------------------------------------

    if not question:

        return {

            "status": "error",

            "answer":
                "Vui lòng nhập câu hỏi.",

        }


    # --------------------------------------------------------
    # KIỂM TRA API KEY
    # --------------------------------------------------------

    if not GEMINI_API_KEY:

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI chưa được cấu hình Gemini API.",

        }


    # --------------------------------------------------------
    # KIỂM TRA CLIENT
    # --------------------------------------------------------

    if gemini_client is None:

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI chưa kết nối được Gemini API. "
                "Vui lòng thử lại sau.",

        }


    # --------------------------------------------------------
    # KIỂM TRA FILE SEARCH
    # --------------------------------------------------------

    if not GEMINI_FILE_SEARCH_STORE:

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI chưa có kho dữ liệu "
                "Gemini File Search.",

        }


    # --------------------------------------------------------
    # GỌI GEMINI
    # --------------------------------------------------------

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

            "engine":
                "Gemini File Search",

            "model":
                GEMINI_MODEL,

        }

        if sources:

            response[
                "sources"
            ] = sources

        return response


    except asyncio.TimeoutError:

        print(
            "GEMINI TIMEOUT"
        )

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI xử lý quá lâu. "
                "Vui lòng thử lại.",

            "engine":
                "Gemini File Search",

            "model":
                GEMINI_MODEL,

        }


    except Exception as e:

        print(
            "GEMINI KHÔNG TRẢ LỜI:",
            repr(e)
        )

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI tạm thời chưa lấy được "
                "câu trả lời từ kho dữ liệu Gemini. "
                "Hệ thống đã tự thử lại. "
                "Vui lòng thử lại sau ít giây.",

            "engine":
                "Gemini File Search",

            "model":
                GEMINI_MODEL,

        }


# ============================================================
# RUN LOCAL
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

        port=port,

    )
