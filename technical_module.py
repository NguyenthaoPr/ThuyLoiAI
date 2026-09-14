# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import base64
import json
import os
import re
import threading
from datetime import datetime, timedelta
from time import monotonic
from urllib.parse import quote, urlencode
import urllib.request
import csv
import io

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession, Request as GoogleAuthRequest
except ImportError:  # pragma: no cover
    service_account = None
    AuthorizedSession = None
    GoogleAuthRequest = None

# ============================================================
# THUY LOI AI - TECHNICAL MODULE V2.7.0
# DIRECT GOOGLE SHEETS - KHONG DUNG APPS SCRIPT
# Doc truc tiep AI_DATA bang Google Sheets API.
# Khong ghi/sua/xoa du lieu Google Sheet.
# ============================================================

app = FastAPI(title="THUY LOI AI - Thong so ky thuat", version="2.7.0")

GOOGLE_SHEETS_ID = os.getenv(
    "GOOGLE_SHEETS_ID",
    "1SJU9aCRZGWeAeHw6UfY_08HK8-A34kIlnrEiPJNEnko",
).strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "AI_DATA").strip() or "AI_DATA"
# GID của tab AI_DATA trong file Google Sheet hiện tại. Dùng GID để tránh export nhầm tab RAW_DATA.
GOOGLE_SHEET_GID = os.getenv("GOOGLE_SHEET_GID", "1866404435").strip()
GOOGLE_SHEETS_RANGE = os.getenv("GOOGLE_SHEETS_RANGE", f"{GOOGLE_SHEET_NAME}!A:K").strip()
GOOGLE_SHEETS_TIMEOUT = float(os.getenv("GOOGLE_SHEETS_TIMEOUT", "15"))
GOOGLE_SHEETS_CACHE_SECONDS = float(os.getenv("GOOGLE_SHEETS_CACHE_SECONDS", "30"))
# Cho phép đọc Sheet công khai trực tiếp, không cần Apps Script/Service Account.
# Nếu Sheet đặt "Bất kỳ ai có liên kết - Người xem", chế độ này hoạt động ngay.
GOOGLE_SHEETS_PUBLIC = os.getenv("GOOGLE_SHEETS_PUBLIC", "1").strip().lower() in {"1", "true", "yes", "on"}
GOOGLE_SHEETS_API_KEY = os.getenv("GOOGLE_SHEETS_API_KEY", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_cache_lock = threading.Lock()
_sheet_cache = {"loaded_at": 0.0, "rows": None}
_creds = None
_session = None

# ------------------------------------------------------------
# Semantic dictionary - dữ liệu đúng theo AI_DATA hiện tại
# ------------------------------------------------------------
WATER_ALIASES = {
    "WATER_LEVEL": ["h (m)", "h", "mực nước", "muc nuoc"],
    "WATER_LEVEL_UPSTREAM": ["htl (m)", "htl", "mực nước thượng lưu", "muc nuoc thuong luu"],
    "WATER_LEVEL_DOWNSTREAM": ["hhl (m)", "hhl", "mực nước hạ lưu", "muc nuoc ha luu"],
}
RAINFALL_ALIASES = {
    "RAINFALL": ["x (mm)", "lượng mưa", "luong mua", "mưa", "mua"],
    "RAINFALL_T1": ["x t1 (mm)", "x t1"],
    "RAINFALL_C24": ["x c24 (mm)", "x c24"],
}

def _norm(v):
    import unicodedata
    s = "" if v is None else str(v)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower().replace("đ", "d")
    s = re.sub(r"[()\[\]{}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("−", "-")
    if not s:
        return None
    # Dữ liệu AI_DATA dùng dấu phẩy thập phân; đồng thời chịu được 1.234,56.
    s = s.replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None

def _label_value(text, label):
    if text is None:
        return None
    nlabel = _norm(label)
    pattern = re.escape(nlabel).replace(r"\ ", r"\s*")
    m = re.search(pattern + r"\s*[:=]?\s*([-+]?\d+(?:[\.,]\d+)?)", _norm(str(text)))
    if m:
        return _num(m.group(1))
    # Fallback giữ nguyên chuỗi gốc để bắt dấu thập phân/thực tế.
    m = re.search(re.escape(str(label)) + r"\s*[:=]?\s*([-+]?\d+(?:[\.,]\d+)?)", str(text), re.I)
    return _num(m.group(1)) if m else None

def _extract_limit(row, label):
    # MNDBT/MNDGC thường nằm trong cột F/G như ảnh AI_DATA.
    for idx in (5, 6, 4, 7):
        if idx < len(row):
            value = _label_value(row[idx], label)
            if value is not None:
                return value
    return None

def _classify_parameter(name):
    n = _norm(name)
    if n in {_norm(x) for x in WATER_ALIASES["WATER_LEVEL"]}:
        return "WATER_LEVEL"
    if n in {_norm(x) for x in WATER_ALIASES["WATER_LEVEL_UPSTREAM"]} or n.startswith("htl "):
        return "WATER_LEVEL_UPSTREAM"
    if n in {_norm(x) for x in WATER_ALIASES["WATER_LEVEL_DOWNSTREAM"]} or n.startswith("hhl "):
        return "WATER_LEVEL_DOWNSTREAM"
    if n in {_norm(x) for x in RAINFALL_ALIASES["RAINFALL"]}:
        return "RAINFALL"
    if n in {_norm(x) for x in RAINFALL_ALIASES["RAINFALL_T1"]}:
        return "RAINFALL_T1"
    if n in {_norm(x) for x in RAINFALL_ALIASES["RAINFALL_C24"]}:
        return "RAINFALL_C24"
    return None

def _build_credentials():
    global _creds
    if _creds is not None:
        return _creds
    if service_account is None:
        raise RuntimeError("Thiếu thư viện google-auth. Thêm google-auth vào requirements.txt.")
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            _creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            return _creds
        except Exception as exc:
            raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_JSON không hợp lệ: {exc}") from exc
    if GOOGLE_SERVICE_ACCOUNT_B64:
        try:
            info = json.loads(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_B64).decode("utf-8"))
            _creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            return _creds
        except Exception as exc:
            raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_B64 không hợp lệ: {exc}") from exc
    if GOOGLE_APPLICATION_CREDENTIALS:
        try:
            _creds = service_account.Credentials.from_service_account_file(GOOGLE_APPLICATION_CREDENTIALS, scopes=SCOPES)
            return _creds
        except Exception as exc:
            raise RuntimeError(f"Không đọc được GOOGLE_APPLICATION_CREDENTIALS: {exc}") from exc
    raise RuntimeError("Chưa cấu hình quyền đọc Google Sheet. Nếu Sheet công khai, đặt GOOGLE_SHEETS_PUBLIC=1; nếu Sheet riêng tư, dùng GOOGLE_SERVICE_ACCOUNT_JSON hoặc GOOGLE_SERVICE_ACCOUNT_B64.")

def _get_session():
    global _session
    if _session is None:
        if AuthorizedSession is None:
            raise RuntimeError("Thiếu thư viện google-auth. Thêm google-auth vào requirements.txt.")
        _session = AuthorizedSession(_build_credentials())
    return _session

def _read_public_sheet_values():
    """Đọc AI_DATA trực tiếp từ Google Sheet công khai, không qua Apps Script.
    Dùng endpoint xuất CSV của Google Sheets; không ghi/sửa dữ liệu.
    """
    params = urlencode({"format": "csv", "gid": GOOGLE_SHEET_GID})
    url = f"https://docs.google.com/spreadsheets/d/{quote(GOOGLE_SHEETS_ID, safe='')}/export?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "THUY-LOI-AI/2.1"})
    with urllib.request.urlopen(req, timeout=GOOGLE_SHEETS_TIMEOUT) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    # Nếu Google trả về trang HTML đăng nhập/quyền truy cập thì không coi là dữ liệu CSV.
    head = text.lstrip()[:200].lower()
    if head.startswith("<!doctype html") or "<html" in head:
        raise RuntimeError("Google Sheet chưa cho phép đọc công khai bằng liên kết.")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2:
        raise RuntimeError("Google Sheet công khai không trả về dữ liệu CSV hợp lệ.")

    # Kiểm tra đúng tab AI_DATA. Nếu export nhầm RAW_DATA, dừng thay vì
    # đưa dữ liệu sai cột lên giao diện (đây là nguyên nhân dropdown bị sai).
    header = [str(x).strip().lower() for x in rows[0]]
    expected = ["tháng", "ngày", "giờ", "đơn vị", "công trình", "hạng mục", "thông số", "thông số (đơn vị đo)", "giá trị"]
    matches = sum(1 for i, v in enumerate(expected) if i < len(header) and header[i] == v)
    if matches < 6:
        raise RuntimeError(
            f"Đọc nhầm tab/cấu trúc Google Sheet: header nhận được {rows[0][:11]}. "
            f"Đã khớp {matches}/9 cột AI_DATA. Kiểm tra GOOGLE_SHEET_GID."
        )
    return rows

def _cache_rows(values):
    with _cache_lock:
        _sheet_cache["rows"] = values
        _sheet_cache["loaded_at"] = monotonic()
    return values

def _read_sheet_values(force=False):
    now = monotonic()
    with _cache_lock:
        if not force and _sheet_cache["rows"] is not None and now - _sheet_cache["loaded_at"] < GOOGLE_SHEETS_CACHE_SECONDS:
            return _sheet_cache["rows"]

    public_error = None
    if GOOGLE_SHEETS_PUBLIC:
        try:
            return _cache_rows(_read_public_sheet_values())
        except Exception as exc:
            public_error = str(exc)

    # Fallback bảo mật: Google Sheets API + Service Account/API key.
    encoded_range = quote(GOOGLE_SHEETS_RANGE, safe="")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(GOOGLE_SHEETS_ID, safe='')}/values/{encoded_range}"
    params = {"majorDimension": "ROWS", "valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"}
    if GOOGLE_SHEETS_API_KEY:
        params["key"] = GOOGLE_SHEETS_API_KEY
    try:
        if GOOGLE_SHEETS_API_KEY and not (GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_B64 or GOOGLE_APPLICATION_CREDENTIALS):
            req = urllib.request.Request(url + "?" + urlencode(params), headers={"User-Agent": "THUY-LOI-AI/2.1"})
            with urllib.request.urlopen(req, timeout=GOOGLE_SHEETS_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        else:
            resp = _get_session().get(url, params=params, timeout=GOOGLE_SHEETS_TIMEOUT)
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("error", {}).get("message", resp.text[:300])
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(f"Google Sheets API HTTP {resp.status_code}: {detail}")
            payload = resp.json()
    except RuntimeError as exc:
        if public_error:
            raise RuntimeError(f"Không đọc được Google Sheet trực tiếp. Public CSV: {public_error}. API: {exc}") from exc
        raise
    except Exception as exc:
        if public_error:
            raise RuntimeError(f"Không đọc được Google Sheet trực tiếp. Public CSV: {public_error}. API: {exc}") from exc
        raise RuntimeError(f"Không đọc được Google Sheet: {exc}") from exc

    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"Google Sheet không có dữ liệu trong vùng {GOOGLE_SHEETS_RANGE}.")
    return _cache_rows(values)

def _data_rows():
    values = _read_sheet_values()
    rows=[]
    for raw in values[1:]:
        row=list(raw)+[""]*(11-len(raw))
        if any(str(x).strip() for x in row): rows.append(row[:11])
    return rows

def _row_facility(row): return str(row[4]).strip() if len(row)>4 else ""
def _row_parameter(row): return str(row[7]).strip() if len(row)>7 else ""
def _row_value(row): return _num(row[8] if len(row)>8 else None)

def _row_datetime(row, year):
    try:
        month=int(_num(row[0])); day=int(_num(row[1])); hour=float(_num(row[2]) or 0)
        minute=int(round((hour-int(hour))*60)); hour=int(hour)
        return datetime(int(year), month, day, hour, minute)
    except Exception:
        return None

def _date_filter(dt, from_date, to_date):
    if dt is None: return False
    if from_date:
        try:
            if dt < datetime.strptime(from_date, "%Y-%m-%d"): return False
        except ValueError: pass
    if to_date:
        try:
            if dt > datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(microseconds=1): return False
        except ValueError: pass
    return True

def _pick_water_name(names):
    normed=[(n,_classify_parameter(n)) for n in names]
    for code in ("WATER_LEVEL","WATER_LEVEL_UPSTREAM"):
        for name,c in normed:
            if c==code:return name
    return None

def _series(rows, year, codes):
    out={}
    for row in rows:
        name=_row_parameter(row); code=_classify_parameter(name)
        if code not in codes: continue
        value=_row_value(row); dt=_row_datetime(row,year)
        if value is None or dt is None: continue
        out.setdefault(name,[]).append({"time":dt.isoformat(),"value":value})
    for name in out: out[name].sort(key=lambda x:x["time"])
    return out

def _limits(rows):
    bt=gc=None
    for row in rows:
        v=_extract_limit(row,"MNDBT")
        if v is not None: bt=v
        v=_extract_limit(row,"MNDGC")
        if v is not None: gc=v
    return {"mndbt":bt,"mndgc":gc}

