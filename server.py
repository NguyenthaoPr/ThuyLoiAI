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
from functools import partial
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from google import genai
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    BaseDocTemplate,
    PageTemplate,
    Frame,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether,
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfgen_canvas

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
# 4. Bỏ định nghĩa TRÙNG LẶP của "/field-report": trước đây
#    có 2 hàm cùng gắn @app.post("/field-report"). FastAPI chỉ
#    dùng route đăng ký đầu tiên nên hàm thứ hai không bao giờ
#    chạy -> đã gộp lại thành một hàm duy nhất, giữ bản có
#    report_title/report_type dạng Form (khớp với /field-report-pdf).
# 5. Sửa lỗi thụt lề nghiêm trọng trong
#    create_field_report_pdf(): phần xử lý nội dung từng bị dedent
#    ra khỏi thân hàm, và doc.build()/return buffer từng nằm lồng
#    sai bên trong vòng lặp "for line in lines" (chỉ chạy ở lần
#    lặp cuối, không phải return hợp lệ của hàm) -> khiến hàm trả
#    về None và /field-report-pdf sẽ crash. Đã đưa toàn bộ khối xử
#    lý về đúng cấp thụt lề của hàm, và doc.build()/return buffer
#    được đặt sau vòng lặp, ở cấp hàm.
# 6. (MỚI) THIẾT KẾ LẠI PDF BÁO CÁO HIỆN TRƯỜNG:
#    - Khối "THÔNG TIN BÁO CÁO" giờ có dải tiêu đề màu như khối
#      "NỘI DUNG BÁO CÁO", tên đơn vị hiển thị dạng viết tắt.
#    - Thêm khối cảnh báo "⚠ LƯU Ý" (nền vàng nhạt, có viền)
#      thay cho dòng miễn trừ trách nhiệm nhỏ ở cuối trang.
#    - Thêm khối "XÁC NHẬN HIỆN TRƯỜNG" với 2 cột chữ ký
#      (Người lập / Người kiểm tra).
#    - Chân trang hiển thị đúng "Trang X/Y" (tổng số trang thật)
#      nhờ NumberedCanvas (kỹ thuật 2 lượt vẽ chuẩn của ReportLab),
#      thay vì chỉ hiển thị số trang hiện tại như bản cũ.
# 7. Sửa lỗi thụt lề trong /field-report-pdf (khối if image: bị đặt
#    ngoài hàm) và trong create_field_report_pdf (các khối xử lý ảnh,
#    nội dung bị đặt sai cấp).
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
# FIELD REPORT PDF
# Dự thảo báo cáo → PDF
# ============================================================

# ============================================================
# PDF - CẤU HÌNH GIAO DIỆN / THƯƠNG HIỆU
# ============================================================

PDF_ORG_NAME = "CHI NHÁNH THỦY LỢI VU GIA - THU BỒN"
PDF_ORG_SHORT = "Chi nhánh Thủy lợi VGTB"
PDF_APP_NAME = "THỦY LỢI AI"
PDF_DOC_LABEL = "BÁO CÁO NHANH HIỆN TRƯỜNG"

PDF_COLOR_PRIMARY = colors.HexColor("#0B4F6C")   # xanh đậm - header
PDF_COLOR_ACCENT = colors.HexColor("#1B7A8C")    # xanh phụ - heading nội dung
PDF_COLOR_TEXT = colors.HexColor("#20303B")      # màu chữ chính
PDF_COLOR_MUTED = colors.HexColor("#5A6B75")     # màu chữ phụ / ghi chú
PDF_COLOR_BORDER = colors.HexColor("#C9D8DE")    # viền / đường kẻ nhạt
PDF_COLOR_LABEL_BG = colors.HexColor("#EAF2F5")  # nền ô nhãn trong bảng thông tin

# Màu riêng cho khối cảnh báo "⚠ LƯU Ý"
PDF_COLOR_WARN_HEADER_BG = colors.HexColor("#F4C542")
PDF_COLOR_WARN_BODY_BG = colors.HexColor("#FFF6DE")
PDF_COLOR_WARN_BORDER = colors.HexColor("#E7B93C")
PDF_COLOR_WARN_TEXT = colors.HexColor("#5B4300")


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


