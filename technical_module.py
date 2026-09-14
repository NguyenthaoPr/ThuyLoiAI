# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ============================================================
# THUY LOI AI - TECHNICAL MODULE V1.7
# BUOC 1: GIAO DIEN DOC LAP
# Khong import, khong sua server.py
# ============================================================

app = FastAPI(
    title="THUY LOI AI - Thong so ky thuat",
    version="1.7.0",
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

def fetch_apps_script_api_(api, params=None):
    query = {"api": api}
    if params:
        query.update({k: v for k, v in params.items() if v not in (None, "")})
    url = APPS_SCRIPT_API_URL + "?" + urlencode(query)

    req = Request(
        url,
        headers={
            "User-Agent": "THUY-LOI-AI-Technical/1.7"
        }
    )

    with urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8")

    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "Apps Script API trả lỗi.")
    return data

HTML = '''<!doctype html>
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
    <button class="primary-btn" onclick="refreshModule()">↻ Làm mới</button>
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
        <button class="primary-btn" style="min-height:40px;padding:0 11px" onclick="exportExcel()">Excel</button>
        <button class="ghost-btn" style="min-height:40px;padding:0 11px" onclick="clearDataFilter()">Xóa lọc</button>
      </div>
      <div class="page-info" id="pageInfo">0 bản ghi</div>
    </div>
    <div class="table"><table><thead><tr><th>Ngày</th><th>Giờ</th><th>Công trình</th><th>Thông số</th><th>Giá trị</th><th>Đơn vị</th></tr></thead><tbody id="dataBody"></tbody></table></div>
    <div id="mobileData" class="mobile-data"></div>
    <div id="pagination" class="pagination"></div>
  </section>

  <div class="footer">THUY LOI AI · Technical Module V1.7 · Smart Control Room · Apps Script Proxy · Dashboard kỹ thuật</div>
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

function resetData(message='Chọn công trình để tải dữ liệu.'){
  water.textContent='—';state.textContent='—';stateDetail.textContent='Chưa có dữ liệu';mndbt.textContent='—';mndgc.textContent='—';rainTotal.textContent='—';
  technicalSummary.innerHTML='<div class="empty">'+escapeHtml(message)+'</div>';allRows=[];filteredRows=[];currentPage=1;renderTable();
  if(hydroChart){hydroChart.destroy();hydroChart=null}
  const ab=document.getElementById('alertBanner');
  ab.className='alert-banner safe';
  document.getElementById('alertIcon').textContent='●';
  document.getElementById('alertTitle').textContent='Vận hành bình thường';
  document.getElementById('alertDetail').textContent='Đang chờ dữ liệu mực nước.';
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
    const response=await fetch('/api/parameters?facility='+encodeURIComponent(f.value));const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.error||'Không tải được thông số.');
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
    const response=await fetch('/api/chart?'+params.toString());const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.error||'Không tải được dữ liệu.');
    currentData=result.data;renderData(currentData)
  }catch(err){console.error(err);state.textContent='Lỗi dữ liệu';stateDetail.textContent=err.message||'Không tải được dữ liệu.'}
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
  f.disabled=true;f.innerHTML='<option value="">Đang tải công trình...</option>';
  try{
    const response=await fetch('/api/facilities');const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.error||'Không tải được danh sách công trình.');
    const facilities=Array.isArray(result.data)?result.data:[];f.innerHTML='<option value="">Chọn công trình...</option>';
    facilities.forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name;f.appendChild(option)});
    if(!facilities.length){f.innerHTML='<option value="">Không có công trình</option>';s.textContent='Không có dữ liệu'}
  }catch(err){console.error(err);f.innerHTML='<option value="">Lỗi tải dữ liệu</option>';s.textContent='Không kết nối được API'}
  finally{f.disabled=false}
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
    return fetch_apps_script_api_("facilities")

@app.get("/api/parameters")
def api_parameters(facility: str):
    """Proxy bộ thông số thực tế của một công trình từ Apps Script."""
    return fetch_apps_script_api_("parameters", {"facility": facility})

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
    return fetch_apps_script_api_("chart", {
        "facility": facility,
        "year": year,
        "days": days,
        "waterParameter": waterParameter,
        "rainfallParameters": ",".join(rain),
        "fromDate": fromDate,
        "toDate": toDate,
    })

@app.get("/", response_class=HTMLResponse)
def technical_dashboard():
    return HTML

@app.get("/health")
def health():
    return {"module":"technical_module","version":"1.6.0","status":"ok","stage":4,"mode":"apps_script_proxy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("technical_module:app", host="0.0.0.0", port=8001, reload=False)
