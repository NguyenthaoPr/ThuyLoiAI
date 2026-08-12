import os
import uuid
import asyncio
import random
import time
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from collections import OrderedDict, defaultdict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

try:
    from notebooklm import NotebookLMClient
except Exception as exc:
    NotebookLMClient = None
    NOTEBOOKLM_IMPORT_ERROR = repr(exc)
else:
    NOTEBOOKLM_IMPORT_ERROR = None

try:
    import httpx
except Exception as exc:
    httpx = None
    HTTPX_IMPORT_ERROR = repr(exc)
else:
    HTTPX_IMPORT_ERROR = None


# ============================================================
# THỦY LỢI AI - SERVER NOTEBOOKLM V9
# Kiến trúc: NotebookLM (nguồn chính) + nhiều tầng tự phục hồi
#            + nguồn dự phòng (fallback) + hàng đợi/giới hạn tải
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

NOTEBOOK_ID = (
    os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
    or os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip()
)
AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "").strip()
ADMIN_TOKEN = os.getenv("THUYLOIA_ADMIN_TOKEN", "").strip()

MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "3")))
REQUEST_TIMEOUT = max(30, int(os.getenv("REQUEST_TIMEOUT", "180")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "3")))
WATCHDOG_SECONDS = max(60, int(os.getenv("WATCHDOG_SECONDS", "300")))
CACHE_TTL = max(0, int(os.getenv("ANSWER_CACHE_TTL", "600")))
CACHE_SIZE = max(10, int(os.getenv("ANSWER_CACHE_SIZE", "100")))
CIRCUIT_THRESHOLD = max(1, int(os.getenv("CIRCUIT_THRESHOLD", "3")))
CIRCUIT_COOLDOWN = max(30, int(os.getenv("CIRCUIT_COOLDOWN", "120")))
HEADLESS_REAUTH = os.getenv("NOTEBOOKLM_HEADLESS_REAUTH", "0").strip() == "1"

# --- V9: tự giám sát / phân loại sự cố ---
RECOVERY_MAX_ATTEMPTS = max(1, int(os.getenv("RECOVERY_MAX_ATTEMPTS", "3")))
RECOVERY_BACKOFF_BASE = max(1, int(os.getenv("RECOVERY_BACKOFF_BASE", "5")))
RECOVERY_LOCK_TIMEOUT = max(5, int(os.getenv("RECOVERY_LOCK_TIMEOUT", "20")))
HEALTH_STALE_SECONDS = max(60, int(os.getenv("HEALTH_STALE_SECONDS", "900")))
APP_INCIDENT_ENABLED = os.getenv("APP_INCIDENT_ENABLED", "1").strip() == "1"

# --- Auth recovery ---
AUTH_REFRESH_BEFORE_RECONNECT = os.getenv("AUTH_REFRESH_BEFORE_RECONNECT", "1").strip() == "1"
AUTH_REFRESH_COOLDOWN = max(30, int(os.getenv("AUTH_REFRESH_COOLDOWN", "300")))
last_auth_refresh = 0.0

# --- Hàng đợi / chống quá tải ---
QUEUE_MAX_WAIT = max(5, int(os.getenv("QUEUE_MAX_WAIT", "25")))          # giây chờ tối đa để có "chỗ" xử lý
QUEUE_MAX_PENDING = max(
    MAX_CONCURRENT, int(os.getenv("QUEUE_MAX_PENDING", str(MAX_CONCURRENT * 6)))
)  # số request được phép xếp hàng chờ cùng lúc
HARD_DEADLINE = max(
    REQUEST_TIMEOUT, int(os.getenv("HARD_DEADLINE", str(REQUEST_TIMEOUT * MAX_RETRIES + 30)))
)  # giới hạn cứng tổng thời gian xử lý 1 câu hỏi (bao gồm retry)

# --- Giới hạn tần suất theo IP (token bucket) ---
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1").strip() == "1"
RATE_LIMIT_PER_MINUTE = max(1, int(os.getenv("RATE_LIMIT_PER_MINUTE", "20")))
RATE_LIMIT_BURST = max(1, int(os.getenv("RATE_LIMIT_BURST", "5")))
RATE_LIMIT_CLEANUP_SECONDS = max(60, int(os.getenv("RATE_LIMIT_CLEANUP_SECONDS", "600")))

# --- Nguồn dự phòng (fallback) khi NotebookLM không phản hồi được ---
ENABLE_FALLBACK = os.getenv("ENABLE_FALLBACK", "1").strip() == "1"
FALLBACK_TIMEOUT = max(10, int(os.getenv("FALLBACK_TIMEOUT", "60")))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_FALLBACK_MODEL = os.getenv("ANTHROPIC_FALLBACK_MODEL", "").strip()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.0-flash").strip()

SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của Chi nhánh Thủy lợi
Vu Gia - Thu Bồn.

NGUYÊN TẮC:
1. Ưu tiên tuyệt đối nội dung trong NotebookLM và các tài liệu của notebook.
2. Không tự bịa số liệu, điều khoản, tên/số văn bản, ngày tháng, thông số kỹ thuật
   hoặc quy trình vận hành.
