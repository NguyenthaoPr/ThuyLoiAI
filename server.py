import os
import re
import asyncio
import random
import tempfile
import time
import hashlib
import base64
from PIL import Image, ImageOps
from io import BytesIO
from collections import OrderedDict
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from google import genai
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether,
)
from reportlab.lib import colors
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from datetime import datetime

# ============================================================
# THỦY LỢI AI - SERVER.PY
# BẢN NÂNG CẤP ỔN ĐỊNH - GIỮ NGUYÊN KIẾN TRÚC HIỆN TẠI
#
# ĐÃ SỬA (so với bản trước):
# 1. extract_answer_and_sources(): return/raise đã ở ngoài
#    try/except nên luôn trả về giá trị, không còn crash khi
#    trích citation thành công.
# 2. /image-analyze: sửa lỗi thụt lề sai (mix nhiều mức indent
#    trong cùng một khối try) từng khiến server bị SyntaxError
#    và không khởi động được.
# 3. SYSTEM_PROMPT: bỏ placeholder "{question}" ở cuối vì không
#    hề được .format() ở bất kỳ đâu -> trước đây Gemini nhận
#    nguyên văn chữ "{question}" thay vì câu hỏi thật. Câu hỏi
#    thật vẫn luôn được gửi riêng qua input/gemini_input.
# 4. (MỚI) Bỏ định nghĩa TRÙNG LẶP của "/field-report": trước đây
#    có 2 hàm cùng gắn @app.post("/field-report"). FastAPI chỉ
#    dùng route đăng ký đầu tiên nên hàm thứ hai không bao giờ
#    chạy -> đã gộp lại thành một hàm duy nhất, giữ bản có
#    report_title/report_type dạng Form (khớp với /field-report-pdf).
# 5. (MỚI) Sửa lỗi thụt lề nghiêm trọng trong
#    create_field_report_pdf(): phần xử lý nội dung từng bị dedent
#    ra khỏi thân hàm, và doc.build()/return buffer từng nằm lồng
#    sai bên trong vòng lặp "for line in lines" (chỉ chạy ở lần
#    lặp cuối, không phải return hợp lệ của hàm) -> khiến hàm trả
#    về None và /field-report-pdf sẽ crash. Đã đưa toàn bộ khối xử
#    lý về đúng cấp thụt lề của hàm, và doc.build()/return buffer
#    được đặt sau vòng lặp, ở cấp hàm.
# 6. (MỚI) NÂNG CẤP PDF: thiết kế chuyên nghiệp, header/footer,
#    bảng, màu sắc, font Unicode, phân cấp rõ ràng.
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

# ----------------------------
# BẢO VỆ HỆ THỐNG
# ----------------------------
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "2")))
REQUEST_TIMEOUT = max(15, int(os.getenv("REQUEST_TIMEOUT", "45")))
QUEUE_TIMEOUT = max(5, int(os.getenv("QUEUE_TIMEOUT", "20")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "2")))
MAX_QUESTION_LENGTH = max(100, int(os.getenv("MAX_QUESTION_LENGTH", "2000")))

# Upload: tránh một file quá lớn làm cạn RAM server.
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "25")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_OPERATION_TIMEOUT = max(
    30, int(os.getenv("UPLOAD_OPERATION_TIMEOUT", "300"))
)

# ----------------------------
# CACHE ỔN ĐỊNH TRONG PROCESS
# Giữ cache hiện tại nhưng nâng từ 200 -> 1000.
# Đây vẫn là RAM cache; tầng lưu bền vững sẽ làm ở bước sau.
# ----------------------------
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() in {
    "1", "true", "yes", "on"
}
CACHE_TTL = max(60, int(os.getenv("CACHE_TTL", "3600")))
CACHE_MAX_ENTRIES = max(100, int(os.getenv("CACHE_MAX_ENTRIES", "1000")))

_answer_cache = OrderedDict()
_cache_lock = asyncio.Lock()

# Chống nhiều request cùng lúc hỏi đúng một câu MISS.
_inflight = {}
_inflight_lock = asyncio.Lock()

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý chuyên ngành Thủy lợi của
Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
Trả lời chính xác, ngắn gọn, có căn cứ dựa trên:
- Hồ sơ, tài liệu, quy định, quy trình trong kho THỦY LỢI AI.
- Dữ liệu được cung cấp trong câu hỏi.
- Hình ảnh do người dùng gửi.
- Kết quả File Search khi công cụ được sử dụng.

==================================================
I. NGUYÊN TẮC CHUNG
==================================================

1. Ưu tiên thông tin trong kho hồ sơ THỦY LỢI AI.

2. Không tự bịa:
   - số liệu
   - tên công trình
   - thông số kỹ thuật
   - tên văn bản
   - số hiệu văn bản
   - ngày tháng
   - quy trình vận hành
   - kết luận chuyên môn.

3. Nếu thông tin không có căn cứ, phải nói rõ:
   "Chưa tìm thấy đủ căn cứ trong kho hồ sơ THỦY LỢI AI."

4. Nếu câu hỏi có nhiều dữ liệu liên quan:
   - tổng hợp;
   - phân tích;
   - chỉ ra điểm giống và khác;
   - nêu rõ nguồn nếu xác định được.

5. Với văn bản, quy định và quy trình:
   ưu tiên nội dung có trong hồ sơ THỦY LỢI AI.

6. Nếu có nhiều tài liệu khác nhau:
   không tự ý chọn một tài liệu nếu chưa có căn cứ;
   phải nêu sự khác nhau giữa các tài liệu.

7. Không biến suy đoán thành kết luận chính thức.

8. Trả lời bằng tiếng Việt.

9. Ưu tiên cách trình bày:
   rõ ràng, ngắn gọn, có cấu trúc, dễ sử dụng trong công việc
   thủy lợi thực tế.

10. Với số liệu:
    giữ nguyên số liệu và đơn vị theo tài liệu.

==================================================
II. KHI NGƯỜI DÙNG GỬI HÌNH ẢNH
==================================================

Khi có hình ảnh, phải thực hiện theo trình tự sau:

BƯỚC 1 - QUAN SÁT HÌNH ẢNH

Chỉ mô tả những gì thực sự quan sát được.

Có thể nhận diện:
- công trình;
- nhà trạm;
- máy bơm;
- cửa van;
- kênh;
- đường ống;
- thiết bị;
- khu vực sản xuất;
- mực nước;
- dòng chảy;
- biển báo;
- chữ, số và thông tin xuất hiện trên ảnh.

Không khẳng định những chi tiết không thể xác định chắc chắn từ hình ảnh.

Nếu không nhìn rõ:
nói rõ "Hình ảnh chưa đủ rõ để xác định."

--------------------------------------------------

BƯỚC 2 - ĐỌC THÔNG TIN TRÊN ẢNH

Nếu ảnh có:
- chữ;
- số;
- tên công trình;
- ngày tháng;
- thông số;
- biển báo;
- sơ đồ;
- bảng biểu;

hãy đọc và sử dụng chúng khi có thể.

Nếu chữ hoặc số không rõ:
không tự suy đoán.

--------------------------------------------------

BƯỚC 3 - ĐỐI CHIẾU KHO HỒ SƠ

Sau khi quan sát ảnh, sử dụng File Search khi cần thiết để
đối chiếu với hồ sơ THỦY LỢI AI.

Ưu tiên tìm:
- tên công trình;
- hồ sơ công trình;
- quy mô;
- thông số kỹ thuật;
- nhiệm vụ công trình;
- quy trình vận hành;
- lịch sử vận hành;
- quy định liên quan.

Nếu tìm thấy tài liệu phù hợp:
phải phân biệt rõ đâu là thông tin nhìn thấy từ ảnh
và đâu là thông tin lấy từ hồ sơ.

--------------------------------------------------

BƯỚC 4 - PHÂN TÍCH CHUYÊN NGÀNH

