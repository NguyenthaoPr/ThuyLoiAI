import os
import re
import asyncio
import random
import tempfile
import time
import hashlib
import base64
import math
import json
import qrcode
from PIL import Image, ImageOps
from io import BytesIO
from collections import OrderedDict
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from functools import partial
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from google import genai
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.units import mm
from reportlab.platypus import (
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
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfgen_canvas

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
# ===== HỆ THỐNG BẢN ĐỒ KÊNH MƯƠNG KML/KMZ =====

KML_DATA_DIR = BASE_DIR / "kml_data"
KML_DATA_DIR.mkdir(parents=True, exist_ok=True)
# ===== GIS MASTER DATA - BỔ SUNG, KHÔNG THAY ĐỔI HỆ THỐNG CŨ =====
GIS_MASTER_DIR = BASE_DIR / "gis_master"
GIS_MASTER_DIR.mkdir(parents=True, exist_ok=True)

GIS_MASTER_KMZ = GIS_MASTER_DIR / "master.kmz"
print(f"[GIS MASTER] BASE_DIR = {BASE_DIR}")
print(f"[GIS MASTER] GIS_MASTER_DIR = {GIS_MASTER_DIR}")
print(f"[GIS MASTER] GIS_MASTER_KMZ = {GIS_MASTER_KMZ}, EXISTS = {GIS_MASTER_KMZ.exists()}")
GIS_MASTER_INDEX = GIS_MASTER_DIR / "gis_index.json"


def parse_kml_coordinates(text):
    """Đọc chuỗi tọa độ KML theo dạng longitude,latitude,altitude."""
    coordinates = []

    if not text:
        return coordinates

    for item in text.strip().split():
        parts = item.split(",")

        if len(parts) >= 2:
            try:
                longitude = float(parts[0])
                latitude = float(parts[1])
                altitude = float(parts[2]) if len(parts) >= 3 else 0.0

                coordinates.append({
                    "lat": latitude,
                    "lng": longitude,
                    "alt": altitude
                })
            except (ValueError, TypeError):
                continue

    return coordinates


def parse_kml_kmz(file_path):
    """
    Đọc KML hoặc KMZ và trích xuất:
    - tên tuyến/đối tượng
    - mô tả
    - tọa độ
    - loại hình học
    """

    file_path = Path(file_path)

    try:
        # ----- KML -----
        if file_path.suffix.lower() == ".kml":
            tree = ET.parse(file_path)
            root = tree.getroot()

        # ----- KMZ -----
        elif file_path.suffix.lower() == ".kmz":
            with zipfile.ZipFile(file_path, "r") as archive:
                kml_names = [
                    name for name in archive.namelist()
                    if name.lower().endswith(".kml")
                ]

                if not kml_names:
                    return []

                kml_data = archive.read(kml_names[0])
                root = ET.fromstring(kml_data)

        else:
            return []

        # KML thường sử dụng namespace
        namespace = {
            "kml": "http://www.opengis.net/kml/2.2"
        }

        results = []

        for placemark in root.findall(".//kml:Placemark", namespace):

            name_element = placemark.find("kml:name", namespace)
            description_element = placemark.find(
                "kml:description",
                namespace
            )

            name = (
                name_element.text.strip()
                if name_element is not None and name_element.text
                else ""
            )

            description = (
                description_element.text.strip()
                if description_element is not None and description_element.text
                else ""
            )

            # ----- Point -----
            point = placemark.find(".//kml:Point/kml:coordinates", namespace)

            # ----- LineString -----
            line = placemark.find(
                ".//kml:LineString/kml:coordinates",
                namespace
            )

            # ----- Polygon -----
            polygon = placemark.find(
                ".//kml:Polygon//kml:coordinates",
                namespace
            )

            geometry_type = None
            coordinates = []

            if point is not None:
                geometry_type = "Point"
                coordinates = parse_kml_coordinates(point.text)

            elif line is not None:
                geometry_type = "LineString"
                coordinates = parse_kml_coordinates(line.text)

            elif polygon is not None:
                geometry_type = "Polygon"
                coordinates = parse_kml_coordinates(polygon.text)

            if coordinates:
                results.append({
                    "name": name,
                    "description": description,
                    "geometry_type": geometry_type,
                    "coordinates": coordinates
                })

        return results

    except Exception as e:
        print(f"[KML] Lỗi đọc {file_path}: {e}")
        return []

# ============================================================
# GIS MASTER DATA - ĐỌC KMZ DÙNG CHUNG
# BỔ SUNG MỚI - KHÔNG THAY ĐỔI HỆ THỐNG CŨ
# ============================================================

def load_gis_master():
    """
    Đọc dữ liệu GIS Master từ file master.kmz.

    File master.kmz được nạp một lần vào hệ thống.
    Người dùng AI chỉ sử dụng dữ liệu đã có,
    không cần và không được nạp lại KMZ.
    """

    try:
        if not GIS_MASTER_KMZ.exists():
            return {
                "success": False,
                "message": "Chưa có file GIS Master KMZ.",
                "data": []
            }

        data = parse_kml_kmz(GIS_MASTER_KMZ)

        return {
            "success": True,
            "message": "Đã đọc dữ liệu GIS Master.",
            "file": GIS_MASTER_KMZ.name,
            "count": len(data),
            "data": data
        }

    except Exception as e:
        print(f"[GIS MASTER] Lỗi đọc KMZ: {e}")

        return {
            "success": False,
            "message": f"Lỗi đọc GIS Master: {str(e)}",
            "data": []
        }


# Bộ nhớ GIS Master trong phiên chạy hiện tại
GIS_MASTER_CACHE = None


def get_gis_master():
    """
    Lấy dữ liệu GIS Master.

    Chỉ đọc file khi cần lần đầu.
    Các lần sau sử dụng dữ liệu đã lưu trong bộ nhớ.
    """

    global GIS_MASTER_CACHE

    if GIS_MASTER_CACHE is None:
        GIS_MASTER_CACHE = load_gis_master()

    return GIS_MASTER_CACHE
# ============================================================
# GIS ENGINE - BƯỚC 1
# KMZ/KML -> GeoJSON
# Chỉ bổ sung, không thay đổi parser hiện tại
# ============================================================

def kml_items_to_geojson(items):
    """
    Chuyển dữ liệu do parse_kml_kmz() tạo ra
    sang GeoJSON FeatureCollection.

    Không sửa dữ liệu gốc.
    Chỉ tạo một lớp dữ liệu chuẩn để BẢN ĐỒ AI,
    tính lý trình và PDF có thể dùng chung.
    """
    features = []

    geometry_map = {
        "Point": "Point",
        "LineString": "LineString",
        "Polygon": "Polygon",
    }

    for index, item in enumerate(items or []):
        geometry_type = item.get("geometry_type")
        coordinates = item.get("coordinates") or []

        if geometry_type not in geometry_map:
            continue

        if not coordinates:
            continue

        if geometry_type == "Point":
            geometry_coordinates = [
                coordinates[0]["lng"],
                coordinates[0]["lat"],
            ]

        elif geometry_type == "LineString":
            geometry_coordinates = [
                [point["lng"], point["lat"]]
                for point in coordinates
            ]

        elif geometry_type == "Polygon":
            ring = [
                [point["lng"], point["lat"]]
                for point in coordinates
            ]

            # GeoJSON Polygon cần vòng khép kín
            if ring and ring[0] != ring[-1]:
                ring.append(ring[0])

            geometry_coordinates = [ring]

        else:
            continue

        features.append({
            "type": "Feature",
            "id": index,
            "properties": {
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "geometry_type": geometry_type,
                "gis_class": item.get("gis_class", "CONG_TRINH"),
            },
            "geometry": {
                "type": geometry_map[geometry_type],
                "coordinates": geometry_coordinates,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }

# ============================================================
# GIS CLASSIFICATION - BƯỚC 1
# TÁCH KHU TƯỚI KHỎI CÔNG TRÌNH
#
# NGUYÊN TẮC:
# - KHU_TUOI chỉ dùng xác định GPS có nằm trong khu tưới hay không.
# - Không dùng đường bao khu tưới để nhận diện công trình.
# - Công trình được xử lý độc lập.
# - Không thay đổi parser KML/KMZ hiện tại.
# ============================================================

def normalize_gis_text(text):
    """
    Chuẩn hóa tên/mô tả GIS để phân loại.
    Không thay đổi dữ liệu gốc.
    """
    if not text:
        return ""

    value = str(text).strip().lower()

    # Chuẩn hóa một số ký tự thường gặp
    value = value.replace("_", " ")
    value = value.replace("-", " ")

    # Gom khoảng trắng
    value = re.sub(r"\s+", " ", value)

    return value


def classify_gis_item(item):
    """
    Phân loại một đối tượng GIS.

    Kết quả:
        KHU_TUOI
        CONG_TRINH

    Lưu ý:
    Đây mới là BƯỚC 1.
    Chưa phân loại chi tiết Kênh / Đập / Trạm bơm...
    """

    if not isinstance(item, dict):
        return "CONG_TRINH"

    name = normalize_gis_text(item.get("name", ""))
    description = normalize_gis_text(item.get("description", ""))

    text = f"{name} {description}".strip()

    # --------------------------------------------------------
    # KHU TƯỚI
    # --------------------------------------------------------
    khu_tuoi_keywords = (
        "khu tưới",
        "khu tuoi",
        "diện tích tưới",
        "dien tich tuoi",
        "phạm vi tưới",
        "pham vi tuoi",
        "vùng tưới",
        "vung tuoi",
    )

    if any(keyword in text for keyword in khu_tuoi_keywords):
        return "KHU_TUOI"

    # --------------------------------------------------------
    # Một số trường hợp đường bao có tên "khu tưới"
    # nhưng không nằm trong description.
    # --------------------------------------------------------
    if "khu" in name and "tưới" in name:
        return "KHU_TUOI"

    return "CONG_TRINH"


def split_gis_items(items):
    """
    Tách GIS Master thành 2 nhóm độc lập:

        cong_trinh_items
        khu_tuoi_items

    Không xóa và không sửa dữ liệu gốc.
    """

    cong_trinh_items = []
    khu_tuoi_items = []

    for item in items or []:
        item_copy = dict(item)

        gis_type = classify_gis_item(item_copy)

        # Gắn loại nội bộ để các bước sau sử dụng.
        # Không thay đổi name/description/geometry.
        item_copy["gis_class"] = gis_type

        if gis_type == "KHU_TUOI":
            item_copy["construction_type"] = "KHU_TUOI"
            khu_tuoi_items.append(item_copy)
        else:
            item_copy["construction_type"] = (
            classify_construction_type(item_copy)
        )

            cong_trinh_items.append(item_copy)

    return cong_trinh_items, khu_tuoi_items
    # ============================================================
# GIS CONSTRUCTION CLASSIFICATION
# BƯỚC 2 - PHÂN LOẠI CÔNG TRÌNH
#
# CHỈ phân loại các đối tượng CONG_TRINH.
# KHU_TUOI không được dùng để nhận diện công trình.
# ============================================================

def classify_construction_type(item):
    """
    Phân loại công trình GIS thành:

        KENH
        DAP_DANG
        TRAM_BOM
        HO_CHUA
        DAP_CHINH
        DAP_PHU
        KHAC

    Chỉ áp dụng cho đối tượng CONG_TRINH.
    """

    if not isinstance(item, dict):
        return "KHAC"

    # KHU_TUOI tuyệt đối không phân loại thành công trình
    if item.get("gis_class") == "KHU_TUOI":
        return "KHU_TUOI"

    name = normalize_gis_text(
        item.get("name", "")
    )

    description = normalize_gis_text(
        item.get("description", "")
    )

    text = f"{name} {description}".strip()

    # ========================================================
    # 1. TRẠM BƠM
    # ========================================================
    if any(keyword in text for keyword in (
        "trạm bơm",
        "tram bom",
    )):
        return "TRAM_BOM"

    # ========================================================
    # 2. HỒ CHỨA
    # ========================================================
    if any(keyword in text for keyword in (
        "hồ chứa",
        "ho chua",
    )):
        return "HO_CHUA"

    # ========================================================
    # 3. ĐẬP CHÍNH
    # ========================================================
    if any(keyword in text for keyword in (
        "đập chính",
        "dap chinh",
    )):
        return "DAP_CHINH"

    # ========================================================
    # 4. ĐẬP PHỤ
    # ========================================================
    if any(keyword in text for keyword in (
        "đập phụ",
        "dap phu",
    )):
        return "DAP_PHU"

    # ========================================================
    # 5. ĐẬP DÂNG
    # ========================================================
    if any(keyword in text for keyword in (
        "đập dâng",
        "dap dang",
    )):
        return "DAP_DANG"

    # ========================================================
    # 6. KÊNH
    # ========================================================
    if any(keyword in text for keyword in (
        "kênh",
        "kenh",
    )):
        return "KENH"

    # ========================================================
    # 7. CHƯA XÁC ĐỊNH
    # ========================================================
    return "KHAC"
def get_active_kml_file():
    """
    Tìm file KML/KMZ đang có trong thư mục kml_data.

    Ưu tiên file KMZ mới nhất.
    Không tạo cơ chế nạp dữ liệu mới.
    """
    if not KML_DATA_DIR.exists():
        return None

    files = [
        path
        for path in KML_DATA_DIR.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".kml", ".kmz"}
    ]

    if not files:
        return None

    kmz_files = [
        path for path in files
        if path.suffix.lower() == ".kmz"
    ]

    if kmz_files:
        return max(kmz_files, key=lambda path: path.stat().st_mtime)

    return max(files, key=lambda path: path.stat().st_mtime)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "2")))
