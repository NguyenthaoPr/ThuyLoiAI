from plan_data_engine_v6 import PlanDataEngineV6

e = PlanDataEngineV6()

tests = [
    ("Hồ chứa nước Phú Lộc tưới bao nhiêu ha?", 318.46),
    ("Hồ Phú Lộc vụ Đông Xuân tưới bao nhiêu ha?", 159.23),
    ("Hồ chứa nước Phú Lộc vụ Đông Xuân có bao nhiêu ha lúa?", 132.70),
    ("Phú Lộc cả năm có bao nhiêu ha lúa?", 265.40),
    ("Hồ Phú Lộc Chủ động bao nhiêu ha?", 318.46),
    ("Hồ Phú Lộc Tạo nguồn bao nhiêu ha?", 0.00),
    ("Hồ Phú Ninh Chủ động bao nhiêu ha?", 500.50),
    ("Hồ Phú Ninh Tạo nguồn bao nhiêu ha?", 91.00),
    ("Kênh N1 Nam Phước tưới bao nhiêu ha?", 75.60),
    ("Kênh N1 Nam Phước Đông Xuân bao nhiêu ha?", 37.80),
    ("Kênh N1 Nam Phước Đông Xuân có bao nhiêu ha lúa?", 32.70),
    ("Kênh N1 Nam Phước Hè Thu bao nhiêu ha?", 37.80),
]

passed = 0
for q, expected in tests:
    r = e.query(q)
    got = r.get("value") if r.get("operation") == "lookup" else None
    ok = got is not None and abs(got - expected) < 1e-9
    print(("PASS" if ok else "FAIL"), f"{q} => {got} (expected {expected})")
    if not ok:
        print("  RESULT:", r)
    passed += ok

for q in [
    "Hồ chứa nước Phú Lộc chi tiết vụ Đông Xuân",
    "Hồ chứa nước Phú Ninh chi tiết cả năm",
]:
    r = e.query(q)
    ok = r.get("operation") == "detail" and r.get("found") and len(r.get("breakdown", [])) == 4
    print(("PASS" if ok else "FAIL"), f"DETAIL: {q}")
    if not ok:
        print("  RESULT:", r)
    passed += ok

print(f"\n{passed}/{len(tests)+2} PASS")
raise SystemExit(0 if passed == len(tests)+2 else 1)
