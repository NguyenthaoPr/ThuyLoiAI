import os
import re
import json
import time
import uuid
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from collections import OrderedDict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

try:
    import importlib.metadata as importlib_metadata
    NOTEBOOKLM_PY_VERSION = importlib_metadata.version("notebooklm-py")
except Exception:
    NOTEBOOKLM_PY_VERSION = "unknown"

try:
    from notebooklm import NotebookLMClient
    NOTEBOOKLM_IMPORT_ERROR = None
except Exception as exc:
    NotebookLMClient = None
    NOTEBOOKLM_IMPORT_ERROR = repr(exc)


# ============================================================
# THUY LOI AI - SERVER V20
# NotebookLM-first / Render / one shared client / rate-limit shield
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("thuyloiai-v20")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

NOTEBOOK_ID = (
    os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
    or os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip()
)
AUTH_JSON = os.getenv("NOTEBOOKLM_AUTH_JSON", "").strip()
STORAGE_PATH = os.getenv(
    "NOTEBOOKLM_STORAGE_PATH",
    "/tmp/thuyloiai/notebooklm/storage_state.json",
).strip()

MAX_CONCURRENT = 1
CHAT_TIMEOUT = max(60, int(os.getenv("CHAT_TIMEOUT", "180")))
REQUEST_TIMEOUT = max(90, int(os.getenv("REQUEST_TIMEOUT", "240")))
MAX_ATTEMPTS = max(1, int(os.getenv("NOTEBOOKLM_MAX_ATTEMPTS", "2")))
RATE_COOLDOWN = max(30, int(os.getenv("RATE_COOLDOWN", "30")))
RATE_COOLDOWN_MAX = max(RATE_COOLDOWN, int(os.getenv("RATE_COOLDOWN_MAX", "120")))
CIRCUIT_THRESHOLD = max(2, int(os.getenv("CIRCUIT_THRESHOLD", "3")))
CIRCUIT_COOLDOWN = max(60, int(os.getenv("CIRCUIT_COOLDOWN", "180")))
CACHE_TTL = max(0, int(os.getenv("CACHE_TTL", "300")))
CACHE_SIZE = max(20, int(os.getenv("CACHE_SIZE", "100")))

SYSTEM_PROMPT = r"""
Báº¡n lÃ  THá»¦Y Lá»¢I AI, trá»£ lÃ½ AI chuyÃªn ngÃ nh Thá»§y lá»£i cá»§a Chi nhÃ¡nh Thá»§y lá»£i Vu Gia - Thu Bá»n.

HÃ£y tráº£ lá»i chá»§ yáº¿u dá»±a trÃªn cÃ¡c nguá»n tÃ i liá»u trong NotebookLM ÄÆ°á»£c cáº¥u hÃ¬nh cho THá»¦Y Lá»¢I AI.
Æ¯u tiÃªn quy Äá»nh, quy trÃ¬nh, há» sÆ¡, sá» liá»u, thÃ´ng sá» ká»¹ thuáº­t vÃ  tÃ i liá»u ná»i bá».
KhÃ´ng tá»± bá»a sá» liá»u, Äiá»u khoáº£n, sá» vÄn báº£n, ngÃ y thÃ¡ng hoáº·c thÃ´ng sá» ká»¹ thuáº­t.
Náº¿u kho tÃ i liá»u khÃ´ng Äá»§ cÄn cá»©, nÃ³i rÃµ: "ChÆ°a tÃ¬m tháº¥y Äá»§ cÄn cá»© trong há» sÆ¡ THá»¦Y Lá»¢I AI."
Náº¿u NotebookLM cung cáº¥p trÃ­ch dáº«n [1], [2]..., giá»¯ nguyÃªn cÃ¡c trÃ­ch dáº«n ÄÃ³ trong cÃ¢u tráº£ lá»i.
Tráº£ lá»i báº±ng tiáº¿ng Viá»t, rÃµ rÃ ng, ngáº¯n gá»n nhÆ°ng Äá»§ Ã½ vÃ  phÃ¹ há»£p nghiá»p vá»¥ thá»§y lá»£i.
"""