REQUEST_TIMEOUT = max(15, int(os.getenv("REQUEST_TIMEOUT", "45")))
QUEUE_TIMEOUT = max(5, int(os.getenv("QUEUE_TIMEOUT", "20")))
MAX_RETRIES = max(1, int(os.getenv("MAX_RETRIES", "2")))
MAX_QUESTION_LENGTH = max(100, int(os.getenv("MAX_QUESTION_LENGTH", "2000")))
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "25")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_OPERATION_TIMEOUT = max(30, int(os.getenv("UPLOAD_OPERATION_TIMEOUT", "300")))

CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
CACHE_TTL = max(60, int(os.getenv("CACHE_TTL", "3600")))
CACHE_MAX_ENTRIES = max(100, int(os.getenv("CACHE_MAX_ENTRIES", "1000")))

_answer_cache = OrderedDict()
_cache_lock = asyncio.Lock()
_inflight = {}
_inflight_lock = asyncio.Lock()

gemini_client = None
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT)

# ============================================================
# SYSTEM PROMPT (giữ nguyên toàn bộ)
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
    if not GEMINI_API_KEY:
        print("GEMINI API: CHƯA CÓ API KEY")
    else:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            print("GEMINI API: ĐÃ KHỞI TẠO CLIENT")
        except Exception as e:
            gemini_client = None
            print("GEMINI API: LỖI KHỞI TẠO:", repr(e))
    print("FILE SEARCH STORE:", GEMINI_FILE_SEARCH_STORE or "CHƯA CẤU HÌNH")
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
# GIS DATA API - BƯỚC 1
# ============================================================

@app.get("/gis/data")
async def gis_data():
    """
    Trả dữ liệu hệ thống kênh dưới dạng GeoJSON.

    Đây là API mới, không ảnh hưởng các API cũ.
    """
    try:
        active_file = GIS_MASTER_KMZ

        if active_file is None:
            return {
                "success": False,
                "message": "Chưa có dữ liệu KML/KMZ trong hệ thống.",
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [],
                },
            }

        items = parse_kml_kmz(active_file)

        cong_trinh_items, khu_tuoi_items = split_gis_items(items)
        
        geojson = kml_items_to_geojson(
            cong_trinh_items + khu_tuoi_items
        )

        return {
            "success": True,
            "filename": active_file.name,
            "objects": len(items),
            "cong_trinh": len(cong_trinh_items),
            "khu_tuoi": len(khu_tuoi_items),
            "features": len(geojson["features"]),
            "geojson": geojson,
        }

    except Exception as e:
        print("[GIS DATA ERROR]", repr(e))

        return {
            "success": False,
            "message": "Không thể đọc dữ liệu GIS.",
            "error": str(e),
            "geojson": {
                "type": "FeatureCollection",
                "features": [],
            },
        }
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
        raise RuntimeError("Gemini File Search Store chưa được cấu hình.")

def store_name():
    return GEMINI_FILE_SEARCH_STORE.strip()

def normalize_question(text: str) -> str:
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
# HOME & HEALTH & API INFO
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
        documents = await asyncio.wait_for(asyncio.to_thread(list_documents_sync), timeout=30)
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
# ============================================================
@app.get("/cache")
async def get_cache():
    return {"success": True, **(await cache_info())}

@app.delete("/cache")
async def clear_cache():
    await clear_answer_cache()
    return {"success": True, "message": "Đã xóa toàn bộ cache câu hỏi."}

# ============================================================
# GEMINI RETRY
# ============================================================
def is_retryable_error(error: Exception) -> bool:
    text = str(error).lower()
    permanent = ["400", "401", "403", "bad request", "unauthenticated", "permission denied", "api key", "invalid argument", "not found"]
    if any(x in text for x in permanent):
        return False
    retryable = ["408", "409", "429", "500", "502", "503", "504", "rate limit", "resource exhausted", "unavailable", "timeout", "deadline", "temporarily", "internal", "connection", "reset", "server error"]
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

def extract_answer_and_sources(result):
    answer = (getattr(result, "output_text", None) or "").strip()
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
                    file_name = getattr(annotation, "file_name", None) or "Tài liệu THỦY LỢI AI"
                    page_number = getattr(annotation, "page_number", None)
                    source = getattr(annotation, "source", None)
                    key = (str(file_name), str(page_number), str(source))
                    if key in seen_sources:
                        continue
                    seen_sources.add(key)
                    sources.append({
                        "file_name": file_name,
                        "page_number": page_number,
                        "source": source,
                    })
    except Exception as source_error:
        print("FILE SEARCH CITATION ERROR:", repr(source_error))
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
    answer = answer.strip()
    if not answer:
        raise RuntimeError("Gemini không trả về nội dung.")
    return answer, sources

