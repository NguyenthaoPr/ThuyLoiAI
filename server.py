import os
import re
import json
import time
import uuid
import asyncio
import logging
from pathlib import Path
from collections import OrderedDict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    import importlib.metadata as importlib_metadata
    NOTEBOOKLM_PY_VERSION = importlib_metadata.version("notebooklm-py")
except Exception:
    NOTEBOOKLM_PY_VERSION = "unknown"


# ============================================================
# THUY LOI AI - SERVER V15
# NotebookLM-first / Queue / Rate-Limit Shield / Recovery
#
# Muc tieu:
# 1) NotebookLM la nguon tra loi chinh.
# 2) Khong reconnect khi gap RATE_LIMIT.
# 3) Moi thoi diem chi cho 1 request NotebookLM chay.
# 4) RATE_LIMIT: cooldown tang dan 30 -> 60 -> 120 giay.
# 5) AUTH error: huy client cu, nap lai storage_state 1 lan.
# 6) Circuit breaker: khi NotebookLM qua loi, khoa tam thoi
#    thay vi lam hang loat request tiep tuc dap vao NotebookLM.
# 7) Cache cau hoi thanh cong de giam tai.
# 8) Dedupe: 2 nguoi cung hoi cung luc chi tao 1 request.
# 9) /health va /status de quan sat he thong.
#
# Luu y bao mat:
# storage_state.json / NOTEBOOKLM_AUTH_JSON la bearer credential.
# Khong commit vao GitHub, khong log noi dung cookie.
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("thuyloiai-v15")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

# ----------------------------
# Environment
# ----------------------------
NOTEBOOK_ID = (
    os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
    or os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip()
)

AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "").strip()

STORAGE_PATH = os.getenv(
    "NOTEBOOKLM_STORAGE_PATH",
    "/tmp/thuyloai/notebooklm/storage_state.json",
).strip()

# Optional master token file. V15 does not expose its contents.
MASTER_TOKEN_PATH = os.getenv(
    "NOTEBOOKLM_MASTER_TOKEN_PATH",
    str(Path(STORAGE_PATH).expanduser().with_name("master_token.json")),
).strip()

MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "1")))
REQUEST_TIMEOUT = max(30, int(os.getenv("REQUEST_TIMEOUT", "150")))
CHAT_TIMEOUT = max(30, int(os.getenv("CHAT_TIMEOUT", "180")))

# Retry for NotebookLM transient errors.
MAX_NBLM_ATTEMPTS = max(1, int(os.getenv("NOTEBOOKLM_MAX_ATTEMPTS", "3")))

# Rate-limit cooldown. Never reconnect just because of RATE_LIMIT.
RATE_COOLDOWN = max(10, int(os.getenv("RATE_COOLDOWN", "30")))
RATE_COOLDOWN_MAX = max(RATE_COOLDOWN, int(os.getenv("RATE_COOLDOWN_MAX", "120")))

# Circuit breaker.
CIRCUIT_THRESHOLD = max(1, int(os.getenv("CIRCUIT_THRESHOLD", "3")))
CIRCUIT_COOLDOWN = max(30, int(os.getenv("CIRCUIT_COOLDOWN", "180")))

# Cache.
CACHE_TTL = max(0, int(os.getenv("CACHE_TTL", "300")))
CACHE_SIZE = max(10, int(os.getenv("CACHE_SIZE", "100")))

SYSTEM_PROMPT = r"""
Bạn là THỦY LỢI AI, trợ lý AI chuyên ngành Thủy lợi của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

MỤC TIÊU:
- Trả lời dựa chủ yếu trên kho tài liệu của NotebookLM được cấu hình cho THỦY LỢI AI.
- Ưu tiên quy định, quy trình, hồ sơ, số liệu, thông số kỹ thuật và tài liệu nội bộ có trong kho.
- Không tự bịa thông tin khi tài liệu không có.
- Nếu không tìm thấy căn cứ trong kho, phải nói rõ: "Chưa tìm thấy căn cứ trong hồ sơ THỦY LỢI AI."
- Khi có căn cứ, trình bày rõ nguồn/căn cứ nếu NotebookLM cung cấp tham chiếu.
- Trả lời bằng tiếng Việt, ngắn gọn nhưng đủ ý, ưu tiên tính thực tế cho cán bộ quản lý vận hành thủy lợi.
"""