def register_pdf_fonts():
    """
    Đăng ký font Unicode (Regular + Bold) để PDF hiển thị
    tiếng Việt có dấu, kể cả trong các đoạn <b>...</b>.

    QUAN TRỌNG: nếu chỉ đăng ký 1 font Regular mà không đăng ký
    kèm font Bold và gọi registerFontFamily(), thì bất kỳ thẻ
    <b> nào trong Paragraph sẽ khiến ReportLab tự động chuyển
    sang "Helvetica-Bold" mặc định - font này KHÔNG có dấu
    tiếng Việt, chữ in đậm sẽ bị mất dấu hoặc lỗi. Vì vậy luôn
    đăng ký cặp Regular/Bold cùng lúc.

    Trả về: (font_regular, font_bold)
    """

    regular_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    def first_existing(paths):
        for p in paths:
            if os.path.exists(p):
                return p
        return None

    regular_path = first_existing(regular_candidates)
    bold_path = first_existing(bold_candidates)

    font_regular = "Helvetica"
    font_bold = "Helvetica-Bold"

    if regular_path:
        try:
            pdfmetrics.registerFont(
                TTFont("ThuyLoiUnicode", regular_path)
            )
            font_regular = "ThuyLoiUnicode"
        except Exception as e:
            print("PDF FONT REGULAR ERROR:", repr(e))

    if bold_path:
        try:
            pdfmetrics.registerFont(
                TTFont("ThuyLoiUnicode-Bold", bold_path)
            )
            font_bold = "ThuyLoiUnicode-Bold"
        except Exception as e:
            print("PDF FONT BOLD ERROR:", repr(e))
    elif font_regular == "ThuyLoiUnicode":
        # Không có file Bold riêng -> dùng Regular thay thế để
        # không bị rơi về Helvetica-Bold (mất dấu tiếng Việt).
        font_bold = "ThuyLoiUnicode"

    # Đăng ký family để thẻ <b>...</b> trong Paragraph tự động
    # dùng đúng font_bold thay vì Helvetica-Bold mặc định.
    try:
        pdfmetrics.registerFontFamily(
            font_regular,
            normal=font_regular,
            bold=font_bold,
            italic=font_regular,
            boldItalic=font_bold,
        )
    except Exception as e:
        print("PDF FONT FAMILY ERROR:", repr(e))

    return font_regular, font_bold


# Giữ tên hàm cũ để tương thích ngược nếu nơi khác còn gọi.
def register_pdf_font():
    font_regular, _ = register_pdf_fonts()
    return font_regular


# ============================================================
# HEADER / FOOTER - VẼ TRÊN MỖI TRANG
#
# LƯU Ý: từ bản này, phần "Trang X" ở chân trang KHÔNG còn vẽ
# ở đây nữa. Vì onPage chỉ chạy MỘT LƯỢT khi dựng story, tại
# thời điểm đó tổng số trang thật sự CHƯA XÁC ĐỊNH (đặc biệt
# với báo cáo dài nhiều trang). Số trang "X/Y" chính xác được
# NumberedCanvas vẽ riêng ở bước save() (xem create_field_report_pdf).
# ============================================================

def _draw_pdf_header_footer(
    canvas_obj,
    doc_obj,
    report_title,
    font_regular,
    font_bold,
    generated_at,
):
    canvas_obj.saveState()

    page_width, page_height = A4

    # ------------------------------------------------------------
    # DẢI HEADER MÀU (letterhead)
    # ------------------------------------------------------------

    header_height = 24 * mm

    canvas_obj.setFillColor(PDF_COLOR_PRIMARY)
    canvas_obj.rect(
        0,
        page_height - header_height,
        page_width,
        header_height,
        stroke=0,
        fill=1,
    )

    canvas_obj.setFillColor(colors.white)

    canvas_obj.setFont(font_bold, 12.5)
    canvas_obj.drawString(
        20 * mm,
        page_height - 10 * mm,
        PDF_ORG_NAME,
    )

    canvas_obj.setFont(font_regular, 9.5)
    canvas_obj.drawString(
        20 * mm,
        page_height - 16 * mm,
        f"{PDF_DOC_LABEL} • {report_title}",
    )

    canvas_obj.setFont(font_bold, 11)
    canvas_obj.drawRightString(
        page_width - 20 * mm,
        page_height - 13 * mm,
        PDF_APP_NAME,
    )

    # ------------------------------------------------------------
    # CHÂN TRANG (footer)
    # Dòng kẻ + tên đơn vị căn giữa. Số trang "X/Y" được
    # NumberedCanvas vẽ đè lên ở góc phải, cùng độ cao với
    # dòng tên đơn vị, sau khi build() biết tổng số trang.
    # ------------------------------------------------------------

    canvas_obj.setStrokeColor(PDF_COLOR_BORDER)
    canvas_obj.setLineWidth(0.6)
    canvas_obj.line(
        20 * mm,
        16 * mm,
        page_width - 20 * mm,
        16 * mm,
    )

    canvas_obj.setFillColor(PDF_COLOR_MUTED)
    canvas_obj.setFont(font_regular, 8)

    canvas_obj.drawCentredString(
        page_width / 2,
        11 * mm,
        f"{PDF_APP_NAME} • {PDF_ORG_NAME.title()}",
    )

    canvas_obj.restoreState()