async def _gemini_once(question: str):
    try:
        await asyncio.wait_for(request_semaphore.acquire(), timeout=QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        raise TimeoutError("Hệ thống đang có nhiều yêu cầu. Hàng đợi đã quá thời gian chờ.")
    try:
        result = await asyncio.wait_for(asyncio.to_thread(call_gemini, question), timeout=REQUEST_TIMEOUT)
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
            print(f"GEMINI SUCCESS attempt={attempt + 1}/{MAX_RETRIES} time={elapsed:.1f}s")
            return answer, sources
        except Exception as e:
            last_error = e
            elapsed = time.monotonic() - started
            print(f"GEMINI ERROR attempt={attempt + 1}/{MAX_RETRIES} time={elapsed:.1f}s error={repr(e)}")
            retryable = is_retryable_error(e)
            print("RETRYABLE:", retryable)
            if not retryable or attempt >= MAX_RETRIES - 1:
                break
            delay = min(6, 2 ** attempt) + random.uniform(0.2, 0.8)
            print(f"THỬ LẠI LẦN {attempt + 2}/{MAX_RETRIES} SAU {delay:.1f} GIÂY...")
            await asyncio.sleep(delay)
    raise last_error or RuntimeError("Gemini không thể xử lý câu hỏi.")

async def ask_with_singleflight(question: str):
    key = normalize_question(question)
    cached = await get_cached_answer(question)
    if cached:
        print("CACHE HIT -", f"age={cached['age_seconds']}s")
        return cached["answer"], cached["sources"], True
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
            answer, sources = await asyncio.wait_for(asyncio.shield(future), timeout=REQUEST_TIMEOUT + QUEUE_TIMEOUT + 15)
            return answer, sources, False
        except Exception:
            async with _inflight_lock:
                if _inflight.get(key) is future:
                    _inflight.pop(key, None)
            return await ask_with_singleflight(question)
    try:
        print("CACHE MISS - ĐANG GỬI CÂU HỎI GEMINI...")
        answer, sources = await ask_gemini_with_retry(question)
        await set_cached_answer(question, answer, sources)
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
        return {"status": "error", "answer": "Vui lòng nhập câu hỏi."}
    if len(question) > MAX_QUESTION_LENGTH:
        return {"status": "error", "answer": f"Câu hỏi quá dài. Vui lòng nhập tối đa {MAX_QUESTION_LENGTH} ký tự."}
    cached = await get_cached_answer(question)
    if cached:
        print("CACHE HIT - TRẢ CÂU TRẢ LỜI TỪ CACHE")
        response = {"status": "ok", "answer": cached["answer"], "engine": "Local Cache", "model": GEMINI_MODEL, "cache": True}
        if cached["sources"]:
            response["sources"] = cached["sources"]
        return response
    if not GEMINI_API_KEY:
        return {"status": "error", "answer": "THỦY LỢI AI chưa được cấu hình Gemini API."}
    if gemini_client is None:
        return {"status": "error", "answer": "THỦY LỢI AI chưa kết nối được Gemini API. Vui lòng thử lại sau."}
    if not GEMINI_FILE_SEARCH_STORE:
        return {"status": "error", "answer": "THỦY LỢI AI chưa có kho dữ liệu Gemini File Search."}
    try:
        answer, sources, was_cache = await ask_with_singleflight(question)
        response = {"status": "ok", "answer": answer, "engine": "Gemini File Search", "model": GEMINI_MODEL, "cache": False}
        if sources:
            response["sources"] = sources
        return response
    except Exception as e:
        print("GEMINI KHÔNG TRẢ LỜI:", repr(e))
        return {"status": "error", "answer": "THỦY LỢI AI tạm thời chưa lấy được câu trả lời từ kho dữ liệu Gemini. Hệ thống đã tự kiểm tra và thử lại. Vui lòng thử lại sau ít giây.", "engine": "Gemini File Search", "model": GEMINI_MODEL, "cache": False}

# ============================================================
# STORE / DOCUMENT HELPERS
# ============================================================
def serialize_store(store):
    return {
        "name": str(getattr(store, "name", "") or ""),
        "display_name": str(getattr(store, "display_name", None) or getattr(store, "displayName", None) or ""),
    }

def serialize_document(doc):
    name = str(getattr(doc, "name", "") or "")
    display_name = str(getattr(doc, "display_name", None) or getattr(doc, "displayName", None) or "")
    state = str(getattr(doc, "state", "") or "")
    mime_type = str(getattr(doc, "mime_type", None) or getattr(doc, "mimeType", None) or "")
    return {"name": name, "display_name": display_name, "mime_type": mime_type, "state": state}

def list_documents_sync():
    require_gemini()
    documents = []
    pager = gemini_client.file_search_stores.documents.list(parent=store_name(), config={"page_size": 20})
    for doc in pager:
        documents.append(serialize_document(doc))
    return documents

@app.get("/stores")
async def list_stores():
    if gemini_client is None:
        return {"success": False, "error": "Gemini API chưa được kết nối."}
    try:
        stores = []
        def load():
            for s in gemini_client.file_search_stores.list():
                stores.append(serialize_store(s))
        await asyncio.to_thread(load)
        return {"success": True, "count": len(stores), "stores": stores}
    except Exception as e:
        print("STORE LIST ERROR:", repr(e))
        return {"success": False, "error": str(e)}

@app.get("/documents")
async def list_documents():
    if gemini_client is None:
        return {"success": False, "store": store_name(), "count": 0, "documents": [], "error": "Gemini API chưa được kết nối."}
    if not GEMINI_FILE_SEARCH_STORE:
        return {"success": False, "store": "", "count": 0, "documents": [], "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE."}
    try:
        documents = await asyncio.to_thread(list_documents_sync)
        return {"success": True, "store": store_name(), "count": len(documents), "documents": documents}
    except Exception as e:
        print("DOCUMENT LIST ERROR:", repr(e))
        return {"success": False, "store": store_name(), "count": 0, "documents": [], "error": str(e)}

def is_pdf_document(doc):
    name = (doc.get("display_name") or "").strip().lower()
    mime = (doc.get("mime_type") or "").strip().lower()
    resource_name = (doc.get("name") or "").strip().lower()
    return name.endswith(".pdf") or mime == "application/pdf" or ".pdf" in resource_name

@app.get("/documents/pdf")
async def list_pdf_documents():
    if gemini_client is None:
        return {"success": False, "count": 0, "documents": [], "error": "Gemini API chưa được kết nối."}
    if not GEMINI_FILE_SEARCH_STORE:
        return {"success": False, "count": 0, "documents": [], "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE."}
    try:
        documents = await asyncio.to_thread(list_documents_sync)
        pdfs = [doc for doc in documents if is_pdf_document(doc)]
        return {"success": True, "store": store_name(), "count": len(pdfs), "documents": pdfs, "message": "Chỉ liệt kê PDF. Chưa xóa tài liệu nào."}
    except Exception as e:
        print("PDF LIST ERROR:", repr(e))
        return {"success": False, "count": 0, "documents": [], "error": str(e)}

def delete_pdf_documents_sync():
    require_gemini()
    documents = list_documents_sync()
    pdfs = [doc for doc in documents if is_pdf_document(doc)]
    deleted = []
    failed = []
    for doc in pdfs:
        try:
            gemini_client.file_search_stores.documents.delete(name=doc["name"], config={"force": True})
            deleted.append(doc)
        except Exception as e:
            print("PDF DELETE ERROR:", doc["name"], repr(e))
            failed.append({"document": doc, "error": str(e)})
    return deleted, failed

@app.delete("/documents/pdf")
async def delete_pdf_documents():
    if gemini_client is None:
        return {"success": False, "deleted_count": 0, "failed_count": 0, "error": "Gemini API chưa được kết nối."}
    if not GEMINI_FILE_SEARCH_STORE:
        return {"success": False, "deleted_count": 0, "failed_count": 0, "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE."}
    try:
        deleted, failed = await asyncio.to_thread(delete_pdf_documents_sync)
        await clear_answer_cache()
        return {"success": len(failed) == 0, "store": store_name(), "deleted_count": len(deleted), "failed_count": len(failed), "deleted": deleted, "failed": failed, "message": f"Đã xóa {len(deleted)} PDF. Còn lỗi: {len(failed)}. Cache câu trả lời đã được làm mới."}
    except Exception as e:
        print("PDF DELETE ALL ERROR:", repr(e))
        return {"success": False, "deleted_count": 0, "failed_count": 0, "error": str(e)}

# ============================================================
# UPLOAD
# ============================================================
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if gemini_client is None:
        return {"success": False, "error": "Gemini API chưa được kết nối."}
    if not GEMINI_FILE_SEARCH_STORE:
        return {"success": False, "error": "Chưa cấu hình GEMINI_FILE_SEARCH_STORE."}
    if not file.filename:
        return {"success": False, "error": "Chưa chọn file."}
    suffix = Path(file.filename).suffix
    temp_path = None
    try:
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return {"success": False, "filename": file.filename, "error": f"File quá lớn. Kích thước tối đa {MAX_UPLOAD_MB} MB."}
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp.write(content)
            temp_path = temp.name
        def do_upload():
            operation = gemini_client.file_search_stores.upload_to_file_search_store(
                file=temp_path,
                file_search_store_name=store_name(),
                config={"display_name": file.filename},
            )
            started = time.monotonic()
            while not operation.done:
                if time.monotonic() - started > UPLOAD_OPERATION_TIMEOUT:
                    raise TimeoutError("Gemini upload quá thời gian chờ.")
                time.sleep(0.5)
                operation = gemini_client.operations.get(operation)
            return operation
        operation = await asyncio.to_thread(do_upload)
        await clear_answer_cache()
        return {"success": True, "filename": file.filename, "store": store_name(), "message": "Đã đưa file vào Gemini File Search Store. Cache câu trả lời đã được làm mới.", "operation": str(operation)}
    except Exception as e:
        print("UPLOAD ERROR:", repr(e))
        return {"success": False, "filename": file.filename, "error": str(e)}
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
# KML / KMZ UPLOAD
# ============================================================

@app.post("/kml-upload")
async def kml_upload(file: UploadFile = File(...)):
    """
    Nhận file KML/KMZ của hệ thống kênh mương,
    lưu vào thư mục kml_data và đọc dữ liệu tọa độ.
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Chưa chọn file KML/KMZ."
        )

    filename = Path(file.filename).name
    suffix = Path(filename).suffix.lower()

    if suffix not in {".kml", ".kmz"}:
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ file KML hoặc KMZ."
        )

    try:
        content = await file.read()

        if not content:
            raise HTTPException(
                status_code=400,
                detail="File KML/KMZ rỗng."
            )

        save_path = KML_DATA_DIR / filename

        with open(save_path, "wb") as f:
            f.write(content)

        kml_items = parse_kml_kmz(save_path)

        total_coordinates = sum(
            len(item.get("coordinates", []))
            for item in kml_items
        )

        return {
            "success": True,
            "filename": filename,
            "file_path": str(save_path),
            "objects": len(kml_items),
            "coordinates": total_coordinates,
            "message": "Đã nạp hệ thống KML/KMZ thành công."
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"[KML UPLOAD] Lỗi: {e}")

        raise HTTPException(
            status_code=500,
            detail=f"Không thể nạp KML/KMZ: {str(e)}"
        )

# ============================================================
# KML / KMZ DIAGNOSTIC - KIỂM TRA CẤU TRÚC ĐỘC LẬP
# Không thay đổi parser KML/KMZ hiện tại
# ============================================================

def inspect_kml_structure(file_path, sample_limit=20):
    """
    Kiểm tra độc lập cấu trúc KML/KMZ.

    Mục đích:
    - Xác định Folder
    - Xác định đường dẫn Folder cha/con
    - Đếm Placemark
    - Đếm Point / LineString / Polygon
    - Kiểm tra tên đối tượng
    - Kiểm tra tọa độ

    Không thay đổi dữ liệu của parser hiện tại.
    """

    file_path = Path(file_path)

    if file_path.suffix.lower() == ".kml":
        tree = ET.parse(file_path)
        root = tree.getroot()

    elif file_path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(file_path, "r") as archive:
            kml_names = [
                name for name in archive.namelist()
                if name.lower().endswith(".kml")
            ]

            if not kml_names:
                raise ValueError("KMZ không chứa file KML.")

            kml_data = archive.read(kml_names[0])
            root = ET.fromstring(kml_data)

    else:
        raise ValueError("Chỉ hỗ trợ KML hoặc KMZ.")

    namespace = {
        "kml": "http://www.opengis.net/kml/2.2"
    }

    stats = {
        "folders": 0,
        "placemarks": 0,
        "points": 0,
        "linestrings": 0,
        "polygons": 0,
        "coordinates": 0,
        "named_placemarks": 0,
        "unnamed_placemarks": 0
    }

    folder_samples = []
    object_samples = []

    def read_coordinates(element):
        if element is None:
            return []

        return parse_kml_coordinates(element.text)

    def process_element(element, folder_path):
        tag = element.tag.split("}")[-1]

        if tag == "Folder":

            name_element = element.find("kml:name", namespace)

            folder_name = (
                name_element.text.strip()
                if name_element is not None and name_element.text
                else ""
            )

            stats["folders"] += 1

            new_path = list(folder_path)

            if folder_name:
                new_path.append(folder_name)

                if len(folder_samples) < sample_limit:
                    folder_samples.append({
                        "name": folder_name,
                        "path": new_path
                    })

            for child in list(element):
                process_element(child, new_path)

        elif tag == "Placemark":

            stats["placemarks"] += 1

            name_element = element.find("kml:name", namespace)

            name = (
                name_element.text.strip()
                if name_element is not None and name_element.text
                else ""
            )

            if name:
                stats["named_placemarks"] += 1
            else:
                stats["unnamed_placemarks"] += 1

            geometry_type = None
            coordinates = []

            point = element.find(
                ".//kml:Point/kml:coordinates",
                namespace
            )

            line = element.find(
                ".//kml:LineString/kml:coordinates",
                namespace
            )

            polygon = element.find(
                ".//kml:Polygon//kml:coordinates",
                namespace
            )

            if point is not None:
                geometry_type = "Point"
                coordinates = read_coordinates(point)

                stats["points"] += 1

            elif line is not None:
                geometry_type = "LineString"
                coordinates = read_coordinates(line)

                stats["linestrings"] += 1

            elif polygon is not None:
                geometry_type = "Polygon"
                coordinates = read_coordinates(polygon)

                stats["polygons"] += 1

            stats["coordinates"] += len(coordinates)

            if len(object_samples) < sample_limit:
                object_samples.append({
                    "name": name,
                    "geometry_type": geometry_type,
                    "folder_path": folder_path,
                    "coordinate_count": len(coordinates),
                    "first_coordinate": (
                        coordinates[0]
                        if coordinates
                        else None
                    )
                })

        else:

            for child in list(element):
                process_element(child, folder_path)

    process_element(root, [])

    return {
        "success": True,
        "file": file_path.name,
        "stats": stats,
        "folder_samples": folder_samples,
        "object_samples": object_samples
    }


@app.get("/kml-diagnostic")
async def kml_diagnostic():
    """
    API chẩn đoán độc lập cấu trúc KML/KMZ.

    Không thay đổi dữ liệu hệ thống hiện tại.
    """
    # Ưu tiên GIS Master KMZ
    if GIS_MASTER_KMZ.exists():
        file_path = GIS_MASTER_KMZ

    else:
        # Nếu chưa có GIS Master thì giữ cơ chế KML/KMZ cũ
        files = sorted(
            KML_DATA_DIR.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        kml_files = [
            p for p in files
            if p.suffix.lower() in {".kml", ".kmz"}
        ]

        if not kml_files:
            return {
                "success": False,
                "message": "Chưa có file KML/KMZ trong hệ thống."
            }

        file_path = kml_files[0]

    try:
        result = inspect_kml_structure(file_path)

        result["message"] = (
            "Đã kiểm tra cấu trúc KML/KMZ độc lập."
        )

        return result

    except Exception as e:

        print(
            "[KML DIAGNOSTIC ERROR]",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi kiểm tra KML/KMZ: {str(e)}"
        )

# ============================================================
# KML GIS INDEX - BƯỚC THỬ NGHIỆM
# Chỉ tạo Index trong RAM, chưa lưu hệ thống
# Không thay đổi parser KML/KMZ hiện tại
# ============================================================

@app.get("/kml-index-preview")
async def kml_index_preview():
    """
    Tạo GIS Index thử nghiệm từ file KML/KMZ hiện tại.

    Mục đích:
    - Tạo ID nội bộ cho từng đối tượng
    - Giữ nguyên tên
    - Giữ nguyên Folder path
    - Chuẩn hóa latitude / longitude
    - Không ghi đè dữ liệu KML/KMZ hiện tại
    - Không thay đổi parser hiện tại
    """
    # Ưu tiên GIS MASTER KMZ
    if GIS_MASTER_KMZ.exists():
        file_path = GIS_MASTER_KMZ

    else:
        # Giữ cơ chế KML/KMZ cũ làm dự phòng
        files = sorted(
            KML_DATA_DIR.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        kml_files = [
            p for p in files
            if p.suffix.lower() in {".kml", ".kmz"}
        ]

        if not kml_files:
            return {
                "success": False,
                "message": "Chưa có file KML/KMZ trong hệ thống."
            }

        file_path = kml_files[0]

    try:
        diagnostic = inspect_kml_structure(
            file_path,
            sample_limit=100
        )

        if not diagnostic.get("success"):
            return diagnostic

        gis_index = []

        for index, item in enumerate(
            diagnostic.get("object_samples", []),
            start=1
        ):

            coordinate = item.get("first_coordinate")

            latitude = None
            longitude = None
            altitude = None

            if coordinate:
                latitude = coordinate.get("lat")
                longitude = coordinate.get("lng")
                altitude = coordinate.get("alt")

            gis_index.append({
                "id": f"GIS-{index:06d}",
                "name": item.get("name", ""),
                "geometry_type": item.get("geometry_type"),
                "folder_path": item.get("folder_path", []),
                "coordinate_count": item.get(
                    "coordinate_count",
                    0
                ),
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude
            })

        return {
            "success": True,
            "file": file_path.name,
            "index_count": len(gis_index),
            "samples": gis_index[:20],
            "message": (
                "Đã tạo GIS Index thử nghiệm trong RAM. "
                "Chưa thay đổi dữ liệu hệ thống."
            )
        }

    except Exception as e:

        print(
            "[KML INDEX PREVIEW ERROR]",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi tạo GIS Index thử nghiệm: {str(e)}"
        )

# ============================================================
# KML GIS INDEX - BUILD TOÀN BỘ
# BƯỚC 3C - CHỈ KIỂM TRA, CHƯA GHI ĐÈ DỮ LIỆU CŨ
# ============================================================

@app.get("/kml-index-build")
async def kml_index_build():
    """
    Xây GIS Index đầy đủ từ dữ liệu KML/KMZ hiện tại.

    Nguyên tắc:
    - Không thay đổi parser hiện tại.
    - Không thay đổi dữ liệu KML/KMZ gốc.
    - Không thay đổi hệ thống AI hiện tại.
    - Chỉ đọc và chuẩn hóa dữ liệu GIS trong RAM.
    """

    # Ưu tiên GIS MASTER KMZ
    if GIS_MASTER_KMZ.exists():
        file_path = GIS_MASTER_KMZ

    else:
        # Giữ cơ chế KML/KMZ cũ làm dự phòng
        files = sorted(
            KML_DATA_DIR.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        kml_files = [
            p for p in files
            if p.suffix.lower() in {".kml", ".kmz"}
        ]

        if not kml_files:
            return {
                "success": False,
                "message": "Chưa có file KML/KMZ trong hệ thống."
            }

        file_path = kml_files[0]

    try:
        # =====================================================
        # ĐỌC TOÀN BỘ BẰNG PARSER HIỆN TẠI
        # =====================================================

        kml_items = parse_kml_kmz(file_path)

        if not kml_items:
            return {
                "success": False,
                "message": "Không đọc được đối tượng từ KML/KMZ."
            }

        # =====================================================
        # THỐNG KÊ
        # =====================================================

        total_coordinates = sum(
            len(item.get("coordinates", []))
            for item in kml_items
        )

        point_count = sum(
            1
            for item in kml_items
            if item.get("geometry_type") == "Point"
        )

        linestring_count = sum(
            1
            for item in kml_items
            if item.get("geometry_type") == "LineString"
        )

        polygon_count = sum(
            1
            for item in kml_items
            if item.get("geometry_type") == "Polygon"
        )

        named_count = sum(
            1
            for item in kml_items
            if str(item.get("name", "")).strip()
        )

        unnamed_count = len(kml_items) - named_count

        # =====================================================
        # TẠO GIS INDEX TRONG RAM
        # =====================================================

        gis_index = []

        for index, item in enumerate(kml_items, start=1):

            coordinates = item.get("coordinates", [])

            first_coordinate = (
                coordinates[0]
                if coordinates
                else None
            )

            latitude = None
            longitude = None
            altitude = None

            if first_coordinate:

                if isinstance(first_coordinate, dict):

                    latitude = first_coordinate.get("lat")
                    longitude = first_coordinate.get("lng")
                    altitude = first_coordinate.get("alt")

                elif isinstance(first_coordinate, (list, tuple)):

                    if len(first_coordinate) >= 2:
                        longitude = first_coordinate[0]
                        latitude = first_coordinate[1]

                    if len(first_coordinate) >= 3:
                        altitude = first_coordinate[2]

            gis_index.append({
                "id": f"GIS-{index:06d}",
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "geometry_type": item.get("geometry_type"),
                "coordinate_count": len(coordinates),
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude
            })

        # =====================================================
        # KIỂM TRA TOÀN BỘ INDEX
        # =====================================================

        invalid_index = [
            item
            for item in gis_index
            if item.get("latitude") is None
            or item.get("longitude") is None
        ]

        return {
            "success": True,
            "file": file_path.name,

            "source": {
                "objects": len(kml_items),
                "coordinates": total_coordinates,
                "points": point_count,
                "linestrings": linestring_count,
                "polygons": polygon_count,
                "named": named_count,
                "unnamed": unnamed_count
            },

            "gis_index": {
                "count": len(gis_index),
                "valid_coordinates": (
                    len(gis_index) - len(invalid_index)
                ),
                "invalid_coordinates": len(invalid_index)
            },

            "samples": gis_index[:20],

            "message": (
                "Đã xây dựng GIS Index toàn bộ trong RAM. "
                "Chưa thay đổi dữ liệu hệ thống."
            )
        }

    except Exception as e:

        print(
            "[KML GIS INDEX BUILD ERROR]",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi xây dựng GIS Index: {str(e)}"
        )

# ============================================================
# KML GIS - KIỂM TRA LINESTRING ĐỘC LẬP
# BƯỚC THỬ NGHIỆM GPS → TUYẾN
#
# NGUYÊN TẮC:
# - Không thay đổi parser hiện tại
# - Không thay đổi GIS Index hiện tại
# - Không thay đổi dữ liệu KML/KMZ gốc
# - Chỉ đọc LineString để kiểm tra
# ============================================================

@app.get("/kml-lines-preview")
async def kml_lines_preview():
    """
    Lấy mẫu các đối tượng LineString từ KML/KMZ hiện tại.

    Mục đích:
    - Xác nhận tuyến dạng LineString thực tế.
    - Kiểm tra tên tuyến.
    - Kiểm tra số lượng tọa độ.
    - Kiểm tra tọa độ đầu và cuối tuyến.

    Chưa thực hiện tính khoảng cách GPS.
    Chưa thay đổi dữ liệu hệ thống.
    """

    # Ưu tiên GIS MASTER KMZ
    if GIS_MASTER_KMZ.exists():
        file_path = GIS_MASTER_KMZ

    else:
        # Giữ cơ chế KML/KMZ cũ làm dự phòng
        files = sorted(
            KML_DATA_DIR.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        kml_files = [
            p for p in files
            if p.suffix.lower() in {".kml", ".kmz"}
        ]

        if not kml_files:
            return {
                "success": False,
                "message": "Chưa có file KML/KMZ trong hệ thống."
            }

        file_path = kml_files[0]

    try:
        kml_items = parse_kml_kmz(file_path)

        if not kml_items:
            return {
                "success": False,
                "message": "Không đọc được đối tượng từ KML/KMZ."
            }

        lines = [
            item
            for item in kml_items
            if item.get("geometry_type") == "LineString"
        ]

        samples = []

        for index, item in enumerate(lines[:10], start=1):
            coordinates = item.get("coordinates", [])

            first_coordinate = (
                coordinates[0]
                if coordinates
                else None
            )

            last_coordinate = (
                coordinates[-1]
                if coordinates
                else None
            )

            samples.append({
                "id": f"LINE-{index:04d}",
                "name": item.get("name", ""),
                "geometry_type": item.get(
                    "geometry_type"
                ),
                "coordinate_count": len(
                    coordinates
                ),
                "first_coordinate": first_coordinate,
                "last_coordinate": last_coordinate
            })

        return {
            "success": True,
            "file": file_path.name,
            "linestring_count": len(lines),
            "samples": samples,
            "message": (
                "Đã đọc các tuyến LineString "
                "từ KML/KMZ. Chưa thay đổi dữ liệu hệ thống."
            )
        }

    except Exception as e:
        print(
            "[KML LINE PREVIEW ERROR]",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"Lỗi kiểm tra LineString: {str(e)}"
            )
        )
# ============================================================
# THỦY LỢI AI - BỘ MÁY XÁC ĐỊNH LÝ TRÌNH
# BƯỚC 1: CHUẨN HÓA MỐC LÝ TRÌNH
# ============================================================

CHAINAGE_PATTERN = re.compile(
    r'(?i)(?:K|Km)\s*(\d+)\s*\+\s*(\d+(?:\.\d+)?)'
)


def parse_chainage(text):
    """
    Đọc lý trình từ tên hoặc mô tả GIS.

    Ví dụ:
        K3+101
        Km3+101
        K 3+101
        K3+101.5

    Trả về:
        mét tính từ Km0
    """

    if not text:
        return None

    match = CHAINAGE_PATTERN.search(str(text))

    if not match:
        return None

    try:
        km = float(match.group(1))
        met = float(match.group(2))

        return km * 1000.0 + met

    except (TypeError, ValueError):
        return None


def format_chainage(distance_m):
    """
    Chuyển số mét thành dạng:
        K3+198
    """

    if distance_m is None:
        return None

    try:
        distance_m = float(distance_m)

        km = int(distance_m // 1000)
        met = distance_m - km * 1000

        if abs(met - round(met)) < 0.01:
            met_text = str(int(round(met)))
        else:
            met_text = f"{met:.1f}"

        return f"K{km}+{met_text}"

    except (TypeError, ValueError):
        return None

# ============================================================
# KML GIS - GPS -> TUYẾN KÊNH GẦN NHẤT
# BƯỚC THỬ NGHIỆM ĐỘC LẬP
#
# Không thay đổi parser hiện tại
# Không thay đổi GIS Index hiện tại
# Không thay đổi /ask
# Không thay đổi image-analyze
# ============================================================

@app.get("/kml-gps-test")
async def kml_gps_test(
    latitude: float,
    longitude: float
):
    """
    Thử nghiệm xác định tuyến LineString gần nhất
    từ một tọa độ GPS.

    Chưa kết nối với ảnh.
    Chưa kết nối báo cáo.
    Chỉ dùng để kiểm tra thuật toán GIS.
    """
    # Ưu tiên GIS MASTER KMZ
    if GIS_MASTER_KMZ.exists():
        file_path = GIS_MASTER_KMZ
    else:
        files = sorted(
            KML_DATA_DIR.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        kml_files = [
            p for p in files
            if p.suffix.lower() in {".kml", ".kmz"}
        ]
        if not kml_files:
            return {
                "success": False,
                "message": "Chưa có file KML/KMZ trong hệ thống."
            }
        file_path = kml_files[0]

    print("KML GPS TEST FILE:", file_path)

    try:
        kml_items = parse_kml_kmz(file_path)

        cong_trinh_items, khu_tuoi_items = split_gis_items(kml_items)

        lines = [
            item
            for item in cong_trinh_items
            if item.get("construction_type") == "KENH"
            and item.get("geometry_type") == "LineString"
            and item.get("coordinates")
        ]

        if not lines:
            return {
                "success": False,
                "message": "Không tìm thấy tuyến LineString."
            }

        def get_lat_lon(point):
            if not isinstance(point, dict):
                return None, None
            lat = point.get("lat")
            lon = point.get("lon")
            if lon is None:
                lon = point.get("lng")
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                return None, None

        earth_radius = 6371000.0

        def distance_to_segment(
            gps_lat,
            gps_lon,
            lat1,
            lon1,
            lat2,
            lon2
        ):
            ref_lat = math.radians(gps_lat)
            scale_x = earth_radius * math.cos(ref_lat) * math.pi / 180.0
            scale_y = earth_radius * math.pi / 180.0

            x1 = (lon1 - gps_lon) * scale_x
            y1 = (lat1 - gps_lat) * scale_y
            x2 = (lon2 - gps_lon) * scale_x
            y2 = (lat2 - gps_lat) * scale_y

            dx = x2 - x1
            dy = y2 - y1
            segment_length_sq = dx*dx + dy*dy

            if segment_length_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, -(x1*dx + y1*dy) / segment_length_sq))

            nearest_x = x1 + t * dx
            nearest_y = y1 + t * dy
            distance = math.sqrt(nearest_x*nearest_x + nearest_y*nearest_y)

            nearest_lat = gps_lat + nearest_y / scale_y
            nearest_lon = gps_lon + nearest_x / scale_x
            segment_length = math.sqrt(segment_length_sq)

            return distance, nearest_lat, nearest_lon, t, segment_length

        results = []

        for item in lines:
            coordinates = item.get("coordinates", [])
            cumulative_distance = 0.0
            best_distance = None
            best_lat = None
            best_lon = None
            best_ratio = None
            best_segment_length = None
            best_distance_along_line = None

            for i in range(len(coordinates) - 1):
                lat1, lon1 = get_lat_lon(coordinates[i])
                lat2, lon2 = get_lat_lon(coordinates[i+1])
                if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
                    continue

                dist, nlat, nlon, ratio, seg_len = distance_to_segment(
                    latitude, longitude, lat1, lon1, lat2, lon2
                )

                if best_distance is None or dist < best_distance:
                    best_distance = dist
                    best_lat = nlat
                    best_lon = nlon
                    best_ratio = ratio
                    best_segment_length = seg_len
                    best_distance_along_line = cumulative_distance + ratio * seg_len

                cumulative_distance += seg_len

            if best_distance is not None:
                results.append({
                    "name": item.get("name", ""),
                    "geometry_type": "LineString",
                    "coordinate_count": len(coordinates),
                    "distance_m": round(best_distance, 2),
                    "distance_along_line_m": round(best_distance_along_line, 2),
                    "nearest_point": {
                        "latitude": round(best_lat, 8),
                        "longitude": round(best_lon, 8)
                    }
                })

        results.sort(key=lambda x: x["distance_m"])

        for item in results:
            distance = item.get("distance_m", 999999)
            if distance <= 20:
                item["status"] = "RẤT GẦN"
                item["status_code"] = "GREEN"
                item["assessment"] = "Có thể xác nhận vị trí trên tuyến."
            elif distance <= 50:
                item["status"] = "GẦN"
                item["status_code"] = "YELLOW"
                item["assessment"] = "Gần tuyến, cần kiểm tra thực tế."
            elif distance <= 100:
                item["status"] = "XA"
                item["status_code"] = "ORANGE"
                item["assessment"] = "Khoảng cách lớn, cần kiểm tra lại GPS."
            else:
                item["status"] = "NGOÀI PHẠM VI"
                item["status_code"] = "RED"
                item["assessment"] = "Không đủ cơ sở xác nhận vị trí trên tuyến."

        gis_identification = None

        if results:
            sorted_results = sorted(results, key=lambda x: x.get("distance_m", 999999))
            nearest = sorted_results[0]
            nearest_distance = nearest.get("distance_m", 999999)

            if nearest_distance <= 50:
                gis_identification = {
                    "identified": True,
                    "name": nearest.get("name", ""),
                    "geometry_type": nearest.get("geometry_type", "LineString"),
                    "construction_type": "KENH",
                    "distance_m": nearest_distance,
                    "nearest_point": nearest.get("nearest_point"),
                    "status": nearest.get("status", "CHƯA XÁC ĐỊNH"),
                    "status_code": nearest.get("status_code", "RED"),
                    "distance_along_line_m": nearest.get("distance_along_line_m"),
                    "assessment": nearest.get("assessment", ""),
                    "source": "GIS MASTER KMZ"
                }
            else:
                gis_identification = {
                    "identified": False,
                    "name": "",
                    "geometry_type": None,
                    "construction_type": None,
                    "distance_m": nearest_distance,
                    "nearest_point": nearest.get("nearest_point"),
                    "status": "NGOÀI PHẠM VI",
                    "status_code": "RED",
                    "distance_along_line_m": None,
                    "assessment": (
                        f"GPS cách tuyến kênh gần nhất {nearest_distance:.1f} m, "
                        "không đủ cơ sở xác định công trình."
                    ),
                    "source": "GIS MASTER KMZ"
                }

        # === TRẢ VỀ KẾT QUẢ CHO MỌI TRƯỜNG HỢP ===
        return {
            "success": True,
            "file": file_path.name,
            "gps": {
                "latitude": latitude,
                "longitude": longitude
            },
            "gis_identification": gis_identification,
            "linestring_count": len(lines),
            "nearest": results[:10] if results else [],
            "message": "Đã kiểm tra GPS với hệ thống tuyến LineString."
        }

    except Exception as e:
        print("[KML GPS TEST ERROR]", repr(e))
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi kiểm tra GPS: {str(e)}"
        )

# ============================================================
# GIS MASTER UPLOAD
# BỔ SUNG MỚI - CHỈ DÀNH CHO QUẢN TRỊ DỮ LIỆU
# KHÔNG THAY ĐỔI API CŨ
# ============================================================

@app.post("/admin/gis-master-upload")
async def gis_master_upload(
    file: UploadFile = File(...)
):
    """
    Nạp file KMZ Master vào hệ thống.

    Chức năng này chỉ phục vụ quản trị dữ liệu GIS.
    Người dùng AI Thủy lợi thông thường không cần nạp KMZ.
    """

    global GIS_MASTER_CACHE

    try:
        # ----------------------------------------------------
        # 1. Kiểm tra định dạng
        # ----------------------------------------------------
        filename = (file.filename or "").strip()

        if not filename.lower().endswith(".kmz"):
            raise HTTPException(
                status_code=400,
                detail="Chỉ chấp nhận file KMZ."
            )

        # ----------------------------------------------------
        # 2. Đọc dữ liệu upload
        # ----------------------------------------------------
        content = await file.read()

        if not content:
            raise HTTPException(
                status_code=400,
                detail="File KMZ rỗng."
            )

        # ----------------------------------------------------
        # 3. Ghi vào file tạm
        #    Không ghi đè master.kmz ngay
        # ----------------------------------------------------
        temp_path = GIS_MASTER_DIR / "_master_upload_tmp.kmz"

        temp_path.write_bytes(content)

        # ----------------------------------------------------
        # 4. Kiểm tra KMZ có đọc được hay không
        # ----------------------------------------------------
        test_data = parse_kml_kmz(temp_path)

        if not test_data:
            try:
                temp_path.unlink()
            except Exception:
                pass

            raise HTTPException(
                status_code=400,
                detail="Không đọc được dữ liệu từ KMZ. "
                       "File có thể không hợp lệ hoặc không chứa dữ liệu KML."
            )

        # ----------------------------------------------------
        # 5. KMZ hợp lệ -> thay thế Master
        # ----------------------------------------------------
        temp_path.replace(GIS_MASTER_KMZ)

        # ----------------------------------------------------
        # 6. Xóa cache cũ để hệ thống đọc Master mới
        # ----------------------------------------------------
        GIS_MASTER_CACHE = None

        # Đọc lại Master ngay sau khi nạp
        master_data = get_gis_master()

        return {
            "success": True,
            "message": "Đã nạp GIS Master KMZ thành công.",
            "file": GIS_MASTER_KMZ.name,
            "source_filename": filename,
            "count": master_data.get("count", 0)
                if isinstance(master_data, dict)
                else 0
        }

    except HTTPException:
        raise

    except Exception as e:
        print(f"[GIS MASTER UPLOAD ERROR] {repr(e)}")

        raise HTTPException(
            status_code=500,
            detail=f"Lỗi nạp GIS Master: {str(e)}"
        )

# ============================================================
# IMAGE UPLOAD
# ============================================================
@app.post("/image-upload")
async def image_upload(file: UploadFile = File(...)):
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    max_image_bytes = 10 * 1024 * 1024
    filename = Path(file.filename or "image").name
    content_type = (file.content_type or "").lower().strip()
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP.")
    content = await file.read()
    if len(content) > max_image_bytes:
        raise HTTPException(status_code=413, detail="Ảnh vượt quá giới hạn 10 MB.")
    try:
        image = Image.open(BytesIO(content))
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=75, optimize=True)
        content = output.getvalue()
    except Exception:
        raise HTTPException(status_code=400, detail="Không thể xử lý ảnh.")
    image_hash = hashlib.sha256(content).hexdigest()
    print("IMAGE RECEIVED | %s | %.2f KB | %s | SHA256=%s" % (filename, len(content) / 1024, content_type, image_hash))
    return {"success": True, "status": "received", "filename": filename, "mime_type": content_type, "size_bytes": len(content), "image_hash": image_hash}

# ============================================================
# IMAGE ANALYZE
# ============================================================
@app.post("/image-analyze")
async def image_analyze(file: UploadFile = File(...), question: str = "Hãy phân tích hình ảnh này."):
    if not GEMINI_API_KEY:
        return {"success": False, "error": "THỦY LỢI AI chưa được cấu hình Gemini API."}
    if gemini_client is None:
        return {"success": False, "error": "Gemini API chưa được kết nối."}
    if not file.filename:
        return {"success": False, "error": "Chưa chọn ảnh."}
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    content_type = (file.content_type or "").lower().strip()
    if content_type not in allowed_types:
        return {"success": False, "error": "Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP."}
    try:
        content = await file.read()
        if not content:
            return {"success": False, "error": "Ảnh rỗng."}
        original_size_bytes = len(content)
        if original_size_bytes > 10 * 1024 * 1024:
            return {"success": False, "error": "Ảnh vượt quá giới hạn 10 MB."}
        try:
            image = Image.open(BytesIO(content))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            if image.mode != "RGB":
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=75, optimize=True)
            content = output.getvalue()
        except Exception as image_error:
            print("IMAGE PROCESS ERROR:", repr(image_error))
            return {"success": False, "status": "error", "error": "Không thể xử lý ảnh."}
        processed_size_bytes = len(content)
        image_b64 = base64.b64encode(content).decode("utf-8")
        question = (question or "").strip()
        if not question:
            question = "Hãy phân tích hình ảnh này."
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
        tools = []
        if GEMINI_FILE_SEARCH_STORE:
            tools.append({"type": "file_search", "file_search_store_names": [store_name()]})
        result = await asyncio.to_thread(
            lambda: gemini_client.interactions.create(
                model=GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
                input=gemini_input,
                tools=tools if tools else None,
            )
        )
        answer, sources = extract_answer_and_sources(result)
        if not answer:
            raise RuntimeError("Gemini không trả về nội dung phân tích ảnh.")
        return {
            "success": True,
            "status": "analyzed",
            "filename": file.filename,
            "mime_type": "image/jpeg",
            "original_size_bytes": original_size_bytes,
            "processed_size_bytes": processed_size_bytes,
            "question": question,
            "answer": answer,
            "sources": sources,
            "engine": "Gemini Vision",
            "model": GEMINI_MODEL,
            "file_search": bool(GEMINI_FILE_SEARCH_STORE),
        }
    except Exception as e:
        print("IMAGE ANALYZE ERROR:", repr(e))
        return {"success": False, "status": "error", "error": str(e)}

# ============================================================
# FIELD REPORT
# ============================================================
@app.post("/field-report")
async def field_report(
    file: UploadFile = File(...),
    report_type: str = Form("incident"),
    question: str = Form(""),
    kml_context: str = Form(""),
):
    if not GEMINI_API_KEY:
        return {"success": False, "error": "THỦY LỢI AI chưa được cấu hình Gemini API."}
    if gemini_client is None:
        return {"success": False, "error": "Gemini API chưa được kết nối."}
    if not file.filename:
        return {"success": False, "error": "Chưa chọn ảnh."}
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    content_type = (file.content_type or "").lower().strip()
    if content_type not in allowed_types:
        return {"success": False, "error": "Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP."}
    allowed_report_types = {
        "incident": "SỰ CỐ CÔNG TRÌNH",
        "corridor": "KIỂM TRA HÀNH LANG BẢO VỆ CÔNG TRÌNH THỦY LỢI",
        "dry_area": "KHU VỰC KHÔ / THIẾU NƯỚC",
        "water_level": "MỰC NƯỚC HỒ / KÊNH",
    }
    if report_type not in allowed_report_types:
        report_type = "incident"
    report_title = allowed_report_types[report_type]
    try:
        content = await file.read()
        if not content:
            return {"success": False, "error": "Ảnh rỗng."}
        if len(content) > 10 * 1024 * 1024:
            return {"success": False, "error": "Ảnh vượt quá giới hạn 10 MB."}
    except Exception as e:
        return {"success": False, "error": f"Không thể đọc ảnh: {str(e)}"}
    try:
        image = Image.open(BytesIO(content))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=75, optimize=True)
        content = output.getvalue()
    except Exception as e:
        return {"success": False, "error": f"Không thể xử lý ảnh: {str(e)}"}
    image_b64 = base64.b64encode(content).decode("utf-8")
    question = (question or "").strip()
    if not question:
        question = "Hãy lập dự thảo báo cáo hiện trường dựa trên hình ảnh."

    # ===== DỮ LIỆU GIS/KML =====
    kml_context = (kml_context or "").strip()

    if kml_context:
        try:
            kml_data = json.loads(kml_context)
            kml_context_text = json.dumps(
                kml_data,
                ensure_ascii=False,
                indent=2
            )
        except Exception:
            kml_context_text = kml_context
    else:
        kml_context_text = "Chưa có dữ liệu đối chiếu GIS/KML."

    report_prompt = f"""
Bạn là THỦY LỢI AI, trợ lý chuyên ngành thủy lợi
của Chi nhánh Thủy lợi Vu Gia - Thu Bồn.

NHIỆM VỤ:

Lập DỰ THẢO báo cáo hiện trường dựa trên hình ảnh
người dùng cung cấp.

LOẠI BÁO CÁO:

{report_title}

NGUYÊN TẮC BẮT BUỘC:
DỮ LIỆU ĐỐI CHIẾU GIS/KML:

{kml_context_text}

Khi lập báo cáo, phải sử dụng dữ liệu GIS/KML ở trên nếu có
để đối chiếu vị trí hiện trường.

Nếu dữ liệu GIS/KML xác định được:
- tên tuyến;
- tên công trình;
- tọa độ;
- khoảng cách đến tuyến;
- đối tượng gần nhất;
- trạng thái vị trí;

thì phải đưa thông tin phù hợp vào báo cáo.

Phải phân biệt rõ:
- thông tin quan sát từ hình ảnh;
- thông tin xác định từ GPS/GIS/KML;
- thông tin lấy từ hồ sơ;
- nhận định hoặc đánh giá chuyên môn.

Không được tự suy diễn tên công trình hoặc tuyến kênh
nếu dữ liệu GIS/KML không đủ căn cứ.


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
Lấy từ thông tin người dùng cung cấp hoặc thời gian ghi nhận hiện trường.
Không được tự suy đoán nếu không có căn cứ.

- Địa điểm:
Ưu tiên sử dụng tọa độ GPS/GIS/KML nếu dữ liệu có cung cấp.
Nếu GIS/KML xác định được vị trí thì phải ghi tọa độ và thông tin vị trí tương ứng.
Nếu không xác định được thì ghi rõ: "Chưa xác định."

- Công trình:
Ưu tiên sử dụng tên tuyến, tên công trình hoặc đối tượng gần nhất được xác định từ dữ liệu GIS/KML.
Nếu GIS/KML xác định được tên công trình/tuyến thì phải sử dụng tên đó trong báo cáo.
Không được ghi "Chưa xác định từ hình ảnh" nếu dữ liệu GIS/KML đã cung cấp thông tin xác định.
Nếu GIS/KML cũng không đủ căn cứ thì ghi rõ: "Chưa xác định."

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
    gemini_input = [
        {"type": "text", "text": report_prompt},
        {"type": "image", "data": image_b64, "mime_type": "image/jpeg"}
    ]
    tools = []
    if GEMINI_FILE_SEARCH_STORE:
        tools.append({"type": "file_search", "file_search_store_names": [store_name()]})
    try:
        result = await asyncio.to_thread(
            lambda: gemini_client.interactions.create(
                model=GEMINI_MODEL,
                system_instruction=SYSTEM_PROMPT,
                input=gemini_input,
                tools=tools if tools else None,
            )
        )
        answer, sources = extract_answer_and_sources(result)
    except Exception as e:
        print("FIELD REPORT ERROR:", repr(e))
        return {"success": False, "status": "error", "error": str(e)}
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
        "file_search": bool(GEMINI_FILE_SEARCH_STORE),
        "next_step": "review",
    }