def _rain_total(rainfall):
    # Tổng KPI chỉ cộng chuỗi lượng mưa tức thời/đơn vị; C24 là tích lũy 24h nên không cộng dồn.
    primary=[]
    for item in rainfall:
        code=_classify_parameter(item.get("parameter"))
        if code=="RAINFALL": primary.extend(item.get("data",[]))
    if primary: return round(sum(float(x["value"]) for x in primary), 3)
    t1=[]
    for item in rainfall:
        if _classify_parameter(item.get("parameter"))=="RAINFALL_T1": t1.extend(item.get("data",[]))
    if t1: return round(sum(float(x["value"]) for x in t1), 3)
    c24=[]
    for item in rainfall:
        if _classify_parameter(item.get("parameter"))=="RAINFALL_C24": c24.extend(item.get("data",[]))
    return round(float(c24[-1]["value"]),3) if c24 else None

def _build_chart(facility, year, days, from_date, to_date, hours=0):
    rows=[r for r in _data_rows() if _row_facility(r)==facility]

    # Mốc neo duy nhất cho mọi cửa sổ nhanh.
    latest_dt=None
    parsed_rows=[]
    for r in rows:
        dt=_row_datetime(r,year)
        parsed_rows.append((r,dt))
        if dt and (latest_dt is None or dt>latest_dt):
            latest_dt=dt

    quick_cutoff=None
    if not from_date and not to_date and hours and latest_dt:
        quick_cutoff=latest_dt-timedelta(hours=int(hours))

    def _in_window(dt):
        if not dt:
            return False
        if from_date or to_date:
            return _date_filter(dt,from_date,to_date)
        if quick_cutoff is not None:
            return quick_cutoff<=dt<=latest_dt
        if days and days>0 and latest_dt is not None:
            return latest_dt-timedelta(days=int(days))<=dt<=latest_dt
        return True

    all_names=[]
    for r in rows:
        p=_row_parameter(r)
        if p and p not in all_names: all_names.append(p)

    water_names=[
        n for n in all_names
        if _classify_parameter(n) in {"WATER_LEVEL","WATER_LEVEL_UPSTREAM"}
    ]
    water_name=_pick_water_name(water_names)

    water=[]
    if water_name:
        for r,dt in parsed_rows:
            if _row_parameter(r)!=water_name: continue
            value=_row_value(r)
            if dt and value is not None and _in_window(dt):
                water.append({"time":dt.isoformat(),"value":value})
    water.sort(key=lambda x:x["time"])

    rain_map={}
    for r,dt in parsed_rows:
        p=_row_parameter(r)
        code=_classify_parameter(p)
        if code not in {"RAINFALL","RAINFALL_T1","RAINFALL_C24"}: continue
        value=_row_value(r)
        if dt and value is not None and _in_window(dt):
            rain_map.setdefault(p,[]).append({"time":dt.isoformat(),"value":value})

    rainfall=[]
    for p,data in rain_map.items():
        data.sort(key=lambda x:x["time"])
        rainfall.append({"parameter":p,"code":_classify_parameter(p),"data":data})

    totals={
        x["parameter"]:round(sum(float(p["value"]) for p in x["data"]),3)
        for x in rainfall
        if _classify_parameter(x["parameter"])!="RAINFALL_C24"
    }
    for x in rainfall:
        if _classify_parameter(x["parameter"])=="RAINFALL_C24" and x["data"]:
            totals[x["parameter"]]=round(float(x["data"][-1]["value"]),3)

    filtered_rows=[r for r,dt in parsed_rows if _in_window(dt)]
    return {
        "facility":facility,
        "year":year,
        "days":days,
        "hours":int(hours or 0),
        "fromDate":from_date or "",
        "toDate":to_date or "",
        "windowLatest":latest_dt.isoformat() if latest_dt else None,
        "limits":_limits(rows),
        "waterParameter":water_name,
        "water":water,
        "waterVariants":_series(
            filtered_rows,year,
            {"WATER_LEVEL","WATER_LEVEL_UPSTREAM","WATER_LEVEL_DOWNSTREAM"}
        ),
        "rainfall":rainfall,
        "rainfallTotalsByParameter":totals,
        "totalRainfall":_rain_total(rainfall),
        "source":"google_sheets",
        "sheet":GOOGLE_SHEET_NAME,
        "range":GOOGLE_SHEETS_RANGE
    }

