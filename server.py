import os
import time
import tempfile
import logging
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from google import genai
from google.genai import types


# ============================================================
# THUY LOI AI 3.1 - STABLE
# Browser -> FastAPI -> Gemini File Search Store -> Gemini
# ============================================================

APP_VERSION = "3.1.0"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("Thieu bien moi truong GEMINI_API_KEY")

if not FILE_SEARCH_STORE:
    raise RuntimeError("Thieu bien moi truong GEMINI_FILE_SEARCH_STORE")

if not FILE_SEARCH_STORE.startswith("fileSearchStores/"):
    FILE_SEARCH_STORE = f"fileSearchStores/{FILE_SEARCH_STORE}"

client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("thuyloiai")

app = FastAPI(
    title="THUY LOI AI",
    description="Tro ly AI chuyen nganh Thuy loi",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


# ============================================================
# DOCUMENT CACHE
# Khong goi documents.list moi lan hoi.
# ============================================================

_doc_cache_count = None
_doc_cache_time = 0.0
_doc_lock = Lock()
DOC_CACHE_SECONDS = 30


def _document_list():
    return list(
        client.file_search_stores.documents.list(
            parent=FILE_SEARCH_STORE
        )
    )


def _document_count(force=False):
    global _doc_cache_count, _doc_cache_time

    now = time.time()

    with _doc_lock:
        if (
            not force
            and _doc_cache_count is not None
            and now - _doc_cache_time < DOC_CACHE_SECONDS
        ):
            return _doc_cache_count

    docs = _document_list()
    count = len(docs)

    with _doc_lock:
        _doc_cache_count = count
        _doc_cache_time = time.time()

    return count


def _invalidate_document_cache():
    global _doc_cache_count, _doc_cache_time

    with _doc_lock:
        _doc_cache_count = None
        _doc_cache_time = 0.0


def _serialize_document(doc):
    result = {
        "name": getattr(doc, "name", None),
        "display_name": getattr(doc, "display_name", None),
    }

    for attr in ("state", "create_time", "update_time", "mime_type"):
        value = getattr(doc, attr, None)
        if value is not None:
            result[attr] = str(value)

    return result


def _extract_sources(response):
    """Lay ten tai lieu tu grounding neu Gemini tra ve."""
    sources = []

    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return sources

        metadata = getattr(candidates[0], "grounding_metadata", None)
        if not metadata:
            return sources

        chunks = getattr(metadata, "grounding_chunks", None) or []

        for chunk in chunks:
            retrieved = getattr(chunk, "retrieved_context", None)
            if not retrieved:
                continue

            title = getattr(retrieved, "title", None)
            if title and title not in sources:
                sources.append(title)

    except Exception as exc:
        logger.warning("Khong doc duoc grounding source: %s", exc)

    return sources


# ============================================================
# HOME
# ============================================================

@app.get("/", include_in_schema=False)
def home():
    index_file = Path(__file__).with_name("index.html")

    if index_file.exists():
        return FileResponse(index_file)

    return {
        "service": "THUY LOI AI",
        "version": APP_VERSION,
        "status": "online",
        "engine": "Gemini File Search",
        "store": FILE_SEARCH_STORE,
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    try:
        count = _document_count()

        return {
            "status": "ok",
            "service": "THUY LOI AI",
            "version": APP_VERSION,
            "engine": "Gemini File Search",
            "gemini_configured": bool(GEMINI_API_KEY),
            "file_search_configured": bool(FILE_SEARCH_STORE),
            "file_search_ready": True,
            "file_search_store": FILE_SEARCH_STORE,
            "documents": count,
            "model": MODEL,
        }

    except Exception as exc:
        logger.exception("Health check loi")

        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "service": "THUY LOI AI",
                "version": APP_VERSION,
                "engine": "Gemini File Search",
                "gemini_configured": bool(GEMINI_API_KEY),
                "file_search_configured": bool(FILE_SEARCH_STORE),
                "file_search_ready": False,
                "file_search_store": FILE_SEARCH_STORE,
                "error": str(exc),
            },
        )


# ============================================================
# API INFO
# ============================================================

@app.get("/api")
def api_info():
    return {
        "service": "THUY LOI AI",
        "version": APP_VERSION,
        "model": MODEL,
        "store": FILE_SEARCH_STORE,
        "endpoints": {
            "ask": "POST /ask",
            "upload": "POST /upload",
            "documents": "GET /documents",
            "stores": "GET /stores",
            "health": "GET /health",
        },
    }


# ============================================================
# STORES
# ============================================================

@app.get("/stores")
def stores():
    try:
        result = []

        for store in client.file_search_stores.list():
            result.append(
                {
                    "name": getattr(store, "name", None),
                    "display_name": getattr(store, "display_name", None),
                    "active": getattr(store, "active", None),
                }
            )

        return {
            "success": True,
            "configured_store": FILE_SEARCH_STORE,
            "count": len(result),
            "stores": result,
        }

    except Exception as exc:
        logger.exception("/stores loi")
        return JSONResponse(
            status_code=200,
            content={"success": False, "error": str(exc)},
        )


# ============================================================
# DOCUMENTS
# ============================================================

