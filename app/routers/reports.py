"""조회 API (API 12~14). SQL은 설계서 5-5를 그대로 사용한다.

(정렬 순서를 고정하기 위해 ORDER BY만 덧붙였다)
"""
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from app.db import get_conn
from app.errors import not_found
from app.schemas import InventoryOut, TraceOut, YieldByEquipment, YieldByWorkOrder

router = APIRouter(tags=["조회"])

# 품목별 현재 재고 (재고 = IN 합계 − OUT 합계)
SQL_INVENTORY = """
SELECT i.item_code, i.item_name, i.unit,
       SUM(CASE t.tx_type WHEN 'IN' THEN t.qty ELSE -t.qty END) AS stock_qty
FROM inventory_tx t
JOIN lot  l ON l.lot_id  = t.lot_id
JOIN item i ON i.item_id = l.item_id
GROUP BY i.item_id, i.item_code, i.item_name, i.unit
ORDER BY i.item_id
"""

# 완제품 LOT 추적: 완제품 LOT → 실적 → 같은 실적의 CONSUME → 원자재 LOT
SQL_TRACE = """
SELECT fg.lot_no AS product_lot, e.equipment_code,
       raw.lot_no AS input_lot, ri.item_name AS input_item, t.qty AS input_qty
FROM lot fg
JOIN production_result pr ON pr.result_id = fg.result_id
JOIN equipment e          ON e.equipment_id = pr.equipment_id
JOIN inventory_tx t       ON t.result_id = pr.result_id AND t.reason = 'CONSUME'
JOIN lot raw              ON raw.lot_id = t.lot_id
JOIN item ri              ON ri.item_id = raw.item_id
WHERE fg.lot_no = :lot_no
ORDER BY t.tx_id
"""

# 설비별 양품률
SQL_YIELD_BY_EQUIPMENT = """
SELECT e.equipment_code,
       SUM(pr.good_qty) AS good, SUM(pr.defect_qty) AS defect,
       ROUND(SUM(pr.good_qty) / SUM(pr.good_qty + pr.defect_qty) * 100, 1) AS yield_pct
FROM production_result pr
JOIN equipment e ON e.equipment_id = pr.equipment_id
GROUP BY e.equipment_id, e.equipment_code
ORDER BY e.equipment_id
"""

# 작업지시별 양품률 (설비별 SQL과 같은 형태)
SQL_YIELD_BY_WORK_ORDER = """
SELECT w.wo_no,
       SUM(pr.good_qty) AS good, SUM(pr.defect_qty) AS defect,
       ROUND(SUM(pr.good_qty) / SUM(pr.good_qty + pr.defect_qty) * 100, 1) AS yield_pct
FROM production_result pr
JOIN work_order w ON w.wo_id = pr.wo_id
GROUP BY w.wo_id, w.wo_no
ORDER BY w.wo_id
"""


@router.get("/inventory", response_model=list[InventoryOut])
def get_inventory(conn: Connection = Depends(get_conn)):
    return conn.execute(text(SQL_INVENTORY)).mappings().all()


@router.get("/lots/{lot_no}/trace", response_model=TraceOut)
def trace_lot(lot_no: str, conn: Connection = Depends(get_conn)):
    rows = conn.execute(text(SQL_TRACE), {"lot_no": lot_no}).mappings().all()
    # 원자재 LOT이거나 없는 LOT이면 결과가 비어 있다
    if not rows:
        raise not_found(f"완제품 LOT {lot_no} 없음")
    return {
        "product_lot": rows[0]["product_lot"],
        "equipment_code": rows[0]["equipment_code"],
        "inputs": [
            {"input_lot": r["input_lot"], "input_item": r["input_item"], "input_qty": r["input_qty"]}
            for r in rows
        ],
    }


@router.get(
    "/reports/yield",
    response_model=list[YieldByEquipment] | list[YieldByWorkOrder],
)
def yield_report(by: Literal["equipment", "work_order"], conn: Connection = Depends(get_conn)):
    sql = SQL_YIELD_BY_EQUIPMENT if by == "equipment" else SQL_YIELD_BY_WORK_ORDER
    return conn.execute(text(sql)).mappings().all()