# ============================================================
# NUMBEREDCANVAS-STYLE PAGE COUNTER
# Kỹ thuật 2 lượt vẽ chuẩn của ReportLab: showPage() không xuất
# trang ngay mà lưu lại trạng thái, đến save() mới biết chính
# xác tổng số trang và vẽ "Trang X/Y" cho từng trang đã lưu.
# ============================================================

def make_numbered_canvas(font_regular, footer_text_color):
    class NumberedCanvas(pdfgen_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdfgen_canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)

            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_page_count(total_pages)
                pdfgen_canvas.Canvas.showPage(self)

            pdfgen_canvas.Canvas.save(self)

        def _draw_page_count(self, total_pages):
            page_width, _ = A4

            self.saveState()
            self.setFont(font_regular, 8)
            self.setFillColor(footer_text_color)
            self.drawRightString(
                page_width - 20 * mm,
                11 * mm,
                f"Trang {self._pageNumber}/{total_pages}",
            )
            self.restoreState()

    return NumberedCanvas


# ============================================================
# CHUẨN HÓA NỘI DUNG BÁO CÁO TRƯỚC KHI DỰNG PDF
# ============================================================

_PDF_HEADING_KEYWORDS = (
    "THÔNG TIN",
    "HIỆN TRẠNG",
    "KIẾN NGHỊ",
    "ĐỀ XUẤT",
    "NHẬN XÉT",
    "KẾT LUẬN",
    "NGUYÊN NHÂN",
    "GIẢI PHÁP",
    "TÌNH HÌNH",
    "ĐÁNH GIÁ",
    "ĐỐI CHIẾU",
    "QUAN SÁT",
    "PHÂN TÍCH",
    "CẦN BỔ SUNG",
    "CẦN KIỂM TRA",
)


def _strip_duplicate_leading_title(text):
    """
    AI thường mở đầu câu trả lời bằng chính tiêu đề báo cáo
    (vd: "# BÁO CÁO NHANH HIỆN TRƯỜNG"), trong khi PDF đã có
    tiêu đề này ở phần header/letterhead. Nếu không loại bỏ,
    tiêu đề sẽ bị in LẶP LẠI 2 lần trong PDF.

    Hàm này chỉ xóa tiêu đề trùng nếu nó xuất hiện trong vài
    dòng đầu tiên (không đụng vào nội dung phía sau nếu cụm từ
    này tình cờ xuất hiện lại ở giữa báo cáo).
    """

    lines = text.split("\n")
    cleaned = []
    checked_lines = 0
    already_removed = False

    for line in lines:
        stripped = line.strip()
        bare = re.sub(r"^#{1,6}\s*", "", stripped).strip()
        bare = re.sub(r"^\**\s*", "", bare).strip()
        bare = re.sub(r"\**$", "", bare).strip()

        is_title_line = bool(
            re.match(
                r"^BÁO\s+CÁO\s+NHANH\s+HIỆN\s+TRƯỜNG\s*[:\-]?\s*$",
                bare,
                re.IGNORECASE,
            )
        )

        if (
            not already_removed
            and checked_lines < 3
            and is_title_line
        ):
            already_removed = True
            checked_lines += 1
            continue

        if stripped:
            checked_lines += 1

        cleaned.append(line)

    return "\n".join(cleaned)


def _pdf_inline_markup(text):
    """
    Chuyển markdown cơ bản (đậm/nghiêng) sang thẻ ReportLab,
    đồng thời escape HTML để không phá cấu trúc Paragraph.
    """

    text = esc_pdf(str(text or ""))

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)

    return text