# ============================================================
# PDF CONFIG & HELPERS
# ============================================================
PDF_ORG_NAME = "CHI NHÁNH THỦY LỢI VU GIA - THU BỒN"
PDF_ORG_SHORT = "Chi nhánh Thủy lợi VGTB"
PDF_APP_NAME = "THỦY LỢI AI"
PDF_DOC_LABEL = "BÁO CÁO NHANH HIỆN TRƯỜNG"

PDF_COLOR_PRIMARY = colors.HexColor("#0B4F6C")
PDF_COLOR_ACCENT = colors.HexColor("#1B7A8C")
PDF_COLOR_ACCENT_LIGHT = colors.HexColor("#EAF2F5")
PDF_COLOR_TEXT = colors.HexColor("#20303B")
PDF_COLOR_MUTED = colors.HexColor("#5A6B75")
PDF_COLOR_BORDER = colors.HexColor("#C9D8DE")
PDF_COLOR_LABEL_BG = colors.HexColor("#EAF2F5")
PDF_COLOR_WARN_HEADER_BG = colors.HexColor("#F4C542")
PDF_COLOR_WARN_BODY_BG = colors.HexColor("#FFF6DE")
PDF_COLOR_WARN_BORDER = colors.HexColor("#E7B93C")
PDF_COLOR_WARN_TEXT = colors.HexColor("#5B4300")