class TTLCache:
    def __init__(self, size: int, ttl: int):
        self.size = size
        self.ttl = ttl
        self.data = OrderedDict()
        self.lock = asyncio.Lock()

    async def get(self, key):
        if self.ttl <= 0:
            return None
        async with self.lock:
            item = self.data.get(key)
            if item is None:
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
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    async def clear(self):
        async with self.lock:
            self.data.clear()


class ServiceUnavailable(Exception):
    def __init__(self, message, kind="SERVICE_UNAVAILABLE", retry_after=0):
        super().__init__(message)
        self.kind = kind
        self.retry_after = int(retry_after or 0)


class NotebookRuntime:
    def __init__(self):
        self.client = None
        self.client_cm = None
        self.client_lock = asyncio.Lock()
        self.ask_lock = asyncio.Lock()
        self.inflight = {}
        self.inflight_lock = asyncio.Lock()
        self.cache = TTLCache(CACHE_SIZE, CACHE_TTL)

        self.rate_failures = 0
        self.rate_cooldown_until = 0.0
        self.circuit_failures = 0
        self.circuit_open_until = 0.0

        self.total_requests = 0
        self.total_success = 0
        self.total_failures = 0
        self.auth_recoveries = 0
        self.rate_limit_events = 0
        self.last_success = 0.0
        self.last_error_kind = ""
        self.last_error = ""

    def state(self):
        now = time.monotonic()
        if now < self.circuit_open_until:
            return "CIRCUIT_OPEN"
        if now < self.rate_cooldown_until:
            return "RATE_COOLDOWN"
        if self.client is None:
            return "CLIENT_NOT_CONNECTED"
        return "READY"

    def rate_remaining(self):
        return max(0, int(self.rate_cooldown_until - time.monotonic()))

    def circuit_remaining(self):
        return max(0, int(self.circuit_open_until - time.monotonic()))

    def note_rate_limit(self):
        self.rate_failures += 1
        self.rate_limit_events += 1
        delay = min(RATE_COOLDOWN * (2 ** (self.rate_failures - 1)), RATE_COOLDOWN_MAX)
        self.rate_cooldown_until = max(
            self.rate_cooldown_until,
            time.monotonic() + delay,
        )
        log.warning("RATE LIMIT: cooldown=%ss; GIU NGUYEN client.", delay)

    def note_success(self):
        self.total_success += 1
        self.last_success = time.time()
        self.last_error = ""
        self.last_error_kind = ""
        self.rate_failures = 0
        self.rate_cooldown_until = 0.0
        self.circuit_failures = 0
        self.circuit_open_until = 0.0

    def note_failure(self, kind, message):
        self.total_failures += 1
        self.circuit_failures += 1
        self.last_error_kind = kind
        self.last_error = str(message)[:500]
        if self.circuit_failures >= CIRCUIT_THRESHOLD:
            self.circuit_open_until = time.monotonic() + CIRCUIT_COOLDOWN
            log.error("CIRCUIT OPEN: %ss", CIRCUIT_COOLDOWN)

    @staticmethod
    def classify(exc):
        text = f"{type(exc).__name__}: {exc}".lower()
        if any(x in text for x in (
            "authentication expired", "authentication invalid", "unauthorized",
            "not signed in", "invalid session", "no sid", "auth error", "401",
        )):
            return "AUTH"
        if any(x in text for x in (
            "rate limit", "rate-limited", "rate limited", "too many requests",
            "resource exhausted", "429", "quota",
        )):
            return "RATE_LIMIT"
        if any(x in text for x in ("timeout", "timed out", "read timeout", "connect timeout")):
            return "TIMEOUT"
        if any(x in text for x in (
            "connection reset", "connection error", "connecterror", "server disconnected",
            "temporarily unavailable", "502", "503", "504",
        )):
            return "NETWORK"
        return "UNKNOWN"

    async def close_client(self):
        async with self.client_lock:
            cm = self.client_cm
            self.client = None
            self.client_cm = None
        if cm is not None:
            try:
                result = cm.__aexit__(None, None, None)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log.warning("Dong NotebookLM client: %s", type(exc).__name__)

    async def connect(self, force=False):
        if NotebookLMClient is None:
            raise RuntimeError(
                "KhÃ´ng import ÄÆ°á»£c NotebookLMClient: " + (NOTEBOOKLM_IMPORT_ERROR or "unknown")
            )
        if not NOTEBOOK_ID:
            raise RuntimeError("Thiáº¿u NOTEBOOKLM_NOTEBOOK trÃªn Render.")

        async with self.client_lock:
            if self.client is not None and not force:
                return self.client

            if AUTH_JSON:
                # notebooklm-py v0.7.3 reads NOTEBOOKLM_AUTH_JSON itself.
                # Do not log the JSON or cookies.
                try:
                    parsed = json.loads(AUTH_JSON)
                    if not isinstance(parsed, dict) or not parsed.get("cookies"):
                        raise ValueError("auth JSON khÃ´ng cÃ³ cookies")
                except Exception as exc:
                    raise RuntimeError(f"NOTEBOOKLM_AUTH_JSON khÃ´ng há»£p lá»: {type(exc).__name__}")

                log.info("NotebookLM: dÃ¹ng NOTEBOOKLM_AUTH_JSON.")
                cm = NotebookLMClient.from_storage(
                    chat_timeout=CHAT_TIMEOUT,
                    rate_limit_max_retries=0,
                    server_error_max_retries=0,
                    max_concurrent_rpcs=1,
                )
            else:
                path = Path(STORAGE_PATH).expanduser()
                if not path.exists():
                    raise RuntimeError(f"KhÃ´ng tÃ¬m tháº¥y storage_state.json: {path}")
                log.info("NotebookLM: dÃ¹ng storage file %s", path)
                cm = NotebookLMClient.from_storage(
                    str(path),
                    chat_timeout=CHAT_TIMEOUT,
                    rate_limit_max_retries=0,
                    server_error_max_retries=0,
                    max_concurrent_rpcs=1,
                )

            client = await cm.__aenter__()
            self.client_cm = cm
            self.client = client
            log.info("NotebookLM: CLIENT READY")
            return client

    async def ensure_client(self):
        if self.client is not None:
            return self.client
        return await self.connect()

    async def _chat_once(self, question):
        client = await self.ensure_client()
        result = await asyncio.wait_for(
            client.chat.ask(NOTEBOOK_ID, question),
            timeout=CHAT_TIMEOUT,
        )

        answer = getattr(result, "answer", None)
        if isinstance(result, dict):
            answer = answer or result.get("answer")
        answer = (answer or "").strip()
        if not answer:
            raise RuntimeError("NotebookLM tráº£ vá» answer rá»ng.")

        refs = []
        raw_refs = getattr(result, "references", None)
        if raw_refs is None and isinstance(result, dict):
            raw_refs = result.get("references")
        for ref in raw_refs or []:
            try:
                refs.append({
                    "citation_number": getattr(ref, "citation_number", None),
                    "source_id": getattr(ref, "source_id", None),
                    "cited_text": getattr(ref, "cited_text", None),
                })
            except Exception:
                pass

        return {
            "answer": answer,
            "references": refs,
            "conversation_id": getattr(result, "conversation_id", None),
        }

    async def _execute(self, question, request_id, cache_key):
        auth_recovered = False
        rate_retry_used = False

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.circuit_remaining():
                raise ServiceUnavailable(
                    f"NotebookLM Äang táº¡m khÃ³a. Thá»­ láº¡i sau {self.circuit_remaining()} giÃ¢y.",
                    "CIRCUIT_OPEN", self.circuit_remaining()
                )
            if self.rate_remaining():
                if rate_retry_used:
                    raise ServiceUnavailable(
                        f"NotebookLM Äang giá»i háº¡n tá»c Äá». Thá»­ láº¡i sau {self.rate_remaining()} giÃ¢y.",
                        "RATE_COOLDOWN", self.rate_remaining()
                    )
                rate_retry_used = True
                await asyncio.sleep(self.rate_remaining())
                continue

            try:
                log.info("[%s] NotebookLM attempt %s/%s", request_id, attempt, MAX_ATTEMPTS)
                async with self.ask_lock:
                    result = await self._chat_once(question)
                await self.cache.put(cache_key, result)
                self.note_success()
                log.info("[%s] NotebookLM SUCCESS", request_id)
                return result
            except Exception as exc:
                kind = self.classify(exc)
                log.warning(
                    "[%s] NotebookLM failed | kind=%s | %s",
                    request_id, kind, str(exc)[:500]
                )

                if kind == "AUTH" and not auth_recovered:
                    auth_recovered = True
                    self.auth_recoveries += 1
                    log.warning("[%s] AUTH: ÄÃ³ng client cÅ© vÃ  náº¡p láº¡i auth.", request_id)
                    await self.close_client()
                    try:
                        await self.connect(force=True)
                    except Exception as recover_exc:
                        self.note_failure("AUTH_RECOVERY", recover_exc)
                        raise ServiceUnavailable(
                            "PhiÃªn NotebookLM ÄÃ£ háº¿t háº¡n hoáº·c khÃ´ng há»£p lá». Cáº§n cáº­p nháº­t NOTEBOOKLM_AUTH_JSON trÃªn Render.",
                            "AUTH_RECOVERY",
                        )
                    continue

                if kind == "RATE_LIMIT":
                    self.note_rate_limit()
                    if not rate_retry_used and attempt < MAX_ATTEMPTS:
                        rate_retry_used = True
                        delay = self.rate_remaining()
                        log.warning("[%s] RATE LIMIT: giá»¯ client, chá» %ss rá»i thá»­ 1 láº§n.", request_id, delay)
                        await asyncio.sleep(delay)
                        continue
                    self.note_failure(kind, exc)
                    raise ServiceUnavailable(
                        f"NotebookLM Äang giá»i háº¡n tá»c Äá». Vui lÃ²ng thá»­ láº¡i sau {self.rate_remaining() or RATE_COOLDOWN} giÃ¢y.",
                        "RATE_LIMIT", self.rate_remaining() or RATE_COOLDOWN
                    )

                if kind in ("TIMEOUT", "NETWORK") and attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(min(5 * attempt, 10))
                    continue

                self.note_failure(kind, exc)
                raise ServiceUnavailable(
                    "THá»¦Y Lá»¢I AI chÆ°a láº¥y ÄÆ°á»£c cÃ¢u tráº£ lá»i tá»« NotebookLM. Há» thá»ng ÄÃ£ tá»± phá»¥c há»i nhÆ°ng láº§n nÃ y chÆ°a thÃ nh cÃ´ng.",
                    kind,
                )

        raise ServiceUnavailable("NotebookLM chÆ°a tráº£ lá»i.", "UNKNOWN")

    async def ask(self, question, request_id):
        self.total_requests += 1
        normalized = re.sub(r"\s+", " ", question or "").strip()
        cache_key = "q:" + normalized.casefold()

        cached = await self.cache.get(cache_key)
        if cached:
            return {**cached, "cached": True, "mode": "notebooklm-cache"}

        if self.circuit_remaining():
            raise ServiceUnavailable(
                f"NotebookLM Äang táº¡m khÃ³a. Thá»­ láº¡i sau {self.circuit_remaining()} giÃ¢y.",
                "CIRCUIT_OPEN", self.circuit_remaining()
            )
        if self.rate_remaining():
            raise ServiceUnavailable(
                f"Kho dá»¯ liá»u Äang Äiá»u tiáº¿t truy cáº­p. Thá»­ láº¡i sau {self.rate_remaining()} giÃ¢y.",
                "RATE_COOLDOWN", self.rate_remaining()
            )

        async with self.inflight_lock:
            task = self.inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._execute(normalized, request_id, cache_key))
                self.inflight[cache_key] = task

        try:
            result = await asyncio.wait_for(task, timeout=REQUEST_TIMEOUT)
            return {**result, "cached": False, "mode": "notebooklm"}
        finally:
            async with self.inflight_lock:
                if self.inflight.get(cache_key) is task:
                    self.inflight.pop(cache_key, None)


