# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from time import monotonic

# ============================================================
# THUY LOI AI - TECHNICAL MODULE V1.8
# BUOC 1: GIAO DIEN DOC LAP
# Khong import, khong sua server.py
# ============================================================

app = FastAPI(
    title="THUY LOI AI - Thong so ky thuat",
    version="1.8.0",
)

# ============================================================
# BƯỚC 3.2 - KẾT NỐI APPS SCRIPT API
# Chỉ đọc dữ liệu. Không ghi/sửa/xóa AI_DATA.
# Backend proxy giúp trình duyệt không phải gọi trực tiếp
# Apps Script, tránh vấn đề CORS.
# ============================================================
APPS_SCRIPT_API_URL = os.getenv(
    "APPS_SCRIPT_API_URL",
    "https://script.google.com/macros/s/AKfycbzP3yXgeBs0WDuvQdrYa4ptJSeK9cHnCe0lrM78pR1WVohagQyOjn8LFtBB7QhmltWupQ/exec"
)
APPS_SCRIPT_TIMEOUT = float(os.getenv("APPS_SCRIPT_TIMEOUT", "12"))

def _safe_error_message(exc):
    if isinstance(exc, HTTPError):
        return f"Apps Script HTTP {exc.code}: {exc.reason or 'Upstream trả lỗi HTTP.'}"
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        return f"Không kết nối được Apps Script: {reason or 'lỗi mạng/DNS.'}"
    if isinstance(exc, TimeoutError):
        return f"Apps Script timeout sau {APPS_SCRIPT_TIMEOUT:g} giây."
    if isinstance(exc, json.JSONDecodeError):
        return "Apps Script trả về dữ liệu không phải JSON hợp lệ."
    return str(exc) or "Lỗi không xác định khi gọi Apps Script."

def fetch_apps_script_api_(api, params=None):
    query = {"api": api}
    if params:
        query.update({k: v for k, v in params.items() if v not in (None, "")})
    url = APPS_SCRIPT_API_URL + "?" + urlencode(query)
    req = Request(
        url,
        headers={
            "User-Agent": "THUY-LOI-AI-Technical/1.8",
            "Accept": "application/json,text/plain,*/*",
            "Cache-Control": "no-cache",
        }
    )
    try:
        with urlopen(req, timeout=APPS_SCRIPT_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("Apps Script trả về JSON nhưng không đúng cấu trúc object.")
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or f"Apps Script API '{api}' trả ok=false.")
        return data
    except Exception as exc:
        raise RuntimeError(_safe_error_message(exc)) from exc

def _proxy_call(api, params=None):
    try:
        return fetch_apps_script_api_(api, params)
    except RuntimeError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "source": "apps_script",
                "api": api,
                "error": str(exc),
            },
        )