Khi người dùng yêu cầu phân tích chuyên ngành thủy lợi,
có thể đánh giá:

- hiện trạng công trình;
- tình trạng vận hành;
- khả năng phù hợp với hồ sơ;
- dấu hiệu bất thường nhìn thấy được;
- nguy cơ kỹ thuật có thể quan sát;
- vấn đề về cấp nước, tiêu nước, tưới;
- tình trạng kênh, máy bơm, cửa van, đường ống;
- khả năng ảnh hưởng đến vận hành.

Tuy nhiên:

Không được khẳng định một sự cố kỹ thuật chỉ từ hình ảnh
nếu chưa đủ căn cứ.

Sử dụng cách diễn đạt:
- "Có dấu hiệu..."
- "Có thể nhận thấy..."
- "Hình ảnh cho thấy..."
- "Cần kiểm tra thêm..."
- "Chưa đủ căn cứ để kết luận..."

--------------------------------------------------

BƯỚC 5 - KẾT LUẬN

Nếu đủ căn cứ, đưa ra nhận xét chuyên môn.

Nếu chưa đủ căn cứ:
nêu rõ những thông tin cần bổ sung.

Ví dụ:
- ảnh chụp gần hơn;
- ảnh toàn cảnh;
- ảnh bảng thông số;
- tên công trình;
- thời điểm chụp;
- số máy đang vận hành;
- lưu lượng;
- mực nước;
- độ mặn;
- số liệu vận hành liên quan.

==================================================
III. CẤU TRÚC TRẢ LỜI KHI PHÂN TÍCH ẢNH
==================================================

Khi phù hợp, sử dụng cấu trúc:

### 1. Quan sát hình ảnh
Mô tả những gì nhìn thấy.

### 2. Thông tin đọc được
Nêu chữ, số, tên công trình hoặc thông số đọc được.

### 3. Đối chiếu hồ sơ
Nêu thông tin tương ứng tìm thấy trong kho
THỦY LỢI AI.

### 4. Phân tích chuyên ngành
Đánh giá dựa trên hình ảnh và hồ sơ.

### 5. Nhận xét
Nêu kết luận ngắn gọn.

### 6. Kiến nghị kiểm tra
Chỉ đưa ra khi thực sự cần thiết.

==================================================
IV. NGUYÊN TẮC PHÂN BIỆT NGUỒN
==================================================

Luôn phân biệt:

[ẢNH]
Thông tin quan sát trực tiếp từ hình ảnh.

[HỒ SƠ]
Thông tin được tìm thấy trong kho THỦY LỢI AI.

[PHÂN TÍCH]
Nhận định được suy ra từ việc đối chiếu ảnh và hồ sơ.

Không được trình bày một nhận định suy luận như thể đó
là thông tin có sẵn trong hồ sơ.

==================================================
V. AN TOÀN VÀ ĐỘ TIN CẬY
==================================================

1. Không bịa nguồn.

2. Không bịa số liệu.

3. Không bịa nội dung văn bản.

4. Không khẳng định tình trạng an toàn của công trình
chỉ dựa vào một hình ảnh.

5. Không thay thế quyết định của cán bộ kỹ thuật hoặc
người có thẩm quyền.

6. Khi thông tin chưa đủ:
nói rõ thiếu thông tin gì.

==================================================
VI. CÁCH TRẢ LỜI
==================================================