def esc_pdf(text):
    text = str(text or "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def register_pdf_fonts():
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
            pdfmetrics.registerFont(TTFont("ThuyLoiUnicode", regular_path))
            font_regular = "ThuyLoiUnicode"
        except Exception as e:
            print("PDF FONT REGULAR ERROR:", repr(e))
    if bold_path:
        try:
            pdfmetrics.registerFont(TTFont("ThuyLoiUnicode-Bold", bold_path))
            font_bold = "ThuyLoiUnicode-Bold"
        except Exception as e:
            print("PDF FONT BOLD ERROR:", repr(e))
    elif font_regular == "ThuyLoiUnicode":
        font_bold = "ThuyLoiUnicode"
    try:
        pdfmetrics.registerFontFamily(font_regular, normal=font_regular, bold=font_bold, italic=font_regular, boldItalic=font_bold)
    except Exception as e:
        print("PDF FONT FAMILY ERROR:", repr(e))
    return font_regular, font_bold

def _draw_pdf_header_footer(canvas_obj, doc_obj, report_title, font_regular, font_bold, generated_at):
    canvas_obj.saveState()
    page_width, page_height = A4
    header_height = 24 * mm
    canvas_obj.setFillColor(PDF_COLOR_PRIMARY)
    canvas_obj.rect(0, page_height - header_height, page_width, header_height, stroke=0, fill=1)
    canvas_obj.setFillColor(colors.white)
    canvas_obj.setFont(font_bold, 12.5)
    canvas_obj.drawString(20 * mm, page_height - 10 * mm, PDF_ORG_NAME)
    canvas_obj.setFont(font_regular, 9.5)
    canvas_obj.drawString(20 * mm, page_height - 16 * mm, f"{PDF_DOC_LABEL} • {report_title}")
    canvas_obj.setFont(font_bold, 11)
    canvas_obj.drawRightString(page_width - 20 * mm, page_height - 13 * mm, PDF_APP_NAME)
    canvas_obj.setStrokeColor(PDF_COLOR_BORDER)
    canvas_obj.setLineWidth(0.6)
    canvas_obj.line(20 * mm, 16 * mm, page_width - 20 * mm, 16 * mm)
    canvas_obj.setFillColor(PDF_COLOR_MUTED)
    canvas_obj.setFont(font_regular, 8)
    canvas_obj.drawCentredString(page_width / 2, 11 * mm, f"{PDF_APP_NAME} • {PDF_ORG_NAME.title()}")
    canvas_obj.restoreState()

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
            self.drawRightString(page_width - 20 * mm, 11 * mm, f"Trang {self._pageNumber}/{total_pages}")
            self.restoreState()
    return NumberedCanvas

_PDF_HEADING_KEYWORDS = (
    "THÔNG TIN", "HIỆN TRẠNG", "KIẾN NGHỊ", "ĐỀ XUẤT", "NHẬN XÉT",
    "KẾT LUẬN", "NGUYÊN NHÂN", "GIẢI PHÁP", "TÌNH HÌNH", "ĐÁNH GIÁ",
    "ĐỐI CHIẾU", "QUAN SÁT", "PHÂN TÍCH", "CẦN BỔ SUNG", "CẦN KIỂM TRA",
)

def _strip_duplicate_leading_title(text):
    lines = text.split("\n")
    cleaned = []
    checked_lines = 0
    already_removed = False
    for line in lines:
        stripped = line.strip()
        bare = re.sub(r"^#{1,6}\s*", "", stripped).strip()
        bare = re.sub(r"^\**\s*", "", bare).strip()
        bare = re.sub(r"\**$", "", bare).strip()
        is_title_line = bool(re.match(r"^BÁO\s+CÁO\s+NHANH\s+HIỆN\s+TRƯỜNG\s*[:\-]?\s*$", bare, re.IGNORECASE))
        if not already_removed and checked_lines < 3 and is_title_line:
            already_removed = True
            checked_lines += 1
            continue
        if stripped:
            checked_lines += 1
        cleaned.append(line)
    return "\n".join(cleaned)

def _pdf_inline_markup(text):
    text = esc_pdf(str(text or ""))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    return text

def _build_pdf_content_flowables(answer_text, heading_style, subheading_style, body_style, bullet_style, number_style, note_style):
    flowables = []
    text = str(answer_text or "").strip()
    if not text:
        flowables.append(Paragraph("Chưa có nội dung báo cáo.", note_style))
        return flowables

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Xóa các dòng chỉ toàn ký tự # (ví dụ "##", "###")
    text = re.sub(r"(?m)^\s*#{1,4}\s*$", "", text)
    # Gộp heading bị tách dòng
    text = re.sub(r"(##?\s*\d+\.?\s*)\n\s*([^\n#]+)", r"\1\2", text)
    # Tách các mục nếu AI viết liền
    text = re.sub(r"\s+(?=\d+[.)]\s+)", "\n", text)
    text = re.sub(r"\s+(?=#{1,4}\s+)", "\n", text)

    text = _strip_duplicate_leading_title(text)
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if not line:
            flowables.append(Spacer(1, 3))
            i += 1
            continue

        # Xử lý heading markdown (#, ##, ###, ####)
        heading_match = re.match(r"^#{1,4}\s*(.+)$", line)
        if heading_match:
            heading_text = heading_match.group(1).strip()
            # Nếu heading chỉ là số (vd "1", "2."), lấy dòng tiếp theo làm nội dung
            if re.match(r"^\d+\.?\s*$", heading_text):
                if i + 1 < len(lines):
                    next_line = lines[i+1].strip()
                    if next_line and not re.match(r"^#", next_line):
                        heading_text = heading_text + " " + next_line
                        i += 1
            # Tạo heading
            style = subheading_style if raw_line.startswith("###") else heading_style
            flowables.append(Paragraph(_pdf_inline_markup(heading_text), style))
            i += 1
            continue

        # Xử lý số thứ tự: "1. Nội dung", "2) Nội dung"
        num_match = re.match(r"^(\d+)[.)]\s+(.+)$", line)
        if num_match:
            number = num_match.group(1)
            content = num_match.group(2).strip()
            upper_content = content.upper()
            is_heading = upper_content.startswith(_PDF_HEADING_KEYWORDS) or len(content) <= 60
            if is_heading:
                flowables.append(Paragraph(f"{number}. {_pdf_inline_markup(content)}", heading_style))
            else:
                flowables.append(Paragraph(f"<b>{number}.</b> {_pdf_inline_markup(content)}", number_style))
            i += 1
            continue

        # Gạch đầu dòng
        bullet_match = re.match(r"^[-*•]\s+(.+)$", line)
        if bullet_match:
            flowables.append(Paragraph("•&nbsp;&nbsp;" + _pdf_inline_markup(bullet_match.group(1)), bullet_style))
            i += 1
            continue

        # Ghi chú / Lưu ý
        if re.match(r"^(ghi ch[uú]|l[uư]u [yý]|ki[eê]́n ngh[iị]|đ[eê]̀ xu[aâ]́t)\s*:", line, flags=re.IGNORECASE):
            flowables.append(Paragraph(_pdf_inline_markup(line), note_style))
            i += 1
            continue

        # Đoạn văn thông thường
        flowables.append(Paragraph(_pdf_inline_markup(line), body_style))
        i += 1

    return flowables

def _pdf_section_header_table(text, doc_width, bg_color):
    header_table = Table([[text]], colWidths=[doc_width])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return header_table
# ============================================================
# GIS MAP IMAGE - TẠO ẢNH VỊ TRÍ THỰC TẾ TRÊN NỀN GIS MASTER
# Không dùng để xác định loại công trình.
# Chỉ dùng để trực quan hóa kết quả GIS đã xác định.
# ============================================================

def create_gis_location_map(
    latitude,
    longitude,
    gis_identification=None,
):
    """
    Tạo ảnh bản đồ GIS từ GIS MASTER.

    Thành phần:
    - Nền tuyến/công trình từ GIS MASTER
    - GPS hiện trường
    - Công trình được xác định
    - Thông tin loại công trình
    - Lý trình
    - Khoảng cách GPS -> đối tượng

    Lưu ý:
    Polygon khu tưới chỉ được hiển thị trực quan,
    KHÔNG được dùng để xác định loại công trình.
    """

    try:
        from PIL import Image, ImageDraw, ImageFont

        # ----------------------------------------------------
        # 1. LẤY GIS MASTER
        # ----------------------------------------------------
        gis_master = get_gis_master()

        if not isinstance(gis_master, dict):
            print("⚠️ GIS MASTER không hợp lệ.")
            return None

        if not gis_master.get("success"):
            print("⚠️ GIS MASTER chưa sẵn sàng.")
            return None

        items = gis_master.get("data") or []

        if not items:
            print("⚠️ GIS MASTER không có dữ liệu.")
            return None

        # ----------------------------------------------------
        # 2. TỌA ĐỘ GPS
        # ----------------------------------------------------
        gps_lat = float(latitude)
        gps_lng = float(longitude)

        # ----------------------------------------------------
        # 3. TÌM ĐỐI TƯỢNG GIS ĐƯỢC XÁC ĐỊNH
        # ----------------------------------------------------
        identified_name = ""

        if isinstance(gis_identification, dict):
            identified_name = str(
                gis_identification.get("name", "")
            ).strip()

        # ----------------------------------------------------
        # 4. CHỌN VÙNG BẢN ĐỒ
        #
        # Ưu tiên vùng quanh GPS.
        # Không lấy toàn bộ GIS MASTER để tránh bản đồ quá nhỏ.
        # ----------------------------------------------------
        map_radius = 0.0035

        min_lng = gps_lng - map_radius
        max_lng = gps_lng + map_radius
        min_lat = gps_lat - map_radius
        max_lat = gps_lat + map_radius

        # ----------------------------------------------------
        # 5. KÍCH THƯỚC ẢNH
        # ----------------------------------------------------
        width = 1400
        height = 900

        image = Image.new(
            "RGB",
            (width, height),
            "white"
        )

        draw = ImageDraw.Draw(image)

        # ----------------------------------------------------
        # 6. FONT
        # ----------------------------------------------------
        try:
            font_regular = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                24
            )

            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                18
            )

            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                30
            )

            font_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                34
            )

        except Exception:
            font_regular = None
            font_small = None
            font_title = None
            font_big = None

        # ----------------------------------------------------
        # 7. HÀM CHUYỂN LAT/LNG -> PIXEL
        # ----------------------------------------------------
        margin_left = 70
        margin_right = 70
        margin_top = 110
        margin_bottom = 70

        map_width = width - margin_left - margin_right
        map_height = height - margin_top - margin_bottom

        def geo_to_pixel(lng, lat):
            x_ratio = (
                (lng - min_lng)
                / (max_lng - min_lng)
            )

            y_ratio = (
                (max_lat - lat)
                / (max_lat - min_lat)
            )

            x = int(
                margin_left
                + x_ratio * map_width
            )

            y = int(
                margin_top
                + y_ratio * map_height
            )

            return x, y

        # ----------------------------------------------------
        # 8. KHUNG BẢN ĐỒ
        # ----------------------------------------------------
        draw.rectangle(
            [
                margin_left,
                margin_top,
                width - margin_right,
                height - margin_bottom
            ],
            outline="gray",
            width=2
        )

        # ----------------------------------------------------
        # 9. VẼ POLYGON
        #
        # Polygon có thể là khu tưới/phạm vi.
        # Chỉ hiển thị nền, KHÔNG dùng nhận dạng.
        # ----------------------------------------------------
        for item in items:

            if item.get("geometry_type") != "Polygon":
                continue

            coordinates = item.get("coordinates") or []

            polygon_points = []

            for point in coordinates:

                try:
                    lng = float(point["lng"])
                    lat = float(point["lat"])

                    if (
                        min_lng <= lng <= max_lng
                        and
                        min_lat <= lat <= max_lat
                    ):
                        polygon_points.append(
                            geo_to_pixel(lng, lat)
                        )

                except Exception:
                    continue

            if len(polygon_points) >= 3:
                draw.polygon(
                    polygon_points,
                    outline="lightgray",
                    fill="#f3f3f3"
                )

        # ----------------------------------------------------
        # 10. VẼ TUYẾN LINESTRING
        # ----------------------------------------------------
        for item in items:

            if item.get("geometry_type") != "LineString":
                continue

            coordinates = item.get("coordinates") or []

            line_points = []

            for point in coordinates:

                try:
                    lng = float(point["lng"])
                    lat = float(point["lat"])

                    if (
                        min_lng <= lng <= max_lng
                        and
                        min_lat <= lat <= max_lat
                    ):
                        line_points.append(
                            geo_to_pixel(lng, lat)
                        )

                except Exception:
                    continue

            if len(line_points) >= 2:

                draw.line(
                    line_points,
                    fill="#287f8f",
                    width=5
                )

        # ----------------------------------------------------
        # 11. VẼ CÁC CÔNG TRÌNH POINT
        # ----------------------------------------------------
        for item in items:

            if item.get("geometry_type") != "Point":
                continue

            coordinates = item.get("coordinates") or []

            if not coordinates:
                continue

            try:
                lng = float(coordinates[0]["lng"])
                lat = float(coordinates[0]["lat"])

                if not (
                    min_lng <= lng <= max_lng
                    and
                    min_lat <= lat <= max_lat
                ):
                    continue

                x, y = geo_to_pixel(lng, lat)

                is_identified = (
                    identified_name
                    and
                    str(item.get("name", "")).strip()
                    == identified_name
                )

                radius = 12 if is_identified else 7

                draw.ellipse(
                    [
                        x - radius,
                        y - radius,
                        x + radius,
                        y + radius
                    ],
                    fill="red" if is_identified else "gray",
                    outline="white",
                    width=3
                )

            except Exception:
                continue

        # ----------------------------------------------------
        # 12. ĐIỂM GPS HIỆN TRƯỜNG
        # ----------------------------------------------------
        gps_x, gps_y = geo_to_pixel(
            gps_lng,
            gps_lat
        )

        # Vòng tròn GPS
        draw.ellipse(
            [
                gps_x - 22,
                gps_y - 22,
                gps_x + 22,
                gps_y + 22
            ],
            outline="red",
            width=6
        )

        draw.ellipse(
            [
                gps_x - 8,
                gps_y - 8,
                gps_x + 8,
                gps_y + 8
            ],
            fill="red"
        )

        # ----------------------------------------------------
        # 13. TIÊU ĐỀ
        # ----------------------------------------------------
        draw.text(
            (70, 25),
            "VỊ TRÍ THỰC TẾ TRÊN BẢN ĐỒ GIS",
            fill="#0f5872",
            font=font_title
        )

        draw.text(
            (70, 65),
            f"GPS: {gps_lat:.6f}, {gps_lng:.6f}",
            fill="black",
            font=font_small
        )

        # ----------------------------------------------------
        # 14. THÔNG TIN GIS
        # ----------------------------------------------------
        info_x = 930
        info_y = 135

        draw.rectangle(
            [
                info_x,
                info_y,
                width - 70,
                380
            ],
            fill="white",
            outline="gray",
            width=2
        )

        draw.text(
            (info_x + 20, info_y + 20),
            "THÔNG TIN GIS",
            fill="#0f5872",
            font=font_title
        )

        if isinstance(gis_identification, dict):

            gis_name = str(
                gis_identification.get("name", "")
            )

            gis_type = str(
                gis_identification.get(
                    "construction_type",
                    gis_identification.get(
                        "geometry_type",
                        ""
                    )
                )
            )

            chainage = str(
                gis_identification.get(
                    "chainage",
                    ""
                )
            )

            distance_m = gis_identification.get(
                "distance_m"
            )

            info_lines = [
                f"Công trình: {gis_name or 'Chưa xác định'}",
                f"Loại: {gis_type or 'Chưa xác định'}",
                f"Lý trình: {chainage or 'Chưa xác định'}",
            ]

            if distance_m is not None:
                try:
                    info_lines.append(
                        f"Khoảng cách GPS: {float(distance_m):.1f} m"
                    )
                except Exception:
                    pass

        else:

            info_lines = [
                "Công trình: Chưa xác định",
                "Loại: Chưa xác định",
                "Lý trình: Chưa xác định",
            ]

        y = info_y + 80

        for line in info_lines:

            draw.text(
                (info_x + 20, y),
                line,
                fill="black",
                font=font_regular
            )

            y += 48

        # ----------------------------------------------------
        # 15. CHÚ GIẢI
        # ----------------------------------------------------
        legend_x = 80
        legend_y = height - 130

        draw.ellipse(
            [
                legend_x,
                legend_y,
                legend_x + 18,
                legend_y + 18
            ],
            fill="red"
        )

        draw.text(
            (legend_x + 30, legend_y - 5),
            "GPS hiện trường",
            fill="black",
            font=font_small
        )

        draw.line(
            [
                legend_x,
                legend_y + 45,
                legend_x + 45,
                legend_y + 45
            ],
            fill="#287f8f",
            width=5
        )

        draw.text(
            (legend_x + 60, legend_y + 35),
            "Tuyến GIS",
            fill="black",
            font=font_small
        )

        # ----------------------------------------------------
        # 16. HƯỚNG BẮC
        # ----------------------------------------------------
        north_x = width - 110
        north_y = height - 150

        draw.line(
            [
                north_x,
                north_y + 55,
                north_x,
                north_y
            ],
            fill="black",
            width=5
        )

        draw.polygon(
            [
                (north_x, north_y - 15),
                (north_x - 10, north_y + 10),
                (north_x + 10, north_y + 10)
            ],
            fill="black"
        )

        draw.text(
            (north_x - 10, north_y - 55),
            "N",
            fill="black",
            font=font_big
        )

        # ----------------------------------------------------
        # 17. GHI CHÚ
        # ----------------------------------------------------
        draw.text(
            (width - 500, height - 55),
            "Nguồn: GIS MASTER / master.kmz",
            fill="gray",
            font=font_small
        )

        # ----------------------------------------------------
        # 18. XUẤT PNG RA MEMORY
        # ----------------------------------------------------
        buffer = BytesIO()

        image.save(
            buffer,
            format="PNG"
        )

        buffer.seek(0)

        result = buffer.getvalue()

        print(
            f"🗺️ GIS MAP CREATED: {len(result)} bytes"
        )

        return result

    except Exception as e:

        print(
            "❌ CREATE GIS MAP ERROR:",
            repr(e)
        )

        return None