HTML = r'''<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#071426">
<title>THUY LOI AI - Thông số kỹ thuật</title>

<!-- Chart.js chỉ dùng cho lớp hiển thị biểu đồ; API/backend hiện tại không thay đổi. -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>

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
.date-filter{display:grid;grid-template-columns:1fr 1fr auto;gap:10px;margin:-4px 0 16px}
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
.toolbar{display:grid;grid-template-columns:1.5fr .9fr .8fr auto;gap:10px;margin-bottom:16px}
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
.grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}
.panel{overflow:hidden}.head{padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px}
.head-title{font-weight:900;font-size:18px}.head-sub{font-size:12px;color:var(--muted);margin-top:3px}
.chart-wrap{padding:12px 14px 16px;height:410px}.chart-wrap canvas{width:100%!important;height:100%!important}
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
  .toolbar{grid-template-columns:1fr 1fr}.toolbar .control:first-child{grid-column:1/-1}.kpi-grid{grid-template-columns:1fr 1fr;gap:8px}
  .kpi{padding:13px}.kpi-value{font-size:22px}.chart-wrap{height:330px;padding:9px}.summary-grid{grid-template-columns:1fr 1fr}.trend-grid{grid-template-columns:1fr 1fr}
  .table{display:none}.mobile-data{display:block}.data-toolbar{padding:10px}.page-info{margin-left:0;width:100%}
}
@media(max-width:430px){.toolbar{grid-template-columns:1fr}.toolbar .control:first-child{grid-column:auto}.summary-grid,.trend-grid{grid-template-columns:1fr 1fr}.title{font-size:16px}}
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
    <div class="control"><label>THÔNG SỐ</label><select id="parameter"><option value="">Mực nước</option></select></div>
    <div class="control"><label>THỜI GIAN</label><select id="period"><option value="24 gio">24 giờ</option><option value="3 ngay">3 ngày</option><option value="7 ngay" selected>7 ngày</option><option value="30 ngay">30 ngày</option><option value="90 ngay">90 ngày</option></select></div>
    <button class="ghost-btn" onclick="checkConnections()">🧪 Kiểm tra kết nối</button><button class="primary-btn" onclick="refreshModule()">↻ Làm mới</button>
  </section>

  <section class="date-filter">
    <div class="date-box"><label>TỪ NGÀY</label><input id="fromDate" type="date" onchange="applyCustomDateRange()"></div>
    <div class="date-box"><label>ĐẾN NGÀY</label><input id="toDate" type="date" onchange="applyCustomDateRange()"></div>
    <button class="primary-btn date-apply" style="min-height:44px" onclick="applyCustomDateRange()">📅 Áp dụng khoảng ngày</button>
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
      <div class="head"><div><div class="head-title">Biểu đồ diễn biến</div><div class="head-sub">Mực nước · lượng mưa · MNDBT · MNDGC</div></div><button class="ghost-btn" style="min-height:36px;padding:0 11px" onclick="fitChart()">↺</button></div>
      <div class="chart-wrap"><canvas id="hydroChart"></canvas></div>
    </div>

    <div class="panel">
      <div class="head"><div><div class="head-title">Thông tin công trình</div><div class="head-sub">Khu vực thông tin kỹ thuật</div></div></div>
      <div class="panel-body">
        <div class="info-card"><div class="info-label">CÔNG TRÌNH ĐANG CHỌN</div><b id="selected">Chưa chọn</b></div>
        <div class="info-card"><div class="info-label">TRẠM MƯA</div><div class="chips" id="rainPills"><span class="chip">Chưa có chuỗi mưa</span></div></div>
        <div class="info-card"><div class="info-label">CHẾ ĐỘ HIỂN THỊ</div><div class="chips"><span class="chip active">Thực tế</span><span class="chip">Kỹ thuật</span></div></div>
      </div>
    </div>
  </section>

  <section class="panel" style="margin-top:16px">
    <div class="head"><div><div class="head-title">Tóm tắt kỹ thuật</div><div class="head-sub">Phân tích số liệu thực tế; không tự động gán mức cảnh báo ngoài các ngưỡng MNDBT/MNDGC được cung cấp.</div></div></div>
    <div id="technicalSummary" class="panel-body"><div class="empty">Chọn công trình để phân tích.</div></div>
  </section>

  <section class="panel" style="margin-top:16px">
    <div class="head"><div><div class="head-title">Dữ liệu gần nhất</div><div class="head-sub">Dữ liệu thực tế từ AI_DATA qua Apps Script API</div></div></div>
    <div class="data-toolbar">
      <div class="search-box"><input id="dataSearch" type="search" placeholder="⌕ Tìm ngày, công trình, thông số, giá trị..." oninput="applyDataFilter()"></div>
      <input id="gridFrom" class="data-date" type="date" title="Từ ngày" onchange="applyDataFilter()">
      <input id="gridTo" class="data-date" type="date" title="Đến ngày" onchange="applyDataFilter()">
      <div class="export-group">
        <button class="ghost-btn" style="min-height:40px;padding:0 11px" onclick="exportCSV()">CSV</button>
        <button class="primary-btn" style="min-height:40px;padding:0 11px" onclick="exportExcel()">Excel</button><button class="ghost-btn" style="min-height:40px;padding:0 11px" onclick="exportQuickReportWord()">📄 Báo cáo nhanh</button>
        <button class="ghost-btn" style="min-height:40px;padding:0 11px" onclick="clearDataFilter()">Xóa lọc</button>
      </div>
      <div class="page-info" id="pageInfo">0 bản ghi</div>
    </div>
    <div class="table"><table><thead><tr><th>Ngày</th><th>Giờ</th><th>Công trình</th><th>Thông số</th><th>Giá trị</th><th>Đơn vị</th></tr></thead><tbody id="dataBody"></tbody></table></div>
    <div id="mobileData" class="mobile-data"></div>
    <div id="pagination" class="pagination"></div>
  </section>

  <div class="footer">THUY LOI AI · Technical Module V1.9 · Smart Control Room · Apps Script Proxy · Dashboard kỹ thuật</div>
</main>

<script>
const f=document.getElementById('facility'), parameter=document.getElementById('parameter'), period=document.getElementById('period');
const s=document.getElementById('selected'), water=document.getElementById('water'), state=document.getElementById('state');
const stateDetail=document.getElementById('stateDetail'), mndbt=document.getElementById('mndbt'), mndgc=document.getElementById('mndgc'), rainTotal=document.getElementById('rainTotal');
const rainPills=document.getElementById('rainPills'), dataBody=document.getElementById('dataBody'), mobileData=document.getElementById('mobileData');
const technicalSummary=document.getElementById('technicalSummary'), alertBanner=document.getElementById('alertBanner');
let currentParameters={waterLevel:[],rainfall:[]},currentData=null,hydroChart=null;
let allRows=[],filteredRows=[],currentPage=1; const PAGE_SIZE=10;
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


function setSelectedFacility(){s.textContent=f.value||'Chưa chọn'}
function periodDays(){return ({'24 gio':1,'3 ngay':3,'7 ngay':7,'30 ngay':30,'90 ngay':90})[period.value]||7}
function formatNumber(v,digits=2){if(v===null||v===undefined||v==='')return '—';const n=Number(v);return Number.isFinite(n)?n.toLocaleString('vi-VN',{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—'}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

const CLIENT_TIMEOUT=15000;
function sleep(ms){return new Promise(r=>setTimeout(r,ms))}
async function fetchJson(url, options={}, retries=2){
  let lastError;
  for(let attempt=0;attempt<=retries;attempt++){
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),CLIENT_TIMEOUT);
    try{
      const response=await fetch(url,{...options,signal:controller.signal,cache:'no-store'});
      let result;
      try{result=await response.json()}catch(e){throw new Error('FastAPI trả về dữ liệu không hợp lệ.')}
      if(!response.ok||!result.ok)throw new Error(result?.error||`HTTP ${response.status}`);
      return result;
    }catch(err){
      lastError=err.name==='AbortError'?new Error(`Timeout sau ${CLIENT_TIMEOUT/1000} giây.`):err;
      if(attempt<retries)await sleep(600*(attempt+1));
    }finally{clearTimeout(timer)}
  }
  throw lastError||new Error('Không kết nối được API.');
}
async function checkConnections(){
  const buttons=[...document.querySelectorAll('button')].filter(b=>b.textContent.includes('Kiểm tra kết nối'));
  buttons.forEach(b=>{b.disabled=true;b.textContent='⏳ Đang kiểm tra...'});
  try{
    const result=await fetchJson('/api/connection',{},0);
    if(result.apps_script_ok){
      alertBanner.className='alert-banner safe show';
      document.getElementById('alertIcon').textContent='🟢';
      document.getElementById('alertTitle').textContent='Kết nối dữ liệu OK';
      document.getElementById('alertDetail').textContent=`FastAPI → Apps Script → AI_DATA hoạt động · ${result.apps_script_ms||0} ms`;
      return true;
    }
    setDataError(result.apps_script_error||'Apps Script/AI_DATA không phản hồi.');
    return false;
  }catch(err){
    setDataError(err.message||'Không kiểm tra được kết nối.');
    return false;
  }finally{
    buttons.forEach(b=>{b.disabled=false;b.textContent='🧪 Kiểm tra kết nối'});
  }
}

function resetData(message='Chọn công trình để tải dữ liệu.'){
  water.textContent='—';state.textContent='—';stateDetail.textContent='Chưa có dữ liệu';mndbt.textContent='—';mndgc.textContent='—';rainTotal.textContent='—';
  technicalSummary.innerHTML='<div class="empty">'+escapeHtml(message)+'</div>';allRows=[];filteredRows=[];currentPage=1;renderTable();
  if(hydroChart){hydroChart.destroy();hydroChart=null}
  const ab=document.getElementById('alertBanner');
  ab.className='alert-banner safe';
  document.getElementById('alertIcon').textContent='●';
  document.getElementById('alertTitle').textContent='CHỜ DỮ LIỆU';
  document.getElementById('alertDetail').textContent='Chưa có dữ liệu mực nước hoặc đang chờ kết nối.';
  lastAlertLevel='normal';
}

function toggleTheme(){
  const dark=document.documentElement.classList.toggle('dark');
  localStorage.setItem('tlai-theme',dark?'dark':'light');
  document.getElementById('themeBtn').textContent=dark?'☀️':'🌙';
  if(currentData) renderHydroChart(currentData);
}
(function initTheme(){const dark=localStorage.getItem('tlai-theme')==='dark';if(dark)document.documentElement.classList.add('dark');document.getElementById('themeBtn').textContent=dark?'☀️':'🌙'})();

async function loadParameters(){
  if(!f.value)return;
  try{
    const result=await fetchJson('/api/parameters?facility='+encodeURIComponent(f.value));
    currentParameters=result.data||{waterLevel:[],rainfall:[]};
    const rainList=currentParameters.rainfall||[];
    rainPills.innerHTML=rainList.length?rainList.map(x=>'<span class="chip">'+escapeHtml(x.replace(/\s*\([^)]*\)/g,''))+'</span>').join(''):'<span class="chip">Không có chuỗi mưa</span>';
    const options=[{label:'Mực nước',value:''},...(currentParameters.waterLevel||[]).map(x=>({label:x,value:x})),...rainList.map(x=>({label:x,value:x}))];
    parameter.innerHTML='';const seen=new Set();
    options.forEach(o=>{const key=o.value+'|'+o.label;if(seen.has(key))return;seen.add(key);const opt=document.createElement('option');opt.value=o.value;opt.textContent=o.label;parameter.appendChild(opt)})
  }catch(err){console.error(err);parameter.innerHTML='<option value="">Không tải được thông số</option>'}
}

async function loadChartData(){
  if(!f.value){resetData();return}
  state.textContent='Đang tải...';stateDetail.textContent='Đang lấy dữ liệu thực tế từ AI_DATA';
  try{
    const params=new URLSearchParams({facility:f.value,year:String(new Date().getFullYear()),days:String(periodDays())});
    const from=document.getElementById('fromDate').value,to=document.getElementById('toDate').value;
    if(from)params.set('fromDate',from);
    if(to)params.set('toDate',to);
    const selected=parameter.value;
    if(selected){const isRain=(currentParameters.rainfall||[]).includes(selected);if(!isRain)params.set('waterParameter',selected);else params.set('rainfallParameters',selected)}
    const result=await fetchJson('/api/chart?'+params.toString());
    currentData=result.data;renderData(currentData);
  }catch(err){console.error(err);setDataError(err.message||'Không tải được dữ liệu.')}
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
  const waterSeries=Array.isArray(data.water)?data.water:[],latest=waterSeries.length?waterSeries[waterSeries.length-1]:null;
  water.textContent=latest?formatNumber(latest.value):'—';state.textContent=latest?'Có dữ liệu':'Chưa có mực nước';
  if(latest){const d=new Date(latest.time);stateDetail.textContent='Cập nhật '+d.toLocaleString('vi-VN');document.getElementById('waterNote').textContent='Lần đo mới nhất'}
  mndbt.textContent=data.limits&&data.limits.mndbt!=null?formatNumber(data.limits.mndbt):'—';
  mndgc.textContent=data.limits&&data.limits.mndgc!=null?formatNumber(data.limits.mndgc):'—';
  rainTotal.textContent=data.totalRainfall!=null?formatNumber(data.totalRainfall):'—';
  evaluateAlert(data,latest);updateKpiState(data,latest);buildRows(data);renderTechnicalSummary(data,waterSeries);renderHydroChart(data)
}

function updateKpiState(data,latest){
  const k=document.getElementById('kpiWater'),led=document.getElementById('ledWater'),ledState=document.getElementById('ledState');
  k.classList.remove('warn','danger');led.className='kpi-led';ledState.className='kpi-led';
  if(!latest)return;
  const h=Number(latest.value),bt=Number(data.limits&&data.limits.mndbt),gc=Number(data.limits&&data.limits.mndgc);
  if(Number.isFinite(gc)&&h>=gc){k.classList.add('danger');led.classList.add('alarm');ledState.classList.add('alarm')}
  else if(Number.isFinite(bt)&&h>=bt){k.classList.add('warn');led.classList.add('stale');ledState.classList.add('stale')}
}

function buildRows(data){
  const rows=[],waterSeries=Array.isArray(data.water)?data.water:[];
  waterSeries.slice().reverse().forEach(p=>{const d=new Date(p.time);rows.push({time:d,facility:data.facility,parameter:p.parameter,value:p.value,unit:'m'})});
  (data.rainfall||[]).forEach(series=>(series.data||[]).slice().reverse().forEach(p=>{const d=new Date(p.time);rows.push({time:d,facility:data.facility,parameter:p.parameter,value:p.value,unit:'mm'})}));
  rows.sort((a,b)=>b.time-a.time);allRows=rows;applyDataFilter()
}
function applyDataFilter(){
  const q=(document.getElementById('dataSearch').value||'').trim().toLowerCase();
  const from=localDateStart(document.getElementById('gridFrom').value);
  const to=localDateEnd(document.getElementById('gridTo').value);
  filteredRows=allRows.filter(r=>{
    if(from&&r.time<from)return false;
    if(to&&r.time>to)return false;
    if(q&&!(`${r.time.toLocaleDateString('vi-VN')} ${r.facility} ${r.parameter} ${r.value} ${r.unit}`).toLowerCase().includes(q))return false;
    return true;
  });
  currentPage=1;renderTable();
}
function clearDataFilter(){
  ['dataSearch','gridFrom','gridTo'].forEach(id=>document.getElementById(id).value='');
  applyDataFilter();
}
function applyCustomDateRange(){
  const from=document.getElementById('fromDate'),to=document.getElementById('toDate');
  if(from.value&&to.value&&from.value>to.value)to.value=from.value;
  if(f.value)loadChartData();
}
function exportRows(){
  return filteredRows.map(r=>({
    'Ngày':r.time.toLocaleDateString('vi-VN'),
    'Giờ':String(r.time.getHours()).padStart(2,'0')+':00',
    'Công trình':r.facility,
    'Thông số':r.parameter,
    'Giá trị':Number(r.value),
    'Đơn vị':r.unit
  }));
}
function exportFileStamp(){
  return new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
}
function exportCSV(){
  const rows=exportRows();
  if(!rows.length){alert('Không có dữ liệu phù hợp để xuất.');return}
  const headers=['Ngày','Giờ','Công trình','Thông số','Giá trị','Đơn vị'];
  const lines=[headers,...rows.map(r=>headers.map(h=>`"${String(r[h]??'').replace(/"/g,'""')}"`))];
  const csv='\ufeff'+lines.map(a=>a.join(',')).join('\r\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=`THUY_LOI_AI_Du_lieu_${exportFileStamp()}.csv`;a.click();URL.revokeObjectURL(url);
}
function exportExcel(){
  const rows=exportRows();
  if(!rows.length){alert('Không có dữ liệu phù hợp để xuất.');return}
  if(!window.XLSX){alert('Thư viện Excel chưa tải xong. Vui lòng thử lại.');return}
  const ws=XLSX.utils.json_to_sheet(rows),wb=XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb,ws,'Du lieu');
  XLSX.writeFile(wb,`THUY_LOI_AI_Du_lieu_${exportFileStamp()}.xlsx`);
}


function reportDateRange(series){
  const points=(series||[]).map(p=>({time:new Date(p.time),value:Number(p.value)})).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime())).sort((a,b)=>a.time-b.time);
  const from=document.getElementById('fromDate').value, to=document.getElementById('toDate').value;
  if(from||to)return {from:from||'—',to:to||'—',points};
  if(points.length)return {from:points[0].time.toLocaleDateString('vi-VN'),to:points[points.length-1].time.toLocaleDateString('vi-VN'),points};
  return {from:'—',to:'—',points:[]};
}
function fmtReportDate(d){return d instanceof Date?d.toLocaleString('vi-VN'):String(d||'—')}
function reportAnalysis(data){
  const r=reportDateRange(data.water||[]), pts=r.points, latest=pts[pts.length-1], first=pts[0];
  const bt=Number(data.limits&&data.limits.mndbt), gc=Number(data.limits&&data.limits.mndgc);
  const rainfallSeries=Array.isArray(data.rainfall)?data.rainfall:[];
  const rainPoints=rainfallSeries.flatMap(x=>(Array.isArray(x.data)?x.data:[]).map(p=>({time:new Date(p.time),value:Number(p.value),name:x.parameter||'Lượng mưa'}))).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime()));
  const totalRain=Number.isFinite(Number(data.totalRainfall))?Number(data.totalRainfall):rainPoints.reduce((sum,p)=>sum+(p.value||0),0);
  const increase=latest&&first?latest.value-first.value:null;
  const min=pts.length?Math.min(...pts.map(p=>p.value)):null, max=pts.length?Math.max(...pts.map(p=>p.value)):null;
  const maxRise=pts.length>1?Math.max(...pts.slice(1).map((p,i)=>p.value-pts[i].value)):null;
  const maxDrop=pts.length>1?Math.min(...pts.slice(1).map((p,i)=>p.value-pts[i].value)):null;
  const abnormal=[];
  const addAbnormal=x=>{if(x&&!abnormal.includes(x))abnormal.push(x)};
  if(latest&&Number.isFinite(gc)&&latest.value>=gc)addAbnormal(`Mực nước mới nhất ${formatNumber(latest.value)} m chạm/vượt MNDGC ${formatNumber(gc)} m.`);
  else if(max!==null&&Number.isFinite(gc)&&max>=gc)addAbnormal(`Trong kỳ có thời điểm mực nước chạm/vượt MNDGC ${formatNumber(gc)} m.`);
  if(latest&&Number.isFinite(bt)&&latest.value>=bt)addAbnormal(`Mực nước mới nhất không thấp hơn MNDBT ${formatNumber(bt)} m.`);
  else if(max!==null&&Number.isFinite(bt)&&max>=bt)addAbnormal(`Trong kỳ có thời điểm mực nước đạt/vượt MNDBT ${formatNumber(bt)} m.`);
  for(let i=1;i<pts.length;i++){
    const d=pts[i].value-pts[i-1].value, hours=(pts[i].time-pts[i-1].time)/3600000;
    if(hours>0&&Math.abs(d)>=0.30)addAbnormal(`Biến động đáng chú ý: ${d>=0?'+':''}${formatNumber(d)} m trong ${hours.toFixed(1)} giờ so với lần đo trước.`);
  }
  const trendFor=hours=>{if(!latest)return null;const cutoff=latest.time.getTime()-hours*3600000,a=pts.filter(p=>p.time.getTime()>=cutoff);return a.length>1?latest.value-a[0].value:null};
  const trend24=trendFor(24),trend72=trendFor(72),trend168=trendFor(168);
  const rate24=trend24!==null?trend24/24:null;
  const rainPeak=rainPoints.length?rainPoints.reduce((a,b)=>b.value>a.value?b:a):null;
  let forecast='Chưa đủ dữ liệu để dự kiến xu hướng.';
  const projected={h6:null,h12:null,h24:null,btHours:null,gcHours:null};
  if(latest&&trend24!==null){
    projected.h6=latest.value+trend24/4; projected.h12=latest.value+trend24/2; projected.h24=latest.value+trend24;
    forecast=`Theo xu hướng 24 giờ gần nhất (ngoại suy tuyến tính, chỉ mang tính tham khảo): sau 6 giờ khoảng ${formatNumber(projected.h6)} m; 12 giờ khoảng ${formatNumber(projected.h12)} m; 24 giờ khoảng ${formatNumber(projected.h24)} m nếu điều kiện và xu hướng hiện tại tiếp diễn.`;
    if(rate24>0){
      if(Number.isFinite(bt)&&latest.value<bt)projected.btHours=(bt-latest.value)/rate24;
      if(Number.isFinite(gc)&&latest.value<gc)projected.gcHours=(gc-latest.value)/rate24;
      if(projected.btHours!==null&&projected.btHours<=24)forecast+=` Với tốc độ tăng trung bình hiện tại, có khả năng tiến đến MNDBT sau khoảng ${formatNumber(projected.btHours,1)} giờ.`;
      if(projected.gcHours!==null&&projected.gcHours<=48)forecast+=` Với cùng xu hướng, có khả năng tiến đến MNDGC sau khoảng ${formatNumber(projected.gcHours,1)} giờ.`;
    }
  }
  let assessment='Chưa đủ dữ liệu để đánh giá.';
  if(latest&&Number.isFinite(gc)&&latest.value>=gc)assessment='Mực nước hiện tại chạm/vượt MNDGC; cần tăng cường kiểm tra, theo dõi và thực hiện chế độ vận hành/cảnh báo theo quy trình của công trình.';
  else if(latest&&Number.isFinite(bt)&&latest.value>=bt)assessment='Mực nước hiện tại từ MNDBT trở lên; cần tiếp tục theo dõi diễn biến mực nước và lượng mưa, đồng thời đối chiếu quy trình vận hành.';
  else if(latest)assessment='Mực nước hiện tại thấp hơn MNDBT; tiếp tục theo dõi xu thế mực nước, lượng mưa và các yếu tố vận hành liên quan.';
  return {r,pts,latest,first,bt,gc,totalRain,rainPoints,rainPeak,increase,min,max,maxRise,maxDrop,abnormal,trend24,trend72,trend168,rate24,projected,forecast,assessment};
}
function exportQuickReportWord(){
  if(!currentData||!Array.isArray(currentData.water)||!currentData.water.length){alert('Chưa có dữ liệu mực nước để lập Báo cáo nhanh.');return}
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
<tr><td>Số lần đo mực nước</td><td>${a.pts.length} lần</td></tr><tr><td>Mực nước đầu kỳ</td><td>${escapeHtml(firstText)}</td></tr><tr><td>Mực nước mới nhất</td><td>${escapeHtml(latestText)}</td></tr><tr><td>Mực nước thấp nhất</td><td>${a.min!==null?formatNumber(a.min)+' m':'—'}</td></tr><tr><td>Mực nước cao nhất</td><td>${a.max!==null?formatNumber(a.max)+' m':'—'}</td></tr><tr><td>Biến động đầu kỳ → cuối kỳ</td><td>${trendText(a.increase)}</td></tr><tr><td>Tăng lớn nhất giữa hai lần đo</td><td>${a.maxRise!==null?trendText(a.maxRise):'—'}</td></tr><tr><td>Giảm lớn nhất giữa hai lần đo</td><td>${a.maxDrop!==null?trendText(a.maxDrop):'—'}</td></tr><tr><td>Xu hướng 24 giờ</td><td>${trendText(a.trend24)}</td></tr><tr><td>Xu hướng 3 ngày</td><td>${trendText(a.trend72)}</td></tr><tr><td>Xu hướng 7 ngày</td><td>${trendText(a.trend168)}</td></tr><tr><td>Tổng lượng mưa</td><td>${formatNumber(a.totalRain)} mm</td></tr><tr><td>Lượng mưa lớn nhất ghi nhận</td><td>${escapeHtml(peakRain)}</td></tr></table>
<h2>2. So sánh mực nước với MNDBT, MNDGC</h2><table><tr><th>Ngưỡng</th><th>Giá trị</th><th>Chênh lệch với H mới nhất</th><th>Đánh giá</th></tr>
<tr><td>MNDBT</td><td>${Number.isFinite(a.bt)?formatNumber(a.bt)+' m':'—'}</td><td>${marginBT!==null?(marginBT>=0?'+':'')+formatNumber(marginBT)+' m':'—'}</td><td>${a.latest&&Number.isFinite(a.bt)?(a.latest.value>=a.bt?'Đạt/vượt MNDBT':'Thấp hơn MNDBT'):'Chưa đủ dữ liệu'}</td></tr>
<tr><td>MNDGC</td><td>${Number.isFinite(a.gc)?formatNumber(a.gc)+' m':'—'}</td><td>${marginGC!==null?(marginGC>=0?'+':'')+formatNumber(marginGC)+' m':'—'}</td><td>${a.latest&&Number.isFinite(a.gc)?(a.latest.value>=a.gc?'CHẠM/VƯỢT MNDGC':'Chưa vượt MNDGC'):'Chưa đủ dữ liệu'}</td></tr></table>
<h2>3. Tổng hợp lượng mưa</h2>${rainTable}
<h2>4. Bất thường / điểm cần chú ý</h2><ul>${abnormalHtml}</ul>
<h2>5. Nhận định kỹ thuật</h2><p>${escapeHtml(a.assessment)}</p>
<h2>6. Dự kiến diễn biến</h2><p>${escapeHtml(a.forecast)}</p>
<h2>7. Kiến nghị theo dõi</h2><ul><li>Tiếp tục cập nhật mực nước và lượng mưa theo tần suất quy định của công trình.</li><li>Đối chiếu diễn biến với MNDBT, MNDGC và quy trình vận hành hồ/công trình hiện hành.</li><li>Nếu mực nước tăng nhanh, tiến sát/vượt ngưỡng hoặc lượng mưa tăng mạnh, tăng cường theo dõi và thực hiện chế độ báo cáo/cảnh báo theo quy định.</li><li>Đánh giá đồng thời lượng mưa, xu thế mực nước và tình trạng vận hành trước khi quyết định điều hành.</li></ul>
<p class="note">Lưu ý: “Dự kiến diễn biến” là ngoại suy xu hướng từ số liệu quan trắc đang có, chỉ mang tính tham khảo; không phải dự báo khí tượng thủy văn chính thức và không thay thế quy trình vận hành hoặc quyết định của người có thẩm quyền. Tiêu chí biến động đáng chú ý ≥ 0,30 m giữa hai lần đo là tiêu chí phân tích của báo cáo, không phải ngưỡng quy chuẩn.</p>
<div class="footer">THUY LOI AI · Báo cáo nhanh tự động từ dữ liệu đang hiển thị trên Dashboard.</div></body></html>`;
  const blob=new Blob(['\ufeff',html],{type:'application/msword;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`Bao_cao_nhanh_${String(facility).replace(/[^a-zA-Z0-9À-ỹ _-]/g,'_')}_${exportFileStamp()}.doc`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function renderTable(){
  const total=filteredRows.length,pages=Math.max(1,Math.ceil(total/PAGE_SIZE));if(currentPage>pages)currentPage=pages;
  const start=(currentPage-1)*PAGE_SIZE,rows=filteredRows.slice(start,start+PAGE_SIZE);
  document.getElementById('pageInfo').textContent=`${total} bản ghi · trang ${currentPage}/${pages}`;
  dataBody.innerHTML=rows.length?rows.map(r=>'<tr><td>'+r.time.toLocaleDateString('vi-VN')+'</td><td>'+String(r.time.getHours()).padStart(2,'0')+':00</td><td>'+escapeHtml(r.facility)+'</td><td>'+escapeHtml(r.parameter)+'</td><td><b>'+formatNumber(r.value)+'</b></td><td>'+r.unit+'</td></tr>').join(''):'<tr><td colspan="6" class="empty">Không có dữ liệu phù hợp.</td></tr>';
  mobileData.innerHTML=rows.length?rows.map(r=>'<div class="data-item"><div class="dt">'+r.time.toLocaleDateString('vi-VN')+' · '+String(r.time.getHours()).padStart(2,'0')+':00</div><div class="pn">'+escapeHtml(r.parameter)+'</div><div class="pv">'+formatNumber(r.value)+' '+r.unit+'</div></div>').join(''):'<div class="empty">Không có dữ liệu phù hợp.</div>';
  const pag=document.getElementById('pagination');pag.innerHTML='';
  for(let i=1;i<=pages&&i<=7;i++){const b=document.createElement('button');b.className='page-btn'+(i===currentPage?' active':'');b.textContent=i;b.onclick=()=>{currentPage=i;renderTable()};pag.appendChild(b)}
}

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
  const points=series.map(p=>({time:new Date(p.time),value:Number(p.value)})).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime())).sort((a,b)=>a.time-b.time);
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
  const ws=(data.water||[]).map(p=>({x:new Date(p.time),y:Number(p.value)})).filter(p=>Number.isFinite(p.y));
  const rain=(data.rainfall||[]).flatMap(s=>(s.data||[]).map(p=>({x:new Date(p.time),y:Number(p.value),name:s.parameter}))).filter(p=>Number.isFinite(p.y));
  const bt=Number(data.limits&&data.limits.mndbt),gc=Number(data.limits&&data.limits.mndgc);
  if(hydroChart)hydroChart.destroy();
  const dark=document.documentElement.classList.contains('dark'),grid=dark?'rgba(170,195,220,.10)':'rgba(50,85,120,.10)',text=dark?'#9fb0c6':'#687386';
  const datasets=[];
  if(rain.length)datasets.push({type:'bar',label:'Lượng mưa',data:rain,yAxisID:'rain',backgroundColor:dark?'rgba(71,214,181,.38)':'rgba(104,116,128,.38)',borderWidth:0,barPercentage:.72,categoryPercentage:.9});
  if(ws.length)datasets.push({type:'line',label:'Mực nước',data:ws,yAxisID:'water',borderColor:dark?'#20c5ef':'#0878c9',backgroundColor:'transparent',borderWidth:3,pointRadius:2.5,pointHoverRadius:6,tension:.32});
  if(Number.isFinite(bt))datasets.push({type:'line',label:'MNDBT',data:ws.length?ws.map(p=>({x:p.x,y:bt})):[],yAxisID:'water',borderColor:dark?'#ffb13b':'#d88900',borderWidth:2,borderDash:[8,6],pointRadius:0});
  if(Number.isFinite(gc))datasets.push({type:'line',label:'MNDGC',data:ws.length?ws.map(p=>({x:p.x,y:gc})):[],yAxisID:'water',borderColor:dark?'#ff684e':'#e14b32',borderWidth:2,borderDash:[5,5],pointRadius:0});
  hydroChart=new Chart(document.getElementById('hydroChart'),{
    data:{datasets},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
      plugins:{legend:{labels:{color:text,usePointStyle:true,padding:14}},tooltip:{callbacks:{
        title(items){return items[0]?.parsed?.x?new Date(items[0].parsed.x).toLocaleString('vi-VN'):''},
        label(ctx){return `${ctx.dataset.label}: ${formatNumber(ctx.parsed.y)} ${ctx.dataset.yAxisID==='rain'?'mm':'m'}`}
      }}},
      scales:{x:{type:'time',time:{unit:periodDays()<=1?'hour':periodDays()<=7?'day':'day'},ticks:{color:text,maxRotation:0},grid:{color:grid}},
        water:{position:'left',title:{display:true,text:'H (m)',color:text},ticks:{color:text},grid:{color:grid}},
        rain:{position:'right',title:{display:true,text:'Mưa (mm)',color:text},ticks:{color:text},grid:{drawOnChartArea:false}}
      }}
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
      f.innerHTML='<option value="">Không có công trình</option>';s.textContent='Không có dữ liệu';
      resetData('Apps Script đã kết nối nhưng không trả về danh sách công trình.');
      return;
    }
    /* V1.8: tự chọn công trình đầu tiên để chuỗi dữ liệu chạy hoàn chỉnh ngay sau khi kết nối. */
    f.value=facilities[0];setSelectedFacility();resetData('Đang tải dữ liệu thực tế...');
    await loadParameters();await loadChartData();
  }catch(err){
    console.error(err);
    f.innerHTML='<option value="">🔴 Mất kết nối Apps Script</option>';
    s.textContent='Không kết nối được';
    setDataError(err.message||'Không tải được danh sách công trình.');
  }finally{f.disabled=false}
}
f.addEventListener('change',async()=>{setSelectedFacility();resetData('Đang tải dữ liệu thực tế...');await loadParameters();await loadChartData()});
parameter.addEventListener('change',loadChartData);period.addEventListener('change',loadChartData);
async function refreshModule(){if(!f.value){setSelectedFacility();resetData();return}setSelectedFacility();await loadParameters();await loadChartData()}
loadFacilities();
</script>
</body>
</html>
'''