3. Nếu tài liệu không đủ căn cứ, nói rõ rằng chưa tìm thấy đủ căn cứ trong kho.
4. Khi có căn cứ, nêu tên tài liệu hoặc nguồn nếu NotebookLM cung cấp.
5. Nếu nhiều tài liệu khác nhau, chỉ ra điểm khác nhau.
6. Phân biệt rõ nội dung tài liệu với nhận định/suy luận.
7. Trả lời bằng tiếng Việt, rõ ràng, ngắn gọn nhưng đủ căn cứ.
8. Với quy trình, ưu tiên trình bày theo từng bước.
9. Giữ nguyên số liệu và đơn vị theo tài liệu.
"""

FALLBACK_SYSTEM_PROMPT = """
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của Chi nhánh Thủy lợi
Vu Gia - Thu Bồn. HIỆN TẠI bạn KHÔNG có quyền truy cập vào kho tài liệu
NotebookLM (đang gặp sự cố kỹ thuật tạm thời).

NGUYÊN TẮC BẮT BUỘC:
1. KHÔNG bịa số liệu, tên/số văn bản, ngày tháng, thông số kỹ thuật cụ thể mà
   bạn không chắc chắn — nếu không chắc, hãy nói rõ là chưa thể xác nhận.
2. LUÔN mở đầu câu trả lời bằng một câu cảnh báo ngắn rằng đây là câu trả lời
   tổng quát, CHƯA được đối chiếu với kho tài liệu nội bộ của đơn vị, và người
   dùng cần kiểm tra lại với NotebookLM hoặc tài liệu gốc khi hệ thống phục hồi.
3. Trả lời bằng tiếng Việt, ngắn gọn, rõ ràng, đúng trọng tâm câu hỏi.
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("thuyloiai")


# -------------------------
# Runtime state
# -------------------------
client = None
client_lock = asyncio.Lock()
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

pending_count = 0
pending_lock = asyncio.Lock()

state = {
    "status": "starting",
    "connected": False,
    "last_check": None,
    "last_success": None,
    "last_error": None,
    "last_error_time": None,
    "consecutive_failures": 0,
    "recovery_count": 0,
    "circuit_open": False,
    "circuit_until": 0.0,
    "last_recovery": None,
    "last_recovery_reason": None,
    "last_recovery_level": 0,
    "recovery_in_progress": False,
    "recovery_needed": False,
    "auth_expired_suspected": False,
    "last_request_id": None,
}

metrics = {
    "requests_total": 0,
    "requests_ok": 0,
    "requests_error": 0,
    "cache_hits": 0,
    "fallback_used": 0,
    "rejected_overload": 0,
    "rejected_rate_limit": 0,
    "started_at": None,
}

cache = OrderedDict()


class Question(BaseModel):
    question: str


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# -------------------------
# Cache
# -------------------------
def cache_key(question: str) -> str:
    return " ".join(question.lower().split())


def cache_get(question: str):
    if CACHE_TTL <= 0:
        return None

    key = cache_key(question)
    item = cache.get(key)
    if not item:
        return None

    if time.time() - item["time"] > CACHE_TTL:
        cache.pop(key, None)
        return None

    cache.move_to_end(key)
    return item["value"]


def cache_put(question: str, value: dict):
    if CACHE_TTL <= 0:
        return

    key = cache_key(question)
    cache[key] = {
        "time": time.time(),
        "value": value,
    }
    cache.move_to_end(key)

    while len(cache) > CACHE_SIZE:
        cache.popitem(last=False)


# -------------------------
# Rate limiter (token bucket theo IP)
# -------------------------
class RateLimiter:
    def __init__(self, rate_per_minute: int, burst: int):
        self.rate_per_second = rate_per_minute / 60.0
        self.capacity = max(burst, 1)
        self.buckets = defaultdict(lambda: {"tokens": float(self.capacity), "updated": time.time()})
        self.lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        async with self.lock:
            bucket = self.buckets[key]
            elapsed = time.time() - bucket["updated"]
            bucket["tokens"] = min(
                self.capacity, bucket["tokens"] + elapsed * self.rate_per_second
            )
            bucket["updated"] = time.time()

            if bucket["tokens"] >= 1:
                bucket["tokens"] -= 1
                return True

            return False

    async def cleanup(self, max_age_seconds: int = 900):
        async with self.lock:
            cutoff = time.time() - max_age_seconds
            stale = [k for k, v in self.buckets.items() if v["updated"] < cutoff]
            for k in stale:
                self.buckets.pop(k, None)
            if stale:
                log.info("RATE-LIMIT: dọn %s bucket không hoạt động.", len(stale))


rate_limiter = RateLimiter(RATE_LIMIT_PER_MINUTE, RATE_LIMIT_BURST)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# -------------------------
# Circuit breaker / lỗi
# -------------------------
def mark_success():
    state["status"] = "ok"
    state["connected"] = True
    state["last_success"] = now_text()
    state["last_error"] = None
    state["consecutive_failures"] = 0
    state["circuit_open"] = False
    state["circuit_until"] = 0.0
    state["recovery_needed"] = False
    state["recovery_in_progress"] = False
    state["auth_expired_suspected"] = False
    state["last_recovery_level"] = 0


