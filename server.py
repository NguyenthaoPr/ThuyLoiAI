import os
import asyncio
import random
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai


# =========================================================
# THỦY LỢI AI - GEMINI
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

# =========================================================
# GEMINI CONFIG
# =========================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_FILE_SEARCH_STORE = os.getenv(
    "GEMINI_FILE_SEARCH_STORE",
    ""
).strip()

# Model ổn định hiện tại
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()

# =========================================================
# BẢO VỆ SERVER
# =========================================================

MAX_CONCURRENT = int(
    os.getenv("MAX_CONCURRENT", "3")
)

REQUEST_TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "60")
)

MAX_RETRIES = int(
    os.getenv("MAX_RETRIES", "3")
)


# =========================================================
# PROMPT CHUYÊN NGÀNH
# =========================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI.

Bạn là trợ lý AI chuyên ngành Thủy lợi của
Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời các câu hỏi dựa trên kho hồ sơ,
tài liệu, quy định, quy trình và dữ liệu
đã được đưa vào Gemini File Search.

NGUYÊN TẮC BẮT BUỘC:

1. Ưu tiên thông tin trong kho hồ sơ THỦY LỢI AI.

2. Không tự bịa:
- số liệu
- điều khoản
- tên văn bản
- số văn bản
- ngày tháng
- thông số kỹ thuật
- quy trình vận hành.

3. Nếu không tìm thấy thông tin phù hợp
trong kho hồ sơ, phải nói rõ:

"Chưa tìm thấy đủ căn cứ trong kho hồ sơ
THỦY LỢI AI."

4. Nếu tài liệu có thông tin liên quan,
hãy tổng hợp và phân tích rõ ràng.

5. Khi có thể xác định nguồn tài liệu,
hãy nêu tên tài liệu hoặc nguồn.

6. Nếu câu hỏi liên quan đến quy định pháp luật,
ưu tiên văn bản có trong kho hồ sơ.

7. Nếu có nhiều tài liệu khác nhau,
hãy so sánh và chỉ ra điểm khác nhau.

8. Không được biến suy đoán của AI thành
kết luận chính thức.

9. Trả lời bằng tiếng Việt.

10. Ưu tiên:
- ngắn gọn
- chính xác
- dễ hiểu
- có căn cứ
- phù hợp nghiệp vụ Thủy lợi.

11. Với câu hỏi yêu cầu quy trình,
có thể trình bày theo từng bước.