# ============================================================
# CREATE FIELD REPORT PDF - ĐÃ SỬA LỖI CÚ PHÁP
# ============================================================
def create_field_report_pdf(
    report_title: str,
    answer: str,
    image_bytes=None,
    reviewer="",
    capture_time=None,
    latitude=None,
    longitude=None,
    gis_identification=None,
    gis_map_bytes=None,
):
    # Lọc bỏ câu mở đầu của AI
    lines = answer.split('\n')
    cleaned = []
    skip_intro = True
    for line in lines:
        stripped = line.strip()
        if skip_intro:
            if re.match(r'^(Chào|Xin chào|Kính chào|Hello|Hi)\s', stripped, re.IGNORECASE):
                continue
            if re.match(r'^Dựa trên hình ảnh|^Theo yêu cầu|^Tôi sẽ', stripped, re.IGNORECASE):
                continue
            if re.match(r'^#{1,4}\s+', stripped):
                skip_intro = False
            if re.match(r'^(\d+[.)]\s+|\s*[-*•]\s+)', stripped):
                skip_intro = False
            if stripped:
                skip_intro = False
        cleaned.append(line)
    answer = '\n'.join(cleaned)

    # Xóa ký hiệu Markdown # ở đầu dòng
    answer = re.sub(r'(?m)^\s*#{1,6}\s*', '', answer)

    # Xóa các dòng trống dư thừa
    answer = re.sub(r'\n{3,}', '\n\n', answer)
    buffer = BytesIO()
    font_regular, font_bold = register_pdf_fonts()
    generated_at = capture_time or time.strftime("%H:%M %d/%m/%Y")
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
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    page_template = PageTemplate(
        id="report",
        frames=[frame],
        onPage=partial(_draw_pdf_header_footer, report_title=report_title, font_regular=font_regular, font_bold=font_bold, generated_at=generated_at),
    )
    doc.addPageTemplates([page_template])

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ThuyLoiTitle", parent=styles["Title"], fontName=font_bold, fontSize=17, leading=22, alignment=TA_CENTER, textColor=PDF_COLOR_PRIMARY, spaceAfter=4)
    label_style = ParagraphStyle("ThuyLoiLabel", parent=styles["BodyText"], fontName=font_bold, fontSize=9.5, leading=13, textColor=PDF_COLOR_MUTED)
    value_style = ParagraphStyle("ThuyLoiValue", parent=styles["BodyText"], fontName=font_regular, fontSize=10, leading=14, textColor=PDF_COLOR_TEXT)
    section_heading_style = ParagraphStyle("ThuyLoiSectionHeading", parent=styles["BodyText"], fontName=font_bold, fontSize=12.5, leading=16, textColor=colors.white, spaceBefore=0, spaceAfter=0)
    heading_style = ParagraphStyle("ThuyLoiHeading", parent=styles["BodyText"], fontName=font_bold, fontSize=11.5, leading=16, textColor=PDF_COLOR_ACCENT, spaceBefore=8, spaceAfter=4)
    subheading_style = ParagraphStyle("ThuyLoiSubHeading", parent=heading_style, fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=3)
    body_style = ParagraphStyle("ThuyLoiBody", parent=styles["BodyText"], fontName=font_regular, fontSize=10.3, leading=15.5, textColor=PDF_COLOR_TEXT, alignment=TA_LEFT, spaceAfter=4)
    bullet_style = ParagraphStyle("ThuyLoiBullet", parent=body_style, leftIndent=12, spaceAfter=2)
    number_style = ParagraphStyle("ThuyLoiNumber", parent=body_style, leftIndent=16, firstLineIndent=-16, spaceAfter=2)
    note_style = ParagraphStyle("ThuyLoiNote", parent=body_style, fontSize=9.3, leading=13.5, textColor=PDF_COLOR_MUTED, backColor=PDF_COLOR_LABEL_BG, borderColor=PDF_COLOR_BORDER, borderWidth=0.6, borderPadding=8, spaceBefore=4, spaceAfter=6)
    warn_header_style = ParagraphStyle("ThuyLoiWarnHeader", parent=styles["BodyText"], fontName=font_bold, fontSize=10.5, leading=14, textColor=PDF_COLOR_WARN_TEXT)
    warn_body_style = ParagraphStyle("ThuyLoiWarnBody", parent=styles["BodyText"], fontName=font_regular, fontSize=9.5, leading=14, textColor=PDF_COLOR_WARN_TEXT)
    signature_title_style = ParagraphStyle("ThuyLoiSignatureTitle", parent=styles["BodyText"], fontName=font_bold, fontSize=11.5, leading=15, alignment=TA_CENTER, textColor=PDF_COLOR_PRIMARY, spaceBefore=2, spaceAfter=6)
    signature_label_style = ParagraphStyle("ThuyLoiSignatureLabel", parent=styles["BodyText"], fontName=font_bold, fontSize=10, leading=13, alignment=TA_CENTER, textColor=PDF_COLOR_TEXT)

    story = []
    story.append(Paragraph(PDF_DOC_LABEL, title_style))
    story.append(Spacer(1, 6))

    # THÔNG TIN BÁO CÁO
    story.append(_pdf_section_header_table(Paragraph("THÔNG TIN BÁO CÁO", section_heading_style), doc.width, PDF_COLOR_PRIMARY))
    meta_rows = [
        [Paragraph("Loại báo cáo", label_style), Paragraph(esc_pdf(report_title), value_style)],
        [Paragraph("Thời gian", label_style), Paragraph(esc_pdf(generated_at), value_style)],
        [Paragraph("Đơn vị", label_style), Paragraph(esc_pdf(PDF_ORG_SHORT), value_style)],
        [Paragraph("Nguồn", label_style), Paragraph(esc_pdf(PDF_APP_NAME), value_style)],
    ]
    meta_table = Table(meta_rows, colWidths=[35 * mm, None], hAlign="LEFT")
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PDF_COLOR_LABEL_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, PDF_COLOR_BORDER),
        ("LINEABOVE", (0, 0), (-1, 0), 0, colors.white),
    ]))
    story.append(KeepTogether(meta_table))
    story.append(Spacer(1, 6))

    # QR VỊ TRÍ CHỤP ẢNH
    if latitude is not None and longitude is not None:
        try:
            latitude = float(latitude)
            longitude = float(longitude)

            maps_url = (
                f"https://www.google.com/maps?q="
                f"{latitude:.8f},{longitude:.8f}"
            )

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=4,
            )
            qr.add_data(maps_url)
            qr.make(fit=True)

            qr_image = qr.make_image(
                fill_color="black",
                back_color="white",
            )

            qr_buffer = BytesIO()
            qr_image.save(qr_buffer, format="PNG")
            qr_buffer.seek(0)

            from reportlab.platypus import Image as RLImage

            qr_rl = RLImage(
                qr_buffer,
                width=32 * mm,
                height=32 * mm,
            )
            qr_rl.hAlign = "CENTER"

            qr_text = Paragraph(
                f"<b>VỊ TRÍ CHỤP ẢNH</b><br/>"
                f"{latitude:.6f}°N, {longitude:.6f}°E<br/>"
                f"<font size='8'>Quét mã QR để mở vị trí trên Google Maps</font>",
                value_style,
            )

            qr_table = Table(
                [[qr_rl, qr_text]],
                colWidths=[40 * mm, doc.width - 40 * mm],
            )

            qr_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.6, PDF_COLOR_BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), PDF_COLOR_LABEL_BG),
            ]))

            story.append(qr_table)
            story.append(Spacer(1, 8))

            print(
                f"✅ QR vị trí đã tạo: "
                f"{latitude:.8f}, {longitude:.8f}"
            )

        except Exception as qr_error:
            print("⚠️ PDF QR ERROR:", repr(qr_error))

    # ẢNH
    if image_bytes:
        try:
            from reportlab.platypus import Image as RLImage
            img_stream = BytesIO(image_bytes)
            pil_img = Image.open(img_stream)
            img_width, img_height = pil_img.size
            print(f"📸 Chèn ảnh vào PDF: {img_width}x{img_height}, {len(image_bytes)} bytes")

            max_width = doc.width
            max_height = 90 * mm
            scale = min(max_width / img_width, max_height / img_height, 1.0)
            display_width = img_width * scale
            display_height = img_height * scale

            story.append(Spacer(1, 6))
            story.append(Paragraph("ẢNH HIỆN TRƯỜNG", subheading_style))
            story.append(Spacer(1, 4))
            field_image = RLImage(BytesIO(image_bytes), width=display_width, height=display_height)
            field_image.hAlign = "CENTER"
            story.append(field_image)
            story.append(Spacer(1, 8))
                # ============================================================
                # BẢN ĐỒ GIS - VỊ TRÍ THỰC TẾ
                # ============================================================
                if gis_map_bytes:
                    try:
                        from reportlab.platypus import Image as RLImage
            
                        story.append(
                            Paragraph(
                                "VỊ TRÍ THỰC TẾ TRÊN BẢN ĐỒ GIS",
                                subheading_style
                            )
                        )
            
                        story.append(Spacer(1, 4))
            
                        gis_image = RLImage(
                            BytesIO(gis_map_bytes),
                            width=doc.width,
                            height=doc.width * 900 / 1400
                        )

            print("✅ Ảnh đã được chèn thành công.")
        except Exception as image_error:
            print("❌ FIELD REPORT IMAGE ERROR:", repr(image_error))
    else:
        print("⚠️ Không có ảnh để chèn vào PDF.")

    story.append(Spacer(1, 4))

    # VỊ TRÍ KỸ THUẬT XÁC ĐỊNH TỪ GPS
    if gis_identification and gis_identification.get("identified"):

        gis_name = gis_identification.get("name", "")
        geometry_type = gis_identification.get("geometry_type", "")
        distance_m = gis_identification.get("distance_m")
        distance_along_line_m = gis_identification.get("distance_along_line_m")

        location_rows = []

        if gis_name:
            location_rows.append([
                Paragraph("<b>Đối tượng</b>", body_style),
                Paragraph(str(gis_name), body_style)
            ])

        if geometry_type == "LineString" and distance_along_line_m is not None:

            ly_trinh_m = float(distance_along_line_m)

            km = int(ly_trinh_m // 1000)
            m = int(round(ly_trinh_m % 1000))

            if m >= 1000:
                km += 1
                m = 0

            ly_trinh = f"K{km}+{m:03d}"

            location_rows.append([
                Paragraph("<b>Loại vị trí</b>", body_style),
                Paragraph("Trên tuyến kênh", body_style)
            ])

            location_rows.append([
                Paragraph("<b>Lý trình</b>", body_style),
                Paragraph(f"<b>{ly_trinh}</b>", body_style)
            ])

            if distance_m is not None:
                location_rows.append([
                    Paragraph("<b>Khoảng cách GPS đến tuyến</b>", body_style),
                    Paragraph(f"{float(distance_m):.1f} m", body_style)
                ])

        elif geometry_type == "Point":

            location_rows.append([
                Paragraph("<b>Loại vị trí</b>", body_style),
                Paragraph("Công trình đầu mối", body_style)
            ])

        if location_rows:

            story.append(Spacer(1, 6))

            story.append(
                _pdf_section_header_table(
                    Paragraph(
                        "📍 VỊ TRÍ KỸ THUẬT XÁC ĐỊNH",
                        section_heading_style
                    ),
                    doc.width,
                    PDF_COLOR_ACCENT
                )
            )

            story.append(Spacer(1, 4))

            location_table = Table(
                location_rows,
                colWidths=[
                    doc.width * 0.32,
                    doc.width * 0.68
                ],
                repeatRows=0
            )

            location_table.setStyle(
                TableStyle([
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "MIDDLE"
                    ),
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        0.4,
                        PDF_COLOR_ACCENT
                    ),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (0, -1),
                        PDF_COLOR_ACCENT_LIGHT
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        6
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        6
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        5
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        5
                    ),
                ])
            )

            story.append(location_table)
            story.append(Spacer(1, 10))

    # NỘI DUNG BÁO CÁO
    story.append(_pdf_section_header_table(Paragraph("NỘI DUNG BÁO CÁO", section_heading_style), doc.width, PDF_COLOR_ACCENT))
    story.append(Spacer(1, 6))
    content_flowables = _build_pdf_content_flowables(
        answer,
        heading_style=heading_style,
        subheading_style=subheading_style,
        body_style=body_style,
        bullet_style=bullet_style,
        number_style=number_style,
        note_style=note_style,
    )
    story.extend(content_flowables)

    # CẢNH BÁO
    story.append(Spacer(1, 6))
    warn_rows = [
        [Paragraph("⚠ LƯU Ý", warn_header_style)],
        [Paragraph(
            "Báo cáo là <b>dự thảo</b> được lập tự động với sự hỗ "
            f"trợ của {esc_pdf(PDF_APP_NAME)}, dựa trên hình ảnh và "
            "dữ liệu hồ sơ hiện có. Báo cáo không thay thế kết luận "
            "kỹ thuật hoặc pháp lý chính thức; mọi kết luận cuối "
            "cùng cần được cán bộ kỹ thuật hoặc người có thẩm quyền "
            "xác nhận.",
            warn_body_style
        )],
    ]
    warn_table = Table(warn_rows, colWidths=[doc.width])
    warn_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), PDF_COLOR_WARN_HEADER_BG),
        ("BACKGROUND", (0, 1), (0, 1), PDF_COLOR_WARN_BODY_BG),
        ("BOX", (0, 0), (-1, -1), 0.7, PDF_COLOR_WARN_BORDER),
        ("LINEBELOW", (0, 0), (0, 0), 0.7, PDF_COLOR_WARN_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 4),
        ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        ("TOPPADDING", (0, 1), (0, 1), 6),
        ("BOTTOMPADDING", (0, 1), (0, 1), 6),
    ]))
    story.append(KeepTogether(warn_table))

    # CHỮ KÝ
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PDF_COLOR_BORDER, spaceBefore=0, spaceAfter=8))
    story.append(Paragraph("XÁC NHẬN HIỆN TRƯỜNG", signature_title_style))
    reviewer_name = reviewer.strip() or "........................."
    signature_cell = [
        Paragraph(f"Người kiểm tra: {esc_pdf(reviewer_name)}", signature_label_style),
        Spacer(1, 18 * mm),
    ]
    signature_table = Table([[signature_cell]], colWidths=[doc.width])
    signature_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(KeepTogether(signature_table))

    # DỰNG PDF
    numbered_canvas_cls = make_numbered_canvas(font_regular, PDF_COLOR_MUTED)
    doc.build(story, canvasmaker=numbered_canvas_cls)
    buffer.seek(0)
    return buffer

