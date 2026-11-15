"""원자재 입고 API (API 7). 입고 1건 = 원자재 LOT 1개 + inventory_tx IN 1건."""
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from app.db import get_conn
from app.errors import invalid_item_type
from app.routers.items import fetch_item
from app.schemas import LotOut, ReceiptCreate
from app.services.lot_no import insert_with_number

router = APIRouter(tags=["입고"])


@router.post("/receipts", response_model=LotOut, status_code=201)
def create_receipt(body: ReceiptCreate, conn: Connection = Depends(get_conn)):
    with conn.begin():
        item = fetch_item(conn, body.item_id)
        if item["item_type"] != "RAW":
            raise invalid_item_type(f"품목 {item['item_code']}은 RAW가 아니므로 입고할 수 없음")

        def insert_lot(lot_no: str) -> int:
            return conn.execute(
                text(
                    "INSERT INTO lot (lot_no, item_id, initial_qty, source_type) "
                    "VALUES (:lot_no, :item_id, :qty, 'RECEIPT')"
                ),
                {"lot_no": lot_no, "item_id": body.item_id, "qty": body.qty},
            ).lastrowid

        # 원자재 LOT 번호는 입고일(오늘) 기준
        _, lot_id = insert_with_number(conn, "lot", "RM", date.today(), insert_lot)

        conn.execute(
            text(
                "INSERT INTO inventory_tx (lot_id, tx_type, qty, reason) "
                "VALUES (:lot_id, 'IN', :qty, 'RECEIPT')"
            ),
            {"lot_id": lot_id, "qty": body.qty},
        )
        return conn.execute(
            text(
                "SELECT lot_id, lot_no, item_id, initial_qty, source_type, result_id, created_at "
                "FROM lot WHERE lot_id = :lot_id"
            ),
            {"lot_id": lot_id},
        ).mappings().one()
