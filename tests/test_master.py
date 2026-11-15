"""2단계: 기준정보·입고·작업지시 API 테스트."""
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from tests.conftest import count_rows

TODAY = date.today().strftime("%Y%m%d")


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == code
    assert detail["message"]


# ---- 품목 ----
def test_시드_품목_3건(client):
    codes = {i["item_code"] for i in client.get("/items").json()}
    assert {"FG-BOLT", "RM-ROD", "RM-PLATING"} <= codes


def test_품목_등록_후_중복이면_409(client):
    body = {"item_code": "T-ITEM-01", "item_name": "테스트 품목", "item_type": "RAW", "unit": "EA"}
    resp = client.post("/items", json=body)
    assert resp.status_code == 201
    assert resp.json()["item_name"] == "테스트 품목"

    assert_error(client.post("/items", json=body), 409, "DUPLICATE_CODE")


def test_품목_입력형식_오류는_422(client):
    body = {"item_code": "T-BAD", "item_name": "x", "item_type": "WIP", "unit": "EA"}
    assert client.post("/items", json=body).status_code == 422


# ---- BOM ----
def test_볼트_BOM_조회(client, item_ids):
    resp = client.get(f"/items/{item_ids['FG-BOLT']}/bom")
    assert resp.status_code == 200
    bom = {r["item_code"]: Decimal(r["qty_per"]) for r in resp.json()}
    assert bom == {"RM-ROD": Decimal("1"), "RM-PLATING": Decimal("0.01")}


def test_BOM_조회_없는_품목은_404(client):
    assert_error(client.get("/items/99999/bom"), 404, "NOT_FOUND")


def test_BOM_등록과_중복(client, item_ids):
    fg = client.post("/items", json={
        "item_code": "T-FG-NUT", "item_name": "너트", "item_type": "FINISHED", "unit": "EA",
    }).json()
    body = {"parent_item_id": fg["item_id"], "child_item_id": item_ids["RM-ROD"], "qty_per": "0.5"}
    resp = client.post("/boms", json=body)
    assert resp.status_code == 201
    assert Decimal(resp.json()["qty_per"]) == Decimal("0.5")

    assert_error(client.post("/boms", json=body), 409, "DUPLICATE_CODE")


def test_BOM_품목유형_오류(client, item_ids):
    # 원자재를 모품목으로
    body = {"parent_item_id": item_ids["RM-ROD"], "child_item_id": item_ids["RM-PLATING"],
            "qty_per": "1"}
    assert_error(client.post("/boms", json=body), 400, "INVALID_ITEM_TYPE")


def test_BOM_없는_품목은_404_자기자신은_422(client, item_ids):
    body = {"parent_item_id": item_ids["FG-BOLT"], "child_item_id": 99999, "qty_per": "1"}
    assert_error(client.post("/boms", json=body), 404, "NOT_FOUND")

    body = {"parent_item_id": item_ids["FG-BOLT"], "child_item_id": item_ids["FG-BOLT"],
            "qty_per": "1"}
    assert client.post("/boms", json=body).status_code == 422


# ---- 설비 ----
def test_시드_설비_2건(client):
    codes = {e["equipment_code"] for e in client.get("/equipment").json()}
    assert {"CNC-01", "CNC-02"} <= codes


def test_설비_등록_후_중복이면_409(client):
    body = {"equipment_code": "T-CNC-99", "equipment_name": "테스트 설비"}
    resp = client.post("/equipment", json=body)
    assert resp.status_code == 201
    assert resp.json()["is_active"] is True

    assert_error(client.post("/equipment", json=body), 409, "DUPLICATE_CODE")


# ---- 입고 ----
def test_입고하면_lot_1건과_IN_1건_생성(client, engine, item_ids):
    resp = client.post("/receipts", json={"item_id": item_ids["RM-ROD"], "qty": "1200"})
    assert resp.status_code == 201, resp.text
    lot = resp.json()
    assert lot["lot_no"] == f"RM-{TODAY}-001"
    assert lot["source_type"] == "RECEIPT"
    assert lot["result_id"] is None
    assert Decimal(lot["initial_qty"]) == Decimal("1200")

    assert count_rows(engine, "lot") == 1
    with engine.connect() as conn:
        txs = conn.execute(text(
            "SELECT lot_id, tx_type, qty, reason, result_id FROM inventory_tx"
        )).mappings().all()
    assert len(txs) == 1
    tx = txs[0]
    assert (tx["lot_id"], tx["tx_type"], tx["reason"], tx["result_id"]) == (
        lot["lot_id"], "IN", "RECEIPT", None
    )
    assert tx["qty"] == Decimal("1200")