# ----------------------------
# Optional NotebookLM import
# ----------------------------
try:
    from notebooklm import NotebookLMClient
    NOTEBOOKLM_IMPORT_ERROR = None
except Exception as exc:
    NotebookLMClient = None
    NOTEBOOKLM_IMPORT_ERROR = repr(exc)


# ============================================================
# In-memory state
# ============================================================
class TTLCache:
    def __init__(self, max_size: int, ttl: int):
        self.max_size = max_size
        self.ttl = ttl
        self.data = OrderedDict()
        self.lock = asyncio.Lock()

    async def get(self, key):
        if self.ttl <= 0:
            return None
        async with self.lock:
            item = self.data.get(key)
            if not item:
                return None
            created, value = item
            if time.monotonic() - created > self.ttl:
                self.data.pop(key, None)
                return None
            self.data.move_to_end(key)
            return value

    async def put(self, key, value):
        if self.ttl <= 0:
            return
        async with self.lock:
            self.data[key] = (time.monotonic(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.max_size:
                self.data.popitem(last=False)

    async def clear(self):
        async with self.lock:
            self.data.clear()


class NotebookRuntime:
    def __init__(self):
        self.client = None
        self.client_lock = asyncio.Lock()

        # Exactly one NotebookLM request at a time.
        self.ask_lock = asyncio.Lock()

        # Prevent duplicate simultaneous questions.
        self.inflight = {}
        self.inflight_lock = asyncio.Lock()

        self.cache = TTLCache(CACHE_SIZE, CACHE_TTL)

        self.rate_failures = 0
        self.rate_cooldown_until = 0.0

        self.circuit_failures = 0
        self.circuit_open_until = 0.0

        self.last_success = 0.0
        self.last_error = ""
        self.last_error_kind = ""
        self.total_requests = 0
        self.total_success = 0
        self.total_failures = 0
        self.auth_recoveries = 0
        self.rate_limit_events = 0

        self.started_at = time.time()

    # ----------------------------
    # State helpers
    # ----------------------------
    def circuit_open(self):
        return time.monotonic() < self.circuit_open_until

    def rate_limited(self):
        return time.monotonic() < self.rate_cooldown_until

    def rate_remaining(self):
        return max(0, int(self.rate_cooldown_until - time.monotonic()))

    def circuit_remaining(self):
        return max(0, int(self.circuit_open_until - time.monotonic()))

    def state(self):
        if self.circuit_open():
            return "CIRCUIT_OPEN"
        if self.rate_limited():
            return "RATE_COOLDOWN"
        if self.client is None:
            return "CLIENT_NOT_CONNECTED"
        return "READY"

    def reset_rate_limit(self):
        self.rate_failures = 0
        self.rate_cooldown_until = 0.0

    def note_rate_limit(self):
        self.rate_failures += 1
        self.rate_limit_events += 1
        seconds = min(
            RATE_COOLDOWN * (2 ** max(0, self.rate_failures - 1)),
            RATE_COOLDOWN_MAX,
        )
        self.rate_cooldown_until = max(
            self.rate_cooldown_until,
            time.monotonic() + seconds,
        )
        log.warning(
            "RATE LIMIT: cooldown=%ss; khong reconnect client.",
            seconds,
        )

    def note_success(self):
        self.last_success = time.time()
        self.last_error = ""
        self.last_error_kind = ""
        self.circuit_failures = 0
        self.reset_rate_limit()
        self.total_success += 1

    def note_failure(self, kind, message):
        self.total_failures += 1
        self.circuit_failures += 1
        self.last_error_kind = kind
        self.last_error = message[:500]

        if self.circuit_failures >= CIRCUIT_THRESHOLD:
            self.circuit_open_until = time.monotonic() + CIRCUIT_COOLDOWN
            log.error(
                "CIRCUIT BREAKER: mo %s giay sau %s loi lien tiep.",
                CIRCUIT_COOLDOWN,
                self.circuit_failures,
            )

    # ----------------------------
    # Client lifecycle
    # ----------------------------
    async def close_client(self):
        async with self.client_lock:
            client = self.client
            self.client = None

        if client is None:
            return

        try:
            result = client.__aexit__(None, None, None)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            log.warning("Dong NotebookLM client: %s", type(exc).__name__)

    async def connect(self, force=False):
        if NotebookLMClient is None:
            raise RuntimeError(
                "NotebookLMClient khong import duoc: "
                + (NOTEBOOKLM_IMPORT_ERROR or "unknown")
            )

        if not NOTEBOOK_ID:
            raise RuntimeError("Chua cau hinh NOTEBOOKLM_NOTEBOOK / NOTEBOOKLM_NOTEBOOK_ID")

        async with self.client_lock:
            if self.client is not None and not force:
                return self.client

            # Never log auth JSON or cookies.
            if AUTH_JSON:
                # notebooklm-py reads NOTEBOOKLM_AUTH_JSON automatically.
                # V16: use the documented from_storage() call; auth is read from the environment.
                # as a keyword argument in the installed 0.7.x API.
                log.info("NotebookLM V15: dung NOTEBOOKLM_AUTH_JSON.")
                cm = NotebookLMClient.from_storage(
                    chat_timeout=CHAT_TIMEOUT,
                )
            else:
                path = Path(STORAGE_PATH).expanduser()
                if not path.exists():
                    raise RuntimeError(
                        f"Khong tim thay storage_state.json tai {path}"
                    )
                log.info("NotebookLM V15: dung storage runtime hien tai: %s", path)

                # Explicit storage path is passed positionally for local/file-based auth.
                cm = NotebookLMClient.from_storage(
                    str(path),
                    chat_timeout=CHAT_TIMEOUT,
                )

            # Enter async context and retain the live client.
            client = await cm.__aenter__()
            self.client = client
            return client

    async def ensure_client(self):
        if self.client is not None:
            return self.client
        return await self.connect()

    # ----------------------------
    # Error classification
    # ----------------------------
    @staticmethod
    def classify_error(exc):
        text = f"{type(exc).__name__}: {exc}".lower()

        auth_words = (
            "authentication expired",
            "authentication invalid",
            "unauthorized",
            "401",
            "login",
            "not signed in",
            "no sid",
            "invalid session",
            "auth error",
        )
        rate_words = (
            "rate limit",
            "rate-limited",
            "rate limited",
            "too many requests",
            "429",
            "quota",
            "resource exhausted",
        )
        timeout_words = (
            "timeout",
            "timed out",
            "read timeout",
            "connect timeout",
        )
        network_words = (
            "connection reset",
            "connection error",
            "connecterror",
            "server disconnected",
            "temporarily unavailable",
            "502",
            "503",
            "504",
        )

        if any(x in text for x in auth_words):
            return "AUTH"
        if any(x in text for x in rate_words):
            return "RATE_LIMIT"
        if any(x in text for x in timeout_words):
            return "TIMEOUT"
        if any(x in text for x in network_words):
            return "NETWORK"
        return "UNKNOWN"

    # ----------------------------
    # Chat call
    # ----------------------------
    async def _chat_once(self, question):
        client = await self.ensure_client()

        # notebooklm-py public Python API:
        # result = await client.chat.ask(notebook_id, question)
        result = await asyncio.wait_for(
            client.chat.ask(NOTEBOOK_ID, question),
            timeout=CHAT_TIMEOUT,
        )

        answer = getattr(result, "answer", None)
        if answer is None and isinstance(result, dict):
            answer = result.get("answer")

        if not answer:
            raise RuntimeError("NotebookLM khong tra ve noi dung answer.")

        references = []
        raw_refs = getattr(result, "references", None)
        if raw_refs is None and isinstance(result, dict):
            raw_refs = result.get("references")

        if raw_refs:
            for ref in raw_refs:
                try:
                    references.append(
                        {
                            "title": getattr(ref, "title", None)
                            or (ref.get("title") if isinstance(ref, dict) else None),
                            "source_id": getattr(ref, "source_id", None)
                            or (ref.get("source_id") if isinstance(ref, dict) else None),
                            "score": getattr(ref, "score", None)
                            or (ref.get("score") if isinstance(ref, dict) else None),
                        }
                    )
                except Exception:
                    continue

        return {
            "answer": str(answer).strip(),
            "references": references,
        }

    async def ask(self, question, request_id):
        self.total_requests += 1
        normalized = normalize_question(question)
        cache_key = cache_key_for(normalized)

        # Cache first.
        cached = await self.cache.get(cache_key)
        if cached:
            log.info("[%s] CACHE HIT", request_id)
            return {
                **cached,
                "cached": True,
                "request_id": request_id,
                "mode": "notebooklm-cache",
            }

        # Circuit breaker.
        if self.circuit_open():
            raise ServiceUnavailable(
                "NotebookLM đang tạm khóa bảo vệ hệ thống. "
                f"Vui lòng thử lại sau khoảng {self.circuit_remaining()} giây.",
                kind="CIRCUIT_OPEN",
                retry_after=self.circuit_remaining(),
            )

        # Rate cooldown.
        if self.rate_limited():
            remaining = self.rate_remaining()
            raise ServiceUnavailable(
                "NotebookLM đang bị giới hạn tốc độ. "
                f"Hệ thống đang tự chờ thêm {remaining} giây.",
                kind="RATE_COOLDOWN",
                retry_after=remaining,
            )

        # One NotebookLM request at a time.
        async with self.ask_lock:
            # Re-check after waiting for previous request.
            if self.circuit_open():
                raise ServiceUnavailable(
                    "NotebookLM đang tạm khóa bảo vệ hệ thống.",
                    kind="CIRCUIT_OPEN",
                    retry_after=self.circuit_remaining(),
                )

            if self.rate_limited():
                remaining = self.rate_remaining()
                raise ServiceUnavailable(
                    "NotebookLM đang trong thời gian chờ rate-limit.",
                    kind="RATE_COOLDOWN",
                    retry_after=remaining,
                )

            # Dedupe exact concurrent question.
            async with self.inflight_lock:
                task = self.inflight.get(cache_key)
                if task is None:
                    task = asyncio.create_task(
                        self._execute_with_recovery(
                            normalized, request_id, cache_key
                        )
                    )
                    self.inflight[cache_key] = task

            try:
                result = await task
                return {
                    **result,
                    "cached": False,
                    "request_id": request_id,
                    "mode": "notebooklm",
                }
            finally:
                async with self.inflight_lock:
                    if self.inflight.get(cache_key) is task:
                        self.inflight.pop(cache_key, None)

    async def _execute_with_recovery(self, question, request_id, cache_key):
        auth_recovered = False

        for attempt in range(1, MAX_NBLM_ATTEMPTS + 1):
            try:
                log.info(
                    "[%s] NotebookLM attempt %s/%s",
                    request_id,
                    attempt,
                    MAX_NBLM_ATTEMPTS,
                )

                result = await self._chat_once(question)

                await self.cache.put(cache_key, result)
                self.note_success()

                log.info("[%s] NotebookLM SUCCESS", request_id)
                return result

            except Exception as exc:
                kind = self.classify_error(exc)
                message = str(exc)

                log.warning(
                    "[%s] NotebookLM attempt %s/%s failed | kind=%s | %s",
                    request_id,
                    attempt,
                    MAX_NBLM_ATTEMPTS,
                    kind,
                    message[:400],
                )

                # ---------- AUTH ----------
                # Authentication error is the only condition that causes
                # a client rebuild. Rate-limit NEVER rebuilds the client.
                if kind == "AUTH" and not auth_recovered:
                    auth_recovered = True
                    self.auth_recoveries += 1
                    log.warning(
                        "[%s] AUTH: huy client cu va nap lai storage state.",
                        request_id,
                    )
                    await self.close_client()

                    try:
                        await self.connect(force=True)
                    except Exception as reconnect_exc:
                        self.note_failure(
                            "AUTH_RECOVERY",
                            str(reconnect_exc),
                        )
                        raise ServiceUnavailable(
                            "Phiên đăng nhập NotebookLM đã hết hạn hoặc không hợp lệ. "
                            "Cần đăng nhập lại NotebookLM trên máy quản trị.",
                            kind="AUTH_RECOVERY",
                        )

                    continue

                # ---------- RATE LIMIT ----------
                if kind == "RATE_LIMIT":
                    self.note_rate_limit()

                    # Do NOT reconnect.
                    # Waiting inside the serialized ask lock prevents another
                    # request from hammering NotebookLM during the cooldown.
                    wait_seconds = self.rate_remaining()

                    if attempt < MAX_NBLM_ATTEMPTS:
                        log.warning(
                            "[%s] RATE LIMIT: khong reconnect; cho %ss roi thu lai.",
                            request_id,
                            wait_seconds,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                    self.note_failure(kind, message)
                    raise ServiceUnavailable(
                        "NotebookLM đang giới hạn tốc độ. "
                        f"Hệ thống đã tự thử {MAX_NBLM_ATTEMPTS} lần. "
                        f"Vui lòng thử lại sau {self.rate_remaining() or RATE_COOLDOWN} giây.",
                        kind="RATE_LIMIT",
                        retry_after=self.rate_remaining() or RATE_COOLDOWN,
                    )

                # ---------- TIMEOUT / NETWORK ----------
                if kind in ("TIMEOUT", "NETWORK"):
                    if attempt < MAX_NBLM_ATTEMPTS:
                        # Short bounded backoff. Do not reconnect blindly.
                        wait_seconds = min(5 * attempt, 15)
                        log.warning(
                            "[%s] %s: cho %ss roi thu lai.",
                            request_id,
                            kind,
                            wait_seconds,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                # ---------- UNKNOWN ----------
                if attempt < MAX_NBLM_ATTEMPTS:
                    wait_seconds = min(3 * attempt, 9)
                    await asyncio.sleep(wait_seconds)
                    continue

                self.note_failure(kind, message)

                raise ServiceUnavailable(
                    "THỦY LỢI AI chưa lấy được câu trả lời từ NotebookLM. "
                    "Hệ thống đã tự phục hồi nhưng lần này chưa thành công.",
                    kind=kind,
                )


class ServiceUnavailable(Exception):
    def __init__(self, message, kind="SERVICE_UNAVAILABLE", retry_after=0):
        super().__init__(message)
        self.kind = kind
        self.retry_after = int(retry_after or 0)


runtime = NotebookRuntime()


# ============================================================
# Helpers
# ============================================================
def normalize_question(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def cache_key_for(text):
    return "q:" + text.casefold()


def mask_path(path):
    try:
        p = Path(path).expanduser()
        return str(p.parent / p.name)
    except Exception:
        return "<path>"


# ============================================================
# FastAPI
# ============================================================
app = FastAPI(
    title="THỦY LỢI AI",
    version="16.0.0",
    description="NotebookLM-first AI assistant for Thủy lợi.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)


@app.on_event("startup")
async def startup_event():
    log.info("=" * 70)
    log.info("THỦY LỢI AI - SERVER V16 STARTING")
    log.info("Notebook ID: %s", "configured" if NOTEBOOK_ID else "MISSING")
    log.info("Storage: %s", mask_path(STORAGE_PATH))
    log.info(
        "Queue=%s | timeout=%ss | attempts=%s | rate=%ss..%ss",
        MAX_CONCURRENT,
        REQUEST_TIMEOUT,
        MAX_NBLM_ATTEMPTS,
        RATE_COOLDOWN,
        RATE_COOLDOWN_MAX,
    )
    if NotebookLMClient is None:
        log.error("NotebookLM import error: %s", NOTEBOOKLM_IMPORT_ERROR)
    log.info("=" * 70)


@app.on_event("shutdown")
async def shutdown_event():
    log.info("THỦY LỢI AI: shutting down...")
    await runtime.close_client()


@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return JSONResponse(
        {
            "name": "THỦY LỢI AI",
            "version": "16.0.0",
            "status": runtime.state(),
        }
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THỦY LỢI AI",
        "version": "16.0.0",
        "state": runtime.state(),
        "notebook_configured": bool(NOTEBOOK_ID),
        "notebook_client_loaded": runtime.client is not None,
        "rate_cooldown_seconds": runtime.rate_remaining(),
        "circuit_cooldown_seconds": runtime.circuit_remaining(),
    }


@app.get("/status")
async def status():
    return {
        "service": "THỦY LỢI AI",
        "version": "16.0.0",
        "state": runtime.state(),
        "notebook_configured": bool(NOTEBOOK_ID),
        "storage_configured": bool(AUTH_JSON or STORAGE_PATH),
        "storage_path": mask_path(STORAGE_PATH),
        "client_loaded": runtime.client is not None,
        "total_requests": runtime.total_requests,
        "total_success": runtime.total_success,
        "total_failures": runtime.total_failures,
        "auth_recoveries": runtime.auth_recoveries,
        "rate_limit_events": runtime.rate_limit_events,
        "rate_cooldown_seconds": runtime.rate_remaining(),
        "circuit_cooldown_seconds": runtime.circuit_remaining(),
        "cache_size": len(runtime.cache.data),
        "last_success": runtime.last_success,
        "last_error_kind": runtime.last_error_kind,
        "last_error": runtime.last_error,
    }


@app.get("/diagnostics")
async def diagnostics():
    """
    Safe deployment diagnostic. Never returns cookies, auth JSON, or secrets.
    """
    return {
        "service": "THỦY LỢI AI",
        "server_version": "16.0.0",
        "notebooklm_py_version": NOTEBOOKLM_PY_VERSION,
        "notebook_client_imported": NotebookLMClient is not None,
        "notebook_client_import_error": NOTEBOOKLM_IMPORT_ERROR,
        "notebook_configured": bool(NOTEBOOK_ID),
        "auth_json_configured": bool(AUTH_JSON),
        "storage_path_configured": bool(STORAGE_PATH),
        "runtime_state": runtime.state(),
    }


@app.post("/ask")
async def ask(payload: Question, request: Request):
    request_id = uuid.uuid4().hex[:10]
    question = normalize_question(payload.question)

    if not NOTEBOOK_ID:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "THỦY LỢI AI chưa cấu hình NOTEBOOKLM_NOTEBOOK.",
                "kind": "CONFIG",
                "request_id": request_id,
            },
        )

    if len(question) < 2:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Vui lòng nhập câu hỏi rõ hơn.",
                "kind": "BAD_REQUEST",
                "request_id": request_id,
            },
        )

    log.info("[%s] CÂU HỎI: %s", request_id, question[:250])

    try:
        result = await asyncio.wait_for(
            runtime.ask(question, request_id),
            timeout=REQUEST_TIMEOUT + CHAT_TIMEOUT + 30,
        )

        return {
            "ok": True,
            "request_id": request_id,
            "answer": result["answer"],
            "references": result.get("references", []),
            "cached": result.get("cached", False),
            "mode": result.get("mode", "notebooklm"),
        }

    except ServiceUnavailable as exc:
        log.error(
            "[%s] ASK thất bại | kind=%s | retry_after=%ss | %s",
            request_id,
            exc.kind,
            exc.retry_after,
            str(exc),
        )

        status_code = 429 if exc.kind in (
            "RATE_LIMIT",
            "RATE_COOLDOWN",
        ) else 503

        return JSONResponse(
            status_code=status_code,
            headers=(
                {"Retry-After": str(exc.retry_after)}
                if exc.retry_after
                else {}
            ),
            content={
                "ok": False,
                "request_id": request_id,
                "answer": None,
                "message": str(exc),
                "kind": exc.kind,
                "retry_after": exc.retry_after,
                "system_state": runtime.state(),
            },
        )

    except asyncio.TimeoutError:
        log.error("[%s] ASK timeout.", request_id)
        return JSONResponse(
            status_code=504,
            content={
                "ok": False,
                "request_id": request_id,
                "answer": None,
                "message": "THỦY LỢI AI đang chờ NotebookLM quá lâu. Vui lòng thử lại sau.",
                "kind": "TIMEOUT",
            },
        )

    except Exception as exc:
        log.exception("[%s] ASK unexpected error", request_id)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "request_id": request_id,
                "answer": None,
                "message": "Hệ thống gặp lỗi nội bộ khi xử lý câu hỏi.",
                "kind": "INTERNAL",
            },
        )


@app.get("/api/info")
async def api_info():
    return {
        "service": "THỦY LỢI AI",
        "version": "16.0.0",
        "architecture": [
            "NotebookLM-first",
            "single-flight queue",
            "duplicate request protection",
            "success cache",
            "rate-limit shield",
            "bounded exponential cooldown",
            "authentication recovery",
            "circuit breaker",
            "health/status monitoring",
        ],
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
