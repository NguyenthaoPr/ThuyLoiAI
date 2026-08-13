# =========================================================
# UPLOAD TÀI LIỆU VÀO GEMINI FILE SEARCH
# =========================================================

from fastapi import UploadFile, File, HTTPException
import tempfile
import os
import time


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Chưa chọn file."
        )

    # Chỉ cho phép các định dạng tài liệu cần thiết
    allowed_extensions = (
        ".pdf",
        ".docx",
        ".doc",
        ".txt",
        ".md",
        ".csv",
        ".xlsx"
    )

    filename = file.filename.lower()

    if not filename.endswith(allowed_extensions):
        raise HTTPException(
            status_code=400,
            detail=(
                "Định dạng file chưa được hỗ trợ. "
                "Chỉ nhận PDF, DOCX, DOC, TXT, MD, CSV, XLSX."
            )
        )

    temp_path = None

    try:

        # -------------------------------------------------
        # Lưu file tạm trên Render
        # -------------------------------------------------

        suffix = os.path.splitext(file.filename)[1]

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        ) as temp:

            temp_path = temp.name

            content = await file.read()

            temp.write(content)

        # -------------------------------------------------
        # Kiểm tra Gemini
        # -------------------------------------------------

        if client is None:
            raise HTTPException(
                status_code=500,
                detail="Gemini API chưa được kết nối."
            )

        if not FILE_SEARCH_STORE:
            raise HTTPException(
                status_code=500,
                detail="Chưa cấu hình File Search Store."
            )

        # -------------------------------------------------
        # Upload trực tiếp vào File Search Store
        # -------------------------------------------------

        operation = client.file_search_stores.upload_to_file_search_store(
            file=temp_path,
            file_search_store_name=FILE_SEARCH_STORE,
            config={
                "display_name": file.filename
            }
        )

        # -------------------------------------------------
        # Chờ Gemini hoàn tất indexing
        # -------------------------------------------------

        while not operation.done:
            time.sleep(2)
            operation = client.operations.get(operation)

        # -------------------------------------------------
        # Trả kết quả
        # -------------------------------------------------

        return {
            "success": True,
            "message": "Đã đưa tài liệu vào Kho tri thức THỦY LỢI AI.",
            "filename": file.filename,
            "store": FILE_SEARCH_STORE,
            "operation": str(operation)
        }

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi upload tài liệu: {str(e)}"
        )

    finally:

        # Xóa file tạm trên Render
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