HTML = r'''<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#071426">
<title>THUY LOI AI - Thông số kỹ thuật</title>

<!-- Chart.js chỉ dùng cho lớp hiển thị biểu đồ. Dữ liệu đọc trực tiếp Google Sheets API. -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/date-fns@4.1.0/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>

<style>
:root{
  --bg:#f3f6fa;--surface:#fff;--surface2:#f8fafc;--text:#172033;--muted:#687386;
  --line:#dfe6ef;--primary:#0878c9;--primary2:#12a7d8;--ok:#15945d;
  --warn:#d88900;--danger:#e14b32;--shadow:0 10px 30px rgba(18,39,65,.08);
  --radius:18px;--header:70px
}
html.dark{
  --bg:#06101f;--surface:#0b1a2d;--surface2:#0f2239;--text:#f4f8ff;--muted:#9fb0c6;
  --line:#1e3854;--primary:#19b7e6;--primary2:#35d7b2;--ok:#27d88c;
  --warn:#ffb13b;--danger:#ff684e;--shadow:0 12px 35px rgba(0,0,0,.28)
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  background:radial-gradient(circle at 20% 0%,rgba(18,120,201,.08),transparent 35%),var(--bg);
  color:var(--text);transition:background .25s,color .25s
}
button,select,input{font:inherit}
button{cursor:pointer}
.header{
  position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--surface) 94%,transparent);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--line)
}
.header-inner{max-width:1440px;margin:auto;padding:12px 18px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{display:flex;gap:12px;align-items:center;min-width:0}
.icon{width:46px;height:46px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(135deg,#e8f5ff,#d9f5f0);font-size:23px}
.dark .icon{background:linear-gradient(135deg,#102d4a,#103d3a)}
.title{font-weight:900;font-size:18px;letter-spacing:.2px}.sub{font-size:12px;color:var(--muted);margin-top:2px}
.header-actions{display:flex;align-items:center;gap:8px}
.live-badge,.theme-btn{
  border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;
  min-height:38px;padding:0 12px;display:flex;align-items:center;gap:7px;font-weight:700
}
.live-dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(21,148,93,.5);animation:pulse 1.8s infinite}
@keyframes pulse{70%{box-shadow:0 0 0 8px rgba(21,148,93,0)}100%{box-shadow:0 0 0 0 rgba(21,148,93,0)}}
.container{max-width:1440px;margin:auto;padding:18px}

/* V1.7 - Smart Control Room */
.alert-banner.safe{display:flex;border-color:rgba(21,148,93,.55);background:linear-gradient(90deg,rgba(21,148,93,.12),var(--surface))}
.alert-actions{display:flex;align-items:center;gap:7px}
.sound-btn{border:1px solid var(--line);background:var(--surface2);color:var(--text);border-radius:10px;min-height:36px;padding:0 10px;font-weight:800}
.sound-btn.on{border-color:var(--primary);color:var(--primary)}
.alert-banner.danger{animation:alertDanger 1.1s infinite alternate}
@keyframes alertDanger{to{box-shadow:0 0 28px rgba(225,75,50,.20),var(--shadow)}}
.date-filter{display:grid;grid-template-columns:1fr 1fr auto minmax(240px,1fr);gap:10px;margin:-4px 0 16px;min-width:0}
.date-box{padding:10px 13px;background:var(--surface);border:1px solid var(--line);border-radius:14px}
.date-box label{display:block;color:var(--muted);font-size:10px;font-weight:900;margin-bottom:5px}
.date-box input{width:100%;border:0;outline:0;background:transparent;color:var(--text);font-weight:750}
.export-group{display:flex;gap:7px;align-items:center}
.data-date{width:145px;padding:9px 10px;border:1px solid var(--line);border-radius:11px;background:var(--surface2);color:var(--text)}
@media(max-width:760px){
  .connection-grid{grid-template-columns:1fr 1fr}.connection-time{grid-column:1/-1;justify-content:flex-start}.connection-head{align-items:flex-start}.connection-btn{font-size:11px}
  .date-filter{grid-template-columns:1fr 1fr}.date-filter .date-apply{grid-column:1/-1}
  .data-date{width:135px}.export-group{width:100%}
}
.alert-banner{
  display:none;align-items:center;gap:12px;padding:13px 16px;margin-bottom:14px;border:1px solid var(--line);
  border-radius:16px;background:var(--surface);box-shadow:var(--shadow)
}
.alert-banner.show{display:flex}.alert-banner.warn{border-color:rgba(216,137,0,.45);background:linear-gradient(90deg,rgba(216,137,0,.13),var(--surface))}
.alert-banner.danger{border-color:rgba(225,75,50,.55);background:linear-gradient(90deg,rgba(225,75,50,.15),var(--surface))}
.alert-icon{font-size:20px}.alert-text{flex:1}.alert-title{font-weight:900}.alert-detail{font-size:12px;color:var(--muted);margin-top:2px}
.toolbar{display:grid;grid-template-columns:1.55fr .85fr auto auto;gap:10px;margin-bottom:16px}
.control,.card,.panel{
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)
}
.control{padding:10px 13px}.control label{display:block;color:var(--muted);font-size:11px;margin-bottom:5px;font-weight:700}
select,input{
  width:100%;border:0;outline:0;background:transparent;color:var(--text);font-weight:700
}
.primary-btn,.ghost-btn{
  border:1px solid transparent;border-radius:14px;min-height:48px;padding:0 18px;font-weight:800;
  display:inline-flex;align-items:center;justify-content:center;gap:7px
}
.primary-btn{background:linear-gradient(135deg,var(--primary),var(--primary2));color:#fff}
.ghost-btn{background:var(--surface2);border-color:var(--line);color:var(--text)}
.kpi-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}
.kpi{position:relative;padding:16px;overflow:hidden}
.kpi::after{content:"";position:absolute;inset:auto -30px -45px auto;width:130px;height:130px;border-radius:50%;background:rgba(18,167,216,.08)}
.kpi.warn{box-shadow:0 0 0 1px rgba(216,137,0,.45),0 0 28px rgba(216,137,0,.13),var(--shadow)}
.kpi.danger{box-shadow:0 0 0 1px rgba(225,75,50,.55),0 0 30px rgba(225,75,50,.18),var(--shadow)}
.kpi-head{display:flex;justify-content:space-between;gap:10px;align-items:center}
.kpi-label{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.5px}
.kpi-led{width:10px;height:10px;border-radius:50%;background:var(--ok);box-shadow:0 0 9px var(--ok)}
.kpi-led.stale{background:var(--warn);box-shadow:0 0 9px var(--warn)}
.kpi-led.alarm{background:var(--danger);box-shadow:0 0 11px var(--danger);animation:alarmBlink .8s infinite}
@keyframes alarmBlink{50%{opacity:.25}}
.kpi-value{font-size:27px;font-weight:900;margin-top:8px;letter-spacing:-.4px}
.kpi-unit{font-size:12px;color:var(--muted);margin-top:3px}
.kpi-note{font-size:11px;color:var(--muted);margin-top:7px}
.grid{display:grid;grid-template-columns:1fr;gap:16px}
.panel{overflow:hidden}.head{padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px}
.head-title{font-weight:900;font-size:18px}.head-sub{font-size:12px;color:var(--muted);margin-top:3px}
.chart-wrap{padding:10px 12px 12px;height:430px;position:relative;overflow:hidden}
.chart-wrap canvas{width:100%!important;height:100%!important;display:block}
@media(max-width:720px){
  .chart-wrap{height:390px;padding:8px 6px 10px}
}
@media(max-width:430px){
  .chart-wrap{height:360px;padding:6px 3px 8px}
}
.panel-body{padding:16px}.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{
  border:1px solid var(--line);border-radius:999px;padding:8px 11px;font-size:12px;
  color:var(--muted);background:var(--surface2);font-weight:700;transition:.18s
}
.chip:hover,.chip.active{color:var(--primary);border-color:color-mix(in srgb,var(--primary) 45%,var(--line));background:color-mix(in srgb,var(--primary) 10%,var(--surface))}
.info-card{background:var(--surface2);border:1px solid var(--line);border-radius:14px;padding:13px;margin-bottom:10px}
.info-label{color:var(--muted);font-size:11px;font-weight:800;margin-bottom:5px}
.summary-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.summary-item{border:1px solid var(--line);border-radius:14px;padding:13px;background:var(--surface2)}
.summary-label{font-size:11px;color:var(--muted);font-weight:800}
.summary-value{font-weight:900;font-size:19px;margin-top:5px}.summary-note{font-size:11px;color:var(--muted);margin-top:3px}
.trend-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:12px}
.trend-item{border:1px solid var(--line);border-radius:14px;padding:12px;background:var(--surface)}
.trend-icon{font-size:18px}.trend-value{font-weight:900;font-size:18px;margin-top:3px}.trend-note{font-size:11px;color:var(--muted);margin-top:3px}
.data-toolbar{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.search-box{flex:1;min-width:220px;background:var(--surface2);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.page-info{font-size:12px;color:var(--muted);margin-left:auto}
.table{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:680px}
th,td{padding:11px 14px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px}
tbody tr{transition:background .15s}tbody tr:hover{background:color-mix(in srgb,var(--primary) 6%,var(--surface))}
.mobile-data{display:none;padding:10px}.data-item{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:12px;margin-bottom:8px}
.data-item .dt{font-size:11px;color:var(--muted)}.data-item .pn{font-weight:800;font-size:14px;margin-top:4px}.data-item .pv{font-weight:900;font-size:18px;margin-top:2px}
.pagination{display:flex;justify-content:flex-end;align-items:center;gap:7px;padding:12px 14px;border-top:1px solid var(--line)}
.page-btn{min-width:36px;height:36px;border-radius:10px;border:1px solid var(--line);background:var(--surface2);color:var(--text);font-weight:800}
.page-btn.active{background:var(--primary);border-color:var(--primary);color:#fff}
.empty{text-align:center;color:var(--muted);padding:26px}.footer{text-align:center;color:var(--muted);font-size:11px;padding:20px 10px 28px}
@media(max-width:1100px){.kpi-grid{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}.trend-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:720px){
  .container{padding:12px}.header-inner{padding:10px 12px}.live-badge{font-size:0;padding:0;width:38px;justify-content:center}
  .toolbar{grid-template-columns:1fr 1fr}.toolbar .control:first-child{grid-column:1/-1}.toolbar .ghost-btn,.toolbar .primary-btn{width:100%}.kpi-grid{grid-template-columns:1fr 1fr;gap:8px}
  .kpi{padding:13px}.kpi-value{font-size:22px}.chart-wrap{height:330px;padding:9px}.summary-grid{grid-template-columns:1fr 1fr}.trend-grid{grid-template-columns:1fr 1fr}
  .table{display:none}.mobile-data{display:block}.data-toolbar{padding:10px}.page-info{margin-left:0;width:100%}
}
@media(max-width:430px){.toolbar{grid-template-columns:1fr}.toolbar .control:first-child{grid-column:auto}.summary-grid,.trend-grid{grid-template-columns:1fr 1fr}.title{font-size:16px}}

/* V1.10 - Quick Report preview */
.report-btn{white-space:nowrap}
.report-wrap{position:relative;display:flex;align-items:center;gap:7px;min-width:0}
.report-btn{border:1px solid rgba(8,120,201,.35);background:linear-gradient(135deg,#0b82d8,#11b9d8);color:#fff;box-shadow:0 8px 22px rgba(8,120,201,.22);font-weight:900}
.report-btn:hover{filter:brightness(1.04);transform:translateY(-1px)}
.dark .report-btn{background:linear-gradient(135deg,#0aa6df,#18c9a0);border-color:rgba(53,215,178,.45);box-shadow:0 0 24px rgba(24,201,160,.20)}
.report-actions{display:none;align-items:center;gap:7px;flex-wrap:wrap}
.report-actions.show{display:flex;min-width:0}
.report-actions .primary-btn,.report-actions .ghost-btn{min-height:44px;padding:0 13px}
.report-actions .ghost-btn{background:var(--surface);border-color:rgba(8,120,201,.28);color:var(--primary);font-weight:900}
.dark .report-actions .ghost-btn{background:var(--surface2);border-color:rgba(53,215,178,.35);color:#7ee9d0}
@media(max-width:760px){
  .date-filter .report-wrap{grid-column:1/-1;width:100%;display:block}
  .report-wrap>.report-btn{width:100%;min-height:46px}
  .report-actions{width:100%;display:none;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:7px}
  .report-actions.show{display:grid}
  .report-actions button{width:100%;min-width:0;min-height:44px;padding:0 5px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
}
@media(max-width:390px){.report-actions{gap:5px}.report-actions button{font-size:11px;padding:0 3px}}
.report-modal{position:fixed;inset:0;z-index:9999;background:rgba(4,12,22,.72);display:none;align-items:center;justify-content:center;padding:18px}
.report-modal.show{display:flex}
.report-modal-card{width:min(980px,100%);height:min(90vh,900px);background:var(--panel,#fff);color:var(--text,#152238);border:1px solid var(--line,#dce4ee);border-radius:18px;box-shadow:0 25px 80px rgba(0,0,0,.35);display:flex;flex-direction:column;overflow:hidden}
.report-modal-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 16px;border-bottom:1px solid var(--line,#dce4ee);font-weight:800}
.report-modal-actions{display:flex;gap:7px;align-items:center}
.report-close{border:0;border-radius:10px;padding:8px 12px;cursor:pointer;background:var(--soft,#eef3f8);color:inherit}
.report-preview{background:#fff;color:#111;overflow:auto;padding:28px;flex:1}
.report-preview h1{text-align:center;font-size:20px;margin:0 0 12px}.report-preview h2{font-size:15px;margin:20px 0 7px;border-bottom:1px solid #777;padding-bottom:4px}.report-preview p{margin:6px 0;line-height:1.5}.report-preview table{border-collapse:collapse;width:100%;margin:8px 0}.report-preview th,.report-preview td{border:1px solid #777;padding:6px;text-align:left;vertical-align:top}.report-preview th{font-weight:bold;background:#eee}.report-preview ul{margin-top:5px}.report-preview .note{font-style:italic;color:#444}.report-preview .footer{margin-top:22px;font-size:12px;color:#555}
@media(max-width:700px){.report-btn{flex:1;min-width:0}.report-preview{padding:16px}.report-modal{padding:8px}.report-modal-card{height:94vh;border-radius:14px}}

/* ============================================================
   BIỂU ĐỒ DIỄN BIẾN — MOBILE FULL BLEED V2.4
   ============================================================ */
@media(max-width:720px){
  .panel:has(#hydroChart){
    margin-left:0!important;
    margin-right:0!important;
    padding-left:0!important;
    padding-right:0!important;
    border-left:0!important;
    border-right:0!important;
    border-radius:0!important;
  }
  .panel:has(#hydroChart) .chart-wrap{
    width:100%!important;
    max-width:none!important;
    margin-left:0!important;
    margin-right:0!important;
    padding-left:0!important;
    padding-right:0!important;
    border-radius:0!important;
  }
  .panel:has(#hydroChart) .panel-head{
    padding-left:12px!important;
    padding-right:12px!important;
  }
}
@media(max-width:430px){
  .panel:has(#hydroChart) .panel-head{
    padding-left:10px!important;
    padding-right:10px!important;
  }
}

/* ============================================================
   BIỂU ĐỒ DIỄN BIẾN — MOBILE FULL BLEED V2.5
   ============================================================ */
@media (max-width:720px){
  .panel:has(#hydroChart),
  .panel:has(.chart-wrap){
    margin-left:0!important;
    margin-right:0!important;
    padding-left:0!important;
    padding-right:0!important;
    width:100%!important;
    max-width:none!important;
    border-left:0!important;
    border-right:0!important;
    border-radius:0!important;
  }

  .panel:has(#hydroChart) .chart-wrap,
  .panel:has(.chart-wrap) .chart-wrap{
    width:100%!important;
    max-width:none!important;
    margin-left:0!important;
    margin-right:0!important;
    padding-left:0!important;
    padding-right:0!important;
    border-left:0!important;
    border-right:0!important;
    border-radius:0!important;
    box-sizing:border-box!important;
  }

  .panel:has(#hydroChart) .panel-head{
    padding-left:12px!important;
    padding-right:12px!important;
  }
}

@media (max-width:430px){
  .panel:has(#hydroChart) .panel-head{
    padding-left:10px!important;
    padding-right:10px!important;
  }
}

/* ============================================================
   BỘ LỌC THỜI GIAN V2.6 — QUICK RANGE MOBILE
   ============================================================ */
.period-control{min-width:0}
.period-quick{
  display:grid;
  grid-template-columns:repeat(5,minmax(0,1fr));
  gap:5px;
  margin-top:1px;
}
.period-quick button{
  min-width:0;
  min-height:36px;
  padding:0 5px;
  border:1px solid var(--line);
  border-radius:10px;
  background:var(--surface2);
  color:var(--text);
  font-size:11px;
  font-weight:850;
  white-space:nowrap;
  cursor:pointer;
}
.period-quick button.active{
  color:#fff;
  border-color:var(--primary);
  background:linear-gradient(135deg,var(--primary),var(--primary2));
  box-shadow:0 5px 14px rgba(8,120,201,.16);
}
.period-quick button:active{transform:translateY(1px)}

.date-filter{
  grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;
  align-items:stretch;
}
.date-filter .date-box{min-width:0}
.date-filter .date-apply{min-width:190px}

@media(max-width:760px){
  .period-quick{gap:4px}
  .period-quick button{font-size:10.5px;min-height:34px;padding:0 3px}
  .date-filter{
    grid-template-columns:minmax(0,1fr) minmax(0,1fr);
    gap:7px;
  }
  .date-filter .date-apply{
    grid-column:1/-1;
    width:100%;
    min-width:0;
  }
}

@media(max-width:430px){
  .period-quick{gap:3px}
  .period-quick button{font-size:9.5px;min-height:33px;border-radius:9px}
  .date-filter{gap:6px}
  .date-box{padding:9px 9px}
  .date-box label{font-size:9px}
}
</style>
</head>

<body>
<header class="header">
  <div class="header-inner">
    <div class="brand">
      <div class="icon">⚙️</div>
      <div><div class="title">THUY LOI AI</div><div class="sub">Thông số kỹ thuật · Control Room</div></div>
    </div>
    <div class="header-actions">
      <div class="live-badge"><span class="live-dot"></span><span>Module độc lập</span></div>
      <button class="theme-btn" id="themeBtn" onclick="toggleTheme()" aria-label="Đổi giao diện">🌙</button>
    </div>
  </div>
</header>

<main class="container">
  <section id="alertBanner" class="alert-banner safe">
    <div id="alertIcon" class="alert-icon">●</div>
    <div class="alert-text"><div id="alertTitle" class="alert-title">Vận hành bình thường</div><div id="alertDetail" class="alert-detail">Đang chờ dữ liệu mực nước.</div></div>
    <div class="alert-actions"><button id="soundBtn" class="sound-btn" onclick="toggleAlertSound()">🔕 Âm thanh tắt</button></div>
  </section>

  <section class="toolbar">
    <div class="control"><label>CÔNG TRÌNH</label><select id="facility"><option value="">Đang tải công trình...</option></select></div>
    <div class="control period-control">
      <label>THỜI GIAN QUAN TRẮC</label>
      <div class="period-quick" id="periodQuick" role="group" aria-label="Khoảng thời gian nhanh">
        <button type="button" data-range="6h" onclick="selectQuickPeriod('6h')">6h</button>
        <button type="button" data-range="12h" onclick="selectQuickPeriod('12h')">12h</button>
        <button type="button" data-range="24h" onclick="selectQuickPeriod('24h')">24h</button>
        <button type="button" data-range="3d" onclick="selectQuickPeriod('3d')">3 ngày</button>
        <button type="button" data-range="7d" class="active" onclick="selectQuickPeriod('7d')">7 ngày</button>
      </div>
      <select id="period" aria-hidden="true" tabindex="-1" style="position:absolute;opacity:0;pointer-events:none;width:1px;height:1px">
        <option value="6 gio">6 giờ</option>
        <option value="12 gio">12 giờ</option>
        <option value="24 gio">24 giờ</option>
        <option value="3 ngay">3 ngày</option>
        <option value="7 ngay" selected>7 ngày</option>
      </select>
    </div>
    <button class="ghost-btn" onclick="checkConnections()">🧪 Kiểm tra kết nối</button><button class="primary-btn" onclick="refreshModule()">↻ Làm mới</button>
  </section>

  <section class="date-filter">
    <div class="date-box"><label>TỪ NGÀY</label><input id="fromDate" type="date"></div>
    <div class="date-box"><label>ĐẾN NGÀY</label><input id="toDate" type="date"></div>
    <button class="primary-btn date-apply" style="min-height:44px" onclick="applyCustomDateRange()">📅 Áp dụng khoảng ngày</button>
    <div class="report-wrap">
      <button id="quickReportBtn" class="ghost-btn report-btn" style="min-height:44px;width:100%" onclick="toggleQuickReportActions()">📄 Báo cáo nhanh</button>
      <div id="quickReportActions" class="report-actions">
        <button class="ghost-btn report-btn" onclick="previewQuickReport()">👁️ Xem trước</button>
        <button class="ghost-btn report-btn" onclick="shareQuickReportZalo()">💬 Gởi Zalo</button>
        <button class="primary-btn report-btn" onclick="downloadQuickReportWord()">⬇️ Tải về</button>
      </div>
    </div>
  </section>

  <section class="kpi-grid">
    <div id="kpiWater" class="card kpi"><div class="kpi-head"><div class="kpi-label">MỰC NƯỚC HIỆN TẠI</div><span id="ledWater" class="kpi-led"></span></div><div class="kpi-value" id="water">—</div><div class="kpi-unit">m</div><div class="kpi-note" id="waterNote">Chưa có dữ liệu</div></div>
    <div id="kpiState" class="card kpi"><div class="kpi-head"><div class="kpi-label">TRẠNG THÁI</div><span id="ledState" class="kpi-led"></span></div><div class="kpi-value" id="state">—</div><div class="kpi-unit" id="stateDetail">Chưa có dữ liệu</div></div>
    <div class="card kpi"><div class="kpi-head"><div class="kpi-label">MNDBT</div><span class="kpi-led"></span></div><div class="kpi-value" id="mndbt">—</div><div class="kpi-unit">m</div><div class="kpi-note">Mực nước dâng bình thường</div></div>
    <div class="card kpi"><div class="kpi-head"><div class="kpi-label">MNDGC</div><span class="kpi-led"></span></div><div class="kpi-value" id="mndgc">—</div><div class="kpi-unit">m</div><div class="kpi-note">Mực nước dâng gia cường</div></div>
    <div class="card kpi"><div class="kpi-head"><div class="kpi-label">TỔNG LƯỢNG MƯA</div><span id="ledRain" class="kpi-led"></span></div><div class="kpi-value" id="rainTotal">—</div><div class="kpi-unit">mm</div><div class="kpi-note">Trong khoảng thời gian chọn</div></div>
  </section>

  <section class="grid">
    <div class="panel">
      <div class="head"><div><div class="head-title">Biểu đồ diễn biến</div><div class="head-sub">HTL · X (mm) · MNDBT · MNDGC</div></div><button class="ghost-btn" style="min-height:36px;padding:0 11px" onclick="fitChart()">↺</button></div>
      <div class="chart-wrap"><canvas id="hydroChart"></canvas></div>
    </div>

  </section>

  <section class="panel" style="margin-top:16px">
    <div class="head"><div><div class="head-title">Tóm tắt kỹ thuật</div><div class="head-sub">Phân tích số liệu thực tế; không tự động gán mức cảnh báo ngoài các ngưỡng MNDBT/MNDGC được cung cấp.</div></div></div>
    <div id="technicalSummary" class="panel-body"><div class="empty">Chọn công trình để phân tích.</div></div>
  </section>


  <div class="footer">THUY LOI AI · Technical Module V2.0.0 · Smart Control Room · Google Sheets Direct · Dashboard kỹ thuật</div>
</main>

<script>
const f=document.getElementById('facility'), parameter=null, period=document.getElementById('period');
const water=document.getElementById('water'), state=document.getElementById('state');
const stateDetail=document.getElementById('stateDetail'), mndbt=document.getElementById('mndbt'), mndgc=document.getElementById('mndgc'), rainTotal=document.getElementById('rainTotal');
const technicalSummary=document.getElementById('technicalSummary'), alertBanner=document.getElementById('alertBanner');
let currentParameters={waterLevel:[],rainfall:[]},currentData=null,hydroChart=null;
let selectedWaterParameter='Mực nước';
let alertSoundEnabled=false,lastAlertLevel='normal';

function toggleAlertSound(){
  alertSoundEnabled=!alertSoundEnabled;
  const b=document.getElementById('soundBtn');
  b.classList.toggle('on',alertSoundEnabled);
  b.textContent=alertSoundEnabled?'🔔 Âm thanh bật':'🔕 Âm thanh tắt';
  /* Kích hoạt AudioContext bằng thao tác người dùng, tránh autoplay bị trình duyệt chặn. */
  if(alertSoundEnabled){
    try{
      const C=window.AudioContext||window.webkitAudioContext;
      if(C){const c=new C(),o=c.createOscillator(),g=c.createGain();o.frequency.value=620;g.gain.value=.025;o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.07)}
    }catch(e){}
  }
}
function alertBeep(level){
  if(!alertSoundEnabled||level==='normal'||level===lastAlertLevel)return;
  try{
    const C=window.AudioContext||window.webkitAudioContext;if(!C)return;
    const c=new C(),o=c.createOscillator(),g=c.createGain();
    o.type='sine';o.frequency.value=level==='danger'?760:560;
    g.gain.setValueAtTime(.035,c.currentTime);g.gain.exponentialRampToValueAtTime(.001,c.currentTime+.35);
    o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+.35);
  }catch(e){}
}
function localDateStart(v){return v?new Date(v+'T00:00:00'):null}
function localDateEnd(v){return v?new Date(v+'T23:59:59.999'):null}


function setSelectedFacility(){return f.value||'Chưa chọn'}

let selectedQuickPeriod='7d';
let chartRequestSerial=0;

function quickPeriodMeta(key){
  return ({
    '6h':{value:'6 gio',hours:6,days:0,label:'6h'},
    '12h':{value:'12 gio',hours:12,days:0,label:'12h'},
    '24h':{value:'24 gio',hours:24,days:0,label:'24h'},
    '3d':{value:'3 ngay',hours:0,days:3,label:'3 ngày'},
    '7d':{value:'7 ngay',hours:0,days:7,label:'7 ngày'}
  })[key]||{value:'7 ngay',hours:0,days:7,label:'7 ngày'};
}

function setQuickButtonsActive(key){
  document.querySelectorAll('#periodQuick button[data-range]').forEach(btn=>{
    btn.classList.toggle('active',btn.dataset.range===key);
  });
}

function clearCustomDates(){
  document.getElementById('fromDate').value='';
  document.getElementById('toDate').value='';
}

function selectQuickPeriod(key){
  const meta=quickPeriodMeta(key);
  selectedQuickPeriod=key;
  period.value=meta.value;
  setQuickButtonsActive(key);
  clearCustomDates();
  if(f.value)loadChartData();
}

function periodDays(){
  return quickPeriodMeta(selectedQuickPeriod).days||1;
}

function applyCustomDateRange(){
  const from=document.getElementById('fromDate').value;
  const to=document.getElementById('toDate').value;

  if(from&&to&&from>to){
    alert('Ngày bắt đầu không được lớn hơn ngày kết thúc.');
    return;
  }

  if(!from&&!to){
    setQuickButtonsActive(selectedQuickPeriod||'7d');
    if(f.value)loadChartData();
    return;
  }

  document.querySelectorAll('#periodQuick button[data-range]').forEach(btn=>btn.classList.remove('active'));

  if(!f.value){
    resetData('Chọn công trình để tải dữ liệu.');
    return;
  }

  loadChartData();
}

function normalizeRawWaterSeries(data){
  let raw=data&&(data.water??data.waterLevel??data.waterSeries??data.waterData);
  if(raw&&typeof raw==='object'&&!Array.isArray(raw)){
    raw=Array.isArray(raw.data)?raw.data:(Array.isArray(raw.series)?raw.series:[]);
  }
  if(!Array.isArray(raw))raw=[];
  return raw.map(p=>{
    if(Array.isArray(p))return {time:p[0],value:p[1]};
    if(!p||typeof p!=='object')return null;
    const time=p.time??p.timestamp??p.datetime??p.dateTime??p.ngayGio??p.ngay_gio??p.ngay??p.date;
    const value=p.value??p.val??p.giaTri??p.gia_tri??p.H??p.h??p.mucNuoc??p.muc_nuoc;
    return {...p,time,value};
  }).filter(Boolean);
}

async function loadChartData(){
  if(!f.value){
    resetData();
    return;
  }

  const requestId=++chartRequestSerial;
  state.textContent='Đang tải...';
  stateDetail.textContent='Đang đọc trực tiếp AI_DATA từ Google Sheet';

  try{
    const from=document.getElementById('fromDate').value;
    const to=document.getElementById('toDate').value;
    const meta=quickPeriodMeta(selectedQuickPeriod);

    const params=new URLSearchParams({
      facility:f.value,
      year:String(new Date().getFullYear()),
      days:String(meta.days),
      hours:String(meta.hours)
    });

    // Custom date là chế độ độc lập và có độ ưu tiên cao nhất.
    if(from||to){
      params.set('days','0');
      params.set('hours','0');
      if(from){
        params.set('fromDate',from);
        params.set('year',String(new Date(from+'T12:00:00').getFullYear()));
      }
      if(to)params.set('toDate',to);
    }

    const result=await fetchJson('/api/chart?'+params.toString(),{},1);

    // Không để response của lần chọn trước ghi đè lựa chọn mới.
    if(requestId!==chartRequestSerial)return;

    const nextData=result.data||{};
    nextData.water=normalizeRawWaterSeries(nextData);
    currentData=nextData;
    renderData(currentData);

    // renderHydroChart lấy min/max từ dataset mới; xóa scale cũ để không giữ window trước.
    if(hydroChart){
      hydroChart.options.scales.x.min=undefined;
      hydroChart.options.scales.x.max=undefined;
      hydroChart.update('none');
    }
  }catch(err){
    if(requestId!==chartRequestSerial)return;
    console.error(err);
    setDataError(err.message||'Không tải được dữ liệu Google Sheet.');
  }
}

function evaluateAlert(data,latest){
  const banner=document.getElementById('alertBanner');
  banner.className='alert-banner safe';
  let level='normal';
  if(!latest){
    document.getElementById('alertIcon').textContent='●';
    document.getElementById('alertTitle').textContent='Vận hành bình thường';
    document.getElementById('alertDetail').textContent='Chưa có mực nước trong khoảng dữ liệu đang chọn.';
    lastAlertLevel='normal';
    return;
  }
  const h=Number(latest.value),bt=Number(data.limits&&data.limits.mndbt),gc=Number(data.limits&&data.limits.mndgc);
  if(!Number.isFinite(h)){
    lastAlertLevel='normal';
    return;
  }
  if(Number.isFinite(gc)&&h>=gc){
    level='danger';
    document.getElementById('alertIcon').textContent='🚨';
    document.getElementById('alertTitle').textContent='CẢNH BÁO ĐỎ · CHẠM/VƯỢT MNDGC';
    document.getElementById('alertDetail').textContent=`H = ${formatNumber(h)} m · MNDGC = ${formatNumber(gc)} m · ${h-gc>=0?'Vượt '+formatNumber(h-gc)+' m':'Còn '+formatNumber(gc-h)+' m'}`;
  }else if(Number.isFinite(bt)&&h>=bt){
    level='warn';
    document.getElementById('alertIcon').textContent='⚠️';
    document.getElementById('alertTitle').textContent='CẢNH BÁO VÀNG · CHẠM/VƯỢT MNDBT';
    document.getElementById('alertDetail').textContent=`H = ${formatNumber(h)} m · MNDBT = ${formatNumber(bt)} m · ${h-bt>=0?'Vượt '+formatNumber(h-bt)+' m':'Còn '+formatNumber(bt-h)+' m'}`;
  }else if(Number.isFinite(bt)&&h>=bt*.95){
    level='warn';
    document.getElementById('alertIcon').textContent='◐';
    document.getElementById('alertTitle').textContent='CẢNH BÁO VÀNG · ĐANG TIẾN SÁT MNDBT';
    document.getElementById('alertDetail').textContent=`H = ${formatNumber(h)} m · MNDBT = ${formatNumber(bt)} m · Khoảng cách ${formatNumber(bt-h)} m`;
  }else{
    document.getElementById('alertIcon').textContent='●';
    document.getElementById('alertTitle').textContent='Vận hành bình thường';
    document.getElementById('alertDetail').textContent=`H = ${formatNumber(h)} m · Dưới MNDBT ${Number.isFinite(bt)?formatNumber(bt-h)+' m':'—'} · Dữ liệu mới nhất`;
  }
  banner.classList.add(level);
  alertBeep(level);
  lastAlertLevel=level;
}

function renderData(data){
  const waterSeries=normalizeSeries(data.water),latest=waterSeries.length?waterSeries[waterSeries.length-1]:null;
  data.water=waterSeries.map(p=>({...p,time:p.time.toISOString()}));
  water.textContent=latest?formatNumber(latest.value):'—';state.textContent=latest?'Có dữ liệu':'Chưa có mực nước';
  if(latest){const d=parseDataTime(latest.time);stateDetail.textContent='Cập nhật '+d.toLocaleString('vi-VN');document.getElementById('waterNote').textContent='Lần đo mới nhất'}
  else { stateDetail.textContent='Chưa tìm thấy chuỗi mực nước'; document.getElementById('waterNote').textContent='Đã thử các tên thông số mực nước'; }
  mndbt.textContent=data.limits&&data.limits.mndbt!=null?formatNumber(data.limits.mndbt):'—';
  mndgc.textContent=data.limits&&data.limits.mndgc!=null?formatNumber(data.limits.mndgc):'—';
  rainTotal.textContent=data.totalRainfall!=null?formatNumber(data.totalRainfall):'—';
  evaluateAlert(data,latest);updateKpiState(data,latest);renderTechnicalSummary(data,waterSeries);
  try{renderHydroChart(data)}catch(chartErr){
    console.warn('Biểu đồ chưa tải được:',chartErr);
    const canvas=document.getElementById('hydroChart');
    if(canvas){const ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);ctx.font='14px Arial';ctx.fillStyle=document.documentElement.classList.contains('dark')?'#9fb0c6':'#687386';ctx.textAlign='center';ctx.fillText(window.Chart?'Biểu đồ không có chuỗi hợp lệ':'Chart.js chưa tải được',canvas.width/2,canvas.height/2);}
  }
}

function updateKpiState(data,latest){
  const k=document.getElementById('kpiWater'),led=document.getElementById('ledWater'),ledState=document.getElementById('ledState');
  k.classList.remove('warn','danger');led.className='kpi-led';ledState.className='kpi-led';
  if(!latest)return;
  const h=Number(latest.value),bt=Number(data.limits&&data.limits.mndbt),gc=Number(data.limits&&data.limits.mndgc);
  if(Number.isFinite(gc)&&h>=gc){k.classList.add('danger');led.classList.add('alarm');ledState.classList.add('alarm')}
  else if(Number.isFinite(bt)&&h>=bt){k.classList.add('warn');led.classList.add('stale');ledState.classList.add('stale')}
}

function parseDataTime(value){
  if(value instanceof Date)return value;
  if(value===null||value===undefined||value==='')return null;
  const raw=String(value).trim();
  /* ISO có timezone/Z: tôn trọng timezone nguồn và hiển thị theo giờ Việt Nam của trình duyệt. */
  if(/T/.test(raw)&&/(Z|[+-]\d{2}:?\d{2})$/.test(raw)){
    const d=new Date(raw);return Number.isFinite(d.getTime())?d:null;
  }
  /* Chuỗi không có timezone phải được hiểu đúng như giờ quan trắc, không tự cộng/trừ UTC. */
  let m=raw.match(/^(\d{4})[-\/]?(\d{2})[-\/]?(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if(m){const d=new Date(Number(m[1]),Number(m[2])-1,Number(m[3]),Number(m[4]),Number(m[5]),Number(m[6]||0),0);return Number.isFinite(d.getTime())?d:null;}
  m=raw.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
  if(m){const d=new Date(Number(m[3]),Number(m[2])-1,Number(m[1]),Number(m[4]||0),Number(m[5]||0),Number(m[6]||0),0);return Number.isFinite(d.getTime())?d:null;}
  const d=new Date(raw);return Number.isFinite(d.getTime())?d:null;
}
function normalizeSeries(series){
  let raw=series;
  if(raw&&typeof raw==='object'&&!Array.isArray(raw))raw=Array.isArray(raw.data)?raw.data:(Array.isArray(raw.series)?raw.series:[]);
  if(!Array.isArray(raw))raw=[];
  return raw.map(p=>{
    if(Array.isArray(p))return {time:parseDataTime(p[0]),value:Number(p[1])};
    const time=p&& (p.time??p.timestamp??p.datetime??p.dateTime??p.ngayGio??p.ngay_gio??p.ngay??p.date);
    const value=p&& (p.value??p.val??p.giaTri??p.gia_tri??p.H??p.h??p.mucNuoc??p.muc_nuoc);
    return {...(p||{}),time:parseDataTime(time),value:Number(value)};
  }).filter(p=>p.time&&Number.isFinite(p.time.getTime())&&Number.isFinite(p.value)).sort((a,b)=>a.time-b.time);
}

function reportDateRange(series){
  const points=(series||[]).map(p=>({time:parseDataTime(p.time),value:Number(p.value)})).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime())).sort((a,b)=>a.time-b.time);
  const from=document.getElementById('fromDate').value, to=document.getElementById('toDate').value;
  if(from||to){const fmt=v=>{if(!v)return '—';const d=new Date(v+'T00:00:00');return d.toLocaleDateString('vi-VN')};return {from:fmt(from),to:fmt(to),points};}
  if(points.length)return {from:points[0].time.toLocaleDateString('vi-VN'),to:points[points.length-1].time.toLocaleDateString('vi-VN'),points};
  return {from:'—',to:'—',points:[]};
}
function fmtReportDate(d){return d instanceof Date?d.toLocaleString('vi-VN'):String(d||'—')}
function reportAnalysis(data){
  const r=reportDateRange(data.water||[]), pts=r.points, latest=pts[pts.length-1], first=pts[0];
  const bt=Number(data.limits&&data.limits.mndbt), gc=Number(data.limits&&data.limits.mndgc);
  const rainfallSeries=Array.isArray(data.rainfall)?data.rainfall:[];
  const rainPoints=rainfallSeries.flatMap(x=>(Array.isArray(x.data)?x.data:[]).map(p=>({time:parseDataTime(p.time),value:Number(p.value),name:x.parameter||'Lượng mưa'}))).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime()));
  const totalRain=Number.isFinite(Number(data.totalRainfall))?Number(data.totalRainfall):rainPoints.reduce((sum,p)=>sum+(p.value||0),0);
  const increase=latest&&first?latest.value-first.value:null;
  const min=pts.length?Math.min(...pts.map(p=>p.value)):null, max=pts.length?Math.max(...pts.map(p=>p.value)):null;
  const maxRise=pts.length>1?Math.max(...pts.slice(1).map((p,i)=>p.value-pts[i].value)):null;
  const maxDrop=pts.length>1?Math.min(...pts.slice(1).map((p,i)=>p.value-pts[i].value)):null;

  /*
   * PHÂN TÍCH 2–3 LẦN ĐO GẦN NHẤT
   * - Không dùng khoảng 24 giờ cố định để dự kiến thời gian đến ngưỡng.
   * - Lấy tối đa 3 điểm cuối, tính đúng thời gian giữa các lần đo.
   * - Tính tốc độ từng khoảng (m/giờ) và tốc độ xu hướng tuyến tính từ 2–3 điểm.
   * - Dùng tốc độ xu hướng để NGOẠI SUY thời gian đến MNDBT/MNDGC.
   * Đây là ngoại suy, không phải “nội suy” theo nghĩa toán học vì dự báo nằm ngoài
   * khoảng quan trắc. Cách gọi trong báo cáo được ghi rõ để tránh hiểu nhầm.
   */
  const recent=pts.slice(-3);
  const intervals=[];
  for(let i=1;i<recent.length;i++){
    const dt=(recent[i].time-recent[i-1].time)/3600000;
    const dh=recent[i].value-recent[i-1].value;
    if(dt>0) intervals.push({from:recent[i-1],to:recent[i],hours:dt,delta:dh,rate:dh/dt});
  }
  let trendSlope=null,trendMethod='Chưa đủ 2 lần đo';
  if(recent.length>=2&&intervals.length){
    if(recent.length===2){
      trendSlope=intervals[intervals.length-1].rate;
      trendMethod='2 lần đo gần nhất';
    }else{
      const t0=recent[0].time.getTime();
      const xs=recent.map(p=>(p.time.getTime()-t0)/3600000), ys=recent.map(p=>p.value);
      const xm=xs.reduce((a,b)=>a+b,0)/xs.length, ym=ys.reduce((a,b)=>a+b,0)/ys.length;
      const den=xs.reduce((sum,x)=>sum+(x-xm)*(x-xm),0);
      trendSlope=den>0?xs.reduce((sum,x,i)=>sum+(x-xm)*(ys[i]-ym),0)/den:null;
      trendMethod='3 lần đo gần nhất · hồi quy tuyến tính theo thời gian';
    }
  }
  const lastInterval=intervals.length?intervals[intervals.length-1]:null;
  const prevInterval=intervals.length>1?intervals[intervals.length-2]:null;
  const recentDelta=lastInterval?lastInterval.delta:null;
  const recentRate=lastInterval?lastInterval.rate:null;
  const previousRate=prevInterval?prevInterval.rate:null;
  const rateChange=(Number.isFinite(recentRate)&&Number.isFinite(previousRate))?recentRate-previousRate:null;
  const accelerationRatio=(Number.isFinite(recentRate)&&Number.isFinite(previousRate)&&previousRate>0)?recentRate/previousRate:null;

  // Tiêu chí nội bộ để nhận diện “tăng nhiều/tăng rất nhanh”; không phải ngưỡng quy chuẩn.
  const RISE_M=0.30;
  const FAST_RATE=0.05;
  const VERY_FAST_RATE=0.10;
  const abnormal=[];
  const addAbnormal=x=>{if(x&&!abnormal.includes(x))abnormal.push(x)};
  if(latest&&Number.isFinite(gc)&&latest.value>=gc)addAbnormal(`Mực nước mới nhất ${formatNumber(latest.value)} m chạm/vượt MNDGC ${formatNumber(gc)} m.`);
  else if(max!==null&&Number.isFinite(gc)&&max>=gc)addAbnormal(`Trong kỳ có thời điểm mực nước chạm/vượt MNDGC ${formatNumber(gc)} m.`);
  if(latest&&Number.isFinite(bt)&&latest.value>=bt)addAbnormal(`Mực nước mới nhất không thấp hơn MNDBT ${formatNumber(bt)} m.`);
  else if(max!==null&&Number.isFinite(bt)&&max>=bt)addAbnormal(`Trong kỳ có thời điểm mực nước đạt/vượt MNDBT ${formatNumber(bt)} m.`);

  if(lastInterval&&lastInterval.delta>=RISE_M){
    addAbnormal(`Mực nước tăng nhiều ở 2 lần đo gần nhất: +${formatNumber(lastInterval.delta)} m trong ${formatNumber(lastInterval.hours,1)} giờ.`);
  }
  if(Number.isFinite(recentRate)&&recentRate>=VERY_FAST_RATE){
    addAbnormal(`Tốc độ tăng mực nước rất nhanh: +${formatNumber(recentRate,3)} m/giờ trong khoảng đo gần nhất.`);
  }else if(Number.isFinite(recentRate)&&recentRate>=FAST_RATE){
    addAbnormal(`Tốc độ tăng mực nước nhanh: +${formatNumber(recentRate,3)} m/giờ trong khoảng đo gần nhất.`);
  }
  if(Number.isFinite(accelerationRatio)&&accelerationRatio>=1.5&&recentRate>0){
    addAbnormal(`Tốc độ tăng của khoảng gần nhất cao khoảng ${formatNumber(accelerationRatio,1)} lần khoảng trước; cần theo dõi sát.`);
  }
  for(let i=1;i<pts.length;i++){
    const d=pts[i].value-pts[i-1].value, hours=(pts[i].time-pts[i-1].time)/3600000;
    const isLast=i===pts.length-1;
    const alreadyReportedLastRise=isLast&&lastInterval&&lastInterval.delta>=RISE_M;
    if(hours>0&&Math.abs(d)>=RISE_M&&!alreadyReportedLastRise)addAbnormal(`Biến động đáng chú ý: ${d>=0?'+':''}${formatNumber(d)} m trong ${hours.toFixed(1)} giờ so với lần đo trước.`);
  }

  const trendFor=hours=>{if(!latest)return null;const cutoff=latest.time.getTime()-hours*3600000,a=pts.filter(p=>p.time.getTime()>=cutoff);return a.length>1?latest.value-a[0].value:null};
  const trend24=trendFor(24),trend72=trendFor(72),trend168=trendFor(168);
  const rate24=trend24!==null?trend24/24:null;
  const rainPeak=rainPoints.length?rainPoints.reduce((a,b)=>b.value>a.value?b:a):null;

  let forecast='Chưa đủ dữ liệu để dự kiến xu hướng.';
  const projected={h1:null,h3:null,h6:null,h12:null,h24:null,btHours:null,gcHours:null,rise30Hours:null,rise50Hours:null,trendSlope,trendMethod};
  if(latest&&Number.isFinite(trendSlope)){
    projected.h1=latest.value+trendSlope*1;
    projected.h3=latest.value+trendSlope*3;
    projected.h6=latest.value+trendSlope*6;
    projected.h12=latest.value+trendSlope*12;
    projected.h24=latest.value+trendSlope*24;
    const slopeText=(trendSlope>=0?'+':'')+formatNumber(trendSlope,3)+' m/giờ';
    forecast=`Dựa trên ${trendMethod}, tốc độ xu hướng hiện tại là ${slopeText}. Ngoại suy từ mực nước mới nhất: sau 1 giờ khoảng ${formatNumber(projected.h1)} m; 3 giờ ${formatNumber(projected.h3)} m; 6 giờ ${formatNumber(projected.h6)} m; 12 giờ ${formatNumber(projected.h12)} m; 24 giờ ${formatNumber(projected.h24)} m, nếu xu hướng tiếp tục không đổi.`;
    if(trendSlope>0){
      projected.rise30Hours=0.30/trendSlope;
      projected.rise50Hours=0.50/trendSlope;
      if(Number.isFinite(bt)&&latest.value<bt)projected.btHours=(bt-latest.value)/trendSlope;
      if(Number.isFinite(gc)&&latest.value<gc)projected.gcHours=(gc-latest.value)/trendSlope;
      if(projected.btHours!==null)forecast+=` Thời gian ước tính đến MNDBT: khoảng ${formatNumber(projected.btHours,1)} giờ.`;
      else if(Number.isFinite(bt)&&latest.value>=bt)forecast+=' Mực nước hiện đã ở mức MNDBT hoặc cao hơn.';
      if(projected.gcHours!==null)forecast+=` Thời gian ước tính đến MNDGC: khoảng ${formatNumber(projected.gcHours,1)} giờ.`;
      else if(Number.isFinite(gc)&&latest.value>=gc)forecast+=' Mực nước hiện đã chạm/vượt MNDGC.';
      forecast+=` Nếu tốc độ này tiếp tục, thời gian để mực nước tăng thêm 0,30 m khoảng ${formatNumber(projected.rise30Hours,1)} giờ và thêm 0,50 m khoảng ${formatNumber(projected.rise50Hours,1)} giờ.`;
    }else if(trendSlope<0){
      forecast+=` Xu hướng đang giảm khoảng ${formatNumber(Math.abs(trendSlope),3)} m/giờ; không tính thời gian tiến đến các ngưỡng cao hơn theo xu hướng giảm hiện tại.`;
    }else{
      forecast+=' Xu hướng gần như ổn định; chưa có cơ sở để xác định thời gian tiến đến ngưỡng cao hơn.';
    }
  }

  let assessment='Chưa đủ dữ liệu để đánh giá.';
  if(latest&&Number.isFinite(gc)&&latest.value>=gc)assessment='Mực nước hiện tại chạm/vượt MNDGC; cần tăng cường kiểm tra, theo dõi và thực hiện chế độ vận hành/cảnh báo theo quy trình của công trình.';
  else if(latest&&Number.isFinite(bt)&&latest.value>=bt)assessment='Mực nước hiện tại từ MNDBT trở lên; cần tiếp tục theo dõi diễn biến mực nước và lượng mưa, đồng thời đối chiếu quy trình vận hành.';
  else if(latest&&Number.isFinite(recentRate)&&recentRate>=VERY_FAST_RATE)assessment=`Mực nước đang tăng rất nhanh, tốc độ khoảng ${formatNumber(recentRate,3)} m/giờ theo 2 lần đo gần nhất; cần theo dõi sát và đối chiếu với lượng mưa, vận hành công trình.`;
  else if(latest&&Number.isFinite(recentRate)&&recentRate>=FAST_RATE)assessment=`Mực nước đang tăng nhanh, tốc độ khoảng ${formatNumber(recentRate,3)} m/giờ theo 2 lần đo gần nhất; cần tăng cường theo dõi.`;
  else if(latest)assessment='Mực nước hiện tại thấp hơn MNDBT; tiếp tục theo dõi xu thế mực nước, lượng mưa và các yếu tố vận hành liên quan.';

  return {r,pts,latest,first,bt,gc,totalRain,rainPoints,rainPeak,increase,min,max,maxRise,maxDrop,abnormal,trend24,trend72,trend168,rate24,recent,intervals,lastInterval,prevInterval,recentDelta,recentRate,previousRate,rateChange,accelerationRatio,trendSlope,trendMethod,projected,forecast,assessment,RISE_M,FAST_RATE,VERY_FAST_RATE};
}

function buildQuickReportHtml(){
  if(!currentData||!Array.isArray(currentData.water)||!currentData.water.length)return null
  const a=reportAnalysis(currentData), d=currentData, facility=d.facility||f.value||'Chưa xác định';
  const rainRows=(d.rainfall||[]).map(x=>{
    const name=x.parameter||'Lượng mưa', fromApi=Number((d.rainfallTotalsByParameter||{})[name]);
    const fallback=(Array.isArray(x.data)?x.data:[]).reduce((sum,p)=>sum+(Number(p.value)||0),0);
    return {name,total:Number.isFinite(fromApi)?fromApi:fallback};
  }).filter(x=>Number.isFinite(x.total));
  const abnormalHtml=a.abnormal.length?a.abnormal.map(x=>`<li>${escapeHtml(x)}</li>`).join(''):'<li>Không phát hiện bất thường rõ rệt theo các tiêu chí tự động của báo cáo trong khoảng dữ liệu đã chọn.</li>';
  const marginBT=a.latest&&Number.isFinite(a.bt)?a.latest.value-a.bt:null, marginGC=a.latest&&Number.isFinite(a.gc)?a.latest.value-a.gc:null;
  const rainTable=rainRows.length?`<table><tr><th>Chuỗi mưa</th><th>Tổng lượng mưa</th></tr>${rainRows.map(x=>`<tr><td>${escapeHtml(x.name)}</td><td>${formatNumber(x.total)} mm</td></tr>`).join('')}</table>`:'<p>Chưa có số liệu tổng hợp lượng mưa theo chuỗi.</p>';
  const trendText=x=>x===null?'—':(x>=0?'+':'')+formatNumber(x)+' m';
  const peakRain=a.rainPeak?`${formatNumber(a.rainPeak.value)} mm tại ${fmtReportDate(a.rainPeak.time)}`:'—';
  const latestText=a.latest?`${formatNumber(a.latest.value)} m tại ${fmtReportDate(a.latest.time)}`:'—';
  const firstText=a.first?`${formatNumber(a.first.value)} m tại ${fmtReportDate(a.first.time)}`:'—';
  const html=`<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;font-size:11pt;line-height:1.45;color:#111}h1{text-align:center;font-size:17pt;margin:0 0 8px}h2{font-size:13pt;margin:16px 0 6px;border-bottom:1px solid #777;padding-bottom:3px}p{margin:5px 0}table{border-collapse:collapse;width:100%;margin:7px 0}th,td{border:1px solid #777;padding:6px;text-align:left;vertical-align:top}th{font-weight:bold;background:#eee}.meta td:first-child{width:28%;font-weight:bold}.note{font-style:italic;color:#444}.footer{margin-top:22px;font-size:9pt;color:#555}</style></head><body>
<h1>BÁO CÁO NHANH DIỄN BIẾN MỰC NƯỚC – LƯỢNG MƯA</h1>
<table class="meta"><tr><td>Công trình</td><td>${escapeHtml(facility)}</td></tr><tr><td>Thời gian</td><td>Từ ${escapeHtml(a.r.from)} đến ${escapeHtml(a.r.to)}</td></tr><tr><td>Ngày lập báo cáo</td><td>${fmtReportDate(new Date())}</td></tr></table>
<h2>1. Tổng hợp số liệu quan trắc</h2><table><tr><th>Nội dung</th><th>Kết quả</th></tr>
<tr><td>Số lần đo mực nước</td><td>${a.pts.length} lần</td></tr><tr><td>Mực nước đầu kỳ</td><td>${escapeHtml(firstText)}</td></tr><tr><td>Mực nước mới nhất</td><td>${escapeHtml(latestText)}</td></tr><tr><td>Mực nước thấp nhất</td><td>${a.min!==null?formatNumber(a.min)+' m':'—'}</td></tr><tr><td>Mực nước cao nhất</td><td>${a.max!==null?formatNumber(a.max)+' m':'—'}</td></tr><tr><td>Biến động đầu kỳ → cuối kỳ</td><td>${trendText(a.increase)}</td></tr><tr><td>Tăng lớn nhất giữa hai lần đo</td><td>${a.maxRise!==null?trendText(a.maxRise):'—'}</td></tr><tr><td>Giảm lớn nhất giữa hai lần đo</td><td>${a.maxDrop!==null?trendText(a.maxDrop):'—'}</td></tr><tr><td>2–3 lần đo gần nhất</td><td>${a.recent.length} lần đo · Phân tích theo thời gian thực giữa các lần đo</td></tr><tr><td>Tăng/giảm lần đo gần nhất</td><td>${a.recentDelta!==null?trendText(a.recentDelta)+' trong '+formatNumber(a.lastInterval.hours,1)+' giờ':'—'}</td></tr><tr><td>Tốc độ biến đổi gần nhất</td><td>${a.recentRate!==null?(a.recentRate>=0?'+':'')+formatNumber(a.recentRate,3)+' m/giờ':'—'}</td></tr><tr><td>Tốc độ xu hướng 2–3 lần đo</td><td>${a.trendSlope!==null?(a.trendSlope>=0?'+':'')+formatNumber(a.trendSlope,3)+' m/giờ':'—'} · ${escapeHtml(a.trendMethod)}</td></tr><tr><td>Xu hướng 24 giờ</td><td>${trendText(a.trend24)}</td></tr><tr><td>Xu hướng 3 ngày</td><td>${trendText(a.trend72)}</td></tr><tr><td>Xu hướng 7 ngày</td><td>${trendText(a.trend168)}</td></tr><tr><td>Tổng lượng mưa</td><td>${formatNumber(a.totalRain)} mm</td></tr><tr><td>Lượng mưa lớn nhất ghi nhận</td><td>${escapeHtml(peakRain)}</td></tr></table>
<h2>2. So sánh mực nước với MNDBT, MNDGC</h2><table><tr><th>Ngưỡng</th><th>Giá trị</th><th>Chênh lệch với H mới nhất</th><th>Đánh giá</th></tr>
<tr><td>MNDBT</td><td>${Number.isFinite(a.bt)?formatNumber(a.bt)+' m':'—'}</td><td>${marginBT!==null?(marginBT>=0?'+':'')+formatNumber(marginBT)+' m':'—'}</td><td>${a.latest&&Number.isFinite(a.bt)?(a.latest.value>=a.bt?'Đạt/vượt MNDBT':'Thấp hơn MNDBT'):'Chưa đủ dữ liệu'}</td></tr>
<tr><td>MNDGC</td><td>${Number.isFinite(a.gc)?formatNumber(a.gc)+' m':'—'}</td><td>${marginGC!==null?(marginGC>=0?'+':'')+formatNumber(marginGC)+' m':'—'}</td><td>${a.latest&&Number.isFinite(a.gc)?(a.latest.value>=a.gc?'CHẠM/VƯỢT MNDGC':'Chưa vượt MNDGC'):'Chưa đủ dữ liệu'}</td></tr></table>
<h2>3. Tổng hợp lượng mưa</h2>${rainTable}
<h2>4. Phân tích tốc độ tăng và thời gian đến ngưỡng</h2><table><tr><th>Nội dung</th><th>Kết quả</th></tr><tr><td>Khoảng đo gần nhất</td><td>${a.lastInterval?`${fmtReportDate(a.lastInterval.from.time)} → ${fmtReportDate(a.lastInterval.to.time)}`:'—'}</td></tr><tr><td>Mực nước tăng/giảm</td><td>${a.recentDelta!==null?trendText(a.recentDelta):'—'}</td></tr><tr><td>Thời gian giữa 2 lần đo</td><td>${a.lastInterval?formatNumber(a.lastInterval.hours,1)+' giờ':'—'}</td></tr><tr><td>Tốc độ gần nhất</td><td>${a.recentRate!==null?(a.recentRate>=0?'+':'')+formatNumber(a.recentRate,3)+' m/giờ':'—'}</td></tr><tr><td>Xu hướng từ 2–3 lần đo</td><td>${a.trendSlope!==null?(a.trendSlope>=0?'+':'')+formatNumber(a.trendSlope,3)+' m/giờ · '+escapeHtml(a.trendMethod):'—'}</td></tr><tr><td>Dự kiến H sau 6 giờ</td><td>${a.projected.h6!==null?formatNumber(a.projected.h6)+' m':'—'}</td></tr><tr><td>Dự kiến H sau 12 giờ</td><td>${a.projected.h12!==null?formatNumber(a.projected.h12)+' m':'—'}</td></tr><tr><td>Dự kiến H sau 24 giờ</td><td>${a.projected.h24!==null?formatNumber(a.projected.h24)+' m':'—'}</td></tr><tr><td>Thời gian để tăng thêm 0,30 m</td><td>${a.projected.rise30Hours!==null?formatNumber(a.projected.rise30Hours,1)+' giờ':'—'}</td></tr><tr><td>Thời gian để tăng thêm 0,50 m</td><td>${a.projected.rise50Hours!==null?formatNumber(a.projected.rise50Hours,1)+' giờ':'—'}</td></tr><tr><td>Thời gian ước tính đến MNDBT</td><td>${a.projected.btHours!==null?formatNumber(a.projected.btHours,1)+' giờ':'—'}</td></tr><tr><td>Thời gian ước tính đến MNDGC</td><td>${a.projected.gcHours!==null?formatNumber(a.projected.gcHours,1)+' giờ':'—'}</td></tr></table><h2>5. Bất thường / điểm cần chú ý</h2><ul>${abnormalHtml}</ul>
<h2>6. Nhận định kỹ thuật</h2><p>${escapeHtml(a.assessment)}</p>
<h2>7. Dự kiến diễn biến</h2><p>${escapeHtml(a.forecast)}</p>
<h2>8. Kiến nghị theo dõi</h2><ul><li>Tiếp tục cập nhật mực nước và lượng mưa theo tần suất quy định của công trình.</li><li>Đối chiếu diễn biến với MNDBT, MNDGC và quy trình vận hành hồ/công trình hiện hành.</li><li>Nếu mực nước tăng nhanh, tiến sát/vượt ngưỡng hoặc lượng mưa tăng mạnh, tăng cường theo dõi và thực hiện chế độ báo cáo/cảnh báo theo quy định.</li><li>Đánh giá đồng thời lượng mưa, xu thế mực nước và tình trạng vận hành trước khi quyết định điều hành.</li></ul>
<p class="note">Lưu ý: Phần dự kiến sử dụng 2–3 lần đo gần nhất, tính đúng khoảng thời gian giữa các lần đo và tốc độ biến đổi mực nước (m/giờ); với 3 điểm, tốc độ xu hướng được ước tính bằng hồi quy tuyến tính theo thời gian. Khi tính thời gian đến MNDBT/MNDGC, đây là ngoại suy ra ngoài khoảng quan trắc, không phải nội suy toán học. Kết quả chỉ mang tính tham khảo, không phải dự báo khí tượng thủy văn chính thức và không thay thế quy trình vận hành hoặc quyết định của người có thẩm quyền. Tiêu chí nội bộ: tăng ≥ 0,30 m giữa 2 lần đo là “tăng nhiều”; tốc độ ≥ 0,05 m/giờ là “tăng nhanh”; ≥ 0,10 m/giờ là “tăng rất nhanh”. Các tiêu chí này cần hiệu chỉnh theo đặc điểm từng công trình, không phải ngưỡng quy chuẩn.</p>
<div class="footer">THUY LOI AI · Báo cáo nhanh tự động từ dữ liệu đang hiển thị trên Dashboard.</div>
</body></html>`;
  return html;
}

function reportPlainTextFromHtml(html){
  const box=document.createElement('div');
  box.innerHTML=html;
  return (box.innerText||box.textContent||'').trim();
}
function reportFileName(){
  const facility=currentData?.facility||f.value||'Cong_trinh';
  return `Bao_cao_nhanh_${String(facility).replace(/[^a-zA-Z0-9À-ỹ _-]/g,'_')}_${exportFileStamp()}.doc`;
}
function requireQuickReport(){
  const html=buildQuickReportHtml();
  if(!html){alert('Chưa có dữ liệu mực nước để lập Báo cáo nhanh.');return null}
  return html;
}
function previewQuickReport(){
  const html=requireQuickReport();if(!html)return;
  document.getElementById('reportPreview').innerHTML=html;
  document.getElementById('reportModal').classList.add('show');
  document.body.style.overflow='hidden';
}
function closeReportPreview(){
  document.getElementById('reportModal').classList.remove('show');
  document.body.style.overflow='';
}
async function shareQuickReportZalo(){
  const html=requireQuickReport();if(!html)return;
  const text=reportPlainTextFromHtml(html);
  const file=new File(['\ufeff',html],reportFileName(),{type:'application/msword'});
  try{
    if(navigator.share){
      if(navigator.canShare&&navigator.canShare({files:[file]})){
        await navigator.share({title:'Báo cáo nhanh - '+(currentData?.facility||f.value||''),text:'Báo cáo nhanh Thủy lợi',files:[file]});
        return;
      }
      await navigator.share({title:'Báo cáo nhanh - '+(currentData?.facility||f.value||''),text:text.slice(0,6000)});
      return;
    }
  }catch(err){
    if(err&&err.name==='AbortError')return;
    console.warn('Không thể dùng Web Share:',err);
  }
  try{
    if(navigator.clipboard)await navigator.clipboard.writeText(text.slice(0,10000));
  }catch(err){console.warn('Clipboard không khả dụng:',err)}
  alert('Đã chuẩn bị nội dung Báo cáo nhanh. Hãy chọn Zalo trong bảng Chia sẻ; nếu trình duyệt không hỗ trợ chia sẻ tệp, nội dung báo cáo đã được sao chép để bạn dán vào Zalo.');
  window.open('https://chat.zalo.me/','_blank','noopener,noreferrer');
}
function downloadQuickReportWord(){
  const html=requireQuickReport();if(!html)return;
  const blob=new Blob(['\ufeff',html],{type:'application/msword;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=reportFileName();document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
// Giữ tên hàm cũ để không phá các tích hợp/onclick cũ nếu còn tồn tại.
function exportQuickReportWord(){downloadQuickReportWord()}
const reportModalEl=document.getElementById('reportModal');
if(reportModalEl)reportModalEl.addEventListener('click',e=>{if(e.target.id==='reportModal')closeReportPreview()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeReportPreview()});

function renderTechnicalSummary(data,series){
  const latest=series.length?series[series.length-1]:null,previous=series.length>1?series[series.length-2]:null;
  const bt=Number(data.limits&&data.limits.mndbt),gc=Number(data.limits&&data.limits.mndgc),h=latest?Number(latest.value):null,delta=latest&&previous?h-Number(previous.value):null;
  let relation='Chưa đủ dữ liệu để so sánh';
  if(Number.isFinite(h)&&Number.isFinite(bt)&&Number.isFinite(gc))relation=h<bt?'Mực nước đang thấp hơn MNDBT':h<=gc?'Mực nước nằm từ MNDBT đến MNDGC':'Mực nước cao hơn MNDGC';
  else if(Number.isFinite(h)&&Number.isFinite(bt))relation=h<bt?'Mực nước đang thấp hơn MNDBT':'Mực nước không thấp hơn MNDBT';
  const rain=(data.rainfall||[]).filter(x=>Array.isArray(x.data)&&x.data.length);
  const rainChips=rain.map(x=>'<span class="chip"><b>'+escapeHtml(x.parameter)+'</b>: '+formatNumber((data.rainfallTotalsByParameter||{})[x.parameter])+' mm</span>').join('');
  technicalSummary.innerHTML='<div class="summary-grid">'+
    '<div class="summary-item"><div class="summary-label">↕ SO VỚI MNDBT</div><div class="summary-value">'+(Number.isFinite(h)&&Number.isFinite(bt)?formatNumber(h-bt)+' m':'—')+'</div><div class="summary-note">'+relation+'</div></div>'+
    '<div class="summary-item"><div class="summary-label">⚑ SO VỚI MNDGC</div><div class="summary-value">'+(Number.isFinite(h)&&Number.isFinite(gc)?formatNumber(h-gc)+' m':'—')+'</div><div class="summary-note">'+(Number.isFinite(h)&&Number.isFinite(gc)?(h<=gc?'Chưa vượt MNDGC':'Đã vượt MNDGC'):'Chưa đủ giới hạn')+'</div></div>'+
    '<div class="summary-item"><div class="summary-label">≈ BIẾN ĐỘNG GẦN NHẤT</div><div class="summary-value">'+(delta!==null?(delta>=0?'+':'')+formatNumber(delta)+' m':'—')+'</div><div class="summary-note">'+(delta!==null?'So với lần đo liền trước':'Chưa đủ 2 lần đo')+'</div></div></div>'+
    renderTrendHtml(series)+(rainChips?'<div class="summary-label" style="margin-top:16px">LƯỢNG MƯA THEO TỪNG CHUỖI</div><div class="chips" style="margin-top:8px">'+rainChips+'</div>':'');
}
function renderTrendHtml(series){
  const points=series.map(p=>({time:parseDataTime(p.time),value:Number(p.value)})).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime())).sort((a,b)=>a.time-b.time);
  if(!points.length)return '';
  const latest=points[points.length-1];
  function stats(hours){const st=new Date(latest.time-hours*3600000),a=points.filter(p=>p.time>=st&&p.time<=latest.time);if(a.length<2)return null;return {d:latest.value-a[0].value,n:a.length,min:Math.min(...a.map(x=>x.value)),max:Math.max(...a.map(x=>x.value))}}
  const arr=[['24 GIỜ',stats(24),'↕'],['3 NGÀY',stats(72),'↗'],['7 NGÀY',stats(168),'〰']];
  const cards=arr.map(([label,x,icon])=>x?`<div class="trend-item"><div class="trend-icon">${icon}</div><div class="summary-label">${label}</div><div class="trend-value">${x.d>=0?'+':''}${formatNumber(x.d)} m</div><div class="trend-note">${x.d>0?'Tăng':x.d<0?'Giảm':'Ổn định'} · ${x.n} lần đo</div></div>`:`<div class="trend-item"><div class="trend-icon">—</div><div class="summary-label">${label}</div><div class="trend-value">—</div><div class="trend-note">Chưa đủ 2 lần đo</div></div>`).join('');
  return '<div class="summary-label" style="margin-top:16px">XU HƯỚNG MỰC NƯỚC</div><div class="trend-grid">'+cards+
    '<div class="trend-item"><div class="trend-icon">↓</div><div class="summary-label">THẤP NHẤT</div><div class="trend-value">'+formatNumber(Math.min(...points.map(p=>p.value)))+' m</div><div class="trend-note">Trong khoảng đang chọn</div></div>'+
    '<div class="trend-item"><div class="trend-icon">↑</div><div class="summary-label">CAO NHẤT</div><div class="trend-value">'+formatNumber(Math.max(...points.map(p=>p.value)))+' m</div><div class="trend-note">Trong khoảng đang chọn</div></div></div>'
}

function renderHydroChart(data){
  // ============================================================
  // THỦY LỢI AI — BIỂU ĐỒ DIỄN BIẾN V2.5
  //
  // X-axis : thời gian quan trắc thực
  // Y-left : HTL (m), MNDBT, MNDGC
  // Y-right: X (mm)
  //
  // HTL    : line + fill nền nhạt, KHÔNG làm mượt / KHÔNG nối
  //          xuyên qua điểm dữ liệu bị thiếu.
  // Mưa    : bar đỏ đơn sắc, alpha 0.25 -> 0.95 theo giá trị.
  //          Cột mảnh: barPercentage 0.5, categoryPercentage 0.7.
  // Tooltip: mode=index, intersect=false.
  // Mobile : full-bleed, legend gọn phía dưới.
  // ============================================================

  const waterSeries=normalizeSeries(data?.water);
  const waterPts=waterSeries
    .map(p=>({x:p.time.getTime(),y:Number(p.value)}))
    .filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));

  const rain=[];
  (Array.isArray(data?.rainfall)?data.rainfall:[]).forEach(series=>{
    const pts=normalizeSeries(series?.data);
    pts.forEach(p=>{
      const x=p.time.getTime();
      const y=Number(p.value);
      if(Number.isFinite(x)&&Number.isFinite(y)){
        rain.push({
          x:x,
          y:y,
          parameter:series?.parameter||'X (mm)'
        });
      }
    });
  });

  // Giữ đúng thứ tự thời gian, đồng thời bảo vệ dữ liệu bất thường.
  waterPts.sort((a,b)=>a.x-b.x);
  rain.sort((a,b)=>a.x-b.x);

  const mndbt=Number(data?.limits?.mndbt);
  const mndgc=Number(data?.limits?.mndgc);

  if(hydroChart){
    hydroChart.destroy();
    hydroChart=null;
  }

  const canvas=document.getElementById('hydroChart');
  if(!canvas)return;

  // Không vẽ chart rỗng.
  if(!waterPts.length&&!rain.length)return;

  const dark=document.documentElement.classList.contains('dark');
  const textColor=dark?'#a9b8c9':'#657286';
  const gridColor=dark?'rgba(170,195,220,.11)':'rgba(50,85,120,.10)';

  // ------------------------------------------------------------
  // Trục thời gian: lấy đúng min/max của dữ liệu thực.
  // ------------------------------------------------------------
  const allTimes=waterPts.map(p=>p.x).concat(rain.map(p=>p.x));
  const minX=Math.min(...allTimes);
  const maxX=Math.max(...allTimes);
  const sameTimeRange=minX===maxX;

  // Nếu chỉ có một mốc, mở rộng nhẹ trục X để chart không co về một điểm.
  const xMin=sameTimeRange?minX-30*60*1000:minX;
  const xMax=sameTimeRange?maxX+30*60*1000:maxX;

  // ------------------------------------------------------------
  // Lượng mưa: toàn bộ tông đỏ.
  // Alpha tăng tuyến tính 0.25 -> 0.95 theo giá trị thực.
  // ------------------------------------------------------------
  const maxRain=rain.length
    ?Math.max(...rain.map(p=>Math.max(0,Number(p.y)||0)))
    :0;

  function rainAlpha(value){
    const v=Math.max(0,Number(value)||0);
    if(maxRain<=0)return 0.25;
    const ratio=Math.max(0,Math.min(1,v/maxRain));
    return 0.25+(0.70*ratio);
  }

  function rainBackground(value){
    return `rgba(185,28,28,${rainAlpha(value).toFixed(3)})`;
  }

  function rainBorder(value){
    const v=Math.max(0,Number(value)||0);
    const ratio=maxRain>0?Math.min(1,v/maxRain):0;
    const alpha=0.45+(0.45*ratio);
    return `rgba(150,20,20,${alpha.toFixed(3)})`;
  }

  const rainBackgroundColors=rain.map(p=>rainBackground(p.y));
  const rainBorderColors=rain.map(p=>rainBorder(p.y));

  // ------------------------------------------------------------
  // Độ rộng cột:
  // barPercentage/categoryPercentage là yêu cầu chính.
  // Không ép barThickness để Chart.js tự tính theo trục thời gian.
  // ------------------------------------------------------------
  const isMobile=window.matchMedia?.('(max-width:720px)').matches;
  const isSmall=window.matchMedia?.('(max-width:430px)').matches;

  const datasets=[];

  // ------------------------------------------------------------
  // X (mm) — TRỤC PHẢI.
  // ------------------------------------------------------------
  if(rain.length){
    datasets.push({
      type:'bar',
      label:'X (mm)',
      data:rain,
      yAxisID:'rain',
      backgroundColor:rainBackgroundColors,
      borderColor:rainBorderColors,
      borderWidth:1,
      barPercentage:0.5,
      categoryPercentage:0.7,
      borderRadius:isMobile?1:2,
      maxBarThickness:isSmall?14:(isMobile?18:24)
    });
  }

  // ------------------------------------------------------------
  // HTL — TRỤC TRÁI + FILL.
  // ------------------------------------------------------------
  if(waterPts.length){
    datasets.push({
      type:'line',
      label:'HTL (m)',
      data:waterPts,
      yAxisID:'water',
      borderColor:dark?'#1db9e8':'#0878c9',
      backgroundColor:dark
        ?'rgba(29,185,232,0.10)'
        :'rgba(8,120,201,0.10)',
      fill:true,
      borderWidth:isMobile?2.5:3,
      pointRadius:isSmall?1.8:(isMobile?2.2:3),
      pointHoverRadius:isMobile?5:6,
      pointHitRadius:12,
      tension:0,
      spanGaps:false,
      stepped:false
    });
  }

  // ------------------------------------------------------------
  // MNDBT — đường ngang đứt nét.
  // ------------------------------------------------------------
  if(Number.isFinite(mndbt)){
    datasets.push({
      type:'line',
      label:'MNDBT',
      data:[
        {x:xMin,y:mndbt},
        {x:xMax,y:mndbt}
      ],
      yAxisID:'water',
      borderColor:dark?'#ffb52e':'#d58900',
      backgroundColor:'transparent',
      borderWidth:isMobile?1.5:2,
      borderDash:[7,5],
      pointRadius:0,
      pointHitRadius:0,
      tension:0,
      spanGaps:true,
      fill:false
    });
  }

  // ------------------------------------------------------------
  // MNDGC — đường ngang đứt nét.
  // ------------------------------------------------------------
  if(Number.isFinite(mndgc)){
    datasets.push({
      type:'line',
      label:'MNDGC',
      data:[
        {x:xMin,y:mndgc},
        {x:xMax,y:mndgc}
      ],
      yAxisID:'water',
      borderColor:dark?'#ff624d':'#df4930',
      backgroundColor:'transparent',
      borderWidth:isMobile?1.5:2,
      borderDash:[5,5],
      pointRadius:0,
      pointHitRadius:0,
      tension:0,
      spanGaps:true,
      fill:false
    });
  }

  hydroChart=new Chart(canvas,{
    data:{datasets:datasets},

    options:{
      responsive:true,
      maintainAspectRatio:false,
      animation:false,
      parsing:false,
      normalized:true,

      // Tooltip: cùng mốc thời gian, không cần click đúng vào điểm.
      interaction:{
        mode:'index',
        intersect:false,
        axis:'x'
      },

      layout:{
        padding:{
          top:isMobile?2:4,
          right:isMobile?2:8,
          bottom:0,
          left:isMobile?2:8
        }
      },

      plugins:{
        legend:{
          display:true,
          position:'bottom',
          align:'center',
          labels:{
            color:textColor,
            usePointStyle:true,
            pointStyle:'line',
            boxWidth:isSmall?8:(isMobile?10:14),
            boxHeight:isSmall?6:(isMobile?7:9),
            padding:isSmall?5:(isMobile?7:13),
            font:{
              size:isSmall?8:(isMobile?9:11),
              weight:'700'
            },
            filter(item){
              return ['HTL (m)','X (mm)','MNDBT','MNDGC'].includes(item.text);
            }
          },
          onClick:null
        },

        tooltip:{
          enabled:true,
          mode:'index',
          intersect:false,
          displayColors:true,
          padding:isSmall?7:(isMobile?8:10),
          titleMarginBottom:5,
          boxPadding:3,
          callbacks:{
            title(items){
              const x=items?.[0]?.parsed?.x;
              if(!Number.isFinite(x))return '';
              return new Date(x).toLocaleString('vi-VN',{
                day:'2-digit',
                month:'2-digit',
                year:'numeric',
                hour:'2-digit',
                minute:'2-digit'
              });
            },

            label(ctx){
              const y=Number(ctx.parsed?.y);
              if(!Number.isFinite(y)){
                return `${ctx.dataset.label}: —`;
              }

              if(ctx.dataset.yAxisID==='rain'){
                return `X (mm): ${formatNumber(y)} mm`;
              }

              return `${ctx.dataset.label}: ${formatNumber(y)} m`;
            }
          }
        }
      },

      scales:{
        // --------------------------------------------------------
        // X — THỜI GIAN QUAN TRẮC.
        // --------------------------------------------------------
        x:{
          type:'time',
          min:xMin,
          max:xMax,
          offset:false,

          time:{
            tooltipFormat:'dd/MM/yyyy HH:mm',
            displayFormats:{
              minute:'HH:mm',
              hour:'HH:mm',
              day:'dd/MM'
            }
          },

          ticks:{
            color:textColor,
            autoSkip:true,
            maxRotation:0,
            minRotation:0,
            maxTicksLimit:isSmall?5:(isMobile?6:8),
            padding:isSmall?2:4,
            font:{
              size:isSmall?8:(isMobile?8.5:10)
            },

            callback(value){
              const d=new Date(Number(value));
              if(!Number.isFinite(d.getTime()))return '';

              if(periodDays()<=1){
                return d.toLocaleString('vi-VN',{
                  day:'2-digit',
                  month:'2-digit',
                  hour:'2-digit'
                });
              }

              return d.toLocaleDateString('vi-VN',{
                day:'2-digit',
                month:'2-digit'
              });
            }
          },

          grid:{
            color:gridColor,
            drawTicks:false
          },

          title:{
            display:!isSmall,
            text:'Thời gian quan trắc',
            color:textColor,
            font:{
              size:10,
              weight:'700'
            }
          }
        },

        // --------------------------------------------------------
        // Y TRÁI — MỰC NƯỚC.
        // --------------------------------------------------------
        water:{
          type:'linear',
          position:'left',
          beginAtZero:false,

          title:{
            display:true,
            text:'HTL (m)',
            color:textColor,
            font:{
              size:isMobile?9.5:11,
              weight:'700'
            }
          },

          ticks:{
            color:textColor,
            maxTicksLimit:isMobile?6:8,
            padding:isSmall?2:4,
            font:{
              size:isMobile?8.5:10
            },
            callback(value){
              return formatNumber(value);
            }
          },

          grid:{
            color:gridColor,
            drawOnChartArea:true,
            drawTicks:false
          }
        },

        // --------------------------------------------------------
        // Y PHẢI — LƯỢNG MƯA.
        // --------------------------------------------------------
        rain:{
          type:'linear',
          position:'right',
          beginAtZero:true,

          title:{
            display:true,
            text:'X (mm)',
            color:textColor,
            font:{
              size:isMobile?9.5:11,
              weight:'700'
            }
          },

          ticks:{
            color:textColor,
            maxTicksLimit:isMobile?5:7,
            padding:isSmall?2:4,
            font:{
              size:isMobile?8.5:10
            },
            callback(value){
              return formatNumber(value);
            }
          },

          grid:{
            drawOnChartArea:false,
            drawTicks:false
          }
        }
      }
    }
  });
}

function fitChart(){if(currentData)renderHydroChart(currentData)}

async function loadFacilities(){
  f.disabled=true;f.innerHTML='<option value="">⏳ Đang tải công trình...</option>';
  try{
    const result=await fetchJson('/api/facilities',{},2);
    let facilities=Array.isArray(result.data)?result.data:[];
    facilities=facilities.map(x=>{
      if(typeof x==='string')return x;
      if(x&&typeof x==='object')return x.name||x.facility||x['CÔNG TRÌNH']||x['Công trình']||x.value||'';
      return '';
    }).filter(Boolean);
    f.innerHTML='<option value="">Chọn công trình...</option>';
    facilities.forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name;f.appendChild(option)});
    if(!facilities.length){
      f.innerHTML='<option value="">Không có công trình</option>';
      resetData('Google Sheet đã kết nối nhưng không trả về danh sách công trình.');
      return;
    }
    /* V1.8: tự chọn công trình đầu tiên để chuỗi dữ liệu chạy hoàn chỉnh ngay sau khi kết nối. */
    f.value=facilities[0];setSelectedFacility();resetData('Đang tải dữ liệu thực tế...');
    await loadParameters();await loadChartData();
  }catch(err){
    console.error(err);
    f.innerHTML='<option value="">🔴 Mất kết nối Google Sheet</option>';
    setDataError(err.message||'Không tải được danh sách công trình.');
  }finally{f.disabled=false}
}
f.addEventListener('change',async()=>{setSelectedFacility();resetData('Đang tải dữ liệu thực tế...');await loadParameters();await loadChartData()});
async function refreshModule(){if(!f.value){setSelectedFacility();resetData();return}setSelectedFacility();await loadParameters();await loadChartData()}
selectedQuickPeriod='7d';
setQuickButtonsActive('7d');
period.value=quickPeriodMeta('7d').value;
loadFacilities();
</script>
<div id="reportModal" class="report-modal" role="dialog" aria-modal="true" aria-labelledby="reportModalTitle">
  <div class="report-modal-card">
    <div class="report-modal-head"><span id="reportModalTitle">📄 Xem trước Báo cáo nhanh</span><button class="report-close" onclick="closeReportPreview()">✕</button></div>
    <div id="reportPreview" class="report-preview"></div>
  </div>
</div>
</body>
</html>
'''