def _build_pdf_content_flowables(
    answer_text,
    heading_style,
    subheading_style,
    body_style,
    bullet_style,
    number_style,
    note_style,
):
    """
    Phân tích nội dung trả lời của AI (markdown cơ bản, mục
    đánh số, gạch đầu dòng...) thành danh sách flowables cho
    ReportLab. Tách riêng khỏi create_field_report_pdf() để dễ
    kiểm thử và tránh lặp/lỗi thụt lề.
    """

    flowables = []

    text = str(answer_text or "").strip()

    if not text:
        flowables.append(
            Paragraph("Chưa có nội dung báo cáo.", note_style)
        )
        return flowables

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Tách các mục nếu AI trả toàn bộ trên một dòng, ví dụ:
    # "1. THÔNG TIN CHUNG ... 2. HIỆN TRẠNG ... 3. KIẾN NGHỊ ..."
    text = re.sub(r"\s+(?=\d+[.)]\s+)", "\n", text)
    text = re.sub(r"\s+(?=#{1,3}\s+)", "\n", text)

    text = _strip_duplicate_leading_title(text)

    lines = [l for l in text.split("\n")]

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            flowables.append(Spacer(1, 4))
            continue

        # ------------------------------------------------------
        # Heading markdown: #, ##, ###
        # ------------------------------------------------------
        m = re.match(r"^#{1,3}\s+(.+)$", line)

        if m:
            heading_text = _pdf_inline_markup(m.group(1))

            style = subheading_style if line.startswith("###") else heading_style

            flowables.append(Paragraph(heading_text, style))
            continue

        # ------------------------------------------------------
        # Mục đánh số: "1. XXX", "2) XXX" ...
        # ------------------------------------------------------
        m = re.match(r"^(\d+)[.)]\s+(.+)$", line)

        if m:
            number = m.group(1)
            content = m.group(2).strip()
            upper_content = content.upper()

            is_heading = (
                upper_content.startswith(_PDF_HEADING_KEYWORDS)
                or len(content) <= 60
            )

            if is_heading:
                flowables.append(
                    Paragraph(
                        f"{number}. {_pdf_inline_markup(content)}",
                        heading_style,
                    )
                )
            else:
                flowables.append(
                    Paragraph(
                        f"<b>{number}.</b> {_pdf_inline_markup(content)}",
                        number_style,
                    )
                )
            continue

        # ------------------------------------------------------
        # Gạch đầu dòng: -, *, •
        # ------------------------------------------------------
        m = re.match(r"^[-*•]\s+(.+)$", line)

        if m:
            flowables.append(
                Paragraph(
                    "•&nbsp;&nbsp;" + _pdf_inline_markup(m.group(1)),
                    bullet_style,
                )
            )
            continue

        # ------------------------------------------------------
        # Ghi chú / Lưu ý / Kiến nghị / Đề xuất mở đầu dòng
        # ------------------------------------------------------
        if re.match(
            r"^(ghi ch[uú]|l[uư]u [yý]|ki[eê]́n ngh[iị]|đ[eê]̀ xu[aâ]́t)\s*:",
            line,
            flags=re.IGNORECASE,
        ):
            flowables.append(
                Paragraph(_pdf_inline_markup(line), note_style)
            )
            continue

        # ------------------------------------------------------
        # Đoạn văn thông thường
        # ------------------------------------------------------
        flowables.append(
            Paragraph(_pdf_inline_markup(line), body_style)
        )

    return flowables


def _pdf_section_header_table(text, doc_width, bg_color):
    """
    Dải tiêu đề màu dùng chung cho các khối "THÔNG TIN BÁO CÁO"
    và "NỘI DUNG BÁO CÁO", để hai khối có cùng phong cách.
    `text` phải là một Paragraph đã dựng sẵn (đúng font/màu chữ).
    """

    header_table = Table(
        [[text]],
        colWidths=[doc_width],
    )

    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    return header_table


