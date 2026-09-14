# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ============================================================
# THUY LOI AI - TECHNICAL MODULE V1.6
# BUOC 1: GIAO DIEN DOC LAP
# Khong import, khong sua server.py
# ============================================================

app = FastAPI(
    title="THUY LOI AI - Thong so ky thuat",
    version="1.5.0",
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
.table{overflow-x:auto}.empty{text-align:center;color:var(--muted);padding:26px}.mobile-data{display:none;padding:10px}.data-item{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:8px}.data-item .dt{font-size:11px;color:var(--muted);margin-bottom:4px}.data-item .pn{font-weight:700;font-size:14px}.data-item .pv{font-weight:800;font-size:18px}.summary-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.summary-item{border:1px solid var(--line);border-radius:12px;padding:12px;background:#fafbfd}.summary-label{font-size:11px;color:var(--muted);margin-bottom:5px}.summary-value{font-weight:800;font-size:17px}.summary-note{font-size:12px;color:var(--muted);margin-top:3px}.rain-series{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.rain-chip{border:1px solid var(--line);border-radius:999px;padding:7px 10px;background:#fff;font-size:12px}.trend-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:10px}.trend-item{border:1px solid var(--line);border-radius:12px;padding:12px;background:#fff}.trend-label{font-size:11px;color:var(--muted);margin-bottom:5px}.trend-value{font-weight:800;font-size:17px}.trend-note{font-size:11px;color:var(--muted);margin-top:3px}
.footer{text-align:center;color:var(--muted);font-size:11px;padding:20px 10px 28px}
@media(max-width:1050px){.cards{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}}
@media(max-width:720px){.container{padding:12px}.toolbar{grid-template-columns:1fr 1fr}.toolbar .control:first-child{grid-column:1/-1}.cards{grid-template-columns:1fr 1fr;gap:8px}.card{padding:13px}.value{font-size:21px}.chart{height:300px;padding:10px}button{min-height:48px}.summary-grid{grid-template-columns:1fr 1fr}.trend-grid{grid-template-columns:1fr 1fr}.table{display:none}.mobile-data{display:block}}
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
<div class="card"><div class="label">TRANG THAI</div><div class="value ok" id="state">—</div><div class="unit" id="stateDetail">Chưa có dữ liệu</div></div>
<div class="card"><div class="label">MNDBT</div><div class="value" id="mndbt">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">MNDGC</div><div class="value" id="mndgc">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">TONG LUONG MUA</div><div class="value" id="rainTotal">—</div><div class="unit">mm</div></div>
</section>

<section class="grid">
<div class="panel"><div class="head"><div><div class="head-title">Bieu do dien bien</div><div class="head-sub">Muc nuoc va luong mua theo thoi gian</div></div>
<div class="pills" id="rainPills"><span class="pill">X</span><span class="pill">X T1</span><span class="pill">X C24</span></div></div>
<div class="chart"><div id="chartArea" class="placeholder"><div><div style="font-size:32px">📈</div><b>Đang chờ dữ liệu</b><br>Chọn công trình để tải dữ liệu thực tế.</div></div></div></div>

<div class="panel"><div class="head"><div><div class="head-title">Thong tin cong trinh</div><div class="head-sub">Khu vuc thong tin ky thuat</div></div></div>
<div style="padding:16px"><div class="card" style="box-shadow:none;margin-bottom:10px"><div class="label">CONG TRINH DANG CHON</div><b id="selected">Chua chon</b></div>
<div class="card" style="box-shadow:none"><div class="label">TRAM MUA</div><div class="pills"><span class="pill">X</span><span class="pill">X T1</span><span class="pill">X C24</span></div></div></div></div>
</section>

<section class="panel" style="margin-top:16px"><div class="head"><div><div class="head-title">Tom tat ky thuat</div><div class="head-sub">Phan tich so lieu tu du lieu thuc te, khong tu dong gan muc canh bao</div></div></div><div id="technicalSummary" style="padding:16px"><div class="empty">Chọn công trình để phân tích.</div></div></section>
<section class="panel" style="margin-top:16px"><div class="head"><div><div class="head-title">Du lieu gan nhat</div><div class="head-sub">Dữ liệu thực tế từ AI_DATA qua Apps Script API</div></div></div>
<div class="table"><table><thead><tr><th>Ngay</th><th>Gio</th><th>Cong trinh</th><th>Thong so</th><th>Gia tri</th><th>Don vi</th></tr></thead>
<tbody id="dataBody"><tr><td colspan="6" class="empty">Chọn công trình để tải dữ liệu.</td></tr></tbody></table></div><div id="mobileData" class="mobile-data"></div></section>
<div class="footer">THUY LOI AI · Technical Module V1.5 · Bước 3.6 — Xu hướng mực nước</div>
</main>

<script>
const f=document.getElementById('facility');
const parameter=document.getElementById('parameter');
const period=document.getElementById('period');
const s=document.getElementById('selected');
const water=document.getElementById('water');
const state=document.getElementById('state');
const stateDetail=document.getElementById('stateDetail');
const mndbt=document.getElementById('mndbt');
const rainPills=document.getElementById('rainPills');
const mndgc=document.getElementById('mndgc');
const rainTotal=document.getElementById('rainTotal');
const dataBody=document.getElementById('dataBody');
const chartArea=document.getElementById('chartArea');
const technicalSummary=document.getElementById('technicalSummary');
const mobileData=document.getElementById('mobileData');

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
  stateDetail.textContent='Chưa có dữ liệu';
  mndbt.textContent='—';
  mndgc.textContent='—';
  rainTotal.textContent='—';
  dataBody.innerHTML='<tr><td colspan="6" class="empty">'+message+'</td></tr>';
  chartArea.innerHTML='<div><div style="font-size:32px">📈</div><b>'+message+'</b></div>';
  technicalSummary.innerHTML='<div class="empty">'+message+'</div>';
  mobileData.innerHTML='';
}

async function loadParameters(){
  if(!f.value) return;
  try{
    const response=await fetch('/api/parameters?facility='+encodeURIComponent(f.value));
    const result=await response.json();
    if(!response.ok || !result.ok) throw new Error(result.error || 'Không tải được thông số.');
    currentParameters=result.data || {waterLevel:[],rainfall:[]};

    // Hiển thị đúng các chuỗi mưa thực tế mà công trình có.
    const rainList=currentParameters.rainfall||[];
    rainPills.innerHTML=rainList.length
      ? rainList.map(x=>'<span class="pill">'+escapeHtml(x.replace(/\\s*\\([^)]*\\)/g,''))+'</span>').join('')
      : '<span class="pill">Không có chuỗi mưa</span>';

    // Giữ lựa chọn Mực nước tổng quát và bổ sung thông số thực tế.
    const options=[
      {label:'Mực nước',value:''},
      ...(currentParameters.waterLevel||[]).map(x=>({label:x,value:x})),
      ...(rainList).map(x=>({label:x,value:x}))
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
  stateDetail.textContent='Đang lấy dữ liệu thực tế từ AI_DATA';
  try{
    const params=new URLSearchParams({
      facility:f.value,
      year:String(new Date().getFullYear()),
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
  if(latest){
    const latestDate=new Date(latest.time);
    stateDetail.textContent='Cập nhật '+latestDate.toLocaleString('vi-VN');
  }else if(data.updatedAt){
    stateDetail.textContent='API cập nhật '+new Date(data.updatedAt).toLocaleString('vi-VN');
  }else{
    stateDetail.textContent='Không có mực nước trong khoảng chọn';
  }
  mndbt.textContent=data.limits && data.limits.mndbt!=null ? formatNumber(data.limits.mndbt) : '—';
  mndgc.textContent=data.limits && data.limits.mndgc!=null ? formatNumber(data.limits.mndgc) : '—';
  rainTotal.textContent=data.totalRainfall!=null ? formatNumber(data.totalRainfall) : '—';

  const rows=[];
  waterSeries.slice().reverse().slice(0,10).forEach(p=>{ const d=new Date(p.time); rows.push({time:d,facility:data.facility,parameter:p.parameter,value:p.value,unit:'m'}); });
  (data.rainfall||[]).forEach(series=>{ series.data.slice().reverse().slice(0,10).forEach(p=>{ const d=new Date(p.time); rows.push({time:d,facility:data.facility,parameter:p.parameter,value:p.value,unit:'mm'}); }); });
  rows.sort((a,b)=>b.time-a.time);
  const limited=rows.slice(0,20);
  dataBody.innerHTML=limited.length ? limited.map(r=>'<tr><td>'+r.time.toLocaleDateString('vi-VN')+'</td><td>'+String(r.time.getHours()).padStart(2,'0')+':00</td><td>'+escapeHtml(r.facility)+'</td><td>'+escapeHtml(r.parameter)+'</td><td>'+formatNumber(r.value)+'</td><td>'+r.unit+'</td></tr>').join('') : '<tr><td colspan="6" class="empty">Không có dữ liệu trong khoảng thời gian đã chọn.</td></tr>';
  mobileData.innerHTML=limited.length ? limited.map(r=>'<div class="data-item"><div class="dt">'+r.time.toLocaleDateString('vi-VN')+' · '+String(r.time.getHours()).padStart(2,'0')+':00</div><div class="pn">'+escapeHtml(r.parameter)+'</div><div class="pv">'+formatNumber(r.value)+' '+r.unit+'</div></div>').join('') : '<div class="empty">Không có dữ liệu trong khoảng thời gian đã chọn.</div>';
  renderTechnicalSummary(data,waterSeries);
  renderSimpleChart(data);
}

function renderTechnicalSummary(data, waterSeries){
  const latest=waterSeries.length?waterSeries[waterSeries.length-1]:null; const previous=waterSeries.length>1?waterSeries[waterSeries.length-2]:null;
  const mndbt=Number(data.limits&&data.limits.mndbt), mndgc=Number(data.limits&&data.limits.mndgc), h=latest?Number(latest.value):null;
  const delta=(latest&&previous)?h-Number(previous.value):null;
  let relation='Chưa đủ dữ liệu để so sánh';
  if(Number.isFinite(h)&&Number.isFinite(mndbt)&&Number.isFinite(mndgc)) relation=h<mndbt?'Mực nước đang thấp hơn MNDBT':(h<=mndgc?'Mực nước nằm từ MNDBT đến MNDGC':'Mực nước cao hơn MNDGC');
  else if(Number.isFinite(h)&&Number.isFinite(mndbt)) relation=h<mndbt?'Mực nước đang thấp hơn MNDBT':'Mực nước không thấp hơn MNDBT';
  const rain=(data.rainfall||[]).filter(s=>Array.isArray(s.data)&&s.data.length);
  const rainChips=rain.map(s=>'<span class="rain-chip"><b>'+escapeHtml(s.parameter)+'</b>: '+formatNumber((data.rainfallTotalsByParameter||{})[s.parameter])+' mm</span>').join('');
  technicalSummary.innerHTML='<div class="summary-grid">'
    +'<div class="summary-item"><div class="summary-label">SO VOI MNDBT</div><div class="summary-value">'+(Number.isFinite(h)&&Number.isFinite(mndbt)?formatNumber(h-mndbt)+' m':'—')+'</div><div class="summary-note">'+relation+'</div></div>'
    +'<div class="summary-item"><div class="summary-label">SO VOI MNDGC</div><div class="summary-value">'+(Number.isFinite(h)&&Number.isFinite(mndgc)?formatNumber(h-mndgc)+' m':'—')+'</div><div class="summary-note">'+(Number.isFinite(h)&&Number.isFinite(mndgc)?(h<=mndgc?'Chưa vượt MNDGC':'Đã vượt MNDGC'):'Chưa đủ giới hạn')+'</div></div>'
    +'<div class="summary-item"><div class="summary-label">BIEN DONG GAN NHAT</div><div class="summary-value">'+(delta!==null?(delta>=0?'+':'')+formatNumber(delta)+' m':'—')+'</div><div class="summary-note">'+(delta!==null?'So với lần đo liền trước':'Chưa đủ 2 lần đo')+'</div></div></div>'
    +renderTrendHtml(waterSeries)
    +(rainChips?'<div class="summary-label" style="margin-top:14px">LUONG MUA THEO TUNG CHUOI</div><div class="rain-series">'+rainChips+'</div>':'');
}

function renderTrendHtml(series){
  const points=series.map(p=>({time:new Date(p.time),value:Number(p.value)})).filter(p=>Number.isFinite(p.value)&&Number.isFinite(p.time.getTime())).sort((a,b)=>a.time-b.time);
  if(!points.length) return '';
  const latest=points[points.length-1];
  function windowStats(hours){
    const start=new Date(latest.time.getTime()-hours*3600000);
    const inWindow=points.filter(p=>p.time>=start&&p.time<=latest.time);
    if(inWindow.length<2) return null;
    const first=inWindow[0], last=latest;
    return {delta:last.value-first.value,min:Math.min(...inWindow.map(p=>p.value)),max:Math.max(...inWindow.map(p=>p.value)),count:inWindow.length,first:first.time};
  }
  const w24=windowStats(24), w72=windowStats(72), w168=windowStats(168);
  function card(label,stats){
    if(!stats) return '<div class="trend-item"><div class="trend-label">'+label+'</div><div class="trend-value">—</div><div class="trend-note">Chưa đủ 2 lần đo</div></div>';
    const d=stats.delta;
    const sign=d>0?'+':'';
    const direction=d>0?'Tăng':(d<0?'Giảm':'Ổn định');
    return '<div class="trend-item"><div class="trend-label">'+label+'</div><div class="trend-value">'+sign+formatNumber(d)+' m</div><div class="trend-note">'+direction+' · '+stats.count+' lần đo</div></div>';
  }
  const min=Math.min(...points.map(p=>p.value)), max=Math.max(...points.map(p=>p.value));
  return '<div class="summary-label" style="margin-top:16px">XU HUONG MUC NUOC</div><div class="trend-grid">'
    +card('24 GIO',w24)+card('3 NGAY',w72)+card('7 NGAY',w168)
    +'<div class="trend-item"><div class="trend-label">THAP NHAT</div><div class="trend-value">'+formatNumber(min)+' m</div><div class="trend-note">Trong khoảng đang chọn</div></div>'
    +'<div class="trend-item"><div class="trend-label">CAO NHAT</div><div class="trend-value">'+formatNumber(max)+' m</div><div class="trend-note">Trong khoảng đang chọn</div></div>'
    +'</div>';
}

function escapeHtml(v){
  return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderSimpleChart(data){
  const waterSeries=Array.isArray(data.water)?data.water:[];
  const rainfallSeries=Array.isArray(data.rainfall)?data.rainfall:[];
  const validRain=rainfallSeries.filter(x=>x.data&&x.data.length);
  if(!waterSeries.length && !validRain.length){
    chartArea.innerHTML='<div><div style="font-size:32px">📈</div><b>Không có dữ liệu</b><br>Trong khoảng thời gian đã chọn.</div>';
    return;
  }

  const W=900,H=340,L=62,R=62,T=35,B=48;
  const allTimes=[...waterSeries.map(p=>p.time),...validRain.flatMap(s=>s.data.map(p=>p.time))];
  const minT=Math.min(...allTimes), maxT=Math.max(...allTimes);
  const wx=waterSeries.map(p=>Number(p.value)).filter(Number.isFinite);
  const rv=validRain.flatMap(s=>s.data.map(p=>Number(p.value))).filter(Number.isFinite);
  let minW=wx.length?Math.min(...wx):0, maxW=wx.length?Math.max(...wx):1;
  if(maxW===minW){minW-=1;maxW+=1;} else {const pad=(maxW-minW)*.12;minW-=pad;maxW+=pad;}
  const maxR=rv.length?Math.max(...rv):1;
  const x=t=>L+(maxT===minT?0.5:(t-minT)/(maxT-minT))*(W-L-R);
  const yW=v=>T+(maxW-v)/(maxW-minW)*(H-T-B);
  const yR=v=>T+(1-v/(maxR||1))*(H-T-B);
  const waterPts=waterSeries.map(p=>x(p.time)+','+yW(Number(p.value))).join(' ');
  const rainBars=[];
  validRain.forEach(series=>series.data.forEach(p=>{
    const bw=7;
    const yy=yR(Number(p.value));
    rainBars.push('<rect x="'+(x(p.time)-bw/2)+'" y="'+yy+'" width="'+bw+'" height="'+Math.max(0,H-B-yy)+'" opacity=".55"><title>'+escapeHtml(series.parameter)+': '+formatNumber(p.value)+' mm</title></rect>');
  }));

  const grid=[];
  for(let i=0;i<=4;i++){
    const yy=T+i*(H-T-B)/4;
    const val=maxW-i*(maxW-minW)/4;
    grid.push('<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="#e5eaf1"/><text x="'+(L-8)+'" y="'+(yy+4)+'" text-anchor="end" font-size="11" fill="#687386">'+formatNumber(val)+'</text>');
  }

  // Hiển thị MNDBT/MNDGC nếu nguồn đã cung cấp, chỉ để tham chiếu.
  const limitLines=[];
  const limits=data.limits||{};
  [['mndbt','MNDBT'],['mndgc','MNDGC']].forEach(([key,label])=>{
    const v=Number(limits[key]);
    if(Number.isFinite(v) && v>=minW && v<=maxW){
      const yy=yW(v);
      limitLines.push('<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="#9aa6b5" stroke-dasharray="6 5"/><text x="'+(W-R-4)+'" y="'+(yy-5)+'" text-anchor="end" font-size="10" fill="#687386">'+label+' '+formatNumber(v)+'</text>');
    }
  });

  const points=waterSeries.map(p=>'<circle cx="'+x(p.time)+'" cy="'+yW(Number(p.value))+'" r="3.2" fill="#1769aa"><title>'+new Date(p.time).toLocaleString('vi-VN')+': '+formatNumber(p.value)+' m</title></circle>').join('');
  const legend='<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-bottom:6px;font-size:12px;color:#687386">'
    +(waterSeries.length?'<span>━ <b>Mực nước</b></span>':'')
    +(validRain.length?'<span>▮ <b>Lượng mưa</b></span>':'')
    +'</div>';

  chartArea.innerHTML=legend+'<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="calc(100% - 25px)" role="img" aria-label="Biểu đồ mực nước và lượng mưa">'
    +grid.join('')+limitLines.join('')+rainBars.join('')
    +(waterPts?'<polyline points="'+waterPts+'" fill="none" stroke="#1769aa" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>':'')
    +points
    +'<text x="'+L+'" y="18" font-size="12" fill="#687386">H (m)</text><text x="'+(W-R)+'" y="18" text-anchor="end" font-size="12" fill="#687386">Mưa (mm)</text>'
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
    return {"module":"technical_module","version":"1.6.0","status":"ok","stage":4,"mode":"apps_script_proxy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("technical_module:app", host="0.0.0.0", port=8001, reload=False)