def mark_failure(error: Exception):
    state["connected"] = False
    state["last_error"] = str(error)[:500]
    state["last_error_time"] = now_text()
    state["last_request_id"] = state.get("last_request_id")
    state["consecutive_failures"] += 1
    state["recovery_needed"] = True
    kind = classify_error(error)
    state["auth_expired_suspected"] = kind == "AUTH"
    state["status"] = "auth_error" if kind == "AUTH" else "degraded"

    if state["consecutive_failures"] >= CIRCUIT_THRESHOLD:
        state["circuit_open"] = True
        state["circuit_until"] = time.time() + CIRCUIT_COOLDOWN
        state["status"] = "recovery"
    else:
        state["status"] = "degraded"


def classify_error(error: Exception) -> str:
    """Phân loại lỗi để APP có thể biết nên xử lý gì."""
    if is_auth_error(error):
        return "AUTH"
    text = str(error).lower()
    if any(x in text for x in ("timeout", "timed out")):
        return "TIMEOUT"
    if any(x in text for x in ("429", "rate limit", "resource exhausted")):
        return "RATE_LIMIT"
    if any(x in text for x in ("connection", "network", "reset", "unavailable")):
        return "NETWORK"
    return "UNKNOWN"


def recovery_level() -> int:
    """0=normal, 1=retry, 2=refresh, 3=reconnect, 4=fallback, 5=human/auth action."""
    if state.get("connected"):
        return 0
    if state.get("auth_expired_suspected"):
        return 5
    if state.get("recovery_in_progress"):
        return max(1, int(state.get("last_recovery_level") or 1))
    if state.get("recovery_needed") or state.get("circuit_open"):
        return 3
    return 1


def set_recovery_state(level: int, reason: str = ""):
    state["last_recovery_level"] = level
    state["last_recovery_reason"] = reason[:300] if reason else state.get("last_recovery_reason")
    state["recovery_needed"] = level > 0


def circuit_available():
    if not state["circuit_open"]:
        return True

    if time.time() >= state["circuit_until"]:
        state["circuit_open"] = False
        state["status"] = "recovering"
        return True

    return False


def is_auth_error(error: Exception) -> bool:
    text = str(error).lower()
    words = (
        "authentication expired",
        "authentication invalid",
        "unauthenticated",
        "401",
        "403",
        "sign-in",
        "signin",
        "login",
        "cookie",
        "csrf",
        "session expired",
        "invalid authentication",
    )
    return any(word in text for word in words)


def is_retryable(error: Exception) -> bool:
    if is_auth_error(error):
        return True

    text = str(error).lower()
    words = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
        "temporarily",
        "unavailable",
        "connection",
        "reset",
        "network",
        "rate limit",
        "resource exhausted",
        "server error",
    )
    return any(word in text for word in words)


@asynccontextmanager
async def acquire_slot(timeout: int):
    """Chiếm 1 'chỗ' xử lý trong giới hạn MAX_CONCURRENT, có timeout chờ.

    Nếu không xin được chỗ trong thời gian `timeout`, ném TimeoutError để
    endpoint trả về 503 thay vì để request treo vô thời hạn.
    """
    acquired = False
    try:
        acquired = await asyncio.wait_for(request_semaphore.acquire(), timeout=timeout)
        yield
    finally:
        if acquired:
            request_semaphore.release()


# -------------------------
# NotebookLM client lifecycle
# -------------------------
async def close_client():
    global client

    old = client
    client = None

    if old is None:
        return

    try:
        await old[1].__aexit__(None, None, None)
    except Exception as exc:
        log.warning("Đóng NotebookLM client lỗi: %s", exc)


async def create_client():
    global client

    if NotebookLMClient is None:
        raise RuntimeError(
            "Thiếu notebooklm-py. Hãy thêm notebooklm-py vào requirements.txt."
        )

    if not NOTEBOOK_ID:
        raise RuntimeError("Chưa cấu hình NOTEBOOKLM_NOTEBOOK (hoặc NOTEBOOKLM_NOTEBOOK_ID).")

    if not AUTH_JSON:
        raise RuntimeError("Chưa cấu hình NOTEBOOKLM_AUTH_JSON.")

    # NOTEBOOKLM_AUTH_JSON được thư viện tự đọc từ Environment.
    # keepalive giúp client sống ổn định hơn trong thời gian process còn chạy.
    # Không ghi credential ra log.
    cm = NotebookLMClient.from_storage(
        keepalive=max(60, int(os.getenv("NOTEBOOKLM_KEEPALIVE", "900"))),
        timeout=min(60.0, float(REQUEST_TIMEOUT)),
        max_concurrent_rpcs=max(1, MAX_CONCURRENT),
    )
    new_client = await cm.__aenter__()

    client = (new_client, cm)

    log.info("NotebookLM: client đã khởi tạo.")
    return new_client


async def _verify_notebook(nb_client):
    """Kiểm tra auth + notebook ID mà không gửi câu hỏi."""
    notebooks = await asyncio.wait_for(
        nb_client.notebooks.list(),
        timeout=min(60, REQUEST_TIMEOUT),
    )
    found = any(str(getattr(nb, "id", "")) == NOTEBOOK_ID for nb in notebooks)
    if not found:
        raise RuntimeError(
            "Đã xác thực NotebookLM nhưng không tìm thấy notebook được cấu hình."
        )
    return True