def create_field_report_pdf(
    report_title: str,
    answer: str,
    image_bytes=None,
):
    """
    Tạo PDF báo cáo nhanh hiện trường - phiên bản chuyên nghiệp.

    Bố cục:
    - Letterhead (dải màu header) lặp lại trên MỌI trang, có tên
      đơn vị, tên báo cáo, thương hiệu THỦY LỢI AI.
    - Khối "THÔNG TIN BÁO CÁO": bảng loại báo cáo / thời gian lập /
      đơn vị (viết tắt) / nguồn, có dải tiêu đề màu.
    - Khối "NỘI DUNG BÁO CÁO": nội dung được phân tích từ
      Markdown/đánh số của AI thành heading, đoạn văn, danh sách.
    - Khối cảnh báo "⚠ LƯU Ý": nền vàng nhạt, nhắc đây là dự thảo.
    - Khối "XÁC NHẬN HIỆN TRƯỜNG": 2 cột chữ ký (Người lập /
      Người kiểm tra).
    - Chân trang lặp lại trên MỌI trang: tên đơn vị căn giữa và
      số trang dạng "Trang X/Y" (tổng số trang thật, nhờ
      NumberedCanvas).

    Sửa so với các bản trước:
    - Không còn lặp tiêu đề báo cáo 2 lần (đã có bước loại bỏ
      dòng tiêu đề trùng lặp do AI tự chèn vào đầu câu trả lời).
    - Đăng ký đầy đủ font Regular + Bold theo family, tránh việc
      thẻ <b> rơi về Helvetica-Bold làm mất dấu tiếng Việt.
    - Toàn bộ logic nằm đúng cấp thụt lề của hàm, doc.build() và
      return buffer luôn được gọi đúng một lần, ở cuối hàm.
    - Số trang chân trang hiển thị đúng "X/Y" thay vì chỉ "X".
    """

    buffer = BytesIO()

    font_regular, font_bold = register_pdf_fonts()

    generated_at = time.strftime("%H:%M %d/%m/%Y")

    report_title = (report_title or PDF_DOC_LABEL).strip()

    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=32 * mm,
        bottomMargin=22 * mm,
        title=f"{PDF_DOC_LABEL} - {PDF_APP_NAME}",
        author=PDF_ORG_NAME,
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="normal",
    )

    page_template = PageTemplate(
        id="report",
        frames=[frame],
        onPage=partial(
            _draw_pdf_header_footer,
            report_title=report_title,
            font_regular=font_regular,
            font_bold=font_bold,
            generated_at=generated_at,
        ),
    )

    doc.addPageTemplates([page_template])

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ThuyLoiTitle",
        parent=styles["Title"],
        fontName=font_bold,
        fontSize=17,
        leading=22,
        alignment=TA_CENTER,
        textColor=PDF_COLOR_PRIMARY,
        spaceAfter=4,
    )

    label_style = ParagraphStyle(
        "ThuyLoiLabel",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=9.5,
        leading=13,
        textColor=PDF_COLOR_MUTED,
    )

    value_style = ParagraphStyle(
        "ThuyLoiValue",
        parent=styles["BodyText"],
        fontName=font_regular,
        fontSize=10,
        leading=14,
        textColor=PDF_COLOR_TEXT,
    )

    section_heading_style = ParagraphStyle(
        "ThuyLoiSectionHeading",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=12.5,
        leading=16,
        textColor=colors.white,
        spaceBefore=0,
        spaceAfter=0,
    )

    heading_style = ParagraphStyle(
        "ThuyLoiHeading",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=11.5,
        leading=16,
        textColor=PDF_COLOR_ACCENT,
        spaceBefore=11,
        spaceAfter=5,
    )

    subheading_style = ParagraphStyle(
        "ThuyLoiSubHeading",
        parent=heading_style,
        fontSize=10.5,
        leading=14,
        spaceBefore=7,
        spaceAfter=4,
    )

    body_style = ParagraphStyle(
        "ThuyLoiBody",
        parent=styles["BodyText"],
        fontName=font_regular,
        fontSize=10.3,
        leading=15.5,
        textColor=PDF_COLOR_TEXT,
        alignment=TA_LEFT,
        spaceAfter=5,
    )

    bullet_style = ParagraphStyle(
        "ThuyLoiBullet",
        parent=body_style,
        leftIndent=12,
        spaceAfter=3,
    )

    number_style = ParagraphStyle(
        "ThuyLoiNumber",
        parent=body_style,
        leftIndent=16,
        firstLineIndent=-16,
        spaceAfter=3,
    )

    note_style = ParagraphStyle(
        "ThuyLoiNote",
        parent=body_style,
        fontSize=9.3,
        leading=13.5,
        textColor=PDF_COLOR_MUTED,
        backColor=PDF_COLOR_LABEL_BG,
        borderColor=PDF_COLOR_BORDER,
        borderWidth=0.6,
        borderPadding=8,
        spaceBefore=6,
        spaceAfter=8,
    )

    # ------------------------------------------------------------
    # KIỂU RIÊNG CHO KHỐI "⚠ LƯU Ý"
    # ------------------------------------------------------------

    warn_header_style = ParagraphStyle(
        "ThuyLoiWarnHeader",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=10.5,
        leading=14,
        textColor=PDF_COLOR_WARN_TEXT,
    )

    warn_body_style = ParagraphStyle(
        "ThuyLoiWarnBody",
        parent=styles["BodyText"],
        fontName=font_regular,
        fontSize=9.5,
        leading=14,
        textColor=PDF_COLOR_WARN_TEXT,
    )

    # ------------------------------------------------------------
    # KIỂU RIÊNG CHO KHỐI "XÁC NHẬN HIỆN TRƯỜNG"
    # ------------------------------------------------------------

    signature_title_style = ParagraphStyle(
        "ThuyLoiSignatureTitle",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=11.5,
        leading=15,
        alignment=TA_CENTER,
        textColor=PDF_COLOR_PRIMARY,
        spaceBefore=4,
        spaceAfter=10,
    )

    signature_label_style = ParagraphStyle(
        "ThuyLoiSignatureLabel",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=10,
        leading=13,
        alignment=TA_CENTER,
        textColor=PDF_COLOR_TEXT,
    )

    signature_caption_style = ParagraphStyle(
        "ThuyLoiSignatureCaption",
        parent=styles["BodyText"],
        fontName=font_regular,
        fontSize=8.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=PDF_COLOR_MUTED,
    )

    story = []

    # ============================================================
    # TIÊU ĐỀ CHÍNH (trong khung nội dung, KHÔNG trùng với dòng
    # nhỏ trên letterhead - letterhead chỉ ghi tên đơn vị/app)
    # ============================================================

    story.append(Paragraph(PDF_DOC_LABEL, title_style))
    story.append(Spacer(1, 8))

    # ============================================================
    # KHỐI "THÔNG TIN BÁO CÁO"
    # ============================================================

    story.append(
        _pdf_section_header_table(
            Paragraph("THÔNG TIN BÁO CÁO", section_heading_style),
            doc.width,
            PDF_COLOR_PRIMARY,
        )
    )

    meta_rows = [
        [
            Paragraph("Loại báo cáo", label_style),
            Paragraph(esc_pdf(report_title), value_style),
        ],
        [
            Paragraph("Thời gian", label_style),
            Paragraph(esc_pdf(generated_at), value_style),
        ],
        [
            Paragraph("Đơn vị", label_style),
            Paragraph(esc_pdf(PDF_ORG_SHORT), value_style),
        ],
        [
            Paragraph("Nguồn", label_style),
            Paragraph(esc_pdf(PDF_APP_NAME), value_style),
        ],
    ]

    meta_table = Table(
        meta_rows,
        colWidths=[35 * mm, None],
        hAlign="LEFT",
    )

    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), PDF_COLOR_LABEL_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, PDF_COLOR_BORDER),
                ("LINEABOVE", (0, 0), (-1, 0), 0, colors.white),
            ]
        )
    )

    story.append(meta_table)

    # ============================================================
    # ẢNH HIỆN TRƯỜNG
    # ============================================================
    if image_bytes:
        try:
            from reportlab.platypus import Image as RLImage

            img_stream = BytesIO(image_bytes)
            pil_img = Image.open(img_stream)

            img_width, img_height = pil_img.size

            # Chiều rộng tối đa theo khổ A4 và lề hiện tại
            max_width = doc.width
            max_height = 105 * mm

            scale = min(
                max_width / img_width,
                max_height / img_height,
                1.0,
            )

            display_width = img_width * scale
            display_height = img_height * scale

            story.append(Spacer(1, 8))

            story.append(
                Paragraph(
                    "ẢNH HIỆN TRƯỜNG",
                    subheading_style,
                )
            )

            story.append(Spacer(1, 5))

            field_image = RLImage(
                BytesIO(image_bytes),
                width=display_width,
                height=display_height,
            )

            field_image.hAlign = "CENTER"

            story.append(field_image)

            story.append(Spacer(1, 10))

        except Exception as image_error:
            print(
                "FIELD REPORT IMAGE ERROR:",
                repr(image_error),
            )

    story.append(Spacer(1, 14))

    # ============================================================
    # KHỐI "NỘI DUNG BÁO CÁO"
    # ============================================================

    story.append(
        _pdf_section_header_table(
            Paragraph("NỘI DUNG BÁO CÁO", section_heading_style),
            doc.width,
            PDF_COLOR_ACCENT,
        )
    )
    story.append(Spacer(1, 10))

    story.extend(
        _build_pdf_content_flowables(
            answer,
            heading_style=heading_style,
            subheading_style=subheading_style,
            body_style=body_style,
            bullet_style=bullet_style,
            number_style=number_style,
            note_style=note_style,
        )
    )

    # ============================================================
    # KHỐI CẢNH BÁO "⚠ LƯU Ý"
    # ============================================================

    story.append(Spacer(1, 10))

    warn_rows = [
        [Paragraph("⚠ LƯU Ý", warn_header_style)],
        [
            Paragraph(
                "Báo cáo là <b>dự thảo</b> được lập tự động với sự hỗ "
                f"trợ của {esc_pdf(PDF_APP_NAME)}, dựa trên hình ảnh và "
                "dữ liệu hồ sơ hiện có. Báo cáo không thay thế kết luận "
                "kỹ thuật hoặc pháp lý chính thức; mọi kết luận cuối "
                "cùng cần được cán bộ kỹ thuật hoặc người có thẩm quyền "
                "xác nhận.",
                warn_body_style,
            )
        ],
    ]

    warn_table = Table(
        warn_rows,
        colWidths=[doc.width],
    )

    warn_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), PDF_COLOR_WARN_HEADER_BG),
                ("BACKGROUND", (0, 1), (0, 1), PDF_COLOR_WARN_BODY_BG),
                ("BOX", (0, 0), (-1, -1), 0.7, PDF_COLOR_WARN_BORDER),
                ("LINEBELOW", (0, 0), (0, 0), 0.7, PDF_COLOR_WARN_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (0, 0), 5),
                ("BOTTOMPADDING", (0, 0), (0, 0), 5),
                ("TOPPADDING", (0, 1), (0, 1), 7),
                ("BOTTOMPADDING", (0, 1), (0, 1), 8),
            ]
        )
    )

    story.append(warn_table)

    # ============================================================
    # KHỐI "XÁC NHẬN HIỆN TRƯỜNG" (chữ ký)
    # ============================================================

    story.append(Spacer(1, 18))
    story.append(
        HRFlowable(
            width="100%",
            thickness=0.5,
            color=PDF_COLOR_BORDER,
            spaceBefore=0,
            spaceAfter=10,
        )
    )

    story.append(
        Paragraph("XÁC NHẬN HIỆN TRƯỜNG", signature_title_style)
    )

    signature_cell_left = [
        Paragraph("Người lập", signature_label_style),
        Spacer(1, 20 * mm),
        Paragraph("(Ký, họ tên)", signature_caption_style),
    ]

    signature_cell_right = [
        Paragraph("Người kiểm tra", signature_label_style),
        Spacer(1, 20 * mm),
        Paragraph("(Ký, họ tên)", signature_caption_style),
    ]

    signature_table = Table(
        [[signature_cell_left, signature_cell_right]],
        colWidths=[doc.width / 2, doc.width / 2],
    )

    signature_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    story.append(signature_table)

    # ============================================================
    # DỰNG PDF (đúng một lần, ở cấp thân hàm)
    # Dùng NumberedCanvas để chân trang hiển thị đúng "Trang X/Y".
    # ============================================================

    numbered_canvas_cls = make_numbered_canvas(
        font_regular,
        PDF_COLOR_MUTED,
    )

    doc.build(story, canvasmaker=numbered_canvas_cls)

    buffer.seek(0)

    return buffer


@app.post("/field-report-pdf")
async def field_report_pdf(
    report_title: str = Form("BÁO CÁO NHANH HIỆN TRƯỜNG"),
    answer: str = Form(""),
    image: UploadFile | None = File(None),
):
    """
    Chuyển dự thảo báo cáo hiện trường thành PDF.
    """
    if not answer.strip():
        return {
            "success": False,
            "error": "Không có nội dung báo cáo để tạo PDF.",
        }

    image_bytes = None
    if image:
        image_bytes = await image.read()

    try:
        pdf_buffer = await asyncio.to_thread(
            create_field_report_pdf,
            report_title,
            answer,
            image_bytes,
        )

        filename = "bao-cao-hien-truong.pdf"

        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except Exception as e:
        print("FIELD REPORT PDF ERROR:", repr(e))
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
