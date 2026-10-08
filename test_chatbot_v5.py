from pathlib import Path
import ast, re, sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_data_engine import PlanDataEngine

SERVER = Path(__file__).with_name('server_chatbot_v5.py')
DATA = Path(__file__).with_name('phu_luc_09_vgtb_2027_normalized.json')

def load_router():
    s = SERVER.read_text(encoding='utf-8')
    mod = ast.parse(s)
    constants = {'PLAN_DATA_TERMS','PLAN_DATA_INTENT_PHRASES','PLAN_DATA_TIME_TERMS','OPERATIONAL_CURRENT_TERMS','OPERATIONAL_PARAMETER_TERMS','OPERATIONAL_EXPLICIT_PHRASES'}
    funcs = {'_normalize_router_text','_contains_any','_has_current_time_context','_looks_like_plan_intent','classify_query_route'}
    parts=[]
    for n in mod.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id in constants for t in targets):
                parts.append(ast.get_source_segment(s,n))
        elif isinstance(n, ast.FunctionDef) and n.name in funcs:
            parts.append(ast.get_source_segment(s,n))
    ns = {
        'DOCUMENT_STRONG_TERMS': ('quy dinh','van ban','nghi dinh','thong tu','quy pham','quy che','quy trinh'),
        'DOCUMENT_KNOWLEDGE_TERMS': ('dien tich','cong trinh','quy mo','thong so ky thuat','so may','dung tich','cao trinh'),
        'CHATBOT_ROUTER_VERSION': 'rag-v5-chatbook',
        're': re,
    }
    exec('\n\n'.join(parts), ns)
    return ns['classify_query_route']

def main():
    classify = load_router()
    cases = [
        ('Hồ Thạch Bàn tưới bao nhiêu ha','plan_data'),
        ('Tứ Câu có bao nhiêu ha?','plan_data'),
        ('Tứ Câu năm 2027 bao nhiêu ha lúa','plan_data'),
        ('Tứ Câu vụ Hè Thu nuôi thủy sản được bao nhiêu?','plan_data'),
        ('Những công trình nào có diện tích lớn nhất?','plan_data'),
        ('Top 5 công trình diện tích lớn nhất','plan_data'),
        ('Qui định về phạm vi bảo vệ công trình Thủy lợi','document'),
        ('Thông số kỹ thuật hồ Thạch Bàn','document'),
        ('Tứ Câu mực nước hiện tại bao nhiêu?','operational'),
        ('Tứ Câu độ mặn hôm nay bao nhiêu?','operational'),
        ('Mưa T1 hiện tại ở hồ Thạch Bàn bao nhiêu?','operational'),
    ]
    failures=[]
    for q, expected in cases:
        got=classify(q)['route']
        if got != expected: failures.append((q,expected,got))
        print(('PASS' if got==expected else 'FAIL'), got, '|', q)

    e=PlanDataEngine(DATA)
    plan_cases=[
        ('Quy mô phục vụ của Tứ Câu trong năm tới',496.08),
        ('Tứ Câu năm sau có bao nhiêu ha lúa',476.82),
        ('Tứ Câu vụ Hè Thu nuôi thủy sản được bao nhiêu?',9.98),
        ('Hồ Thạch Bàn tưới bao nhiêu ha',1444.06),
        ('Tứ Câu năm 2030 có bao nhiêu ha?',None),
    ]
    for q, expected in plan_cases:
        r=e.query(q); got=r.get('value') if r.get('found') else None
        ok=(got==expected)
        if not ok: failures.append((q,expected,got))
        print(('PASS' if ok else 'FAIL'),'PLAN',got,'|',q)

    s=SERVER.read_text(encoding='utf-8')
    cache_get='f"{CACHE_NAMESPACE}:{route}:{normalize_question(question)}"' in s
    cache_set='f"{CACHE_NAMESPACE}:{route}:{normalized}"' in s
    print('PASS CACHE KEY CONSISTENCY' if cache_get and cache_set else 'FAIL CACHE KEY CONSISTENCY')
    if not (cache_get and cache_set): failures.append(('cache_key',True,False))

    if failures:
        print('\nFAILURES:', failures)
        raise SystemExit(1)
    print(f'\nALL TESTS PASS: {len(cases)+len(plan_cases)+1}')

if __name__=='__main__': main()