12. Với câu hỏi về số liệu,
phải giữ nguyên đơn vị và số liệu
theo tài liệu.
"""


# =========================================================
# GEMINI CLIENT
# =========================================================

gemini_client = None

request_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT
)


# =========================================================
# LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global gemini_client

    print("")
    print("====================================")
    print("       KHỞI ĐỘNG THỦY LỢI AI")
    print("====================================")

    print("KIỂM TRA GEMINI...")

    if GEMINI_API_KEY:

        try:

            gemini_client = genai.Client(
                api_key=GEMINI_API_KEY
            )

            print("GEMINI API: ĐÃ KẾT NỐI")

        except Exception as e:

            print("GEMINI API: LỖI")
            print(repr(e))

            gemini_client = None

    else:

        print("GEMINI API: CHƯA CÓ API KEY")

        gemini_client = None

    if GEMINI_FILE_SEARCH_STORE:

        print(
            "FILE SEARCH STORE:",
            GEMINI_FILE_SEARCH_STORE
        )

    else:

        print(
            "FILE SEARCH STORE: CHƯA CẤU HÌNH"
        )

    print(
        "MODEL:",
        GEMINI_MODEL
    )

    print(
        "MAX CONCURRENT:",
        MAX_CONCURRENT
    )

    print("====================================")

    yield

    gemini_client = None

    print(
        "THỦY LỢI AI ĐÃ DỪNG"
    )


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="2.0",
    lifespan=lifespan
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"],
)


# =========================================================
# QUESTION
# =========================================================

class Question(BaseModel):

    question: str


# =========================================================
# TRANG CHỦ
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
        "engine": "Gemini File Search"
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    return {

        "status": "ok",

        "service": "THỦY LỢI AI",

        "engine": "Gemini File Search",

        "gemini_configured":
            bool(GEMINI_API_KEY),

        "file_search_configured":
            bool(GEMINI_FILE_SEARCH_STORE),

        "model":
            GEMINI_MODEL,

        "max_concurrent":
            MAX_CONCURRENT

    }


# =========================================================
# API INFO
# =========================================================

@app.get("/api")
async def api_info():

    return {

        "status": "ok",

        "service": "THỦY LỢI AI",

        "engine": "Gemini",

        "endpoints": {

            "home": "/",

            "health": "/health",

            "ask": "/ask"

        }

    }


# =========================================================
# GỌI GEMINI
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

    result = gemini_client.interactions.create(

        model=GEMINI_MODEL,

        input=(
            SYSTEM_PROMPT
            + "\n\n"
            + "CÂU HỎI CỦA NGƯỜI DÙNG:\n"
            + question
        ),

        tools=[

            {

                "type": "file_search",

                "file_search_store_names": [

                    GEMINI_FILE_SEARCH_STORE

                ]

            }

        ]

    )

    return result


# =========================================================
# LẤY NỘI DUNG TRẢ LỜI
# =========================================================

def extract_answer(result):

    # Cách 1
    output_text = getattr(
        result,
        "output_text",
        None
    )

    if output_text:

        return output_text.strip()


    # Cách 2
    answers = []

    for step in (
        getattr(result, "steps", [])
        or []
    ):

        if getattr(
            step,
            "type",
            None
        ) != "model_output":

            continue


        for block in (
            getattr(
                step,
                "content",
                []
            )
            or []
        ):

            if getattr(
                block,
                "type",
                None
            ) == "text":

                text = getattr(
                    block,
                    "text",
                    ""
                )

                if text:

                    answers.append(text)


    answer = "\n".join(
        answers
    ).strip()


    if answer:

        return answer


    raise RuntimeError(
        "Gemini không trả về nội dung."
    )


# =========================================================
# TỰ ĐỘNG THỬ LẠI
# =========================================================

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

                    timeout=REQUEST_TIMEOUT

                )


            answer = extract_answer(
                result
            )

            return answer


        except Exception as e:

            last_error = e

            error_text = str(e).lower()


            print("")
            print(
                "LỖI GEMINI:",
                repr(e)
            )


            # Các lỗi có khả năng tạm thời
            retryable = any(

                word in error_text

                for word in [

                    "429",

                    "rate limit",

                    "resource exhausted",

                    "503",

                    "unavailable",

                    "timeout",

                    "deadline",

                    "temporarily",

                    "internal",

                    "connection"

                ]

            )


            if not retryable:

                break


            if attempt >= (
                MAX_RETRIES - 1
            ):

                break


            delay = min(

                10,

                2 ** attempt

            ) + random.uniform(
                0,
                0.5
            )


            print(
                f"THỬ LẠI SAU {delay:.1f} GIÂY..."
            )


            await asyncio.sleep(
                delay
            )


    raise last_error


# =========================================================
# ASK
# =========================================================

@app.post("/ask")
async def ask(data: Question):

    question = (
        data.question
        .strip()
    )


    print("")
    print(
        "===================================="
    )

    print(
        "CÂU HỎI:",
        question
    )

    print(
        "===================================="
    )


    if not question:

        return {

            "status": "error",

            "answer":
                "Vui lòng nhập câu hỏi."

        }


    if not GEMINI_API_KEY:

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI chưa được cấu hình Gemini API."

        }


    if not GEMINI_FILE_SEARCH_STORE:

        return {

            "status": "error",

            "answer":
                "THỦY LỢI AI chưa có kho dữ liệu Gemini File Search."

        }


    try:

        print(
            "ĐANG GỬI CÂU HỎI GEMINI..."
        )


        answer = await (
            ask_gemini_with_retry(
                question
            )
        )


        print(
            "ĐÃ NHẬN CÂU TRẢ LỜI GEMINI"
        )


        return {

            "status": "ok",

            "answer": answer,

            "engine":
                "Gemini File Search"

        }


    except Exception as e:

        print("")
        print(
            "===================================="
        )

        print(
            "GEMINI KHÔNG TRẢ LỜI"
        )

        print(
            repr(e)
        )

        print(
            "===================================="
        )


        return {

            "status": "error",

            "answer": (
                "THỦY LỢI AI tạm thời "
                "chưa lấy được câu trả lời "
                "từ kho dữ liệu Gemini. "
                "Vui lòng thử lại sau ít giây."
            ),

            "engine":
                "Gemini File Search",

            "detail":
                str(e)

        }


# =========================================================
# CHẠY LOCAL
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

        port=port

    )
