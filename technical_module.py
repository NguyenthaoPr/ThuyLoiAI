from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ============================================================
# THUY LOI AI - TECHNICAL MODULE V1
# BUOC 1: GIAO DIEN DOC LAP
# Khong import, khong sua server.py
# ============================================================

app = FastAPI(
    title="THUY LOI AI - Thong so ky thuat",
    version="1.1.0",
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
            "User-Agent": "THUY-LOI-AI-Technical/1.1"
        }
    )

    with urlopen(req, timeout=20) as response:
        raw = response.read().decode("utf-8")

    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "Apps Script API trả lỗi.")
    return data

HTML = '''
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>THUY LOI AI - Thong so ky thuat</title>
<style>
:root{--bg:#f4f7fb;--card:#fff;--text:#172033;--muted:#687386;--line:#e5eaf1;--primary:#1769aa;--ok:#168a55;--warn:#c78300}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--text)}
.header{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line)}
.header-inner,.container{max-width:1400px;margin:auto}
.header-inner{padding:14px 18px;display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;gap:10px;align-items:center}.icon{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;background:#e8f3fc;font-size:22px}
.title{font-weight:800;font-size:18px}.sub{font-size:12px;color:var(--muted);margin-top:2px}
.status{font-size:12px;color:var(--ok)}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:5px}
.container{padding:18px}
.toolbar{display:grid;grid-template-columns:1.5fr .8fr .8fr auto;gap:10px;margin-bottom:16px}
.control,.card,.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 24px rgba(30,50,80,.08)}
.control{padding:10px 12px}.control label{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}
select{width:100%;border:0;outline:0;background:transparent;font:inherit;font-weight:600;color:var(--text)}
button{border:0;border-radius:12px;background:var(--primary);color:white;font-weight:700;padding:0 18px;cursor:pointer}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}
.card{padding:16px}.label{color:var(--muted);font-size:12px;margin-bottom:8px}.value{font-size:25px;font-weight:800}.unit{font-size:12px;color:var(--muted);margin-top:4px}.ok{color:var(--ok)}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}.panel{overflow:hidden}
.head{padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px}
.head-title{font-weight:800}.head-sub{font-size:12px;color:var(--muted);margin-top:3px}
.chart{height:360px;padding:16px}.placeholder{height:100%;border:1px dashed #b8c5d5;border-radius:12px;display:grid;place-items:center;text-align:center;color:var(--muted);background:repeating-linear-gradient(0deg,transparent 0,transparent 39px,#edf1f6 40px),repeating-linear-gradient(90deg,transparent 0,transparent 39px,#edf1f6 40px)}
.pills{display:flex;flex-wrap:wrap;gap:8px}.pill{border:1px solid var(--line);border-radius:999px;padding:7px 10px;font-size:12px;color:var(--muted);background:#fafbfd}
table{width:100%;border-collapse:collapse;min-width:620px}th,td{padding:11px 14px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}th{font-size:11px;color:var(--muted)}
.table{overflow-x:auto}.empty{text-align:center;color:var(--muted);padding:26px}
.footer{text-align:center;color:var(--muted);font-size:11px;padding:20px 10px 28px}
@media(max-width:1050px){.cards{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}}
@media(max-width:720px){.container{padding:12px}.toolbar{grid-template-columns:1fr 1fr}.toolbar .control:first-child{grid-column:1/-1}.cards{grid-template-columns:1fr 1fr;gap:8px}.card{padding:13px}.value{font-size:21px}.chart{height:300px;padding:10px}button{min-height:48px}}
@media(max-width:430px){.toolbar{grid-template-columns:1fr}.toolbar .control:first-child{grid-column:auto}}
</style>
</head>
<body>
<header class="header"><div class="header-inner">
<div class="brand"><div class="icon">⚙️</div><div><div class="title">THUY LOI AI</div><div class="sub">Thong so ky thuat</div></div></div>
<div class="status"><span class="dot"></span>Module doc lap</div>
</div></header>

<main class="container">
<section class="toolbar">
<div class="control"><label>CONG TRINH</label>
<select id="facility">
<option value="">Đang tải công trình...</option>
</select></div>
<div class="control"><label>THONG SO</label>
<select id="parameter"><option>Muc nuoc</option><option>HTL</option><option>HHL</option><option>Luong mua</option></select></div>
<div class="control"><label>THOI GIAN</label>
<select id="period"><option>24 gio</option><option>3 ngay</option><option selected>7 ngay</option><option>30 ngay</option><option>90 ngay</option></select></div>
<button onclick="refreshModule()">↻ Lam moi</button>
</section>

<section class="cards">
<div class="card"><div class="label">MUC NUOC HIEN TAI</div><div class="value" id="water">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">TRANG THAI</div><div class="value ok" id="state">—</div><div class="unit">Chua ket noi du lieu</div></div>
<div class="card"><div class="label">MNDBT</div><div class="value" id="mndbt">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">MNDGC</div><div class="value" id="mndgc">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">TONG LUONG MUA</div><div class="value" id="rainTotal">—</div><div class="unit">mm</div></div>
</section>

<section class="grid">
<div class="panel"><div class="head"><div><div class="head-title">Bieu do dien bien</div><div class="head-sub">Muc nuoc va luong mua theo thoi gian</div></div>
<div class="pills"><span class="pill">X</span><span class="pill">X T1</span><span class="pill">X C24</span></div></div>
<div class="chart"><div id="chartArea" class="placeholder"><div><div style="font-size:32px">📈</div><b>Đang chờ dữ liệu</b><br>Chọn công trình để tải dữ liệu thực tế.</div></div></div></div>

<div class="panel"><div class="head"><div><div class="head-title">Thong tin cong trinh</div><div class="head-sub">Khu vuc thong tin ky thuat</div></div></div>
<div style="padding:16px"><div class="card" style="box-shadow:none;margin-bottom:10px"><div class="label">CONG TRINH DANG CHON</div><b id="selected">Chua chon</b></div>
<div class="card" style="box-shadow:none"><div class="label">TRAM MUA</div><div class="pills"><span class="pill">X</span><span class="pill">X T1</span><span class="pill">X C24</span></div></div></div></div>
</section>

<section class="panel" style="margin-top:16px"><div class="head"><div><div class="head-title">Du lieu gan nhat</div><div class="head-sub">Dữ liệu thực tế từ AI_DATA qua Apps Script API</div></div></div>
<div class="table"><table><thead><tr><th>Ngay</th><th>Gio</th><th>Cong trinh</th><th>Thong so</th><th>Gia tri</th><th>Don vi</th></tr></thead>
<tbody id="dataBody"><tr><td colspan="6" class="empty">Chọn công trình để tải dữ liệu.</td></tr></tbody></table></div></section>
<div class="footer">THUY LOI AI · Technical Module V1.2 · Bước 3.3 — Kết nối dữ liệu thực tế</div>
</main>

<script>
const f=document.getElementById('facility');
const parameter=document.getElementById('parameter');
const period=document.getElementById('period');
const s=document.getElementById('selected');
const water=document.getElementById('water');
const state=document.getElementById('state');
const mndbt=document.getElementById('mndbt');
const mndgc=document.getElementById('mndgc');
const rainTotal=document.getElementById('rainTotal');
const dataBody=document.getElementById('dataBody');
const chartArea=document.getElementById('chartArea');

let currentParameters={waterLevel:[],rainfall:[]};
let currentData=null;

function setSelectedFacility(){
  s.textContent = f.value || 'Chưa chọn';
}

function periodDays(){
  const map={'24 gio':1,'3 ngay':3,'7 ngay':7,'30 ngay':30,'90 ngay':90};
  return map[period.value] || 7;
}

function formatNumber(v, digits=2){
  if(v===null || v===undefined || v==='') return '—';
  const n=Number(v);
  if(!Number.isFinite(n)) return '—';
  return n.toLocaleString('vi-VN',{minimumFractionDigits:digits,maximumFractionDigits:digits});
}

function resetData(message='Chọn công trình để tải dữ liệu.'){
  water.textContent='—';
  state.textContent='—';
  mndbt.textContent='—';
  mndgc.textContent='—';
  rainTotal.textContent='—';
  dataBody.innerHTML='<tr><td colspan="6" class="empty">'+message+'</td></tr>';
  chartArea.innerHTML='<div><div style="font-size:32px">📈</div><b>'+message+'</b></div>';
}

async function loadParameters(){
  if(!f.value) return;
  try{
    const response=await fetch('/api/parameters?facility='+encodeURIComponent(f.value));
    const result=await response.json();
    if(!response.ok || !result.ok) throw new Error(result.error || 'Không tải được thông số.');
    currentParameters=result.data || {waterLevel:[],rainfall:[]};

    // Giữ các lựa chọn giao diện quen thuộc, nhưng bổ sung thông số thực tế.
    const options=[
      {label:'Mực nước',value:''},
      ...(currentParameters.waterLevel||[]).map(x=>({label:x,value:x})),
      ...(currentParameters.rainfall||[]).map(x=>({label:x,value:x}))
    ];
    parameter.innerHTML='';
    const seen=new Set();
    options.forEach(o=>{
      if(seen.has(o.value+'|'+o.label)) return;
      seen.add(o.value+'|'+o.label);
      const opt=document.createElement('option');
      opt.value=o.value; opt.textContent=o.label; parameter.appendChild(opt);
    });
  }catch(err){
    console.error(err);
    parameter.innerHTML='<option value="">Không tải được thông số</option>';
  }
}

async function loadChartData(){
  if(!f.value){ resetData(); return; }
  state.textContent='Đang tải...';
  try{
    const params=new URLSearchParams({
      facility:f.value,
      year:'2026',
      days:String(periodDays())
    });
    const selected=parameter.value;
    if(selected){
      const isRain=(currentParameters.rainfall||[]).includes(selected);
      if(!isRain) params.set('waterParameter',selected);
      else params.set('rainfallParameters',selected);
    }

    const response=await fetch('/api/chart?'+params.toString());
    const result=await response.json();
    if(!response.ok || !result.ok) throw new Error(result.error || 'Không tải được dữ liệu.');
    currentData=result.data;
    renderData(currentData);
  }catch(err){
    console.error(err);
    state.textContent='Lỗi dữ liệu';
    resetData(err.message || 'Không tải được dữ liệu.');
  }
}

function renderData(data){
  const waterSeries=Array.isArray(data.water)?data.water:[];
  const latest=waterSeries.length ? waterSeries[waterSeries.length-1] : null;
  water.textContent=latest ? formatNumber(latest.value) : '—';
  state.textContent=latest ? 'Có dữ liệu' : 'Chưa có mực nước';
  mndbt.textContent=data.limits && data.limits.mndbt!=null ? formatNumber(data.limits.mndbt) : '—';
  mndgc.textContent=data.limits && data.limits.mndgc!=null ? formatNumber(data.limits.mndgc) : '—';
  rainTotal.textContent=data.totalRainfall!=null ? formatNumber(data.totalRainfall) : '—';

  const rows=[];
  waterSeries.slice().reverse().slice(0,10).forEach(p=>{
    const d=new Date(p.time);
    rows.push({time:d,facility:data.facility,parameter:p.parameter,value:p.value,unit:'m'});
  });
  (data.rainfall||[]).forEach(series=>{
    series.data.slice().reverse().slice(0,10).forEach(p=>{
      const d=new Date(p.time);
      rows.push({time:d,facility:data.facility,parameter:p.parameter,value:p.value,unit:'mm'});
    });
  });
  rows.sort((a,b)=>b.time-a.time);
  const limited=rows.slice(0,20);
  dataBody.innerHTML=limited.length ? limited.map(r=>
    '<tr><td>'+r.time.toLocaleDateString('vi-VN')+'</td><td>'+String(r.time.getHours()).padStart(2,'0')+':00</td><td>'+escapeHtml(r.facility)+'</td><td>'+escapeHtml(r.parameter)+'</td><td>'+formatNumber(r.value)+'</td><td>'+r.unit+'</td></tr>'
  ).join('') : '<tr><td colspan="6" class="empty">Không có dữ liệu trong khoảng thời gian đã chọn.</td></tr>';

  renderSimpleChart(data);
}

function escapeHtml(v){
  return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderSimpleChart(data){
  const waterSeries=Array.isArray(data.water)?data.water:[];
  const rainfallSeries=Array.isArray(data.rainfall)?data.rainfall:[];
  if(!waterSeries.length && !rainfallSeries.some(x=>x.data&&x.data.length)){
    chartArea.innerHTML='<div><div style="font-size:32px">📈</div><b>Không có dữ liệu</b><br>Trong khoảng thời gian đã chọn.</div>';
    return;
  }

  // Vẽ biểu đồ SVG nhẹ, không thêm thư viện ngoài.
  const W=900,H=320,L=55,R=55,T=25,B=45;
  const allTimes=[...waterSeries.map(p=>p.time),...rainfallSeries.flatMap(s=>s.data.map(p=>p.time))];
  const minT=Math.min(...allTimes), maxT=Math.max(...allTimes);
  const wx=waterSeries.map(p=>Number(p.value)).filter(Number.isFinite);
  const rv=rainfallSeries.flatMap(s=>s.data.map(p=>Number(p.value))).filter(Number.isFinite);
  const minW=wx.length?Math.min(...wx):0, maxW=wx.length?Math.max(...wx):1;
  const maxR=rv.length?Math.max(...rv):1;
  const x=t=>L+(maxT===minT?0.5:(t-minT)/(maxT-minT))*(W-L-R);
  const yW=v=>T+(maxW===minW?0.5:(maxW-v)/(maxW-minW))*(H-T-B);
  const yR=v=>T+(1-v/(maxR||1))*(H-T-B);
  const waterPts=waterSeries.map(p=>x(p.time)+','+yW(Number(p.value))).join(' ');
  const rainBars=[];
  rainfallSeries.forEach(series=>series.data.forEach(p=>{
    const bw=8;
    rainBars.push('<rect x="'+(x(p.time)-bw/2)+'" y="'+yR(Number(p.value))+'" width="'+bw+'" height="'+(H-B-yR(Number(p.value)))+'" opacity=".55"><title>'+escapeHtml(series.parameter)+': '+formatNumber(p.value)+' mm</title></rect>');
  }));
  chartArea.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="100%" role="img" aria-label="Biểu đồ mực nước và lượng mưa">'
    +'<line x1="'+L+'" y1="'+T+'" x2="'+L+'" y2="'+(H-B)+'" stroke="#b8c5d5"/><line x1="'+L+'" y1="'+(H-B)+'" x2="'+(W-R)+'" y2="'+(H-B)+'" stroke="#b8c5d5"/>'
    +rainBars.join('')
    +(waterPts?'<polyline points="'+waterPts+'" fill="none" stroke="#1769aa" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>':'')
    +waterSeries.map(p=>'<circle cx="'+x(p.time)+'" cy="'+yW(Number(p.value))+'" r="3" fill="#1769aa"><title>'+new Date(p.time).toLocaleString('vi-VN')+': '+formatNumber(p.value)+' m</title></circle>').join('')
    +'<text x="'+L+'" y="15" font-size="12" fill="#687386">H (m)</text><text x="'+(W-R-45)+'" y="15" font-size="12" fill="#687386">Mưa (mm)</text>'
    +'</svg>';
}

async function loadFacilities(){
  f.disabled=true;
  f.innerHTML='<option value="">Đang tải công trình...</option>';
  try{
    const response=await fetch('/api/facilities');
    const result=await response.json();
    if(!response.ok || !result.ok) throw new Error(result.error || 'Không tải được danh sách công trình.');
    const facilities=Array.isArray(result.data)?result.data:[];
    f.innerHTML='<option value="">Chọn công trình...</option>';
    facilities.forEach(name=>{
      const option=document.createElement('option'); option.value=name; option.textContent=name; f.appendChild(option);
    });
    if(!facilities.length){ f.innerHTML='<option value="">Không có công trình</option>'; s.textContent='Không có dữ liệu'; }
  }catch(err){
    console.error(err); f.innerHTML='<option value="">Lỗi tải dữ liệu</option>'; s.textContent='Không kết nối được API';
  }finally{ f.disabled=false; }
}

f.addEventListener('change',async()=>{
  setSelectedFacility();
  resetData('Đang tải dữ liệu thực tế...');
  await loadParameters();
  await loadChartData();
});
parameter.addEventListener('change',loadChartData);
period.addEventListener('change',loadChartData);

async function refreshModule(){
  if(!f.value){ setSelectedFacility(); resetData(); return; }
  setSelectedFacility();
  await loadParameters();
  await loadChartData();
}

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
    return {"module":"technical_module","version":"1.2","status":"ok","stage":3,"mode":"apps_script_proxy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("technical_module:app", host="0.0.0.0", port=8001, reload=False)
