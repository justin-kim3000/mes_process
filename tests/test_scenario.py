"""4단계: 설계서 7장 시나리오를 API만으로 처음부터 실행하고 정답을 검증한다."""
from datetime import date
from decimal import Decimal

TODAY = date.today()
T = TODAY.strftime("%Y%m%d")


def ok(resp, status: int = 200) -> dict | list:
    assert resp.status_code == status, resp.text
    return resp.json()


def run_scenario(client) -> dict:
    """7장 1~6번 호출. 기준정보 id도 API로 조회한다."""
    items = {i["item_code"]: i["item_id"] for i in ok(client.get("/items"))}
    equipment = {e["equipment_code"]: e["equipment_id"] for e in ok(client.get("/equipment"))}

    # 1~2. 원자재 입고
    rod = ok(client.post("/receipts", json={"item_id": items["RM-ROD"], "qty": "1200"}), 201)
    plating = ok(client.post("/receipts", json={"item_id": items["RM-PLATING"], "qty": "20"}), 201)
    assert rod["lot_no"] == f"RM-{T}-001"
    assert plating["lot_no"] == f"RM-{T}-002"

    # 3. 작업지시
    wo = ok(client.post("/work-orders", json={
        "item_id": items["FG-BOLT"], "plan_qty": "1000", "plan_date": TODAY.isoformat(),
    }), 201)
    assert wo["status"] == "PLANNED"

    # 4. 시작
    assert ok(client.patch(f"/work-orders/{wo['wo_id']}/start"))["status"] == "IN_PROGRESS"

    def result(eq: str, good: str, defect: str, code: str, rod_qty: str, plating_qty: str,
               start: str, end: str) -> dict:
        return ok(client.post(f"/work-orders/{wo['wo_id']}/results", json={
            "equipment_id": equipment[eq],
            "good_qty": good, "defect_qty": defect, "defect_code": code,
            "worker_name": "김작업",
            "start_at": f"{TODAY}T{start}", "end_at": f"{TODAY}T{end}",
            "inputs": [
                {"lot_id": rod["lot_id"], "qty": rod_qty},
                {"lot_id": plating["lot_id"], "qty": plating_qty},
            ],
        }), 201)

    # 5. CNC-01: 양품 480, 불량 20(SCRATCH), 강봉 500 + 도금액 5
    r1 = result("CNC-01", "480", "20", "SCRATCH", "500", "5", "09:00:00", "12:00:00")
    assert r1["product_lot_no"] == f"FG-{T}-001"
    assert r1["wo_status"] == "IN_PROGRESS"

    # 6. CNC-02: 양품 520, 불량 10(DIMENSION), 강봉 530 + 도금액 5.3
    r2 = result("CNC-02", "520", "10", "DIMENSION", "530", "5.3", "13:00:00", "17:00:00")
    assert r2["product_lot_no"] == f"FG-{T}-002"
    assert r2["wo_status"] == "DONE"

    return {"wo": wo, "rod": rod, "plating": plating}


def test_시나리오_정답(client):
    s = run_scenario(client)

    # 재고: RM-ROD 170, RM-PLATING 9.7, FG-BOLT 1000
    stock = {r["item_code"]: Decimal(r["stock_qty"]) for r in ok(client.get("/inventory"))}
    assert stock == {
        "FG-BOLT": Decimal("1000"),
        "RM-ROD": Decimal("170"),
        "RM-PLATING": Decimal("9.7"),
    }

    # 설비별 양품률: CNC-01 96.0, CNC-02 98.1
    by_eq = ok(client.get("/reports/yield", params={"by": "equipment"}))
    assert {r["equipment_code"]: Decimal(r["yield_pct"]) for r in by_eq} == {
        "CNC-01": Decimal("96.0"),
        "CNC-02": Decimal("98.1"),
    }
    cnc01 = next(r for r in by_eq if r["equipment_code"] == "CNC-01")
    assert (Decimal(cnc01["good"]), Decimal(cnc01["defect"])) == (Decimal("480"), Decimal("20"))

    # 작업지시별 양품률: 97.1
    by_wo = ok(client.get("/reports/yield", params={"by": "work_order"}))
    assert len(by_wo) == 1
    assert by_wo[0]["wo_no"] == s["wo"]["wo_no"]
    assert Decimal(by_wo[0]["yield_pct"]) == Decimal("97.1")
    assert (Decimal(by_wo[0]["good"]), Decimal(by_wo[0]["defect"])) == (
        Decimal("1000"), Decimal("30"))

    # LOT 추적: FG-…-001 → CNC-01, 강봉 RM-…-001 500, 도금액 RM-…-002 5
    trace = ok(client.get(f"/lots/FG-{T}-001/trace"))
    assert trace["product_lot"] == f"FG-{T}-001"
    assert trace["equipment_code"] == "CNC-01"
    assert [
        (i["input_lot"], i["input_item"], Decimal(i["input_qty"])) for i in trace["inputs"]
    ] == [
        (f"RM-{T}-001", "강봉", Decimal("500")),
        (f"RM-{T}-002", "도금액", Decimal("5")),
    ]
    # 응답 형식: 수량은 소수 4자리 문자열
    assert trace["inputs"][0]["input_qty"] == "500.0000"


def test_두번째_완제품_LOT_추적(client):
    run_scenario(client)
    trace = ok(client.get(f"/lots/FG-{T}-002/trace"))
    assert trace["equipment_code"] == "CNC-02"
    assert {i["input_item"]: Decimal(i["input_qty"]) for i in trace["inputs"]} == {
        "강봉": Decimal("530"),
        "도금액": Decimal("5.3"),
    }


def test_추적_원자재_LOT이나_없는_LOT은_404(client):
    run_scenario(client)
    for lot_no in (f"RM-{T}-001", "FG-19990101-001"):
        resp = client.get(f"/lots/{lot_no}/trace")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_조회_빈_상태와_by_파라미터_검증(client):
    assert ok(client.get("/inventory")) == []
    assert ok(client.get("/reports/yield", params={"by": "equipment"})) == []
    assert client.get("/reports/yield").status_code == 422
    assert client.get("/reports/yield", params={"by": "worker"}).status_code == 422