@app.get("/api/facilities")
def api_facilities():
    try:
        rows=_data_rows(); seen=[]; seen_set=set()
        for row in rows:
            name=_row_facility(row)
            if name and name not in seen_set:
                seen.append(name);seen_set.add(name)
        return {"ok":True,"source":"google_sheets","sheet":GOOGLE_SHEET_NAME,"data":seen}
    except RuntimeError as exc:
        return JSONResponse(status_code=502,content={"ok":False,"source":"google_sheets","error":str(exc)})

@app.get("/api/parameters")
def api_parameters(facility: str):
    try:
        rows=[r for r in _data_rows() if _row_facility(r)==facility]
        water=[];rain=[];other=[]
        for r in rows:
            p=_row_parameter(r)
            if not p: continue
            code=_classify_parameter(p)
            target=water if code in {"WATER_LEVEL","WATER_LEVEL_UPSTREAM","WATER_LEVEL_DOWNSTREAM"} else rain if code in {"RAINFALL","RAINFALL_T1","RAINFALL_C24"} else other
            if p not in target: target.append(p)
        return {"ok":True,"source":"google_sheets","data":{"waterLevel":water,"rainfall":rain,"other":other}}
    except RuntimeError as exc:
        return JSONResponse(status_code=502,content={"ok":False,"source":"google_sheets","error":str(exc)})