- Tiếng Việt.
- Rõ ràng.
- Ngắn gọn nhưng đủ ý.
- Ưu tiên gạch đầu dòng.
- Giữ nguyên số liệu và đơn vị.
- Với quy trình: trình bày theo từng bước.
- Với báo cáo: có thể tổng hợp thành các mục.
- Với hình ảnh: luôn phân biệt quan sát, hồ sơ và phân tích.
"""


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gemini_client

    print("=" * 60)
    print("KHỞI ĐỘNG THỦY LỢI AI - BẢN 5.2 STABLE")
    print("=" * 60)
    print("KIỂM TRA GEMINI...")

    if not GEMINI_API_KEY:
        print("GEMINI API: CHƯA CÓ API KEY")
    else:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            print("GEMINI API: ĐÃ KHỞI TẠO CLIENT")
        except Exception as e:
            gemini_client = None
            print("GEMINI API: LỖI KHỞI TẠO:", repr(e))

    print(
        "FILE SEARCH STORE:",
        GEMINI_FILE_SEARCH_STORE or "CHƯA CẤU HÌNH",
    )
    print("MODEL:", GEMINI_MODEL)
    print("MAX CONCURRENT:", MAX_CONCURRENT)
    print("QUEUE TIMEOUT:", QUEUE_TIMEOUT)
    print("REQUEST TIMEOUT:", REQUEST_TIMEOUT)
    print("MAX RETRIES:", MAX_RETRIES)
    print("CACHE ENABLED:", CACHE_ENABLED)
    print("CACHE TTL:", CACHE_TTL)
    print("CACHE MAX ENTRIES:", CACHE_MAX_ENTRIES)
    print("MAX UPLOAD MB:", MAX_UPLOAD_MB)
    print("=" * 60)

    yield

    gemini_client = None
    print("THỦY LỢI AI ĐÃ DỪNG")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi",
    version="5.2",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELS
# ============================================================

class Question(BaseModel):
    question: str


# ============================================================
# BASIC HELPERS
# ============================================================

def require_gemini():
    if gemini_client is None:
        raise RuntimeError("Gemini API chưa được kết nối.")
    if not GEMINI_FILE_SEARCH_STORE:
        raise RuntimeError(
            "Gemini File Search Store chưa được cấu hình."
        )


def store_name():
    return GEMINI_FILE_SEARCH_STORE.strip()


def normalize_question(text: str) -> str:
    """
    Chuẩn hóa câu hỏi để cache nhận diện tốt hơn:
    - bỏ khoảng trắng thừa
    - lowercase
    - bỏ khoảng trắng quanh dấu câu
    """
    value = (text or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([?.!,;:])", r"\1", value)
    return value


# ============================================================
# CACHE
# ============================================================

async def get_cached_answer(question: str):
    if not CACHE_ENABLED:
        return None

    key = normalize_question(question)

    async with _cache_lock:
        item = _answer_cache.get(key)

        if not item:
            return None

        age = time.time() - item["created_at"]

        if age > CACHE_TTL:
            _answer_cache.pop(key, None)
            return None

        # LRU: câu vừa dùng được đưa lên đầu.
        _answer_cache.move_to_end(key, last=False)

        return {
            "answer": item["answer"],
            "sources": item.get("sources", []),
            "created_at": item["created_at"],
            "age_seconds": round(age, 1),
        }


async def set_cached_answer(question: str, answer: str, sources=None):
    if not CACHE_ENABLED:
        return

    key = normalize_question(question)

    # Không lưu câu trả lời rỗng hoặc quá lớn.
    if not key or not answer:
        return

    async with _cache_lock:
        _answer_cache[key] = {
            "answer": answer,
            "sources": sources or [],
            "created_at": time.time(),
        }

        _answer_cache.move_to_end(key, last=False)

        while len(_answer_cache) > CACHE_MAX_ENTRIES:
            _answer_cache.popitem(last=True)


async def clear_answer_cache():
    async with _cache_lock:
        _answer_cache.clear()

    async with _inflight_lock:
        _inflight.clear()


async def cache_info():
    async with _cache_lock:
        now = time.time()
        valid = 0
        expired = 0

        for item in _answer_cache.values():
            if now - item["created_at"] <= CACHE_TTL:
                valid += 1
            else:
                expired += 1

        return {
            "enabled": CACHE_ENABLED,
            "count": len(_answer_cache),
            "valid_count": valid,
            "expired_count": expired,
            "max_entries": CACHE_MAX_ENTRIES,
            "ttl_seconds": CACHE_TTL,
        }


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "version": "5.2",
    }


# ============================================================
# HEALTH
# QUAN TRỌNG:
# /health KHÔNG GỌI GEMINI FILE SEARCH.
# Vì frontend có thể gọi nhiều lần và health phải thật nhẹ.
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "Gemini File Search",
        "model": GEMINI_MODEL,
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_connected": gemini_client is not None,
        "gemini_client_ready": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "max_concurrent": MAX_CONCURRENT,
        "queue_timeout": QUEUE_TIMEOUT,
        "request_timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
        "max_question_length": MAX_QUESTION_LENGTH,
        "cache_enabled": CACHE_ENABLED,
        "cache_ttl": CACHE_TTL,
        "cache_max_entries": CACHE_MAX_ENTRIES,
    }


# ============================================================
# HEALTH DEEP
# Chỉ dùng khi cần kiểm tra thực tế File Search Store.
# Không dùng liên tục từ frontend.
# ============================================================

@app.get("/health/deep")
async def health_deep():
    result = {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "gemini_connected": gemini_client is not None,
        "file_search_configured": bool(GEMINI_FILE_SEARCH_STORE),
        "file_search_ready": False,
        "documents_count": 0,
    }

    if gemini_client is None:
        result["status"] = "error"
        result["message"] = "Gemini API chưa kết nối."
        return result

    if not GEMINI_FILE_SEARCH_STORE:
        result["status"] = "error"
        result["message"] = "Chưa cấu hình File Search Store."
        return result

    try:
        documents = await asyncio.wait_for(
            asyncio.to_thread(list_documents_sync),
            timeout=30,
        )

        result["file_search_ready"] = True
        result["documents_count"] = len(documents)
        result["message"] = "File Search Store hoạt động."
        return result

    except asyncio.TimeoutError:
        result["status"] = "error"
        result["message"] = "Kiểm tra File Search Store quá thời gian."
        return result

    except Exception as e:
        print("HEALTH DEEP ERROR:", repr(e))
        result["status"] = "error"
        result["message"] = "Không truy cập được File Search Store."
        result["error"] = str(e)
        return result


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
        "version": "5.2",
        "endpoints": {
            "home": "/",
            "health": "/health",
            "health_deep": "/health/deep",
            "ask": "/ask",
            "stores": "/stores",
            "documents": "/documents",
            "pdf_documents": "/documents/pdf",
            "delete_pdf": "/documents/pdf",
            "upload": "/upload",
            "image_upload": "/image-upload",
            "image_analyze": "/image-analyze",
            "field_report": "/field-report",
            "field_report_pdf": "/field-report-pdf",
            "cache": "/cache",
            "clear_cache": "/cache",
        },
        "protection": {
            "queue": True,
            "retry": True,
            "cache": True,
            "cache_stampede": True,
        },
    }


# ============================================================
# CACHE API
# Giữ cấu trúc API, chỉ bổ sung để kiểm tra cache.
# ============================================================

@app.get("/cache")
async def get_cache():
    return {
        "success": True,
        **(await cache_info()),
    }


@app.delete("/cache")
async def clear_cache():
    await clear_answer_cache()

    return {
        "success": True,
        "message": "Đã xóa toàn bộ cache câu hỏi.",
    }


# ============================================================
# GEMINI RETRY
# ============================================================

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
        "not found",
    ]

    if any(x in text for x in permanent):
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

    # Timeout luôn cho phép retry tối đa theo MAX_RETRIES.
    return any(x in text for x in retryable)


def call_gemini(question: str):
    require_gemini()

    return gemini_client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        input=question,
        tools=[{
            "type": "file_search",
            "file_search_store_names": [store_name()],
        }],
    )


# ============================================================
# ANSWER + SOURCES
# ============================================================

def extract_answer_and_sources(result):
    answer = (getattr(result, "output_text", None) or "").strip()

    # ----------------------------------------------------
    # LẤY NGUỒN TÀI LIỆU TỪ FILE SEARCH
    # ----------------------------------------------------

    sources = []
    seen_sources = set()

    try:
        for step in getattr(result, "steps", []) or []:

            if getattr(step, "type", None) != "model_output":
                continue

            for block in getattr(step, "content", []) or []:

                if getattr(block, "type", None) != "text":
                    continue

                for annotation in getattr(block, "annotations", []) or []:

                    if getattr(annotation, "type", None) != "file_citation":
                        continue

                    file_name = (
                        getattr(annotation, "file_name", None)
                        or "Tài liệu THỦY LỢI AI"
                    )

                    page_number = getattr(
                        annotation,
                        "page_number",
                        None,
                    )

                    source = getattr(
                        annotation,
                        "source",
                        None,
                    )

                    key = (
                        str(file_name),
                        str(page_number),
                        str(source),
                    )

                    if key in seen_sources:
                        continue

                    seen_sources.add(key)

                    sources.append({
                        "file_name": file_name,
                        "page_number": page_number,
                        "source": source,
                    })

    except Exception as source_error:

        print(
            "FILE SEARCH CITATION ERROR:",
            repr(source_error),
        )
        sources = []

        for step in (getattr(result, "steps", []) or []):
            if getattr(step, "type", None) != "model_output":
                continue

            for block in (getattr(step, "content", []) or []):
                if not answer and getattr(block, "type", None) == "text":
                    answer += getattr(block, "text", "") or ""

                for annotation in (getattr(block, "annotations", []) or []):
                    if getattr(annotation, "type", None) != "file_citation":
                        continue

                    item = {}

                    file_name = getattr(annotation, "file_name", None)
                    source = getattr(annotation, "source", None)

                    if file_name:
                        item["file_name"] = str(file_name)

                    if source:
                        item["source"] = str(source)

                    if item and item not in sources:
                        sources.append(item)

    # --------------------------------------------------------
    # Chạy sau cả hai trường hợp (thành công hoặc lỗi khi trích
    # xuất citation) - không nằm lồng trong except.
    # --------------------------------------------------------

    answer = answer.strip()

    if not answer:
        raise RuntimeError("Gemini không trả về nội dung.")

    return answer, sources


# ============================================================
# GEMINI CALL WITH QUEUE + TIMEOUT + RETRY
# ============================================================

async def _gemini_once(question: str):
    """
    Một lượt gọi Gemini.
    Queue timeout tách riêng với request timeout.
    """

    try:
        await asyncio.wait_for(
            request_semaphore.acquire(),
            timeout=QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            "Hệ thống đang có nhiều yêu cầu. Hàng đợi đã quá thời gian chờ."
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(call_gemini, question),
            timeout=REQUEST_TIMEOUT,
        )
        return extract_answer_and_sources(result)

    finally:
        request_semaphore.release()


async def ask_gemini_with_retry(question: str):
    last_error = None

    for attempt in range(MAX_RETRIES):
        started = time.monotonic()

        try:
            answer, sources = await _gemini_once(question)

            elapsed = time.monotonic() - started

            print(
                f"GEMINI SUCCESS attempt={attempt + 1}/{MAX_RETRIES} "
                f"time={elapsed:.1f}s"
            )

            return answer, sources

        except Exception as e:
            last_error = e

            elapsed = time.monotonic() - started

            print(
                f"GEMINI ERROR attempt={attempt + 1}/{MAX_RETRIES} "
                f"time={elapsed:.1f}s error={repr(e)}"
            )

            retryable = is_retryable_error(e)
            print("RETRYABLE:", retryable)

            if not retryable or attempt >= MAX_RETRIES - 1:
                break

            delay = min(6, 2 ** attempt) + random.uniform(0.2, 0.8)

            print(
                f"THỬ LẠI LẦN {attempt + 2}/{MAX_RETRIES} "
                f"SAU {delay:.1f} GIÂY..."
            )

            await asyncio.sleep(delay)

    raise last_error or RuntimeError(
        "Gemini không thể xử lý câu hỏi."
    )


# ============================================================
# SINGLE-FLIGHT / CACHE STAMPEDE PROTECTION
# ============================================================

async def ask_with_singleflight(question: str):
    key = normalize_question(question)

    # Kiểm tra cache lần đầu.
    cached = await get_cached_answer(question)

    if cached:
        print(
            "CACHE HIT -",
            f"age={cached['age_seconds']}s",
        )
        return cached["answer"], cached["sources"], True

    # Nếu một request khác đang xử lý cùng câu hỏi,
    # chờ đúng request đó thay vì gọi Gemini lần nữa.
    async with _inflight_lock:
        future = _inflight.get(key)

        if future is None:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            _inflight[key] = future
            is_owner = True
        else:
            is_owner = False

    if not is_owner:
        print("CACHE STAMPEDE PROTECTION - CHỜ REQUEST ĐANG XỬ LÝ")

        try:
            answer, sources = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=REQUEST_TIMEOUT + QUEUE_TIMEOUT + 15,
            )
            return answer, sources, False

        except Exception:
            # Request gốc thất bại; request hiện tại sẽ tự xử lý lại.
            async with _inflight_lock:
                if _inflight.get(key) is future:
                    _inflight.pop(key, None)

            # Tiếp tục xuống xử lý Gemini.
            return await ask_with_singleflight(question)

    try:
        print("CACHE MISS - ĐANG GỬI CÂU HỎI GEMINI...")

        answer, sources = await ask_gemini_with_retry(question)

        # Chỉ cache câu trả lời thành công.
        await set_cached_answer(
            question,
            answer,
            sources,
        )

        if not future.done():
            future.set_result((answer, sources))

        print("CACHE SAVED - CÂU TRẢ LỜI ĐÃ ĐƯỢC LƯU")

        return answer, sources, False

    except Exception as e:
        if not future.done():
            future.set_exception(e)

        raise

    finally:
        async with _inflight_lock:
            if _inflight.get(key) is future:
                _inflight.pop(key, None)


# ============================================================
# ASK
# ============================================================

@app.post("/ask")
async def ask(data: Question):
    question = (data.question or "").strip()

    print("=" * 60)
    print("CÂU HỎI:", question)
    print("=" * 60)

    if not question:
        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi.",
        }

    if len(question) > MAX_QUESTION_LENGTH:
        return {
            "status": "error",
            "answer": (
                f"Câu hỏi quá dài. Vui lòng nhập tối đa "
                f"{MAX_QUESTION_LENGTH} ký tự."
            ),
        }

    # CACHE phải được kiểm tra trước khi yêu cầu Gemini.
    cached = await get_cached_answer(question)

    if cached:
        print("CACHE HIT - TRẢ CÂU TRẢ LỜI TỪ CACHE")

        response = {
            "status": "ok",
            "answer": cached["answer"],
            "engine": "Local Cache",
            "model": GEMINI_MODEL,
            "cache": True,
        }

        if cached["sources"]:
            response["sources"] = cached["sources"]

        return response

    if not GEMINI_API_KEY:
        return {
            "status": "error",
            "answer": "THỦY LỢI AI chưa được cấu hình Gemini API.",
        }

    if gemini_client is None:
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa kết nối được Gemini API. "
                "Vui lòng thử lại sau."
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
        answer, sources, was_cache = await ask_with_singleflight(question)

        response = {
            "status": "ok",
            "answer": answer,
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
            "cache": False,
        }

        if sources:
            response["sources"] = sources

        return response

    except Exception as e:
        print("GEMINI KHÔNG TRẢ LỜI:", repr(e))

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI tạm thời chưa lấy được câu trả lời "
                "từ kho dữ liệu Gemini. Hệ thống đã tự kiểm tra "
                "và thử lại. Vui lòng thử lại sau ít giây."
            ),
            "engine": "Gemini File Search",
            "model": GEMINI_MODEL,
            "cache": False,
        }


# ============================================================
# STORE / DOCUMENT HELPERS
# ============================================================

def serialize_store(store):
    return {
        "name": str(getattr(store, "name", "") or ""),
        "display_name": str(
            getattr(store, "display_name", None)
            or getattr(store, "displayName", None)
            or ""
        ),
    }


def serialize_document(doc):
    name = str(getattr(doc, "name", "") or "")

    display_name = str(
        getattr(doc, "display_name", None)
        or getattr(doc, "displayName", None)
        or ""
    )

    state = str(getattr(doc, "state", "") or "")

    mime_type = str(
        getattr(doc, "mime_type", None)
        or getattr(doc, "mimeType", None)
        or ""
    )

    return {
        "name": name,
        "display_name": display_name,
        "mime_type": mime_type,
        "state": state,
    }


def list_documents_sync():
    require_gemini()

    documents = []

    pager = gemini_client.file_search_stores.documents.list(
        parent=store_name(),
        config={"page_size": 20},
    )

    for doc in pager:
        documents.append(serialize_document(doc))

    return documents


# ============================================================
# STORES
# ============================================================

@app.get("/stores")
async def list_stores():
    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa được kết nối.",
        }

    try:
        stores = []

        def load():
            for s in gemini_client.file_search_stores.list():
                stores.append(serialize_store(s))

        await asyncio.to_thread(load)

        return {
            "success": True,
            "count": len(stores),
            "stores": stores,
        }

    except Exception as e:
        print("STORE LIST ERROR:", repr(e))

        return {
            "success": False,
            "error": str(e),
        }


# ============================================================
# DOCUMENTS
# ============================================================

@app.get("/documents")
async def list_documents():
    if gemini_client is None:
        return {
            "success": False,
            "store": store_name(),
            "count": 0,
            "documents": [],
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "store": "",
            "count": 0,
            "documents": [],
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        documents = await asyncio.to_thread(list_documents_sync)

        return {
            "success": True,
            "store": store_name(),
            "count": len(documents),
            "documents": documents,
        }

    except Exception as e:
        print("DOCUMENT LIST ERROR:", repr(e))

        return {
            "success": False,
            "store": store_name(),
            "count": 0,
            "documents": [],
            "error": str(e),
        }


def is_pdf_document(doc):
    name = (doc.get("display_name") or "").strip().lower()
    mime = (doc.get("mime_type") or "").strip().lower()
    resource_name = (doc.get("name") or "").strip().lower()

    return (
        name.endswith(".pdf")
        or mime == "application/pdf"
        or ".pdf" in resource_name
    )


# ============================================================
# PDF LIST
# ============================================================

@app.get("/documents/pdf")
async def list_pdf_documents():
    """Chỉ liệt kê PDF; KHÔNG XÓA."""

    if gemini_client is None:
        return {
            "success": False,
            "count": 0,
            "documents": [],
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "count": 0,
            "documents": [],
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        documents = await asyncio.to_thread(list_documents_sync)
        pdfs = [doc for doc in documents if is_pdf_document(doc)]

        return {
            "success": True,
            "store": store_name(),
            "count": len(pdfs),
            "documents": pdfs,
            "message": "Chỉ liệt kê PDF. Chưa xóa tài liệu nào.",
        }

    except Exception as e:
        print("PDF LIST ERROR:", repr(e))

        return {
            "success": False,
            "count": 0,
            "documents": [],
            "error": str(e),
        }


# ============================================================
# DELETE PDF
# ============================================================

def delete_pdf_documents_sync():
    require_gemini()

    documents = list_documents_sync()
    pdfs = [doc for doc in documents if is_pdf_document(doc)]

    deleted = []
    failed = []

    for doc in pdfs:
        try:
            gemini_client.file_search_stores.documents.delete(
                name=doc["name"],
                config={"force": True},
            )

            deleted.append(doc)

        except Exception as e:
            print(
                "PDF DELETE ERROR:",
                doc["name"],
                repr(e),
            )

            failed.append({
                "document": doc,
                "error": str(e),
            })

    return deleted, failed


@app.delete("/documents/pdf")
async def delete_pdf_documents():
    """
    XÓA TẤT CẢ DOCUMENT PDF trong File Search Store hiện tại.
    Chỉ xóa PDF; không xóa Word, Excel, TXT...
    """

    if gemini_client is None:
        return {
            "success": False,
            "deleted_count": 0,
            "failed_count": 0,
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "deleted_count": 0,
            "failed_count": 0,
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    try:
        deleted, failed = await asyncio.to_thread(
            delete_pdf_documents_sync
        )

        # Nội dung kho thay đổi -> xóa cache trả lời cũ.
        await clear_answer_cache()

        return {
            "success": len(failed) == 0,
            "store": store_name(),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "deleted": deleted,
            "failed": failed,
            "message": (
                f"Đã xóa {len(deleted)} PDF. "
                f"Còn lỗi: {len(failed)}. "
                f"Cache câu trả lời đã được làm mới."
            ),
        }

    except Exception as e:
        print("PDF DELETE ALL ERROR:", repr(e))

        return {
            "success": False,
            "deleted_count": 0,
            "failed_count": 0,
            "error": str(e),
        }


# ============================================================
# UPLOAD
# Giữ nguyên endpoint /upload.
# Bổ sung:
# - giới hạn dung lượng
# - timeout operation
# - không busy-loop liên tục
# - upload thành công -> xóa cache cũ
# ============================================================

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa được kết nối.",
        }

    if not GEMINI_FILE_SEARCH_STORE:
        return {
            "success": False,
            "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE.",
        }

    if not file.filename:
        return {
            "success": False,
            "error": "Chưa chọn file.",
        }

    suffix = Path(file.filename).suffix
    temp_path = None

    try:
        content = await file.read()

        if len(content) > MAX_UPLOAD_BYTES:
            return {
                "success": False,
                "filename": file.filename,
                "error": (
                    f"File quá lớn. Kích thước tối đa "
                    f"{MAX_UPLOAD_MB} MB."
                ),
            }

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp:
            temp.write(content)
            temp_path = temp.name

        def do_upload():
            operation = (
                gemini_client
                .file_search_stores
                .upload_to_file_search_store(
                    file=temp_path,
                    file_search_store_name=store_name(),
                    config={
                        "display_name": file.filename
                    },
                )
            )

            started = time.monotonic()

            while not operation.done:
                if (
                    time.monotonic() - started
                    > UPLOAD_OPERATION_TIMEOUT
                ):
                    raise TimeoutError(
                        "Gemini upload quá thời gian chờ."
                    )

                time.sleep(0.5)
                operation = gemini_client.operations.get(
                    operation
                )

            return operation

        operation = await asyncio.to_thread(do_upload)

        # Kho tài liệu đã thay đổi:
        # không được giữ câu trả lời cache cũ.
        await clear_answer_cache()

        return {
            "success": True,
            "filename": file.filename,
            "store": store_name(),
            "message": (
                "Đã đưa file vào Gemini File Search Store. "
                "Cache câu trả lời đã được làm mới."
            ),
            "operation": str(operation),
        }

    except Exception as e:
        print("UPLOAD ERROR:", repr(e))

        return {
            "success": False,
            "filename": file.filename,
            "error": str(e),
        }

    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass

        try:
            await file.close()
        except Exception:
            pass


# ============================================================
# IMAGE UPLOAD - BƯỚC 13B-1A
# Chỉ nhận ảnh + kiểm tra + tạo SHA-256.
# KHÔNG gọi Gemini.
# KHÔNG đưa ảnh vào File Search.
# ============================================================

@app.post("/image-upload")
async def image_upload(file: UploadFile = File(...)):
    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    max_image_bytes = 10 * 1024 * 1024  # 10 MB

    filename = Path(file.filename or "image").name
    content_type = (file.content_type or "").lower().strip()

    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP."
        )

    content = await file.read()

    if len(content) > max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail="Ảnh vượt quá giới hạn 10 MB."
        )

    # 13B-1B: RESIZE + NÉN ẢNH
    try:
        image = Image.open(BytesIO(content))

        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)

        if image.mode != "RGB":
            image = image.convert("RGB")

        output = BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=75,
            optimize=True
        )

        content = output.getvalue()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Không thể xử lý ảnh."
        )

    image_hash = hashlib.sha256(content).hexdigest()

    print(
        "IMAGE RECEIVED | %s | %.2f KB | %s | SHA256=%s"
        % (
            filename,
            len(content) / 1024,
            content_type,
            image_hash,
        )
    )

    return {
        "success": True,
        "status": "received",
        "filename": filename,
        "mime_type": content_type,
        "size_bytes": len(content),
        "image_hash": image_hash,
    }


# ============================================================
# IMAGE ANALYZE - GEMINI VISION + FILE SEARCH
# ============================================================

@app.post("/image-analyze")
async def image_analyze(
    file: UploadFile = File(...),
    question: str = "Hãy phân tích hình ảnh này."
):
    """
    Phân tích ảnh bằng Gemini Vision.

    Quy trình:
    1. Nhận ảnh JPG / PNG / WebP.
    2. Kiểm tra dung lượng ảnh gốc.
    3. Sửa orientation ảnh điện thoại.
    4. Resize tối đa 1600x1600.
    5. Chuyển sang JPEG quality 75.
    6. Gửi ảnh đã xử lý cho Gemini.
    7. Kết hợp File Search của THỦY LỢI AI.
    8. Trả về answer + sources.
    """

    # ========================================================
    # KIỂM TRA GEMINI
    # ========================================================

    if not GEMINI_API_KEY:
        return {
            "success": False,
            "error": "THỦY LỢI AI chưa được cấu hình Gemini API."
        }

    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa được kết nối."
        }

    # ========================================================
    # KIỂM TRA FILE
    # ========================================================

    if not file.filename:
        return {
            "success": False,
            "error": "Chưa chọn ảnh."
        }

    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    content_type = (
        file.content_type or ""
    ).lower().strip()

    if content_type not in allowed_types:
        return {
            "success": False,
            "error": "Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP."
        }

    try:

        # ====================================================
        # ĐỌC ẢNH GỐC
        # ====================================================

        content = await file.read()

        if not content:
            return {
                "success": False,
                "error": "Ảnh rỗng."
            }

        # Kích thước ảnh gốc
        original_size_bytes = len(content)

        # Giới hạn ảnh gốc 10 MB
        if original_size_bytes > 10 * 1024 * 1024:
            return {
                "success": False,
                "error": "Ảnh vượt quá giới hạn 10 MB."
            }

        # ====================================================
        # RESIZE + NÉN ẢNH
        # ====================================================

        try:

            image = Image.open(
                BytesIO(content)
            )

            # ----------------------------------------------
            # Sửa orientation của ảnh điện thoại
            # ----------------------------------------------

            image = ImageOps.exif_transpose(
                image
            )

            # ----------------------------------------------
            # Resize tối đa 1600 x 1600
            # Giữ nguyên tỷ lệ ảnh
            # ----------------------------------------------

            image.thumbnail(
                (1600, 1600),
                Image.Resampling.LANCZOS
            )

            # ----------------------------------------------
            # JPEG cần RGB
            # ----------------------------------------------

            if image.mode != "RGB":
                image = image.convert("RGB")

            # ----------------------------------------------
            # Lưu JPEG quality 75
            # ----------------------------------------------

            output = BytesIO()

            image.save(
                output,
                format="JPEG",
                quality=75,
                optimize=True
            )

            content = output.getvalue()

        except Exception as image_error:

            print(
                "IMAGE PROCESS ERROR:",
                repr(image_error)
            )

            return {
                "success": False,
                "status": "error",
                "error": "Không thể xử lý ảnh."
            }

        # ====================================================
        # THỐNG KÊ ẢNH SAU XỬ LÝ
        # ====================================================

        processed_size_bytes = len(
            content
        )

        # ====================================================
        # CHUYỂN SANG BASE64
        # ====================================================

        image_b64 = base64.b64encode(
            content
        ).decode("utf-8")

        # ====================================================
        # CÂU HỎI
        # ====================================================

        question = (
            question or ""
        ).strip()

        if not question:
            question = (
                "Hãy phân tích hình ảnh này."
            )

        # ====================================================
        # INPUT CHO GEMINI
        # ====================================================

        gemini_input = [
            {
                "type": "text",
                "text": (
                    "Bạn là THỦY LỢI AI, trợ lý chuyên ngành "
                    "thủy lợi của Chi nhánh Thủy lợi "
                    "Vu Gia - Thu Bồn.\n\n"

                    "NHIỆM VỤ:\n"
                    "1. Quan sát và đọc chính xác hình ảnh "
                    "được cung cấp.\n"

                    "2. Nhận diện chữ, số liệu, bảng biểu, "
                    "bản vẽ, công trình, thiết bị hoặc "
                    "hiện trạng nếu có.\n"

                    "3. Nếu câu hỏi liên quan đến quy định, "
                    "quy trình, vận hành, tiêu chuẩn, hồ sơ "
                    "hoặc nghiệp vụ thủy lợi, hãy sử dụng "
                    "File Search để đối chiếu với kho tài liệu "
                    "THỦY LỢI AI.\n"

                    "4. Ưu tiên thông tin trong hồ sơ "
                    "THỦY LỢI AI khi trả lời các vấn đề "
                    "nghiệp vụ.\n"

                    "5. Phân biệt rõ thông tin nhìn thấy "
                    "trong ảnh với thông tin lấy từ hồ sơ.\n"

                    "6. Nếu không tìm thấy căn cứ phù hợp "
                    "trong hồ sơ, phải nói rõ điều đó.\n"

                    "7. Không tự bịa số liệu, quy định, "
                    "điều khoản hoặc thông tin không nhìn "
                    "thấy trong ảnh và không có trong "
                    "nguồn tài liệu.\n\n"

                    "CÁCH TRẢ LỜI:\n"

                    "- Trình bày rõ ràng, ngắn gọn.\n"

                    "- Nếu có chữ hoặc số liệu trong ảnh, "
                    "đọc lại chính xác.\n"

                    "- Nếu phát hiện vấn đề kỹ thuật, "
                    "nêu rõ vấn đề.\n"

                    "- Nếu có căn cứ từ hồ sơ, "
                    "nêu tên tài liệu liên quan.\n"

                    "- Nếu chưa đủ căn cứ, nói rõ "
                    "cần thêm thông tin.\n\n"

                    f"CÂU HỎI CỦA NGƯỜI DÙNG:\n"
                    f"{question}"
                )
            },
            {
                "type": "image",
                "data": image_b64,
                "mime_type": "image/jpeg"
            }
        ]

        # ====================================================
        # FILE SEARCH
        # ====================================================

        tools = []

        if GEMINI_FILE_SEARCH_STORE:

            tools.append({
                "type": "file_search",
                "file_search_store_names": [
                    store_name()
                ]
            })

        # ====================================================
        # GỌI GEMINI
        # ====================================================

        result = await asyncio.to_thread(
            lambda: gemini_client.interactions.create(
                model=GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
                input=gemini_input,
                tools=(
                    tools
                    if tools
                    else None
                ),
            )
        )

        # ====================================================
        # LẤY ANSWER + SOURCES
        # ====================================================

        answer, sources = (
            extract_answer_and_sources(
                result
            )
        )

        if not answer:
            raise RuntimeError(
                "Gemini không trả về nội dung "
                "phân tích ảnh."
            )

        # ====================================================
        # TRẢ KẾT QUẢ
        # ====================================================

        return {
            "success": True,
            "status": "analyzed",

            "filename": file.filename,

            # Ảnh sau xử lý luôn là JPEG
            "mime_type": "image/jpeg",

            # Dung lượng ảnh gốc
            "original_size_bytes": (
                original_size_bytes
            ),

            # Dung lượng ảnh sau resize/nén
            "processed_size_bytes": (
                processed_size_bytes
            ),

            "question": question,

            "answer": answer,

            "sources": sources,

            "engine": "Gemini Vision",

            "model": GEMINI_MODEL,

            "file_search": bool(
                GEMINI_FILE_SEARCH_STORE
            ),
        }

    except Exception as e:

        print(
            "IMAGE ANALYZE ERROR:",
            repr(e)
        )

        return {
            "success": False,
            "status": "error",
            "error": str(e),
        }


# ============================================================
# FIELD REPORT
# LẬP DỰ THẢO BÁO CÁO HIỆN TRƯỜNG TỪ HÌNH ẢNH
#
# GHI CHÚ: Trước đây file có 2 hàm cùng gắn route
# "/field-report" (trùng lặp) - FastAPI chỉ dùng route đăng ký
# đầu tiên, khiến hàm thứ hai không bao giờ chạy. Đã gộp lại
# thành một hàm duy nhất bên dưới, dùng report_type dạng Form
# với danh sách loại báo cáo cố định + report_title tương ứng,
# để khớp với /field-report-pdf (nhận report_title, answer).
# ============================================================

@app.post("/field-report")
async def field_report(
    file: UploadFile = File(...),
    report_type: str = Form("incident"),
    question: str = Form(""),
):
    """
    Tạo DỰ THẢO báo cáo hiện trường từ ảnh.

    Các loại:
    - incident     : Sự cố công trình
    - corridor     : Vi phạm hành lang
    - dry_area     : Diện tích khô / thiếu nước
    - water_level  : Mực nước hồ / kênh

    Bước này CHỈ tạo dự thảo (chưa gửi Zalo).
    Có thể chuyển tiếp answer/report_title sang /field-report-pdf
    để xuất PDF.
    """

    # --------------------------------------------------------
    # 1. KIỂM TRA GEMINI
    # --------------------------------------------------------

    if not GEMINI_API_KEY:
        return {
            "success": False,
            "error": "THỦY LỢI AI chưa được cấu hình Gemini API."
        }

    if gemini_client is None:
        return {
            "success": False,
            "error": "Gemini API chưa được kết nối."
        }

    # --------------------------------------------------------
    # 2. KIỂM TRA FILE
    # --------------------------------------------------------

    if not file.filename:
        return {
            "success": False,
            "error": "Chưa chọn ảnh."
        }

    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    content_type = (
        file.content_type or ""
    ).lower().strip()

    if content_type not in allowed_types:
        return {
            "success": False,
            "error": "Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP."
        }

    # --------------------------------------------------------
    # 3. KIỂM TRA LOẠI BÁO CÁO
    # --------------------------------------------------------

    allowed_report_types = {
        "incident": "SỰ CỐ CÔNG TRÌNH",
        "corridor": "KIỂM TRA HÀNH LANG BẢO VỆ CÔNG TRÌNH THỦY LỢI",
        "dry_area": "KHU VỰC KHÔ / THIẾU NƯỚC",
        "water_level": "MỰC NƯỚC HỒ / KÊNH",
    }

    if report_type not in allowed_report_types:
        report_type = "incident"

    report_title = allowed_report_types[report_type]

    # --------------------------------------------------------
    # 4. ĐỌC ẢNH
    # --------------------------------------------------------

    try:
        content = await file.read()

        if not content:
            return {
                "success": False,
                "error": "Ảnh rỗng."
            }

        # Giới hạn ảnh gốc 10 MB
        if len(content) > 10 * 1024 * 1024:
            return {
                "success": False,
                "error": "Ảnh vượt quá giới hạn 10 MB."
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Không thể đọc ảnh: {str(e)}"
        }

    # --------------------------------------------------------
    # 5. RESIZE + NÉN ẢNH
    # --------------------------------------------------------

    try:

        image = Image.open(
            BytesIO(content)
        )

        image = ImageOps.exif_transpose(image)

        # Giữ tỷ lệ
        image.thumbnail(
            (1600, 1600),
            Image.Resampling.LANCZOS
        )

        # Chuyển RGB
        if image.mode != "RGB":
            image = image.convert("RGB")

        output = BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=75,
            optimize=True
        )

        content = output.getvalue()

    except Exception as e:

        return {
            "success": False,
            "error": f"Không thể xử lý ảnh: {str(e)}"
        }

    # --------------------------------------------------------
    # 6. BASE64
    # --------------------------------------------------------

    image_b64 = base64.b64encode(
        content
    ).decode("utf-8")

    # --------------------------------------------------------
    # 7. CÂU HỎI / GHI CHÚ CỦA NGƯỜI DÙNG
    # --------------------------------------------------------

    question = (question or "").strip()

    if not question:
        question = (
            "Hãy lập dự thảo báo cáo hiện trường "
            "dựa trên hình ảnh."
        )

    # --------------------------------------------------------
    # 8. PROMPT CHUYÊN BIỆT CHO BÁO CÁO
    # --------------------------------------------------------

    report_prompt = f"""
