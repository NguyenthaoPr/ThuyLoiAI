from __future__ import annotations
import json, re, unicodedata
from pathlib import Path
from difflib import SequenceMatcher

DEFAULT_DATA_FILE = Path(__file__).with_name('phu_luc_09_vgtb_2027_v6.json')

def norm(s):
    s=str(s or '').strip().lower().replace('đ','d')
    s=unicodedata.normalize('NFD',s)
    s=''.join(c for c in s if unicodedata.category(c)!='Mn')
    return re.sub(r'\s+',' ',s).strip()

def num(v):
    if v is None or v=='': return None
    try: return float(v)
    except: return None

def fmt(v):
    if v is None: return None
    return f'{v:,.2f}'.replace(',','X').replace('.',',').replace('X','.')

class PlanDataEngineV6:
    """Deterministic hierarchical query engine for Phụ lục 09 VGTB 2027."""
    def __init__(self,data_file=DEFAULT_DATA_FILE,rows=None):
        self.data_file=Path(data_file)
        if rows is None:
            obj=json.loads(self.data_file.read_text(encoding='utf-8'))
            rows=obj.get('records',[])
        self.rows=[]
        for r in rows:
            x=dict(r)
            x['values']=dict(x.get('values') or {})
            self.rows.append(x)
        self.names=sorted({r['ten_cong_trinh'] for r in self.rows if r.get('ten_cong_trinh')}, key=lambda x:(-len(norm(x)),norm(x)))
        self.locations=sorted({r['dia_diem'] for r in self.rows if r.get('dia_diem')}, key=lambda x:(-len(norm(x)),norm(x)))

    def _year(self,q):
        m=re.search(r'\b(20\d{2})\b',norm(q)); return int(m.group(1)) if m else 2027

    def _group(self,q):
        n=norm(q)
        if 'dong luc' in n: return 'CẤP NƯỚC BẰNG ĐỘNG LỰC'
        if 'trong luc' in n: return 'CẤP NƯỚC BẰNG TRỌNG LỰC'
        return None

    def _construction_candidates(self,q):
        n=norm(q)
        exact=[name for name in self.names if norm(name) in n]
        if exact:
            # Nếu nhiều tên cùng khớp (ví dụ Kênh N1 và Kênh N1 Nam Phước),
            # chọn cụm tên dài nhất để không lấy nhầm công trình cha.
            longest=max(len(norm(x)) for x in exact)
            return [x for x in exact if len(norm(x))==longest]
        # Alias common water-works wording.
        aliases=[
            ('ho phu loc','Hồ chứa nước Phú Lộc'),('phu loc','Hồ chứa nước Phú Lộc'),
            ('ho phu ninh','Hồ chứa nước Phú Ninh'),('phu ninh','Hồ chứa nước Phú Ninh'),
            ('ho phu loc','Hồ chứa nước Phú Lộc')]
        for a,name in aliases:
            if a in n and name in self.names: return [name]
        # Conservative token similarity; avoid short generic matches.
        tokens=[t for t in n.split() if t not in {'ho','chua','nuoc','tram','bom','dap','kenh','cong','tuoi','dien','tich','vu','nam','co','bao','nhieu','ha'}]
        scored=[]
        for name in self.names:
            nt=[t for t in norm(name).split() if t not in {'ho','chua','nuoc','tram','bom','dap','kenh','cong'}]
            if not nt: continue
            overlap=len(set(tokens)&set(nt))/len(set(nt))
            seq=SequenceMatcher(None,' '.join(tokens), ' '.join(nt)).ratio() if tokens else 0
            score=max(overlap,seq)
            if score>=0.78: scored.append((score,name))
        scored.sort(reverse=True)
        return [x[1] for x in scored[:3]]

    def _location(self,q):
        n=norm(q)
        hits=[x for x in self.locations if norm(x) in n]
        return hits[0] if hits else None

    def _season(self,q):
        n=norm(q)
        if 'dong xuan' in n: return 'dong_xuan','Đông Xuân'
        if 'he thu' in n or 'he-thu' in n: return 'he_thu','Hè Thu'
        return 'ca_nam','Cả năm'

    def _crop(self,q):
        n=norm(q)
        if any(x in n for x in ['ntts','nuoi trong thuy san','thuy san','nuoi ca','nuoi tom']): return 'ntts','NTTS'
        if 'cay dl' in n or 'cay dai ngay' in n or 'cay lâu nam' in n: return 'cay_dl','Cây DL'
        if 'lua' in n: return 'lua','Lúa'
        if 'mau' in n or 'hoa mau' in n: return 'mau','Màu'
        return 'tong','Tổng'

    def _method(self,q):
        n=norm(q)
        if 'chu dong 1 phan' in n: return 'Chủ động 1 phần'
        if 'tao nguon' in n: return 'Tạo nguồn'
        if 'chu dong' in n: return 'Chủ động'
        return 'Tổng'

    def _field(self,season,crop):
        if crop!='tong': return f'{season}_{crop}'
        if season=='ca_nam': return 'ca_nam_tong'
        return f'{season}_sum'

    def _value(self,r,season,crop):
        vals=r.get('values',{}) or {}
        if crop!='tong' or season=='ca_nam':
            return num(vals.get(self._field(season,crop)))
        parts=[num(vals.get(f'{season}_{c}')) or 0.0 for c in ('lua','mau','ntts','cay_dl')]
        # Nếu tất cả đều vắng dữ liệu, trả None; nếu có số 0 thực sự thì trả 0.
        keys=[f'{season}_{c}' for c in ('lua','mau','ntts','cay_dl')]
        if not any(vals.get(k) is not None for k in keys): return None
        return round(sum(parts),2)

    def _value(self,r,season,crop):
        vals=r.get('values',{}) or {}
        if crop!='tong' or season=='ca_nam':
            return num(vals.get(self._field(season,crop)))
        keys=[f'{season}_{c}' for c in ('lua','mau','ntts','cay_dl')]
        if not any(vals.get(k) is not None for k in keys): return None
        return round(sum(num(vals.get(k)) or 0.0 for k in keys),2)

    def _base_rows(self,q):
        y=self._year(q); g=self._group(q); names=self._construction_candidates(q); location=self._location(q); method=self._method(q)
        rows=[r for r in self.rows if int(r.get('nam',2027))==y]
        if g: rows=[r for r in rows if r.get('nhom')==g]
        if names: rows=[r for r in rows if r.get('ten_cong_trinh') in names]
        if location: rows=[r for r in rows if norm(r.get('dia_diem'))==norm(location)]
        return y,g,names,location,method,rows

    def query(self,question):
        q=question; n=norm(q); y,g,names,location,method,rows=self._base_rows(q); season,season_label=self._season(q); crop,crop_label=self._crop(q); field=self._field(season,crop)
        explicit_method=method!='Tổng'
        ranking=bool(re.search(r'\btop\s*\d+\b',n) or 'lon nhat' in n or 'cao nhat' in n)
        if ranking and not names:
            m=re.search(r'\btop\s*(\d+)\b',n); limit=int(m.group(1)) if m else 5
            candidates=[]
            for r in rows:
                if r.get('row_type')!='construction': continue
                v=self._value(r,season,crop)
                if v is not None: candidates.append((v,r))
            candidates.sort(reverse=True,key=lambda z:z[0])
            return self._result(q,'ranking',[(r,v) for v,r in candidates[:limit]],y,g,location,method,season,crop,field)

        # Chi tiết theo vụ/loại cây: giữ nguyên cấu trúc bảng, không để LLM tự cộng.
        detail_intent = any(x in n for x in ['chi tiet','gom nhung','bao gom','phan theo','co nhung loai'])
        if names and detail_intent and crop=='tong':
            if explicit_method:
                preferred=[r for r in rows if norm(r.get('bien_phap'))==norm(method)]
                sub=[r for r in preferred if r.get('row_type')=='subrow']
                detail_rows=sub or preferred
            else:
                detail_rows=[r for r in rows if r.get('row_type')=='construction' and norm(r.get('bien_phap')) in ('','tong')]
            if len(detail_rows)==1:
                r=detail_rows[0]
                breakdown=[]
                for c,label in [('lua','Lúa'),('mau','Màu'),('ntts','NTTS'),('cay_dl','Cây DL')]:
                    v=self._value(r,season,c)
                    if v is not None: breakdown.append({'loai':label,'value':round(v,2),'unit':'ha'})
                total=self._value(r,season,'tong')
                return {'found':True,'engine':'PLAN_DATA_V6','operation':'detail','question':q,'construction':r.get('ten_cong_trinh'),'address':r.get('dia_diem'),'method':method,'season':season,'season_label':season_label,'breakdown':breakdown,'total':total,'unit':'ha','source':{'dataset':'Phụ lục 09 (VG-TB) (2027)','excel_row':r.get('excel_row'),'row_type':r.get('row_type')}}

        # No method = construction summary. Explicit method = method row.
        if names:
            if explicit_method:
                preferred=[r for r in rows if norm(r.get('bien_phap'))==norm(method)]
                sub=[r for r in preferred if r.get('row_type')=='subrow']
                rows=sub or preferred
            else:
                rows=[r for r in rows if r.get('row_type')=='construction' and norm(r.get('bien_phap')) in ('','tong')]

        # Aggregate across all construction rows only.
        aggregate=not names and any(x in n for x in ['tong dien tich','tong cong','tat ca','toan bo','tong'])
        if aggregate:
            vals=[]
            for r in rows:
                if r.get('row_type')!='construction': continue
                v=self._value(r,season,crop)
                if v is not None: vals.append((r,v))
            total=round(sum(v for _,v in vals),2)
            return {'found':bool(vals),'engine':'PLAN_DATA_V6','operation':'sum','value':total,'unit':'ha','count':len(vals),'filters':self._filters(y,g,names,location,method,season,crop,field)}

        items=[]
        for r in rows:
            v=self._value(r,season,crop)
            if v is not None: items.append((r,v))
        if not items:
            return {'found':False,'engine':'PLAN_DATA_V6','operation':'lookup','question':q,'reason':'Không tìm thấy bản ghi phù hợp.','filters':self._filters(y,g,names,location,method,season,crop,field)}
        if len(items)==1:
            r,v=items[0]
            return {'found':True,'engine':'PLAN_DATA_V6','operation':'lookup','question':q,'value':round(v,2),'unit':'ha','construction':r.get('ten_cong_trinh'),'address':r.get('dia_diem'),'location':location,'method':method,'season':season,'season_label':season_label,'crop':crop,'crop_label':crop_label,'field':field,'source':{'dataset':'Phụ lục 09 (VG-TB) (2027)','excel_row':r.get('excel_row'),'row_type':r.get('row_type')}}
        # Ambiguous duplicate construction names: return all, never guess.
        return self._result(q,'list',items,y,g,location,method,season,crop,field)

    def _filters(self,y,g,names,location,method,season,crop,field): return {'year':y,'group':g,'construction':names,'location':location,'location':location,'method':method,'season':season,'crop':crop,'field':field}

    def _result(self,q,op,items,y,g,location,method,season,crop,field):
        return {'found':bool(items),'engine':'PLAN_DATA_V6','operation':op,'question':q,'items':[{'construction':r.get('ten_cong_trinh'),'address':r.get('dia_diem'),'method':r.get('bien_phap'),'value':round(v,2),'unit':'ha','excel_row':r.get('excel_row'),'row_type':r.get('row_type')} for r,v in items], 'filters':self._filters(y,g,[],location,method,season,crop,field)}

