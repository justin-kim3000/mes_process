"""3단계: 실적 등록 트랜잭션 테스트.

실패 케이스는 에러 코드와 함께 실적 4개 테이블 행 수가 요청 전과 같은지(롤백) 확인한다.
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import TX_TABLES, count_rows

TODAY = date.today()
TODAY_STR = TODAY.strftime("%Y%m%d")


def snapshot(engine) -> dict[str, int]:
    return {table: count_rows(engine, table) for table in TX_TABLES}


def assert_error_and_rollback(resp, engine, before, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == code
    assert snapshot(engine) == before, "실패한 요청이 데이터를 남김 (롤백 안 됨)"
    return detail


def receipt(client, item_id: int, qty: str) -> dict:
    resp = client.post("/receipts", json={"item_id": item_id, "qty": qty})
    assert resp.status_code == 201, resp.text
    return resp.json()


def new_work_order(client, item_id: int, plan_qty: str = "1000", start: bool = True) -> dict:
    wo = client.post("/work-orders", json={
        "item_id": item_id, "plan_qty": plan_qty, "plan_date": TODAY.isoformat(),
    }).json()
    if start:
        assert client.patch(f"/work-orders/{wo['wo_id']}/start").status_code == 200
    return wo


@pytest.fixture
def ctx(client, item_ids) -> dict:
    """강봉 1200, 도금액 20 입고 + 볼트 1000 작업지시(IN_PROGRESS)."""
    return {
        "items": item_ids,
        "rod": receipt(client, item_ids["RM-ROD"], "1200"),
        "plating": receipt(client, item_ids["RM-PLATING"], "20"),
        "wo": new_work_order(client, item_ids["FG-BOLT"]),
    }


def result_body(ctx, **overrides) -> dict:
    """기본값: CNC-01, 양품 480, 불량 20(SCRATCH), 강봉 500 + 도금액 5."""
    body = {
        "equipment_id": 1,
        "good_qty": "480",
        "defect_qty": "20",
        "defect_code": "SCRATCH",
        "worker_name": "김작업",
        "start_at": f"{TODAY}T09:00:00",
        "end_at": f"{TODAY}T12:00:00",
        "inputs": [
            {"lot_id": ctx["rod"]["lot_id"], "qty": "500"},
            {"lot_id": ctx["plating"]["lot_id"], "qty": "5"},
        ],
    }
    body.update(overrides)
    return body


def post_result(client, wo_id: int, body: dict):
    return client.post(f"/work-orders/{wo_id}/results", json=body)


# ---- 설계서 3단계 완료 확인 케이스 1~8 ----
def test_1_PLANNED_작업지시에_실적은_409(client, engine, ctx):
    planned = new_work_order(client, ctx["items"]["FG-BOLT"], start=False)
    before = snapshot(engine)
    resp = post_result(client, planned["wo_id"], result_body(ctx))
    assert_error_and_rollback(resp, engine, before, 409, "WO_INVALID_STATUS")


@pytest.mark.parametrize("defect_code", [None, "", "   "])
def test_2_불량있는데_불량코드_없으면_400(client, engine, ctx, defect_code):
    before = snapshot(engine)
    resp = post_result(client, ctx["wo"]["wo_id"], result_body(ctx, defect_code=defect_code))
    assert_error_and_rollback(resp, engine, before, 400, "DEFECT_CODE_REQUIRED")


def test_3_도금액_LOT_빠뜨리면_BOM_ITEM_MISMATCH(client, engine, ctx):
    before = snapshot(engine)
    body = result_body(ctx, inputs=[{"lot_id": ctx["rod"]["lot_id"], "qty": "500"}])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    detail = assert_error_and_rollback(resp, engine, before, 400, "BOM_ITEM_MISMATCH")
    assert "RM-PLATING" in detail["message"]


def test_4_강봉_499_투입이면_BOM_QTY_MISMATCH(client, engine, ctx):
    before = snapshot(engine)
    body = result_body(ctx, inputs=[
        {"lot_id": ctx["rod"]["lot_id"], "qty": "499"},
        {"lot_id": ctx["plating"]["lot_id"], "qty": "5"},
    ])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    assert_error_and_rollback(resp, engine, before, 400, "BOM_QTY_MISMATCH")


def test_5_재고보다_많이_투입하면_INSUFFICIENT_STOCK(client, engine, ctx):
    small_rod = receipt(client, ctx["items"]["RM-ROD"], "170")
    before = snapshot(engine)
    body = result_body(ctx, inputs=[
        {"lot_id": small_rod["lot_id"], "qty": "500"},
        {"lot_id": ctx["plating"]["lot_id"], "qty": "5"},
    ])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    detail = assert_error_and_rollback(resp, engine, before, 409, "INSUFFICIENT_STOCK")
    assert detail["message"] == (
        f"LOT {small_rod['lot_no']} 재고 부족 (남은 170.0000, 요청 500.0000)"
    )


def test_6_정상_등록(client, engine, ctx):
    before = snapshot(engine)
    resp = post_result(client, ctx["wo"]["wo_id"], result_body(ctx))
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["product_lot_no"] == f"FG-{TODAY_STR}-001"
    assert out["wo_status"] == "IN_PROGRESS"

    after = snapshot(engine)
    assert after["production_result"] == before["production_result"] + 1
    assert after["lot"] == before["lot"] + 1
    assert after["inventory_tx"] == before["inventory_tx"] + 3

    with engine.connect() as conn:
        fg = conn.execute(text(
            "SELECT item_id, initial_qty, source_type, result_id FROM lot WHERE lot_no = :no"
        ), {"no": out["product_lot_no"]}).mappings().one()
        txs = conn.execute(text(
            "SELECT lot_id, tx_type, reason, qty FROM inventory_tx "
            "WHERE result_id = :rid ORDER BY tx_id"
        ), {"rid": out["result_id"]}).mappings().all()

    assert fg["item_id"] == ctx["items"]["FG-BOLT"]
    assert fg["initial_qty"] == Decimal("480")
    assert fg["source_type"] == "PRODUCTION"
    assert fg["result_id"] == out["result_id"]

    assert [(t["tx_type"], t["reason"]) for t in txs].count(("IN", "PRODUCE")) == 1
    outs = {t["lot_id"]: t["qty"] for t in txs if (t["tx_type"], t["reason"]) == ("OUT", "CONSUME")}
    assert outs == {
        ctx["rod"]["lot_id"]: Decimal("500"),
        ctx["plating"]["lot_id"]: Decimal("5"),
    }


def test_7_양품0_불량10이면_완제품_LOT_없음(client, engine, ctx):
    before = snapshot(engine)
    body = result_body(ctx, good_qty="0", defect_qty="10", inputs=[
        {"lot_id": ctx["rod"]["lot_id"], "qty": "10"},
        {"lot_id": ctx["plating"]["lot_id"], "qty": "0.1"},
    ])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["product_lot_no"] is None
    assert out["wo_status"] == "IN_PROGRESS"

    after = snapshot(engine)
    assert after["production_result"] == before["production_result"] + 1
    assert after["lot"] == before["lot"]
    with engine.connect() as conn:
        kinds = conn.execute(text(
            "SELECT tx_type, reason FROM inventory_tx WHERE result_id = :rid"
        ), {"rid": out["result_id"]}).all()
    assert sorted(kinds) == [("OUT", "CONSUME"), ("OUT", "CONSUME")]


def test_8_DONE_작업지시에_실적은_409(client, engine, ctx):
    # 양품 1000으로 계획수량 달성 → DONE
    body = result_body(ctx, good_qty="1000", defect_qty="0", defect_code=None, inputs=[
        {"lot_id": ctx["rod"]["lot_id"], "qty": "1000"},
        {"lot_id": ctx["plating"]["lot_id"], "qty": "10"},
    ])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["wo_status"] == "DONE"

    before = snapshot(engine)
    resp = post_result(client, ctx["wo"]["wo_id"], result_body(ctx, good_qty="1", defect_qty="0",
                       defect_code=None, inputs=[
                           {"lot_id": ctx["rod"]["lot_id"], "qty": "1"},
                           {"lot_id": ctx["plating"]["lot_id"], "qty": "0.01"},
                       ]))
    assert_error_and_rollback(resp, engine, before, 409, "WO_INVALID_STATUS")


# ---- 추가 케이스 ----
def test_없는_작업지시_설비_LOT은_404(client, engine, ctx):
    before = snapshot(engine)
    assert_error_and_rollback(
        post_result(client, 99999, result_body(ctx)), engine, before, 404, "NOT_FOUND")
    assert_error_and_rollback(
        post_result(client, ctx["wo"]["wo_id"], result_body(ctx, equipment_id=99999)),
        engine, before, 404, "NOT_FOUND")
    body = result_body(ctx, inputs=[
        {"lot_id": 99999, "qty": "500"},
        {"lot_id": ctx["plating"]["lot_id"], "qty": "5"},
    ])
    assert_error_and_rollback(
        post_result(client, ctx["wo"]["wo_id"], body), engine, before, 404, "NOT_FOUND")


def test_BOM에_없는_품목_투입은_BOM_ITEM_MISMATCH(client, engine, ctx):
    other = client.post("/items", json={
        "item_code": "T-RM-OIL", "item_name": "절삭유", "item_type": "RAW", "unit": "L",
    }).json()
    oil = receipt(client, other["item_id"], "10")
    before = snapshot(engine)
    body = result_body(ctx)
    body["inputs"].append({"lot_id": oil["lot_id"], "qty": "1"})
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    detail = assert_error_and_rollback(resp, engine, before, 400, "BOM_ITEM_MISMATCH")
    assert "T-RM-OIL" in detail["message"]


def test_같은_품목_여러_LOT_합산과_같은_LOT_중복_입력(client, engine, ctx):
    rod2 = receipt(client, ctx["items"]["RM-ROD"], "300")
    body = result_body(ctx, inputs=[
        {"lot_id": ctx["rod"]["lot_id"], "qty": "150"},
        {"lot_id": ctx["rod"]["lot_id"], "qty": "50"},   # 같은 LOT 중복 → 200으로 합산
        {"lot_id": rod2["lot_id"], "qty": "300"},        # 강봉 합계 500
        {"lot_id": ctx["plating"]["lot_id"], "qty": "5"},
    ])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    assert resp.status_code == 201, resp.text

    with engine.connect() as conn:
        outs = dict(conn.execute(text(
            "SELECT lot_id, qty FROM inventory_tx "
            "WHERE result_id = :rid AND reason = 'CONSUME'"
        ), {"rid": resp.json()["result_id"]}).all())
    assert outs == {
        ctx["rod"]["lot_id"]: Decimal("200"),
        rod2["lot_id"]: Decimal("300"),
        ctx["plating"]["lot_id"]: Decimal("5"),
    }


def test_같은_LOT_중복_합계가_재고_초과면_409(client, engine, ctx):
    before = snapshot(engine)
    body = result_body(ctx, good_qty="1300", defect_qty="0", defect_code=None, inputs=[
        {"lot_id": ctx["rod"]["lot_id"], "qty": "700"},
        {"lot_id": ctx["rod"]["lot_id"], "qty": "600"},  # 합계 1300 > 재고 1200
        {"lot_id": ctx["plating"]["lot_id"], "qty": "13"},
    ])
    resp = post_result(client, ctx["wo"]["wo_id"], body)
    assert_error_and_rollback(resp, engine, before, 409, "INSUFFICIENT_STOCK")


@pytest.mark.parametrize("overrides", [
    {"good_qty": "0", "defect_qty": "0"},                      # 합계 0
    {"good_qty": "-1"},                                        # 음수
    {"end_at": f"{TODAY}T08:00:00"},                           # 종료 < 시작
    {"inputs": []},                                            # 투입 없음
])
def test_입력형식_오류는_422(client, engine, ctx, overrides):
    before = snapshot(engine)
    resp = post_result(client, ctx["wo"]["wo_id"], result_body(ctx, **overrides))
    assert resp.status_code == 422, resp.text
    assert snapshot(engine) == before


def test_실적_INSERT_이후_예외도_전체_롤백(client, engine, ctx, monkeypatch):
    """7단계(실적 INSERT) 이후 8단계에서 예외가 나도 실적 행이 남지 않아야 한다."""
    def boom(*args, **kwargs):
        raise RuntimeError("완제품 LOT 생성 중 강제 실패")

    monkeypatch.setattr("app.services.production.insert_with_number", boom)
    before = snapshot(engine)
    with pytest.raises(RuntimeError):
        post_result(client, ctx["wo"]["wo_id"], result_body(ctx))
    assert snapshot(engine) == before