async def _try_refresh_existing_client(allow_headless=False):
    """Tầng phục hồi auth ưu tiên: refresh trên client hiện tại.

    Quan trọng: phải refresh TRƯỚC khi đóng client. Nếu đóng client trước,
    refresh_auth() không còn đối tượng để thực hiện.
    """
    global last_auth_refresh

    if not AUTH_REFRESH_BEFORE_RECONNECT or client is None:
        return False

    if time.time() - last_auth_refresh < AUTH_REFRESH_COOLDOWN:
        return False

    current = client[0]
    try:
        last_auth_refresh = time.time()
        log.warning("RECOVERY: thử refresh_auth() trên client hiện tại.")
        await current.refresh_auth(
            allow_headless=allow_headless or HEADLESS_REAUTH
        )
        await _verify_notebook(current)
        mark_success()
        state["recovery_count"] += 1
        state["last_recovery"] = now_text()
        log.info("RECOVERY: refresh_auth + verify thành công.")
        return True
    except Exception as exc:
        log.warning("RECOVERY: refresh_auth trên client hiện tại thất bại: %s", exc)
        return False


async def reconnect(reason="unknown", allow_headless=False):
    global client

    async with client_lock:
        log.warning("RECOVERY: reconnect NotebookLM | reason=%s", reason)

        # TẦNG 1: tận dụng client đang sống và refresh auth trước.
        if await _try_refresh_existing_client(allow_headless):
            return True

        # TẦNG 2: tạo client mới từ NOTEBOOKLM_AUTH_JSON / storage.
        await close_client()

        try:
            nb_client = await create_client()
            await _verify_notebook(nb_client)

            mark_success()
            state["recovery_count"] += 1
            state["last_recovery"] = now_text()
            log.info("RECOVERY: tạo client mới + verify thành công.")
            return True

        except Exception as first_error:
            log.error("RECOVERY: tạo client mới thất bại: %s", first_error)
            mark_failure(first_error)

        # TẦNG 3: nếu client mới còn sống nhưng auth lỗi, thử refresh.
        # (Không phải lúc nào cũng có client sau lỗi create.)
        if client is not None:
            try:
                current = client[0]
                log.warning("RECOVERY: thử refresh_auth() tầng 3.")
                await current.refresh_auth(
                    allow_headless=allow_headless or HEADLESS_REAUTH
                )
                await _verify_notebook(current)
                mark_success()
                state["recovery_count"] += 1
                state["last_recovery"] = now_text()
                log.info("RECOVERY: refresh_auth tầng 3 thành công.")
                return True
            except Exception as second_error:
                log.error("RECOVERY: refresh_auth tầng 3 thất bại: %s", second_error)
                mark_failure(second_error)

        return False


async def ask_notebooklm(question: str, request_id: str):
    global client

    last_error = None

    for attempt in range(MAX_RETRIES):
        if not circuit_available():
            raise RuntimeError("Circuit Breaker đang mở; hệ thống đang tự phục hồi.")

        try:
            async with acquire_slot(QUEUE_MAX_WAIT):
                if client is None:
                    ok = await reconnect("client_none")
                    if not ok:
                        raise RuntimeError("Không kết nối được NotebookLM.")

                nb_client = client[0]

                result = await asyncio.wait_for(
                    nb_client.chat.ask(
                        NOTEBOOK_ID,
                        f"{SYSTEM_PROMPT}\n\nCÂU HỎI:\n{question}",
                    ),
                    timeout=REQUEST_TIMEOUT,
                )

            answer = (getattr(result, "answer", "") or "").strip()

            if not answer:
                raise RuntimeError("NotebookLM không trả về nội dung.")

            references = []
            for ref in (getattr(result, "references", []) or []):
                item = {}
                for attr in ("source_id", "cited_text", "start_char", "end_char"):
                    value = getattr(ref, attr, None)
                    if value is not None:
                        item[attr] = value
                if item:
                    references.append(item)

            mark_success()

            return {
                "status": "ok",
                "answer": answer,
                "engine": "NotebookLM",
                "grounded": True,
                "sources": references,
            }

        except asyncio.TimeoutError as exc:
            # Timeout ở đây có thể đến từ hàng đợi hoặc lời gọi NotebookLM.
            # Tách rõ thông báo để tránh chẩn đoán sai.
            last_error = RuntimeError(
                "NotebookLM hoặc hàng đợi xử lý đã vượt thời gian cho phép."
            )
            log.warning(
                "[%s] NotebookLM: timeout ở tầng xử lý (lần %s/%s): %s",
                request_id, attempt + 1, MAX_RETRIES, exc
            )
            mark_failure(last_error)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1 + attempt)
                continue
            break

        except Exception as exc:
            last_error = exc
            log.warning(
                "[%s] NotebookLM lỗi lần %s/%s: %s",
                request_id, attempt + 1, MAX_RETRIES, exc,
            )
            mark_failure(exc)

            # Tầng 1: retry.
            if attempt < MAX_RETRIES - 1 and is_retryable(exc):
                delay = min(12, 2 ** attempt) + random.uniform(0, 0.7)
                await asyncio.sleep(delay)
                continue

            # Tầng 2/3: auth refresh + reconnect.
            if is_auth_error(exc):
                ok = await reconnect(
                    "authentication_error",
                    allow_headless=HEADLESS_REAUTH,
                )
                if ok:
                    try:
                        async with acquire_slot(QUEUE_MAX_WAIT):
                            nb_client = client[0]
                            result = await asyncio.wait_for(
                                nb_client.chat.ask(
                                    NOTEBOOK_ID,
                                    f"{SYSTEM_PROMPT}\n\nCÂU HỎI:\n{question}",
                                ),
                                timeout=REQUEST_TIMEOUT,
                            )
                        answer = (getattr(result, "answer", "") or "").strip()
                        if answer:
                            mark_success()
                            return {
                                "status": "ok",
                                "answer": answer,
                                "engine": "NotebookLM",
                                "grounded": True,
                                "sources": [],
                            }
                    except Exception as retry_exc:
                        last_error = retry_exc
                        mark_failure(retry_exc)

            break

    raise last_error or RuntimeError("NotebookLM không thể xử lý câu hỏi.")