runtime = NotebookRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 70)
    log.info("THá»¦Y Lá»¢I AI V20 - NOTEBOOKLM FIRST")
    log.info("NotebookLM-py: %s", NOTEBOOKLM_PY_VERSION)
    log.info("Notebook ID: %s", "CONFIGURED" if NOTEBOOK_ID else "MISSING")
    log.info("Auth JSON: %s", "CONFIGURED" if AUTH_JSON else "NOT SET")
    log.info("Storage path: %s", STORAGE_PATH)
    log.info("KhÃ´ng khá»i Äá»ng chat ngay lÃºc deploy; chá» káº¿t ná»i khi cÃ³ /ask hoáº·c /diagnostics/live.")
    log.info("=" * 70)
    yield
    await runtime.close_client()
    log.info("THá»¦Y Lá»¢I AI V20: STOPPED")


app = FastAPI(
    title="THá»¦Y Lá»¢I AI",
    version="20.0.0",
    description="THá»¦Y Lá»¢I AI - NotebookLM-first",
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
    question: str = Field(..., min_length=1, max_length=5000)


@app.get("/")
async def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE, media_type="text/html")
    return {"status": "ok", "service": "THá»¦Y Lá»¢I AI", "engine": "NotebookLM"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "THá»¦Y Lá»¢I AI",
        "version": "20.0.0",
        "engine": "NotebookLM",
        "notebook_configured": bool(NOTEBOOK_ID),
        "auth_json_configured": bool(AUTH_JSON),
        "notebooklm_py_version": NOTEBOOKLM_PY_VERSION,
        "client_loaded": runtime.client is not None,
        "state": runtime.state(),
        "rate_cooldown_seconds": runtime.rate_remaining(),
        "circuit_cooldown_seconds": runtime.circuit_remaining(),
    }


