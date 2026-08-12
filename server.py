# ... (giữ nguyên import và các định nghĩa khác)

# ============================================================
# Helper: lấy storage path
# ============================================================
def get_storage_path_for_client():
    if AUTH_JSON:
        try:
            json.loads(AUTH_JSON)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"NOTEBOOKLM_AUTH_JSON không hợp lệ: {e}")
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp.write(AUTH_JSON)
        tmp.close()
        return tmp.name
    else:
        path = Path(STORAGE_PATH).expanduser()
        if not path.exists():
            raise RuntimeError(f"Không tìm thấy storage_state.json tại {path}")
        return str(path)

# ============================================================
# NotebookRuntime
# ============================================================
class NotebookRuntime:
    # ... (giữ nguyên các thuộc tính)

    async def close_client(self):
        async with self.client_lock:
            client = self.client
            self.client = None
        if client is None:
            return
        try:
            await client.__aexit__(None, None, None)
        except Exception as exc:
            log.warning("Đóng client thất bại: %s", exc)

    async def connect(self, force=False):
        if NotebookLMClient is None:
            raise RuntimeError(
                "NotebookLMClient không import được: " + (NOTEBOOKLM_IMPORT_ERROR or "unknown")
            )
        if not NOTEBOOK_ID:
            raise RuntimeError("Chưa cấu hình NOTEBOOKLM_NOTEBOOK")
        async with self.client_lock:
            if self.client is not None and not force:
                return self.client
            if force and self.client is not None:
                await self.close_client()
            storage_path = get_storage_path_for_client()
            cm = NotebookLMClient.from_storage(
                storage_path=storage_path,
                chat_timeout=CHAT_TIMEOUT,
            )
            client = await cm.__aenter__()
            self.client = client
            return client

    async def _chat_once(self, question):
        if not NOTEBOOK_ID:
            raise RuntimeError("NOTEBOOK_ID chưa được cấu hình")
        client = await self.ensure_client()
        result = await asyncio.wait_for(
            client.chat.ask(NOTEBOOK_ID, question),
            timeout=CHAT_TIMEOUT,
        )
        # ... (phần xử lý result giữ nguyên)

    # ... (các phương thức khác giữ nguyên)

# ============================================================
# mask_path
# ============================================================
def mask_path(path):
    if not path:
        return "<empty>"
    try:
        p = Path(path).expanduser()
        return str(p.parent / p.name)
    except Exception:
        return "<path>"

# ============================================================
# FastAPI startup
# ============================================================
@app.on_event("startup")
async def startup_event():
    log.info("=" * 70)
    log.info("THỦY LỢI AI - SERVER V15 STARTING")
    log.info("Notebook ID: %s", "configured" if NOTEBOOK_ID else "MISSING")
    if AUTH_JSON:
        log.info("Sử dụng NOTEBOOKLM_AUTH_JSON (không log nội dung)")
    else:
        log.info("Storage: %s", mask_path(STORAGE_PATH))
    log.info("...")
