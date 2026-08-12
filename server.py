import os
import asyncio
import random
import time
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from collections import OrderedDict

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

try:
    from notebooklm import NotebookLMClient
except Exception as exc:
    NotebookLMClient = None
    NOTEBOOKLM_IMPORT_ERROR = repr(exc)
else:
    NOTEBOOKLM_IMPORT_ERROR = None


# ============================================================
# THỦY LỢI AI - SERVER NOTEBOOKLM V5
# Kiến trúc: NotebookLM + nhiều tầng tự phục hồi
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
}

cache = OrderedDict()


class Question(BaseModel):
    question: str


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


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


def mark_success():
    state["status"] = "ok"
    state["connected"] = True
    state["last_success"] = now_text()
    state["last_error"] = None
    state["consecutive_failures"] = 0
    state["circuit_open"] = False
    state["circuit_until"] = 0.0


def mark_failure(error: Exception):
    state["connected"] = False
    state["last_error"] = str(error)[:500]
    state["last_error_time"] = now_text()
    state["consecutive_failures"] += 1

    if state["consecutive_failures"] >= CIRCUIT_THRESHOLD:
        state["circuit_open"] = True
        state["circuit_until"] = time.time() + CIRCUIT_COOLDOWN
        state["status"] = "recovery"
    else:
        state["status"] = "degraded"


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


async def close_client():
    global client

    old = client
    client = None

    if old is None:
        return

    try:
        await old.__aexit__(None, None, None)
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


async def reconnect(reason="unknown", allow_headless=False):
    global client

    async with client_lock:
        log.warning("RECOVERY: reconnect NotebookLM | reason=%s", reason)

        await close_client()

        try:
            nb_client = await create_client()

            # Tầng kiểm tra nhẹ: đọc danh sách notebook, không gửi câu hỏi.
            notebooks = await nb_client.notebooks.list()
            found = any(str(getattr(nb, "id", "")) == NOTEBOOK_ID for nb in notebooks)

            if not found:
                raise RuntimeError(
                    "Đã xác thực NotebookLM nhưng không tìm thấy notebook được cấu hình."
                )

            mark_success()
            state["recovery_count"] += 1
            state["last_recovery"] = now_text()

            log.info("RECOVERY: reconnect thành công.")
            return True

        except Exception as first_error:
            log.error("RECOVERY: reconnect thất bại: %s", first_error)

            # Tầng 2: refresh auth của notebooklm-py.
            try:
                if client is not None:
                    current = client[0]
                    log.warning("RECOVERY: thử refresh_auth().")
                    await current.refresh_auth(
                        allow_headless=allow_headless or HEADLESS_REAUTH
                    )

                    notebooks = await current.notebooks.list()
                    found = any(
                        str(getattr(nb, "id", "")) == NOTEBOOK_ID
                        for nb in notebooks
                    )

                    if not found:
                        raise RuntimeError(
                            "Refresh auth thành công nhưng notebook không tồn tại/quyền truy cập không đúng."
                        )

                    mark_success()
                    state["recovery_count"] += 1
                    state["last_recovery"] = now_text()
                    log.info("RECOVERY: refresh_auth thành công.")
                    return True

            except Exception as second_error:
                log.error("RECOVERY: refresh_auth thất bại: %s", second_error)
                mark_failure(second_error)

            return False


async def ask_notebooklm(question: str):
    global client

    last_error = None

    for attempt in range(MAX_RETRIES):
        if not circuit_available():
            raise RuntimeError("Circuit Breaker đang mở; hệ thống đang tự phục hồi.")

        try:
            async with request_semaphore:
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
                "sources": references,
            }

        except Exception as exc:
            last_error = exc
            log.warning(
                "NotebookLM lỗi lần %s/%s: %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
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
                                "sources": [],
                            }
                    except Exception as retry_exc:
                        last_error = retry_exc
                        mark_failure(retry_exc)

            break

    raise last_error or RuntimeError("NotebookLM không thể xử lý câu hỏi.")


async def watchdog():
    # Không hỏi chat để kiểm tra sức khỏe; chỉ kiểm tra notebook/auth.
    await asyncio.sleep(10)

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

        await asyncio.sleep(WATCHDOG_SECONDS)