@app.get("/status")
async def status():
    return {
        "service": "THá»¦Y Lá»¢I AI",
        "version": "20.0.0",
        "engine": "NotebookLM",
        "state": runtime.state(),
        "notebook_configured": bool(NOTEBOOK_ID),
        "auth_json_configured": bool(AUTH_JSON),
        "storage_path_configured": bool(STORAGE_PATH),
        "client_loaded": runtime.client is not None,
        "total_requests": runtime.total_requests,
        "total_success": runtime.total_success,
        "total_failures": runtime.total_failures,
        "auth_recoveries": runtime.auth_recoveries,
        "rate_limit_events": runtime.rate_limit_events,
        "inflight_questions": len(runtime.inflight),
        "cache_size": len(runtime.cache.data),
        "last_success": runtime.last_success,
        "last_error_kind": runtime.last_error_kind,
        "last_error": runtime.last_error,
    }


@app.get("/diagnostics")
async def diagnostics():
    return {
        "service": "THá»¦Y Lá»¢I AI",
        "server_version": "20.0.0",
        "engine": "NotebookLM",
        "notebooklm_py_version": NOTEBOOKLM_PY_VERSION,
        "notebook_client_imported": NotebookLMClient is not None,
        "notebook_client_import_error": NOTEBOOKLM_IMPORT_ERROR,
        "notebook_configured": bool(NOTEBOOK_ID),
        "auth_json_configured": bool(AUTH_JSON),
        "storage_path_configured": bool(STORAGE_PATH),
        "runtime_state": runtime.state(),
        "rate_cooldown_seconds": runtime.rate_remaining(),
        "circuit_cooldown_seconds": runtime.circuit_remaining(),
        "note": "Trang nÃ y khÃ´ng in cookie, token hoáº·c ná»i dung NOTEBOOKLM_AUTH_JSON.",
    }