# -------------------------
# Nguồn dự phòng (fallback) - dùng khi NotebookLM không phản hồi được
# -------------------------
async def ask_fallback_claude(question: str, request_id: str):
    if httpx is None:
        raise RuntimeError(f"Thiếu thư viện httpx: {HTTPX_IMPORT_ERROR}")
    if not ANTHROPIC_API_KEY or not ANTHROPIC_FALLBACK_MODEL:
        raise RuntimeError("Chưa cấu hình ANTHROPIC_API_KEY / ANTHROPIC_FALLBACK_MODEL.")

    async with httpx.AsyncClient(timeout=FALLBACK_TIMEOUT) as http:
        resp = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_FALLBACK_MODEL,
                "max_tokens": 1024,
                "system": FALLBACK_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": question}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    text_parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    answer = "\n".join(p for p in text_parts if p).strip()

    if not answer:
        raise RuntimeError("Claude fallback không trả về nội dung.")

    log.info("[%s] FALLBACK: dùng Claude API thành công.", request_id)
    return {
        "status": "ok",
        "answer": answer,
        "engine": "Claude (fallback)",
        "grounded": False,
        "sources": [],
    }


async def ask_fallback_gemini(question: str, request_id: str):
    if httpx is None:
        raise RuntimeError(f"Thiếu thư viện httpx: {HTTPX_IMPORT_ERROR}")
    if not GOOGLE_API_KEY:
        raise RuntimeError("Chưa cấu hình GOOGLE_API_KEY.")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_FALLBACK_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    )

    async with httpx.AsyncClient(timeout=FALLBACK_TIMEOUT) as http:
        resp = await http.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": FALLBACK_SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": question}]}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        answer = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        answer = ""

    if not answer:
        raise RuntimeError("Gemini fallback không trả về nội dung.")

    log.info("[%s] FALLBACK: dùng Gemini API thành công.", request_id)
    return {
        "status": "ok",
        "answer": answer,
        "engine": "Gemini (fallback)",
        "grounded": False,
        "sources": [],
    }


async def ask_fallback_chain(question: str, request_id: str):
    """Thử lần lượt các nguồn dự phòng đã cấu hình. Trả về None nếu không có
    nguồn nào khả dụng hoặc tất cả đều lỗi."""
    if not ENABLE_FALLBACK:
        return None

    providers = [
        ("claude", ask_fallback_claude),
        ("gemini", ask_fallback_gemini),
    ]

    for name, provider in providers:
        try:
            result = await provider(question, request_id)
            metrics["fallback_used"] += 1
            result["warning"] = (
                "Câu trả lời này CHƯA được đối chiếu với kho tài liệu NotebookLM "
                "do hệ thống chính đang gặp sự cố tạm thời. Vui lòng kiểm tra lại "
                "khi hệ thống phục hồi."
            )
            return result
        except Exception as exc:
            log.warning("[%s] FALLBACK (%s) thất bại: %s", request_id, name, exc)
            continue

    return None


# -------------------------
# Watchdog & dọn dẹp định kỳ
# -------------------------
async def watchdog():
    # Không hỏi chat để kiểm tra sức khỏe; chỉ kiểm tra notebook/auth.
    await asyncio.sleep(10)

    tick = 0
    while True:
        try:
            state["last_check"] = now_text()

            if client is None:
                await reconnect("watchdog_client_none")
            elif circuit_available():
                try:
                    nb_client = client[0]
                    notebooks = await asyncio.wait_for(
                        nb_client.notebooks.list(),
                        timeout=min(60, REQUEST_TIMEOUT),
                    )
                    found = any(
                        str(getattr(nb, "id", "")) == NOTEBOOK_ID
                        for nb in notebooks
                    )

                    if found:
                        mark_success()
                    else:
                        raise RuntimeError(
                            "Watchdog không tìm thấy notebook được cấu hình."
                        )

                except Exception as exc:
                    log.warning("WATCHDOG lỗi: %s", exc)
                    mark_failure(exc)
                    await reconnect("watchdog_failure")

        except Exception as exc:
            log.error("WATCHDOG ngoại lệ: %s", exc)

        # Dọn rate-limit buckets định kỳ (không cần mỗi vòng watchdog).
        tick += 1
        if RATE_LIMIT_ENABLED and tick % 3 == 0:
            try:
                await rate_limiter.cleanup(RATE_LIMIT_CLEANUP_SECONDS)
            except Exception as exc:
                log.warning("RATE-LIMIT cleanup lỗi: %s", exc)

        await asyncio.sleep(WATCHDOG_SECONDS)


