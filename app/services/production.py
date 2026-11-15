"""생산실적 등록 (설계서 5-4). 전 과정을 DB 트랜잭션 1개로 처리한다.

어느 단계에서든 예외가 나면 `with conn.begin()` 블록이 전체를 롤백한다.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Connection, bindparam, text

from app.errors import BusinessError, not_found, wo_invalid_status
from app.schemas import ResultCreate
from app.services.lot_no import insert_with_number

# DECIMAL(12,4)과 같은 소수 4자리
FOUR_PLACES = Decimal("0.0001")


def _q4(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def register_result(conn: Connection, wo_id: int, body: ResultCreate) -> dict:
    with conn.begin():
        # 1. 작업지시 잠금·상태 확인
        wo = conn.execute(
            text(
                "SELECT wo_id, wo_no, item_id, plan_qty, status "
                "FROM work_order WHERE wo_id = :wo_id FOR UPDATE"
            ),
            {"wo_id": wo_id},
        ).mappings().first()
        if wo is None:
            raise not_found(f"작업지시 wo_id={wo_id} 없음")
        if wo["status"] != "IN_PROGRESS":
            raise wo_invalid_status(
                f"작업지시 {wo['wo_no']}는 {wo['status']} 상태라 실적을 등록할 수 없음 "
                "(IN_PROGRESS만 가능)"
            )

        # 2. 설비 존재 확인
        equipment = conn.execute(
            text("SELECT equipment_id FROM equipment WHERE equipment_id = :id"),
            {"id": body.equipment_id},
        ).first()
        if equipment is None:
            raise not_found(f"설비 equipment_id={body.equipment_id} 없음")

        # 3. 불량코드 필수 확인
        defect_code = (body.defect_code or "").strip() or None
        if body.defect_qty > 0 and defect_code is None:
            raise BusinessError(
                "DEFECT_CODE_REQUIRED", f"불량 {body.defect_qty}건에 defect_code가 없음", 400
            )

        # 같은 LOT이 여러 번 들어오면 LOT별로 합산
        qty_by_lot: dict[int, Decimal] = defaultdict(Decimal)
        for inp in body.inputs:
            qty_by_lot[inp.lot_id] += inp.qty
        lot_ids = sorted(qty_by_lot)

        # 4. 투입 LOT 잠금 (lot_id 순서로 잠가 교착 가능성을 줄임)
        lots = conn.execute(
            text(
                "SELECT l.lot_id, l.lot_no, l.item_id, i.item_code "
                "FROM lot l JOIN item i ON i.item_id = l.item_id "
                "WHERE l.lot_id IN :lot_ids ORDER BY l.lot_id FOR UPDATE"
            ).bindparams(bindparam("lot_ids", expanding=True)),
            {"lot_ids": lot_ids},
        ).mappings().all()
        lot_by_id = {row["lot_id"]: row for row in lots}
        missing = [lot_id for lot_id in lot_ids if lot_id not in lot_by_id]
        if missing:
            raise not_found(f"투입 LOT lot_id={missing} 없음")

        # 5. BOM 검증
        bom_rows = conn.execute(
            text(
                "SELECT b.child_item_id, b.qty_per, c.item_code "
                "FROM bom b JOIN item c ON c.item_id = b.child_item_id "
                "WHERE b.parent_item_id = :item_id"
            ),
            {"item_id": wo["item_id"]},
        ).mappings().all()
        bom = {row["child_item_id"]: row for row in bom_rows}
        code_by_item = {row["child_item_id"]: row["item_code"] for row in bom_rows}
        code_by_item.update({row["item_id"]: row["item_code"] for row in lots})

        input_items = {lot_by_id[lot_id]["item_id"] for lot_id in lot_ids}
        if input_items != set(bom):
            extra = sorted(code_by_item[i] for i in input_items - set(bom))
            lacking = sorted(code_by_item[i] for i in set(bom) - input_items)
            raise BusinessError(
                "BOM_ITEM_MISMATCH",
                f"투입 품목이 BOM과 다름 (BOM에 없는 품목 {extra}, 빠진 품목 {lacking})",
                400,
            )

        # 원자재 소요량은 양품이 아니라 투입량(양품 + 불량) 기준
        total_qty = body.good_qty + body.defect_qty
        input_by_item: dict[int, Decimal] = defaultdict(Decimal)
        for lot_id, qty in qty_by_lot.items():
            input_by_item[lot_by_id[lot_id]["item_id"]] += qty
        for item_id, row in bom.items():
            required = _q4(row["qty_per"] * total_qty)
            actual = _q4(input_by_item[item_id])
            if actual != required:
                raise BusinessError(
                    "BOM_QTY_MISMATCH",
                    f"{row['item_code']} 투입 합계 {actual} ≠ 소요량 {required} "
                    f"(qty_per {row['qty_per']} × 투입수량 {_q4(total_qty)})",
                    400,
                )

        # 6. LOT별 남은 재고 확인 (재고 = IN 합계 − OUT 합계)
        stock_by_lot = {
            row["lot_id"]: row["stock"]
            for row in conn.execute(
                text(
                    "SELECT lot_id, "
                    "       SUM(CASE tx_type WHEN 'IN' THEN qty ELSE -qty END) AS stock "
                    "FROM inventory_tx WHERE lot_id IN :lot_ids GROUP BY lot_id"
                ).bindparams(bindparam("lot_ids", expanding=True)),
                {"lot_ids": lot_ids},
            ).mappings()
        }
        for lot_id in lot_ids:
            remaining = stock_by_lot.get(lot_id) or Decimal("0")
            requested = qty_by_lot[lot_id]
            if remaining < requested:
                raise BusinessError(
                    "INSUFFICIENT_STOCK",
                    f"LOT {lot_by_id[lot_id]['lot_no']} 재고 부족 "
                    f"(남은 {remaining:.4f}, 요청 {requested:.4f})",
                    409,
                )

        # 7. 생산실적 INSERT
        result_id = conn.execute(
            text(
                "INSERT INTO production_result "
                "(wo_id, equipment_id, good_qty, defect_qty, defect_code, worker_name, "
                " start_at, end_at) "
                "VALUES (:wo_id, :equipment_id, :good_qty, :defect_qty, :defect_code, "
                "        :worker_name, :start_at, :end_at)"
            ),
            {
                "wo_id": wo_id,
                "equipment_id": body.equipment_id,
                "good_qty": body.good_qty,
                "defect_qty": body.defect_qty,
                "defect_code": defect_code,
                "worker_name": body.worker_name,
                "start_at": body.start_at,
                "end_at": body.end_at,
            },
        ).lastrowid

        # 8. 양품이 있으면 완제품 LOT 생성 + 입고(IN/PRODUCE)
        product_lot_no = None
        if body.good_qty > 0:

            def insert_product_lot(lot_no: str) -> int:
                return conn.execute(
                    text(
                        "INSERT INTO lot (lot_no, item_id, initial_qty, source_type, result_id) "
                        "VALUES (:lot_no, :item_id, :qty, 'PRODUCTION', :result_id)"
                    ),
                    {
                        "lot_no": lot_no,
                        "item_id": wo["item_id"],
                        "qty": body.good_qty,
                        "result_id": result_id,
                    },
                ).lastrowid

            # 완제품 LOT 번호는 실적 등록일(오늘) 기준
            product_lot_no, product_lot_id = insert_with_number(
                conn, "lot", "FG", date.today(), insert_product_lot
            )
            conn.execute(
                text(
                    "INSERT INTO inventory_tx (lot_id, tx_type, qty, reason, result_id) "
                    "VALUES (:lot_id, 'IN', :qty, 'PRODUCE', :result_id)"
                ),
                {"lot_id": product_lot_id, "qty": body.good_qty, "result_id": result_id},
            )

        # 9. 투입 LOT마다 출고(OUT/CONSUME)
        conn.execute(
            text(
                "INSERT INTO inventory_tx (lot_id, tx_type, qty, reason, result_id) "
                "VALUES (:lot_id, 'OUT', :qty, 'CONSUME', :result_id)"
            ),
            [
                {"lot_id": lot_id, "qty": qty_by_lot[lot_id], "result_id": result_id}
                for lot_id in lot_ids
            ],
        )

        # 10. 양품 누계가 계획수량 이상이면 DONE
        good_total = conn.execute(
            text("SELECT SUM(good_qty) FROM production_result WHERE wo_id = :wo_id"),
            {"wo_id": wo_id},
        ).scalar_one()
        wo_status = wo["status"]
        if good_total >= wo["plan_qty"]:
            conn.execute(
                text("UPDATE work_order SET status = 'DONE' WHERE wo_id = :wo_id"),
                {"wo_id": wo_id},
            )
            wo_status = "DONE"

        # 11. with 블록을 정상 종료하면 커밋
        return {
            "result_id": result_id,
            "product_lot_no": product_lot_no,
            "wo_status": wo_status,
        }