Bạn là THỦY LỢI AI, trợ lý chuyên ngành thủy lợi
của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

NHIỆM VỤ:

Lập DỰ THẢO báo cáo hiện trường dựa trên hình ảnh
người dùng cung cấp.

LOẠI BÁO CÁO:

{report_title}

NGUYÊN TẮC BẮT BUỘC:

1. Chỉ mô tả những gì có thể quan sát hoặc có căn cứ.

2. Không tự bịa:
- địa điểm;
- thời gian;
- diện tích;
- mực nước;
- lưu lượng;
- số lượng;
- khoảng cách;
- thông số kỹ thuật.

3. Nếu ảnh không đủ căn cứ xác định một thông tin,
ghi rõ:
"Chưa xác định được từ hình ảnh."

4. Với vi phạm hành lang:
Không được tự kết luận vi phạm chỉ dựa vào hình ảnh.
Phải phân biệt:
- dấu hiệu cần kiểm tra;
- căn cứ hồ sơ;
- kết luận chính thức.

5. Với diện tích khô:
Không tự ước lượng diện tích ha nếu ảnh không có
căn cứ đo đạc hoặc dữ liệu bản đồ.

6. Với mực nước:
Nếu nhìn thấy thước đo hoặc vạch mực nước,
hãy đọc giá trị nếu đủ rõ.
Nếu không rõ, phải nói rõ chưa xác định chính xác.