@app.get("/api/chart")
def api_chart(facility: str, year: int=2026, days: int=7, hours: int=0, waterParameter: str="", rainfallParameters: str="", fromDate: str="", toDate: str=""):
    try:
        data=_build_chart(facility,year,days,fromDate,toDate,hours)
        # Nếu client chỉ yêu cầu một tên mực nước cụ thể và tên đó tồn tại, dùng tên đó.
        if waterParameter:
            rows=[r for r in _data_rows() if _row_facility(r)==facility and _row_parameter(r)==waterParameter]
            if rows:
                facility_rows=[r for r in _data_rows() if _row_facility(r)==facility]
                latest=None
                for r in facility_rows:
                    dt=_row_datetime(r,year)
                    if dt and (latest is None or dt>latest):
                        latest=dt
                cutoff=latest-timedelta(hours=int(hours)) if latest and not (fromDate or toDate) and hours else None

                def selected_window(dt):
                    if not dt:return False
                    if fromDate or toDate:return _date_filter(dt,fromDate,toDate)
                    if cutoff is not None:return cutoff<=dt<=latest
                    if days and days>0 and latest is not None:
                        return latest-timedelta(days=int(days))<=dt<=latest
                    return True

                pts=[]
                for r in rows:
                    dt=_row_datetime(r,year); value=_row_value(r)
                    if dt and value is not None and selected_window(dt):
                        pts.append({"time":dt.isoformat(),"value":value})
                pts.sort(key=lambda x:x["time"])
                data["waterParameter"]=waterParameter
                data["water"]=pts
        return {"ok":True,"source":"google_sheets","data":data}
    except RuntimeError as exc:
        return JSONResponse(status_code=502,content={"ok":False,"source":"google_sheets","error":str(exc)})

@app.get("/api/connection")
def api_connection():
    started=monotonic()
    try:
        rows=_data_rows(); elapsed=round((monotonic()-started)*1000)
        return {"ok":True,"google_sheets_ok":True,"google_sheets_ms":elapsed,"rows":len(rows),"sheet":GOOGLE_SHEET_NAME,"range":GOOGLE_SHEETS_RANGE,"message":"Đọc trực tiếp Google Sheet thành công."}
    except RuntimeError as exc:
        elapsed=round((monotonic()-started)*1000)
        return {"ok":True,"google_sheets_ok":False,"google_sheets_ms":elapsed,"rows":0,"google_sheets_error":str(exc),"message":"FastAPI hoạt động nhưng Google Sheet chưa thể truy cập."}

@app.get("/", response_class=HTMLResponse)
def technical_dashboard(): return HTML

@app.get("/health")
def health():
    return {"module":"technical_module","version":"2.7.0","status":"ok","stage":7,"mode":"direct_google_sheets","sheet":GOOGLE_SHEET_NAME}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8001")),reload=False)