async def lifespan(app: FastAPI):
    log.info("==========================================")
    log.info("       KHỞI ĐỘNG THỦY LỢI AI V5")
    log.info("       ENGINE: NOTEBOOKLM")
    log.info("==========================================")

    if not NOTEBOOK_ID:
        log.error("NOTEBOOKLM_NOTEBOOK: CHƯA CÓ")
    else:
        log.info("NOTEBOOK ID: ĐÃ CẤU HÌNH")

    if not AUTH_JSON:
        log.error("NOTEBOOKLM_AUTH_JSON: CHƯA CÓ")
    else:
        log.info("NOTEBOOKLM AUTH: ĐÃ CẤU HÌNH")

    if NOTEBOOKLM_IMPORT_ERROR:
        log.error("NOTEBOOKLM IMPORT ERROR: %s", NOTEBOOKLM_IMPORT_ERROR)
    else:
        log.info("NOTEBOOKLM PY: ĐÃ IMPORT")

    state["status"] = "starting"

    # Khởi động server trước; lỗi NotebookLM không được làm Render chết.
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
    description="Trợ lý AI chuyên ngành Thủy lợi - NotebookLM",
    version="6.0",
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
        "last_check": state["last_check"],
        "last_success": state["last_success"],
        "last_error": state["last_error"],
        "last_error_time": state["last_error_time"],
        "consecutive_failures": state["consecutive_failures"],
        "recovery_count": state["recovery_count"],
        "circuit_open": state["circuit_open"],
        "watchdog_seconds": WATCHDOG_SECONDS,
    }


@app.get("/status")
async def status():
    return await health()


@app.get("/diagnostics")
async def diagnostics():
    return {
        "service": "THỦY LỢI AI",
        "version": "6.0",
        "engine": "NotebookLM",
        "notebooklm_package_loaded": NotebookLMClient is not None,
        "notebooklm_import_error": NOTEBOOKLM_IMPORT_ERROR,
        "notebook_id_configured": bool(NOTEBOOK_ID),
        "notebook_env_source": (
            "NOTEBOOKLM_NOTEBOOK" if os.getenv("NOTEBOOKLM_NOTEBOOK", "").strip()
            else ("NOTEBOOKLM_NOTEBOOK_ID" if os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip() else None)
        ),
        "auth_configured": bool(AUTH_JSON),
        "runtime": dict(state),
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
            "keepalive_seconds": max(60, int(os.getenv("NOTEBOOKLM_KEEPALIVE", "900"))),
        },
    }


@app.get("/api/info")
async def api_info():
    return {
        "name": "THỦY LỢI AI",
        "engine": "NotebookLM",
        "version": "6.0",
        "notebook_configured": bool(NOTEBOOK_ID),
        "endpoints": {
            "home": "/",
            "health": "/health",
            "status": "/status",
            "diagnostics": "/diagnostics",
            "ask": "/ask",
            "recovery": "/recovery",
        },
    }


@app.post("/ask")
async def ask(data: Question):
    question = data.question.strip()

    if not question:
        return {
            "status": "error",
            "answer": "Vui lòng nhập câu hỏi.",
            "engine": "NotebookLM",
        }

    # Tầng 0: cache câu hỏi đã thành công.
    cached = cache_get(question)
    if cached:
        result = dict(cached)
        result["cached"] = True
        return result

    # Nếu đang trong thời gian circuit breaker, thử recovery trước.
    if not circuit_available():
        ok = await reconnect("ask_circuit_breaker")
        if not ok:
            return {
                "status": "error",
                "answer": (
                    "THỦY LỢI AI đang tự khắc phục kết nối NotebookLM. "
                    "Vui lòng thử lại sau ít phút."
                ),
                "engine": "NotebookLM",
                "recovery": True,
            }

    try:
        result = await ask_notebooklm(question)
        cache_put(question, result)
        return result

    except Exception as exc:
        log.error("ASK thất bại: %s", exc)

        # Tầng cuối trước APP sự cố: trả lại kết quả cũ nếu có.
        cached = cache_get(question)
        if cached:
            result = dict(cached)
            result["cached"] = True
            result["warning"] = "NotebookLM đang gặp sự cố; hiển thị kết quả đã lưu."
            return result

        return {
            "status": "error",
            "answer": (
                "THỦY LỢI AI chưa lấy được câu trả lời từ NotebookLM. "
                "Hệ thống đã tự động thử lại và đang tiếp tục khắc phục kết nối."
            ),
            "engine": "NotebookLM",
            "recovery": True,
        }


@app.post("/recovery")
async def recovery(authorization: str | None = Header(default=None)):
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="APP sự cố chưa được cấu hình THUYLOIA_ADMIN_TOKEN.",
        )

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Không được phép.")

    ok = await reconnect("manual_recovery", allow_headless=HEADLESS_REAUTH)

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


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