7. Nếu có căn cứ từ File Search:
hãy đối chiếu và nêu tên tài liệu liên quan.

8. Phân biệt rõ:
- THÔNG TIN QUAN SÁT TỪ ẢNH
- THÔNG TIN ĐỐI CHIẾU HỒ SƠ
- NHẬN ĐỊNH / KIẾN NGHỊ

9. Đây là DỰ THẢO báo cáo.
Không xem đây là kết luận pháp lý hoặc kết luận
kỹ thuật cuối cùng.

CẤU TRÚC BÁO CÁO:

# BÁO CÁO NHANH HIỆN TRƯỜNG

## 1. Thông tin chung

- Loại báo cáo:
{report_title}
- Thời gian:
Chưa xác định từ hình ảnh.
- Địa điểm:
Chưa xác định từ hình ảnh.
- Công trình:
Chưa xác định từ hình ảnh.

## 2. Hiện trạng quan sát từ hình ảnh

Mô tả chính xác những gì nhìn thấy.

## 3. Đánh giá sơ bộ

Nêu những vấn đề có thể nhận biết từ hình ảnh.

## 4. Đối chiếu hồ sơ

Nếu có căn cứ phù hợp từ File Search,
nêu rõ tài liệu và nội dung liên quan.

Nếu chưa có căn cứ:
"Chưa tìm thấy căn cứ phù hợp trong hồ sơ."

