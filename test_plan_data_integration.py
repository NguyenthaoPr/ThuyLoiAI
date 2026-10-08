from pathlib import Path
import re, unicodedata, sys

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from plan_data_engine import PlanDataEngine

server = (BASE / "server.py").read_text(encoding="utf-8")
start = server.find("DOCUMENT_STRONG_TERMS = (")
end = server.find("\n\nDOCUMENT_QUERY_ALIASES = {", start)
ns = {"re": re, "unicodedata": unicodedata, "CHATBOT_ROUTER_VERSION": "rag-v4-plan-data"}
exec(server[start:end], ns)
engine = PlanDataEngine(BASE / "phu_luc_09_vgtb_2027_normalized.json")

cases = [
    ("Quy định về phạm vi bảo vệ công trình thủy lợi", "document", None),
    ("Tứ Câu nuôi thủy sản bao nhiêu ha?", "plan_data", 9.98),
    ("Tứ Câu kế hoạch năm 2027 bao nhiêu ha?", "plan_data", 496.08),
    ("Tứ Câu phục vụ bao nhiêu diện tích?", "plan_data", 496.08),
    ("Quy mô phục vụ của Tứ Câu trong năm tới", "plan_data", 496.08),
    ("Tứ Câu hiện đang bơm mấy máy?", "operational", None),
    ("Theo kế hoạch 2027 Tứ Câu phục vụ bao nhiêu ha và hiện đang bơm mấy máy?", "hybrid_plan_operational", 496.08),
    ("Tứ Câu vụ Đông Xuân bao nhiêu ha lúa?", "plan_data", 238.41),
]
failed=[]
for q, expected_route, expected_value in cases:
    route = ns["classify_query_route"](q)["route"]
    value = None
    if "plan" in route:
        result = engine.query(q)
        value = result.get("value") if result.get("operation") in ("lookup", "sum") else None
    ok = route == expected_route and (expected_value is None or value == expected_value)
    print(("PASS" if ok else "FAIL"), "|", q, "| route=", route, "| value=", value)
    if not ok: failed.append(q)

print(f"\nRESULT: {len(cases)-len(failed)}/{len(cases)} passed")
if failed:
    raise SystemExit(1)