if __name__=='__main__':
    e=PlanDataEngineV6()
    tests=[
      ('Hồ chứa nước Phú Lộc tưới bao nhiêu ha?',318.46),
      ('Hồ chứa nước Phú Lộc vụ Đông Xuân tưới bao nhiêu ha?',159.23),
      ('Hồ chứa nước Phú Lộc vụ Đông Xuân có bao nhiêu ha lúa?',132.70),
      ('Hồ chứa nước Phú Lộc cả năm có bao nhiêu ha lúa?',265.40),
      ('Hồ chứa nước Phú Lộc Chủ động bao nhiêu ha?',318.46),
      ('Hồ chứa nước Phú Lộc Tạo nguồn bao nhiêu ha?',0.0),
      ('Hồ chứa nước Phú Ninh Chủ động bao nhiêu ha?',500.50),
      ('Hồ chứa nước Phú Ninh Tạo nguồn bao nhiêu ha?',91.00),
      ('Kênh N1 Nam Phước tưới bao nhiêu ha?',75.60),
    ]
    ok=0
    for q,want in tests:
        r=e.query(q); got=r.get('value') if r.get('operation')=='lookup' else None
        good=got is not None and abs(got-want)<1e-6
        print(('PASS' if good else 'FAIL'),q,'=>',r)
        ok+=good
    print(f'{ok}/{len(tests)} PASS')