async def lifespan(app: FastAPI):
    log.info("==========================================")
    log.info("       KHỞI ĐỘNG THỦY LỢI AI V7")
    log.info("       ENGINE CHÍNH: NOTEBOOKLM")
    log.info("       FALLBACK: %s", "BẬT" if ENABLE_FALLBACK else "TẮT")
    log.info("       AUTO AUTH REFRESH: %s", "BẬT" if AUTH_REFRESH_BEFORE_RECONNECT else "TẮT")
    log.info("==========================================")

    if not NOTEBOOK_ID:
        log.error("NOTEBOOKLM_NOTEBOOK: CHƯA CÓ")
    else:
        log.info("NOTEBOOK ID: ĐÃ CẤU HÌNH")

    if not AUTH_JSON:
        log.error("NOTEBOOKLM_AUTH_JSON: CHƯA CÓ")
    else:
        log.info("NOTEBOOKLM AUTH: ĐÃ CẤU HÌNH")
    if AUTH_JSON:
        log.info(
            "NOTEBOOKLM AUTH MODE: dùng NOTEBOOKLM_AUTH_JSON; "
            "cookie xoay vòng có thể sống trong process nhưng không tự ghi ngược secret Render."
        )

    if NOTEBOOKLM_IMPORT_ERROR:
        log.error("NOTEBOOKLM IMPORT ERROR: %s", NOTEBOOKLM_IMPORT_ERROR)
    else:
        log.info("NOTEBOOKLM PY: ĐÃ IMPORT")

    if ENABLE_FALLBACK:
        if httpx is None:
            log.error("FALLBACK: httpx chưa cài đặt (%s) -> fallback sẽ không hoạt động.", HTTPX_IMPORT_ERROR)
        if not (ANTHROPIC_API_KEY and ANTHROPIC_FALLBACK_MODEL):
            log.warning("FALLBACK: Claude chưa được cấu hình đầy đủ (bỏ qua nguồn này).")
        if not GOOGLE_API_KEY:
            log.warning("FALLBACK: Gemini chưa được cấu hình (bỏ qua nguồn này).")

    state["status"] = "starting"
    metrics["started_at"] = now_text()

    # Khởi động server trước; lỗi NotebookLM không được làm server chết.
    try:
        await reconnect("startup")
    except Exception as exc:
        log.error("Startup recovery lỗi: %s", exc)

    task = asyncio.create_task(watchdog())

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await close_client()
    log.info("THỦY LỢI AI: đã dừng.")


app = FastAPI(
    title="THỦY LỢI AI",
    description="Trợ lý AI chuyên ngành Thủy lợi - NotebookLM + nhiều tầng tự phục hồi + fallback",
    version="9.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE), media_type="text/html")

    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "NotebookLM",
        "message": "Backend đang hoạt động.",
        "health": "/health",
        "ask": "/ask",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "engine": "NotebookLM",
        "notebooklm_configured": bool(NOTEBOOK_ID and AUTH_JSON),
        "notebooklm_connected": bool(state["connected"]),
        "notebooklm_status": state["status"],
        "notebook_id_configured": bool(NOTEBOOK_ID),
        "notebook_env_source": (
            "NOTEBOOKLM_NOTEBOOK" if os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
            else ("NOTEBOOKLM_NOTEBOOK_ID" if os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip() else None)
        ),
        "auth_configured": bool(AUTH_JSON),
        "auth_mode": "NOTEBOOKLM_AUTH_JSON" if AUTH_JSON else "missing",
        "last_check": state["last_check"],
        "last_success": state["last_success"],
        "last_error": state["last_error"],
        "last_error_time": state["last_error_time"],
        "consecutive_failures": state["consecutive_failures"],
        "recovery_count": state["recovery_count"],
        "circuit_open": state["circuit_open"],
        "watchdog_seconds": WATCHDOG_SECONDS,
        "fallback_enabled": ENABLE_FALLBACK,
        "fallback_claude_configured": bool(ANTHROPIC_API_KEY and ANTHROPIC_FALLBACK_MODEL),
        "fallback_gemini_configured": bool(GOOGLE_API_KEY),
        "queue_pending": pending_count,
        "queue_max_pending": QUEUE_MAX_PENDING,
        "concurrency_slots_in_use": MAX_CONCURRENT - request_semaphore._value,
        "concurrency_slots_total": MAX_CONCURRENT,
    }


@app.get("/status")
async def status():
    return await health()


@app.get("/metrics")
async def get_metrics():
    return dict(metrics)


