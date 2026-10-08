# PLAN_DATA_ENGINE — THỦY LỢI AI
# Structured query engine for normalized irrigation planning data.
# Runtime dependency: Python standard library only.

from __future__ import annotations
import json, re, unicodedata
from pathlib import Path
from difflib import SequenceMatcher

DEFAULT_DATA_FILE = Path(__file__).with_name("phu_luc_09_vgtb_2027_normalized.json")

def _norm(s: object) -> str:
    s = str(s or "").strip().lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)

def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None

class PlanDataEngine:
    """Query plan-table data deterministically; the LLM does not calculate numbers."""

    def __init__(self, data_file: str | Path = DEFAULT_DATA_FILE, rows=None):
        self.data_file = Path(data_file)
        if rows is None:
            obj = json.loads(self.data_file.read_text(encoding="utf-8"))
            default_year = int(obj.get("nam_ke_hoach", 2027)) if isinstance(obj, dict) else 2027
            raw = obj.get("records") if isinstance(obj, dict) else obj
            rows = []
            for r in raw or []:
                x = dict(r)
                vals = x.pop("values", {}) or {}
                x.update(vals)
                x["nam"] = default_year
                rows.append(x)
        self.rows = rows or []
        self._names = sorted(
            {str(r.get("ten_cong_trinh", "")).strip()
             for r in self.rows if r.get("ten_cong_trinh")},
            key=len, reverse=True
        )

    def _find_year(self, q):
        m = re.search(r"\b(20\d{2})\b", q)
        return int(m.group(1)) if m else (self.rows[0].get("nam", 2027) if self.rows else 2027)

    def _find_group(self, q):
        if "dong luc" in q:
            return "CẤP NƯỚC BẰNG ĐỘNG LỰC"
        if "trong luc" in q:
            return "CẤP NƯỚC BẰNG TRỌNG LỰC"
        return None

    def _find_construction(self, q):
        nq = _norm(q)
        for name in self._names:
            if _norm(name) in nq:
                return name
        best, score = None, 0.0
        qtokens = set(nq.split())
        stop = {"tram", "bom", "ho", "chua", "nuoc", "dap", "kenh", "cong", "kc", "tb"}
        qtokens -= stop
        for name in self._names:
            nn = _norm(name)
            ntokens = set(nn.split()) - stop
            overlap_count = len(qtokens & ntokens)
            overlap = overlap_count / max(1, len(ntokens))
            # Prefer a construction whose distinctive tokens occur in the question.
            s = overlap + (0.15 if overlap_count >= 2 else 0.0)
            if s > score:
                score, best = s, name
        return best if score >= 0.48 else None

    def _find_row_method(self, q):
        if "chu dong 1 phan" in q:
            return "Chủ động 1 phần"
        if "chu dong" in q:
            return "Chủ động"
        if "tao nguon" in q:
            return "Tạo nguồn"
        return "Tổng"

    def _find_season(self, q):
        if "dong xuan" in q:
            return "dong_xuan"
        if "he thu" in q or "he-thu" in q:
            return "he_thu"
        return "ca_nam"

    def _find_crop(self, q):
        if any(x in q for x in ("ntts", "nuoi trong thuy san", "thuy san", "nuoi ca", "nuoi tom")):
            return "ntts"
        if "cay dl" in q:
            return "cay_dl"
        if "lua" in q:
            return "lua"
        if "mau" in q or "hoa mau" in q:
            return "mau"
        return "tong"

    def _field(self, season, crop):
        return f"{season}_{crop}" if crop != "tong" else f"{season}_tong"

    def _select(self, q):
        nq = _norm(q)
        year = self._find_year(nq)
        group = self._find_group(nq)
        aggregate_intent = (
            "tong dien tich" in nq or "tong cong" in nq or
            "tat ca" in nq or "toan bo" in nq
        )
        ranking_intent = (
            re.search(r"\btop\s*\d+\b", nq) is not None or
            "lon nhat" in nq or "cao nhat" in nq
        )
        construction = None if (aggregate_intent or ranking_intent) else self._find_construction(nq)
        method = self._find_row_method(nq)
        rows = [r for r in self.rows if int(r.get("nam", year) or year) == year]
        if group:
            rows = [r for r in rows if r.get("nhom") == group]
        if construction:
            rows = [r for r in rows if r.get("ten_cong_trinh") == construction]
        # Dòng construction tổng hợp có thể không có cột bien_phap (ví dụ Hồ Chứa Nước Thạch Bàn).
        # Với truy vấn Tổng/cả năm, dùng dòng construction làm tổng thay vì loại bỏ nó.
        if construction and method == "Tổng":
            total_rows = [r for r in rows if str(r.get("bien_phap", "")).strip() == "Tổng"]
            construction_rows = [r for r in rows if str(r.get("row_type", "")).strip() == "construction" and r.get("bien_phap") in (None, "")]
            rows = total_rows or construction_rows
        else:
            rows = [r for r in rows if str(r.get("bien_phap", "")).strip() == method]
        return year, group, construction, method, rows

    def query(self, question: str):
        nq = _norm(question)
        year, group, construction, method, rows = self._select(question)
        season = self._find_season(nq)
        crop = self._find_crop(nq)
        field = self._field(season, crop)

        if not rows:
            return {
                "found": False, "engine": "PLAN_DATA", "question": question,
                "filters": {"year": year, "group": group, "construction": construction,
                            "method": method, "season": season, "crop": crop},
                "reason": "Không tìm thấy bản ghi phù hợp."
            }

        # Top-N queries.
        top_m = re.search(r"\btop\s*(\d+)\b", nq)
        if (top_m or "lon nhat" in nq or "cao nhat" in nq) and not construction:
            n = int(top_m.group(1)) if top_m else 5
            candidates = []
            for r in self.rows:
                if int(r.get("nam", year) or year) != year:
                    continue
                if group and r.get("nhom") != group:
                    continue
                if str(r.get("bien_phap", "")).strip() != method:
                    continue
                v = _num(r.get(field))
                if v is not None and r.get("ten_cong_trinh"):
                    candidates.append((v, r))
            candidates.sort(key=lambda z: z[0], reverse=True)
            items = [{
                "cong_trinh": r["ten_cong_trinh"], "value": v, "unit": "ha",
                "method": r.get("bien_phap"), "field": field
            } for v, r in candidates[:n]]
            return {
                "found": bool(items), "engine": "PLAN_DATA", "question": question,
                "operation": "ranking", "items": items,
                "filters": {"year": year, "group": group, "method": method,
                            "season": season, "crop": crop}
            }

        # Aggregate queries. "Tổng diện tích..." is unambiguously aggregate when
        # no individual construction was named.
        aggregate_signal = (
            "tong dien tich" in nq or
            "tong cong" in nq or
            "tat ca" in nq or
            "toan bo" in nq or
            ("tong" in nq and construction is None)
        )
        if construction is None and aggregate_signal:
            total = 0.0
            used = []
            for r in self.rows:
                if int(r.get("nam", year) or year) != year:
                    continue
                if group and r.get("nhom") != group:
                    continue
                if str(r.get("bien_phap", "")).strip() != method:
                    continue
                v = _num(r.get(field))
                if v is not None:
                    total += v
                    used.append(r)
            return {
                "found": bool(used), "engine": "PLAN_DATA", "question": question,
                "operation": "sum", "value": round(total, 2), "unit": "ha",
                "count": len(used), "field": field,
                "filters": {"year": year, "group": group, "method": method,
                            "season": season, "crop": crop}
            }

        if len(rows) == 1:
            r = rows[0]
            value = _num(r.get(field))
            return {
                "found": value is not None, "engine": "PLAN_DATA", "question": question,
                "operation": "lookup", "value": value, "unit": "ha",
                "construction": r.get("ten_cong_trinh"), "address": r.get("dia_diem"),
                "group": r.get("nhom"), "method": r.get("bien_phap"),
                "season": season, "crop": crop, "field": field,
                "source": {"dataset": "Phụ lục 09 (VG-TB)(2027)",
                           "excel_row": r.get("excel_row")}
            }

        items = [{
            "construction": r.get("ten_cong_trinh"), "method": r.get("bien_phap"),
            "value": _num(r.get(field)), "unit": "ha"
        } for r in rows if _num(r.get(field)) is not None]
        return {
            "found": bool(items), "engine": "PLAN_DATA", "question": question,
            "operation": "list", "items": items,
            "filters": {"year": year, "group": group, "method": method,
                        "season": season, "crop": crop, "field": field}
        }