@app.get("/diagnostics/live")
async def diagnostics_live():
    """Kiá»m tra auth + NotebookLM tháº­t báº±ng notebooks.list()."""
    request_id = uuid.uuid4().hex[:10]
    try:
        client = await runtime.ensure_client()
        notebooks = await asyncio.wait_for(client.notebooks.list(), timeout=CHAT_TIMEOUT)
        rows = []
        for nb in notebooks or []:
            rows.append({
                "id": getattr(nb, "id", None),
                "title": getattr(nb, "title", None),
            })
        selected_ok = any(str(x.get("id")) == NOTEBOOK_ID for x in rows)
        return {
            "ok": True,
            "engine": "NotebookLM",
            "request_id": request_id,
            "notebook_count": len(rows),
            "selected_notebook_found": selected_ok,
            "notebooks": rows[:20],
        }
    except Exception as exc:
        kind = runtime.classify(exc)
        log.warning("[%s] DIAGNOSTICS LIVE failed | %s | %s", request_id, kind, str(exc)[:500])
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "engine": "NotebookLM",
                "request_id": request_id,
                "kind": kind,
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
            },
        )


@app.post("/ask")
@app.post("/api/ask")
async def ask(payload: Question, request: Request):
    request_id = uuid.uuid4().hex[:10]
    question = re.sub(r"\s+", " ", payload.question or "").strip()

    if not NOTEBOOK_ID:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error", "ok": False,
                "answer": "THá»¦Y Lá»¢I AI chÆ°a cáº¥u hÃ¬nh NOTEBOOKLM_NOTEBOOK trÃªn Render.",
                "kind": "CONFIG", "request_id": request_id,
            },
        )

    log.info("[%s] CÃU Há»I: %s", request_id, question[:250])

    try:
        result = await runtime.ask(question, request_id)
        return {
            "status": "ok",
            "ok": True,
            "request_id": request_id,
            "answer": result["answer"],
            "message": result["answer"],
            "references": result.get("references", []),
            "conversation_id": result.get("conversation_id"),
            "cached": result.get("cached", False),
            "mode": result.get("mode", "notebooklm"),
            "engine": "NotebookLM",
        }
    except ServiceUnavailable as exc:
        code = 429 if exc.kind in ("RATE_LIMIT", "RATE_COOLDOWN") else 503
        return JSONResponse(
            status_code=code,
            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else {},
            content={
                "status": "error", "ok": False,
                "answer": str(exc),
                "message": str(exc),
                "kind": exc.kind,
                "retry_after": exc.retry_after,
                "request_id": request_id,
            },
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "status": "error", "ok": False,
                "answer": "NotebookLM pháº£n há»i quÃ¡ lÃ¢u. Vui lÃ²ng thá»­ láº¡i sau.",
                "kind": "TIMEOUT", "request_id": request_id,
            },
        )
    except Exception as exc:
        log.exception("[%s] ASK unexpected error", request_id)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error", "ok": False,
                "answer": "THá»¦Y Lá»¢I AI gáº·p lá»i ná»i bá» khi káº¿t ná»i NotebookLM.",
                "kind": "INTERNAL", "request_id": request_id,
                "error_type": type(exc).__name__,
            },
        )


@app.get("/api")
async def api_info():
    return {
        "status": "ok",
        "service": "THá»¦Y Lá»¢I AI",
        "version": "20.0.0",
        "engine": "NotebookLM",
        "endpoints": ["/", "/health", "/status", "/diagnostics", "/diagnostics/live", "/ask", "/api/ask"],
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