@app.get("/diagnostics")
async def diagnostics():
    return {
        "service": "THỦY LỢI AI",
        "version": "9.0",
        "engine": "NotebookLM",
        "notebooklm_package_loaded": NotebookLMClient is not None,
        "notebooklm_import_error": NOTEBOOKLM_IMPORT_ERROR,
        "httpx_loaded": httpx is not None,
        "httpx_import_error": HTTPX_IMPORT_ERROR,
        "notebook_id_configured": bool(NOTEBOOK_ID),
        "notebook_env_source": (
            "NOTEBOOKLM_NOTEBOOK" if os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
            else ("NOTEBOOKLM_NOTEBOOK_ID" if os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip() else None)
        ),
        "auth_configured": bool(AUTH_JSON),
        "auth_mode": "NOTEBOOKLM_AUTH_JSON" if AUTH_JSON else "missing",
        "runtime": dict(state),
        "metrics": dict(metrics),
        "configuration": {
            "max_concurrent": MAX_CONCURRENT,
            "request_timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
            "watchdog_seconds": WATCHDOG_SECONDS,
            "cache_ttl": CACHE_TTL,
            "cache_size": CACHE_SIZE,
            "circuit_threshold": CIRCUIT_THRESHOLD,
            "circuit_cooldown": CIRCUIT_COOLDOWN,
            "headless_reauth": HEADLESS_REAUTH,
            "auth_refresh_before_reconnect": AUTH_REFRESH_BEFORE_RECONNECT,
            "auth_refresh_cooldown": AUTH_REFRESH_COOLDOWN,
            "keepalive_seconds": max(60, int(os.getenv("NOTEBOOKLM_KEEPALIVE", "900"))),
            "queue_max_wait": QUEUE_MAX_WAIT,
            "queue_max_pending": QUEUE_MAX_PENDING,
            "hard_deadline": HARD_DEADLINE,
            "rate_limit_enabled": RATE_LIMIT_ENABLED,
            "rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
            "rate_limit_burst": RATE_LIMIT_BURST,
            "fallback_enabled": ENABLE_FALLBACK,
            "fallback_timeout": FALLBACK_TIMEOUT,
        },
    }


@app.get("/api/info")
async def api_info():
    return {
        "name": "THỦY LỢI AI",
        "engine": "NotebookLM",
        "version": "9.0",
        "notebook_configured": bool(NOTEBOOK_ID),
        "endpoints": {
            "home": "/",
            "health": "/health",
            "status": "/status",
            "metrics": "/metrics",
            "diagnostics": "/diagnostics",
            "ask": "/ask",
            "recovery": "/recovery",
        },
    }


@app.post("/ask")
async def ask(data: Question, request: Request):
    global pending_count

    request_id = uuid.uuid4().hex[:12]
    state["last_request_id"] = request_id
    metrics["requests_total"] += 1

    question = data.question.strip()

    if not question:
        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi.",
            "engine": "NotebookLM",
            "request_id": request_id,
        }

    if len(question) > 4000:
        return JSONResponse(
            status_code=413,
            content={
                "status": "error",
                "answer": "Câu hỏi quá dài, vui lòng rút gọn (tối đa 4000 ký tự).",
                "request_id": request_id,
            },
        )

    # --- Tầng chống lạm dụng: rate limit theo IP ---
    if RATE_LIMIT_ENABLED:
        ip = client_ip(request)
        allowed = await rate_limiter.allow(ip)
        if not allowed:
            metrics["rejected_rate_limit"] += 1
            log.warning("[%s] RATE-LIMIT: từ chối IP %s.", request_id, ip)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "10"},
                content={
                    "status": "error",
                    "answer": "Bạn đang gửi câu hỏi quá nhanh. Vui lòng thử lại sau ít giây.",
                    "request_id": request_id,
                },
            )

    # --- Tầng cache: câu hỏi đã có kết quả gần đây ---
    cached = cache_get(question)
    if cached:
        metrics["cache_hits"] += 1
        result = dict(cached)
        result["cached"] = True
        result["request_id"] = request_id
        return result

    # --- Tầng chống quá tải: giới hạn số request đang chờ xử lý ---
    async with pending_lock:
        if pending_count >= QUEUE_MAX_PENDING:
            metrics["rejected_overload"] += 1
            log.warning("[%s] QUEUE: từ chối do hàng đợi đầy (%s/%s).",
                        request_id, pending_count, QUEUE_MAX_PENDING)
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "15"},
                content={
                    "status": "error",
                    "answer": (
                        "Hệ thống đang có rất nhiều người truy cập. "
                        "Vui lòng thử lại sau ít phút."
                    ),
                    "request_id": request_id,
                    "recovery": True,
                },
            )
        pending_count += 1

    try:
        # Nếu circuit breaker đang mở, thử phục hồi trước khi xử lý.
        if not circuit_available():
            ok = await reconnect("ask_circuit_breaker")
            if not ok:
                fallback_result = await ask_fallback_chain(question, request_id)
                if fallback_result:
                    fallback_result["request_id"] = request_id
                    metrics["requests_ok"] += 1
                    return fallback_result

                metrics["requests_error"] += 1
                return {
                    "status": "error",
                    "answer": (
                        "THỦY LỢI AI đang tự khắc phục kết nối NotebookLM. "
                        "Vui lòng thử lại sau ít phút."
                    ),
                    "engine": "NotebookLM",
                    "recovery": True,
                    "request_id": request_id,
                }

        try:
            result = await asyncio.wait_for(
                ask_notebooklm(question, request_id), timeout=HARD_DEADLINE
            )
            result["request_id"] = request_id
            cache_put(question, result)
            metrics["requests_ok"] += 1
            return result

        except asyncio.TimeoutError:
            last_exc = RuntimeError(
                f"Vượt quá giới hạn thời gian xử lý tổng thể ({HARD_DEADLINE}s)."
            )
            log.error("[%s] ASK: hết hạn deadline cứng.", request_id)
            mark_failure(last_exc)

        except Exception as exc:
            log.error("[%s] ASK thất bại: %s", request_id, exc)

        # --- Tầng cuối 1: thử nguồn dự phòng (Claude / Gemini) ---
        fallback_result = await ask_fallback_chain(question, request_id)
        if fallback_result:
            fallback_result["request_id"] = request_id
            metrics["requests_ok"] += 1
            return fallback_result

        # --- Tầng cuối 2: trả lại kết quả cũ trong cache nếu có ---
        cached = cache_get(question)
        if cached:
            metrics["cache_hits"] += 1
            result = dict(cached)
            result["cached"] = True
            result["warning"] = "NotebookLM đang gặp sự cố; hiển thị kết quả đã lưu."
            result["request_id"] = request_id
            return result

        metrics["requests_error"] += 1
        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa lấy được câu trả lời từ NotebookLM. "
                "Hệ thống đã tự động thử lại và đang tiếp tục khắc phục kết nối."
            ),
            "engine": "NotebookLM",
            "recovery": True,
            "request_id": request_id,
        }

    finally:
        async with pending_lock:
            pending_count -= 1