def test_입고_LOT번호_순번_증가(client, item_ids):
    first = client.post("/receipts", json={"item_id": item_ids["RM-ROD"], "qty": "1"}).json()
    second = client.post("/receipts", json={"item_id": item_ids["RM-PLATING"], "qty": "20"}).json()
    assert first["lot_no"] == f"RM-{TODAY}-001"
    assert second["lot_no"] == f"RM-{TODAY}-002"


def test_완제품_입고는_400(client, engine, item_ids):
    resp = client.post("/receipts", json={"item_id": item_ids["FG-BOLT"], "qty": "10"})
    assert_error(resp, 400, "INVALID_ITEM_TYPE")
    assert count_rows(engine, "lot") == 0
    assert count_rows(engine, "inventory_tx") == 0


def test_입고_없는_품목은_404_수량_0이하는_422(client, item_ids):
    assert_error(client.post("/receipts", json={"item_id": 99999, "qty": "1"}), 404, "NOT_FOUND")
    assert client.post("/receipts", json={"item_id": item_ids["RM-ROD"], "qty": "0"}).status_code == 422
    assert client.post("/receipts", json={"item_id": item_ids["RM-ROD"], "qty": "-5"}).status_code == 422


# ---- 작업지시 ----
def test_작업지시_생성은_PLANNED_번호는_plan_date_기준(client, item_ids):
    resp = client.post("/work-orders", json={
        "item_id": item_ids["FG-BOLT"], "plan_qty": "1000", "plan_date": "2026-12-01",
    })
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert wo["status"] == "PLANNED"
    assert wo["wo_no"] == "WO-20261201-001"

    wo2 = client.post("/work-orders", json={
        "item_id": item_ids["FG-BOLT"], "plan_qty": "10", "plan_date": "2026-12-01",
    }).json()
    assert wo2["wo_no"] == "WO-20261201-002"


def test_원자재_작업지시는_400(client, item_ids):
    resp = client.post("/work-orders", json={
        "item_id": item_ids["RM-ROD"], "plan_qty": "10", "plan_date": "2026-12-01",
    })
    assert_error(resp, 400, "INVALID_ITEM_TYPE")


def test_작업지시_start_두번이면_두번째_409(client, item_ids):
    wo = client.post("/work-orders", json={
        "item_id": item_ids["FG-BOLT"], "plan_qty": "100", "plan_date": "2026-12-01",
    }).json()

    first = client.patch(f"/work-orders/{wo['wo_id']}/start")
    assert first.status_code == 200
    assert first.json()["status"] == "IN_PROGRESS"

    assert_error(client.patch(f"/work-orders/{wo['wo_id']}/start"), 409, "WO_INVALID_STATUS")


def test_없는_작업지시_start는_404(client):
    assert_error(client.patch("/work-orders/99999/start"), 404, "NOT_FOUND")


def test_작업지시_목록_status_필터(client, item_ids):
    def create() -> dict:
        return client.post("/work-orders", json={
            "item_id": item_ids["FG-BOLT"], "plan_qty": "10", "plan_date": "2026-12-02",
        }).json()

    planned, started = create(), create()
    client.patch(f"/work-orders/{started['wo_id']}/start")

    assert len(client.get("/work-orders").json()) == 2
    in_progress = client.get("/work-orders", params={"status": "IN_PROGRESS"}).json()
    assert [w["wo_id"] for w in in_progress] == [started["wo_id"]]
    only_planned = client.get("/work-orders", params={"status": "PLANNED"}).json()
    assert [w["wo_id"] for w in only_planned] == [planned["wo_id"]]
    assert client.get("/work-orders", params={"status": "WRONG"}).status_code == 422