# ============================================================
# ENDPOINT FIELD REPORT PDF
# ============================================================
@app.post("/field-report-pdf")
async def field_report_pdf(
    report_title: str = Form("BÁO CÁO NHANH HIỆN TRƯỜNG"),
    answer: str = Form(""),
    image: UploadFile | None = File(None),
    reviewer: str = Form(""),
    capture_time: str = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
):
    if not answer.strip():
        return {"success": False, "error": "Không có nội dung báo cáo để tạo PDF."}
    image_bytes = None
    if image:
        image_bytes = await image.read()
        print(f"📸 Nhận ảnh từ client: {image.filename}, kích thước: {len(image_bytes)} bytes")
    else:
        print("⚠️ Không nhận được ảnh từ client.")

    gis_identification = None
    if latitude is not None and longitude is not None:
        try:
            gis_result = await kml_gps_test(latitude=latitude, longitude=longitude)
            if isinstance(gis_result, dict):
                gis_identification = gis_result.get("gis_identification")
            print("📍 GIS IDENTIFICATION:", gis_identification)
        except Exception as e:
            print("⚠️ Lỗi xác định GIS tự động:", repr(e))
            gis_identification = None
    # ============================================================
    # TẠO ẢNH VỊ TRÍ THỰC TẾ TRÊN NỀN GIS MASTER
    # ============================================================
    gis_map_bytes = None

    if latitude is not None and longitude is not None:
        try:
            gis_map_bytes = await asyncio.to_thread(
                create_gis_location_map,
                latitude,
                longitude,
                gis_identification
            )

            if gis_map_bytes:
                print(
                    f"🗺️ GIS MAP CREATED: "
                    f"{len(gis_map_bytes)} bytes"
                )
            else:
                print("⚠️ GIS MAP không tạo được.")

        except Exception as e:
            print(
                "⚠️ GIS MAP ERROR:",
                repr(e)
            )
            gis_map_bytes = None
    try:
        pdf_buffer = await asyncio.to_thread(
            create_field_report_pdf,
            report_title,
            answer,
            image_bytes,
            reviewer,
            capture_time,
            latitude,
            longitude,
            gis_identification,
            gis_map_bytes,
        )
        filename = "bao-cao-hien-truong.pdf"
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        print("❌ FIELD REPORT PDF ERROR:", repr(e))
        return {"success": False, "error": str(e)}

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