@app.post("/recovery")
async def recovery(authorization: str | None = Header(default=None)):
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Server chưa được cấu hình THUYLOIA_ADMIN_TOKEN.",
        )

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Không được phép.")

    state["recovery_in_progress"] = True
    set_recovery_state(3, "manual_recovery")
    try:
        ok = await reconnect("manual_recovery", allow_headless=HEADLESS_REAUTH)
    finally:
        state["recovery_in_progress"] = False

    return {
        "status": "ok" if ok else "error",
        "message": (
            "Đã khôi phục kết nối NotebookLM."
            if ok
            else "Khôi phục tự động chưa thành công; cần kiểm tra xác thực NotebookLM."
        ),
        "runtime": dict(state),
    }


@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "THỦY LỢI AI"}


@app.get("/recovery-status")
async def recovery_status():
    """API nhẹ cho APP điện thoại: đọc tình trạng và hướng xử lý."""
    stale = False
    if state.get("last_check"):
        try:
            checked = time.mktime(time.strptime(state["last_check"], "%Y-%m-%d %H:%M:%S"))
            stale = (time.time() - checked) > HEALTH_STALE_SECONDS
        except Exception:
            stale = True

    level = recovery_level()
    action_map = {
        0: "NORMAL",
        1: "RETRY",
        2: "REFRESH_AUTH",
        3: "RECONNECT",
        4: "FALLBACK",
        5: "AUTH_REQUIRED",
    }
    return {
        "service": "THỦY LỢI AI",
        "version": "9.0",
        "notebooklm_connected": bool(state["connected"]),
        "status": state["status"],
        "recovery_level": level,
        "action": action_map.get(level, "UNKNOWN"),
        "recovery_in_progress": state["recovery_in_progress"],
        "recovery_needed": state["recovery_needed"],
        "auth_expired_suspected": state["auth_expired_suspected"],
        "health_stale": stale,
        "consecutive_failures": state["consecutive_failures"],
        "recovery_count": state["recovery_count"],
        "last_error": state["last_error"],
        "last_error_time": state["last_error_time"],
        "last_recovery": state["last_recovery"],
        "last_recovery_reason": state["last_recovery_reason"],
        "circuit_open": state["circuit_open"],
        "circuit_until": state["circuit_until"],
        "admin_recovery_available": bool(ADMIN_TOKEN),
        "note": (
            "Nếu AUTH đã hết hạn hoàn toàn và NOTEBOOKLM_AUTH_JSON là secret tĩnh, "
            "server không thể tự tạo đăng nhập Google mới; APP sẽ báo AUTH_REQUIRED."
            if level == 5 else
            "Hệ thống đang tự phục hồi; chưa cần mở máy tính."
        ),
    }


@app.get("/incident")
async def incident():
    """Giao diện dữ liệu tối giản cho APP sự cố, không lộ secret."""
    if not APP_INCIDENT_ENABLED:
        raise HTTPException(status_code=404, detail="Incident API disabled")
    info = await recovery_status()
    return {
        "ok": info["notebooklm_connected"],
        "severity": "NORMAL" if info["recovery_level"] == 0 else ("CRITICAL" if info["recovery_level"] == 5 else "WARNING"),
        "message": (
            "THỦY LỢI AI hoạt động bình thường."
            if info["recovery_level"] == 0
            else "THỦY LỢI AI đang tự khắc phục sự cố NotebookLM."
        ),
        "recovery": info,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