@app.get("/api/facilities")
def api_facilities():
    """Proxy danh sách công trình từ Apps Script API."""
    return _proxy_call("facilities")

@app.get("/api/parameters")
def api_parameters(facility: str):
    """Proxy bộ thông số thực tế của một công trình từ Apps Script."""
    return _proxy_call("parameters", {"facility": facility})

@app.get("/api/chart")
def api_chart(
    facility: str,
    year: int = 2026,
    days: int = 7,
    waterParameter: str = "",
    rainfallParameters: str = "",
    fromDate: str = "",
    toDate: str = "",
):
    """Proxy dữ liệu mực nước/lượng mưa và giới hạn kỹ thuật từ Apps Script."""
    rain = [x.strip() for x in rainfallParameters.split(",") if x.strip()]
    return _proxy_call("chart", {
        "facility": facility,
        "year": year,
        "days": days,
        "waterParameter": waterParameter,
        "rainfallParameters": ",".join(rain),
        "fromDate": fromDate,
        "toDate": toDate,
    })

@app.get("/api/connection")
def api_connection():
    """Kiểm tra FastAPI -> Apps Script -> AI_DATA qua API facilities."""
    started=monotonic()
    try:
        fetch_apps_script_api_("facilities")
        elapsed=round((monotonic()-started)*1000)
        return {"ok":True,"fastapi_ms":elapsed,"apps_script_ok":True,"apps_script_ms":elapsed,
                "ai_data_ok":True,"message":"Apps Script phản hồi thành công; AI_DATA có thể truy cập qua Apps Script."}
    except RuntimeError as exc:
        elapsed=round((monotonic()-started)*1000)
        return {"ok":True,"fastapi_ms":elapsed,"apps_script_ok":False,"apps_script_ms":elapsed,
                "ai_data_ok":False,"apps_script_error":str(exc),
                "message":"FastAPI hoạt động nhưng Apps Script/AI_DATA không phản hồi."}

@app.get("/", response_class=HTMLResponse)
def technical_dashboard():
    return HTML

@app.get("/health")
def health():
    return {"module":"technical_module","version":"1.9.0","status":"ok","stage":4,"mode":"apps_script_proxy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("technical_module:app", host="0.0.0.0", port=8001, reload=False)
