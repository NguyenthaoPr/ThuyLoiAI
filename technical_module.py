from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ============================================================
# THUY LOI AI - TECHNICAL MODULE V1
# BUOC 1: GIAO DIEN DOC LAP
# Khong import, khong sua server.py
# ============================================================

app = FastAPI(
    title="THUY LOI AI - Thong so ky thuat",
    version="1.0.0",
)

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
<option>Chon cong trinh...</option>
<option>Ho Vinh Trinh</option><option>Ho Phu Loc</option><option>Ho Phu Ninh</option>
<option>Ho Dong Tien</option><option>Ho Cao Ngan</option><option>Dong Quang</option>
<option>Dong Ho</option><option>Dap Thanh Quyt</option><option>Dap Bau Nit</option>
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
<div class="card"><div class="label">MNDBT</div><div class="value">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">MNDGC</div><div class="value">—</div><div class="unit">m</div></div>
<div class="card"><div class="label">TONG LUONG MUA</div><div class="value">—</div><div class="unit">mm</div></div>
</section>

<section class="grid">
<div class="panel"><div class="head"><div><div class="head-title">Bieu do dien bien</div><div class="head-sub">Muc nuoc va luong mua theo thoi gian</div></div>
<div class="pills"><span class="pill">X</span><span class="pill">X T1</span><span class="pill">X C24</span></div></div>
<div class="chart"><div class="placeholder"><div><div style="font-size:32px">📈</div><b>Bieu do se ket noi o Buoc 4</b><br>Buoc 1 chi dung giao dien, chua doc AI_DATA.</div></div></div></div>

<div class="panel"><div class="head"><div><div class="head-title">Thong tin cong trinh</div><div class="head-sub">Khu vuc thong tin ky thuat</div></div></div>
<div style="padding:16px"><div class="card" style="box-shadow:none;margin-bottom:10px"><div class="label">CONG TRINH DANG CHON</div><b id="selected">Chua chon</b></div>
<div class="card" style="box-shadow:none"><div class="label">TRAM MUA</div><div class="pills"><span class="pill">X</span><span class="pill">X T1</span><span class="pill">X C24</span></div></div></div></div>
</section>

<section class="panel" style="margin-top:16px"><div class="head"><div><div class="head-title">Du lieu gan nhat</div><div class="head-sub">Bang chi tiet se ket noi Data Engine o Buoc 3</div></div></div>
<div class="table"><table><thead><tr><th>Ngay</th><th>Gio</th><th>Cong trinh</th><th>Thong so</th><th>Gia tri</th><th>Don vi</th></tr></thead>
<tbody><tr><td colspan="6" class="empty">Chua ket noi du lieu van hanh.</td></tr></tbody></table></div></section>
<div class="footer">THUY LOI AI · Technical Module V1 · Buoc 1 — Giao dien doc lap</div>
</main>

<script>
const f=document.getElementById('facility');
const s=document.getElementById('selected');
f.addEventListener('change',()=>s.textContent=f.value==='Chon cong trinh...'?'Chua chon':f.value);
function refreshModule(){
 if(f.value==='Chon cong trinh...'){s.textContent='Chua chon';return;}
 s.textContent=f.value;
 console.log({facility:f.value,parameter:document.getElementById('parameter').value,period:document.getElementById('period').value});
}
</script>
</body>
</html>
'''

@app.get("/", response_class=HTMLResponse)
def technical_dashboard():
    return HTML

@app.get("/health")
def health():
    return {"module":"technical_module","version":"1.0","status":"ok","stage":1,"mode":"ui_only"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("technical_module:app", host="0.0.0.0", port=8001, reload=False)