@app.get("/documents")
def documents():
    try:
        docs = _document_list()

        global _doc_cache_count, _doc_cache_time
        with _doc_lock:
            _doc_cache_count = len(docs)
            _doc_cache_time = time.time()

        return {
            "success": True,
            "store": FILE_SEARCH_STORE,
            "count": len(docs),
            "documents": [_serialize_document(doc) for doc in docs],
        }

    except Exception as exc:
        logger.exception("/documents loi")
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "store": FILE_SEARCH_STORE,
                "error": str(exc),
            },
        )


# ============================================================
# UPLOAD
# PDF ON DINH
# ============================================================

@app.post("/upload")
def upload(file: UploadFile = File(...)):
    filename = Path(file.filename or "document.pdf").name

    if not filename.lower().endswith(".pdf"):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Hien tai chuc nang upload chi nhan file PDF.",
            },
        )

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".pdf",
        ) as tmp:
            temp_path = tmp.name

            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)

        logger.info("Upload/index: %s", filename)

        operation = client.file_search_stores.upload_to_file_search_store(
            file=temp_path,
            file_search_store_name=FILE_SEARCH_STORE,
            config={"display_name": filename},
        )

        while not operation.done:
            time.sleep(3)
            operation = client.operations.get(operation)

        _invalidate_document_cache()

        return {
            "success": True,
            "message": "Da dua tai lieu vao kho THUY LOI AI.",
            "filename": filename,
            "store": FILE_SEARCH_STORE,
            "operation": getattr(operation, "name", None),
        }

    except Exception as exc:
        logger.exception("Upload loi: %s", filename)
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "filename": filename,
                "error": str(exc),
            },
        )

    finally:
        try:
            file.file.close()
        except Exception:
            pass

        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ============================================================
# ASK
# ============================================================

@app.post("/ask")
def ask(request: AskRequest):
    question = (request.question or "").strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Vui long nhap cau hoi.",
            },
        )

    # Kiem tra kho voi cache. Neu viec kiem tra bi loi tam thoi,
    # van cho phep Gemini thu truy van File Search.
    try:
        if _document_count() == 0:
            return {
                "success": False,
                "status": "no_documents",
                "answer": (
                    "Kho THUY LOI AI hien chua co tai lieu. "
                    "Hay tai tai lieu len bang chuc nang /upload truoc."
                ),
                "store": FILE_SEARCH_STORE,
            }
    except Exception as exc:
        logger.warning(
            "Khong kiem tra duoc document count; van thu Gemini: %s",
            exc,
        )

    prompt = f"""
Ban la THUY LOI AI - tro ly chuyen nganh thuy loi.

NGUON DU LIEU BAT BUOC:
Su dung File Search Store duoc cung cap cho cau hoi nay lam nguon
ho so chinh.

QUY TAC:
1. Uu tien tuyet doi thong tin trong ho so THUY LOI AI.
2. Khong tu bia so lieu, ten cong trinh, thong so ky thuat,
   quy trinh, quy dinh hoac ket luan.
3. Neu ho so khong co thong tin can thiet, noi ro:
   "Toi chua tim thay thong tin nay trong kho tai lieu THUY LOI AI."
4. Neu cau hoi ve mot cong trinh cu the, uu tien tai lieu cua dung
   cong trinh do.
5. Tra loi bang tieng Viet, ro rang va de doc tren dien thoai.
6. Giu nguyen so lieu va don vi trong ho so.
7. Neu xac dinh duoc tai lieu, ghi o cuoi:
   "Nguon ho so: [ten tai lieu]".

CAU HOI NGUOI DUNG:
{question}
"""

    def call_gemini():
        # QUAN TRONG: dung MODEL.
        # Ban code cu dung GEMINI_MODEL nhung bien nay khong duoc khai bao,
        # lam /ask that bai sau khi retry.
        return client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[FILE_SEARCH_STORE]
                        )
                    )
                ]
            ),
        )

    last_error = None

    # Retry 3 lan: 2s -> 4s.
    for attempt in range(1, 4):
        try:
            logger.info(
                "ASK %s/3 | %s",
                attempt,
                question[:120],
            )

            response = call_gemini()
            answer = getattr(response, "text", None)

            if answer:
                answer = answer.strip()

            if answer:
                sources = _extract_sources(response)

                logger.info(
                    "ASK thanh cong | sources=%s",
                    sources,
                )

                return {
                    "success": True,
                    "status": "ok",
                    "answer": answer,
                    "store": FILE_SEARCH_STORE,
                    "sources": sources,
                }

            last_error = "Gemini khong tra ve noi dung."

        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "ASK loi %s/3: %s",
                attempt,
                exc,
            )

        if attempt < 3:
            time.sleep(2 * attempt)

    logger.error("ASK that bai sau 3 lan: %s", last_error)

    return {
        "success": False,
        "status": "temporary_error",
        "answer": (
            "THUY LOI AI tam thoi chua lay duoc cau tra loi "
            "tu kho du lieu Gemini. He thong da tu thu lai 3 lan. "
            "Vui long thu lai sau it giay."
        ),
        "store": FILE_SEARCH_STORE,
    }


# ============================================================
# LOCAL / RENDER
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