## 5. Kiến nghị

Đề xuất các bước kiểm tra hoặc xử lý tiếp theo,
không vượt quá căn cứ có được.

## 6. Thông tin cần bổ sung

Liệt kê những thông tin cán bộ hiện trường
cần cung cấp thêm nếu cần.

GHI CHÚ CỦA NGƯỜI DÙNG:

{question}
"""

    # --------------------------------------------------------
    # 9. INPUT GEMINI
    # --------------------------------------------------------

    gemini_input = [
        {
            "type": "text",
            "text": report_prompt
        },
        {
            "type": "image",
            "data": image_b64,
            "mime_type": "image/jpeg"
        }
    ]

    # --------------------------------------------------------
    # 10. FILE SEARCH
    # --------------------------------------------------------

    tools = []

    if GEMINI_FILE_SEARCH_STORE:

        tools.append({
            "type": "file_search",
            "file_search_store_names": [
                store_name()
            ]
        })

    # --------------------------------------------------------
    # 11. GỌI GEMINI
    # --------------------------------------------------------

    try:

        result = await asyncio.to_thread(
            lambda: gemini_client.interactions.create(
                model=GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
                input=gemini_input,
                tools=tools if tools else None,
            )
        )

        answer, sources = extract_answer_and_sources(
            result
        )

    except Exception as e:

        print(
            "FIELD REPORT ERROR:",
            repr(e)
        )

        return {
            "success": False,
            "status": "error",
            "error": str(e),
        }

    # --------------------------------------------------------
    # 12. TRẢ KẾT QUẢ
    # --------------------------------------------------------

    return {
        "success": True,
        "status": "draft",
        "report_type": report_type,
        "report_title": report_title,
        "filename": file.filename,
        "mime_type": content_type,
        "size_bytes": len(content),
        "question": question,
        "answer": answer,
        "sources": sources,
        "engine": "Gemini Vision",
        "model": GEMINI_MODEL,
        "file_search": bool(
            GEMINI_FILE_SEARCH_STORE
        ),
        "next_step": "review",
    }


# ============================================================
# FIELD REPORT PDF - NÂNG CẤP CHUYÊN NGHIỆP
# ============================================================

def esc_pdf(text):
    """
    Escape ký tự HTML trước khi đưa vào ReportLab Paragraph.
    """
    text = str(text or "")
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def register_pdf_font():
    """
    Tìm font Unicode để PDF hiển thị tiếng Việt.
    """
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont("ThuyLoiUnicode", font_path))
                return "ThuyLoiUnicode"
            except Exception:
                pass
    return "Helvetica"


def create_field_report_pdf(report_title: str, answer: str):
    """
    Tạo PDF báo cáo hiện trường với bố cục chuyên nghiệp, có header/footer,
    bảng, màu sắc, và phân cấp rõ ràng.
    """
    buffer = BytesIO()
    font_name = register_pdf_font()
    pagesize = A4
    width, height = pagesize

    left_margin = 45
    right_margin = 45
    top_margin = 60
    bottom_margin = 50

    # Tạo document với callback cho header/footer
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title="Báo cáo nhanh hiện trường - THỦY LỢI AI",
    )

    # =========================================================
    # STYLES
    # =========================================================
    styles = getSampleStyleSheet()

    # Tiêu đề chính
    title_style = ParagraphStyle(
        "ThuyLoiTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=12,
        textColor=colors.HexColor('#0B3B5C'),
        fontWeight='bold',
    )

    # Loại báo cáo (dưới tiêu đề)
    subtitle_style = ParagraphStyle(
        "ThuyLoiSubtitle",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=12,
        leading=16,
        alignment=TA_CENTER,
        spaceAfter=18,
        textColor=colors.HexColor('#2E5A88'),
    )

    # Các mục chính (1., 2., 3.)
    heading_style = ParagraphStyle(
        "ThuyLoiHeading",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=13,
        leading=18,
        spaceBefore=12,
        spaceAfter=6,
        textColor=colors.HexColor('#004080'),
        fontWeight='bold',
    )

    # Tiêu đề phụ
    subheading_style = ParagraphStyle(
        "ThuyLoiSubHeading",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=11,
        leading=16,
        spaceBefore=8,
        spaceAfter=4,
        textColor=colors.HexColor('#1F4E79'),
        fontWeight='bold',
    )

    # Nội dung thường
    body_style = ParagraphStyle(
        "ThuyLoiBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=16,
        spaceAfter=6,
    )

    # Danh sách có bullet
    bullet_style = ParagraphStyle(
        "ThuyLoiBullet",
        parent=body_style,
        fontName=font_name,
        fontSize=10.5,
        leading=16,
        leftIndent=20,
        bulletIndent=10,
        spaceAfter=4,
    )

    # Nội dung trong bảng
    table_cell_style = ParagraphStyle(
        "ThuyLoiTableCell",
        parent=body_style,
        fontName=font_name,
        fontSize=9,
        leading=12,
    )

    # =========================================================
    # HÀM VẼ HEADER / FOOTER
    # =========================================================
    def draw_header_footer(canvas, doc, page_number):
        canvas.saveState()
        # ----- HEADER -----
        # Vẽ logo nếu có
        try:
            logo_path = BASE_DIR / "static" / "logo.png"
            if logo_path.exists():
                img = ImageReader(str(logo_path))
                canvas.drawImage(img, left_margin, height - top_margin - 50, width=50, height=50, preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

        # Tên đơn vị
        canvas.setFont(font_name, 10)
        canvas.drawString(left_margin + 60, height - top_margin - 20, "CHI NHÁNH THỦY LỢI VU GIA - THU BỒN")
        # Tiêu đề báo cáo (in nhỏ hơn trên header)
        canvas.setFont(font_name, 9)
        canvas.drawString(left_margin + 60, height - top_margin - 35, "BÁO CÁO NHANH HIỆN TRƯỜNG")
        # Đường kẻ ngang dưới header
        canvas.setStrokeColor(colors.HexColor('#004080'))
        canvas.setLineWidth(1.5)
        canvas.line(left_margin, height - top_margin - 60, width - right_margin, height - top_margin - 60)

        # ----- FOOTER -----
        # Đường kẻ ngang trên footer
        canvas.setStrokeColor(colors.HexColor('#004080'))
        canvas.setLineWidth(1)
        canvas.line(left_margin, bottom_margin + 15, width - right_margin, bottom_margin + 15)

        # Số trang bên trái, ngày bên phải
        canvas.setFont(font_name, 9)
        canvas.drawString(left_margin, bottom_margin, f"Trang {page_number}")
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        canvas.drawRightString(width - right_margin, bottom_margin, f"Tạo lúc: {now}")

        canvas.restoreState()

    def onFirstPage(canvas, doc):
        draw_header_footer(canvas, doc, 1)

    def onLaterPage(canvas, doc):
        draw_header_footer(canvas, doc, doc.page)

    # =========================================================
    # XÂY DỰNG NỘI DUNG STORY
    # =========================================================
    story = []

    # Tiêu đề chính
    story.append(Paragraph("BÁO CÁO NHANH HIỆN TRƯỜNG", title_style))
    # Loại báo cáo
    story.append(Paragraph(f"<b>Loại báo cáo:</b> {esc_pdf(report_title)}", subtitle_style))
    story.append(Spacer(1, 6))

    # Xử lý nội dung answer
    text = (answer or "").strip()
    # Chuẩn hóa xuống dòng để dễ xử lý
    text = re.sub(r"\s+(?=\d+\.\s+(?:THÔNG TIN CHUNG|HIỆN TRẠNG|KIẾN NGHỊ)\b)", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?=###\s+\d+\.)", "\n", text)
    text = re.sub(r"\s+(?=##\s+\d+\.)", "\n", text)
    text = re.sub(r"(\d+\.\s+(?:THÔNG TIN CHUNG|HIỆN TRẠNG|KIẾN NGHỊ))\s+", r"\1\n", text, flags=re.IGNORECASE)

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            story.append(Spacer(1, 4))
            i += 1
            continue

        # Kiểm tra nếu dòng là tiêu đề mục (bắt đầu bằng số và từ khóa)
        match_heading = re.match(r'^(\d+)\.\s+(THÔNG TIN CHUNG|HIỆN TRẠNG|KIẾN NGHỊ|ĐÁNH GIÁ|KẾT LUẬN|KIẾN NGHỊ|THÔNG TIN CHUNG|MÔ TẢ|PHÂN TÍCH)\b', line, re.IGNORECASE)
        if match_heading:
            # Thêm đường kẻ mỏng phía trên tiêu đề mục
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#B0C4DE'), spaceBefore=6, spaceAfter=6))
            story.append(Paragraph(esc_pdf(line), heading_style))
            i += 1
            continue

        # Kiểm tra tiêu đề phụ (bắt đầu bằng ###, ##, #)
        if line.startswith("### ") or line.startswith("## ") or line.startswith("# "):
            content = line.lstrip("# ").strip()
            story.append(Paragraph(esc_pdf(content), subheading_style))
            i += 1
            continue

        # Kiểm tra danh sách (bắt đầu bằng - hoặc *)
        if line.startswith("- ") or line.startswith("* "):
            content = line[2:].strip()
            story.append(Paragraph(f"• {esc_pdf(content)}", bullet_style))
            i += 1
            continue

        # Kiểm tra nếu dòng có dấu "|" - có thể là bảng
        if "|" in line and len(line.split("|")) >= 3:
            # Thu thập tất cả các dòng tiếp theo cũng có "|" để tạo bảng
            table_lines = [line]
            j = i + 1
            while j < len(lines) and "|" in lines[j]:
                table_lines.append(lines[j].strip())
                j += 1
            # Xây dựng bảng
            try:
                rows = []
                for row_text in table_lines:
                    # Loại bỏ dòng ngăn cách kiểu "|---|---|"
                    if re.match(r'^\s*[\|\-]+\s*$', row_text.replace(' ', '')):
                        continue
                    cells = [cell.strip() for cell in row_text.split('|') if cell.strip() != '']
                    if cells:
                        rows.append(cells)
                if rows:
                    max_cols = max(len(row) for row in rows)
                    for row in rows:
                        while len(row) < max_cols:
                            row.append("")
                    table_data = []
                    for row in rows:
                        table_data.append([Paragraph(esc_pdf(cell), table_cell_style) for cell in row])
                    col_width = (width - left_margin - right_margin) / max_cols
                    table = Table(table_data, colWidths=[col_width] * max_cols)
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#D9E1F2')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#004080')),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('FONTNAME', (0, 0), (-1, -1), font_name),
                        ('FONTSIZE', (0, 0), (-1, -1), 9),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#999999')),
                        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#004080')),
                    ]))
                    story.append(Spacer(1, 6))
                    story.append(table)
                    story.append(Spacer(1, 6))
                    i = j  # Bỏ qua các dòng đã xử lý
                    continue
            except Exception as e:
                print("Lỗi tạo bảng PDF:", e)
                # Nếu lỗi, xử lý như dòng thường

        # Nội dung thường
        story.append(Paragraph(esc_pdf(line), body_style))
        i += 1

    # =========================================================
    # XÂY DỰNG PDF
    # =========================================================
    doc.build(story, onFirstPage=onFirstPage, onLaterPage=onLaterPage)
    buffer.seek(0)
    return buffer


@app.post("/field-report-pdf")
async def field_report_pdf(
    report_title: str = Form("BÁO CÁO NHANH HIỆN TRƯỜNG"),
    answer: str = Form(""),
):
    """
    Chuyển dự thảo báo cáo hiện trường thành PDF.
    """
    if not answer.strip():
        return {
            "success": False,
            "error": "Không có nội dung báo cáo để tạo PDF.",
        }

    try:
        pdf_buffer = await asyncio.to_thread(
            create_field_report_pdf,
            report_title,
            answer,
        )

        filename = "bao-cao-hien-truong.pdf"

        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                )
            },
        )

    except Exception as e:
        print(
            "FIELD REPORT PDF ERROR:",
            repr(e),
        )

        return {
            "success": False,
            "error": str(e),
        }


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
    )
