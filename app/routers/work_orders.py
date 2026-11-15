"""작업지시 API (API 8~10). 실적 등록(API 11)은 3단계에서 추가."""
from fastapi import APIRouter, Depends
from sqlalchemy import Connection, RowMapping, text

from app.db import get_conn
from app.errors import invalid_item_type, not_found, wo_invalid_status
from app.routers.items import fetch_item
from app.schemas import WoStatus, WorkOrderCreate, WorkOrderOut
from app.services.lot_no import insert_with_number

router = APIRouter(tags=["작업지시"])

_SELECT = (
    "SELECT wo_id, wo_no, item_id, plan_qty, plan_date, status, created_at FROM work_order"
)


def fetch_work_order(conn: Connection, wo_id: int, for_update: bool = False) -> RowMapping:
    """작업지시 1건 조회. 없으면 NOT_FOUND. for_update=True면 행 잠금."""
    sql = f"{_SELECT} WHERE wo_id = :wo_id" + (" FOR UPDATE" if for_update else "")
    row = conn.execute(text(sql), {"wo_id": wo_id}).mappings().first()
    if row is None:
        raise not_found(f"작업지시 wo_id={wo_id} 없음")
    return row


@router.post("/work-orders", response_model=WorkOrderOut, status_code=201)
def create_work_order(body: WorkOrderCreate, conn: Connection = Depends(get_conn)):
    with conn.begin():
        item = fetch_item(conn, body.item_id)
        if item["item_type"] != "FINISHED":
            raise invalid_item_type(
                f"품목 {item['item_code']}은 FINISHED가 아니므로 작업지시를 만들 수 없음"
            )

        def insert_wo(wo_no: str) -> int:
            return conn.execute(
                text(
                    "INSERT INTO work_order (wo_no, item_id, plan_qty, plan_date) "
                    "VALUES (:wo_no, :item_id, :plan_qty, :plan_date)"
                ),
                {"wo_no": wo_no, **body.model_dump()},
            ).lastrowid

        # 작업지시 번호는 plan_date 기준
        _, wo_id = insert_with_number(conn, "work_order", "WO", body.plan_date, insert_wo)
        return fetch_work_order(conn, wo_id)


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(status: WoStatus | None = None, conn: Connection = Depends(get_conn)):
    if status is None:
        return conn.execute(text(f"{_SELECT} ORDER BY wo_id")).mappings().all()
    return conn.execute(
        text(f"{_SELECT} WHERE status = :status ORDER BY wo_id"), {"status": status}
    ).mappings().all()


@router.patch("/work-orders/{wo_id}/start", response_model=WorkOrderOut)
def start_work_order(wo_id: int, conn: Connection = Depends(get_conn)):
    with conn.begin():
        wo = fetch_work_order(conn, wo_id, for_update=True)
        if wo["status"] != "PLANNED":
            raise wo_invalid_status(
                f"작업지시 {wo['wo_no']}는 {wo['status']} 상태라 시작할 수 없음 (PLANNED만 가능)"
            )
        conn.execute(
            text("UPDATE work_order SET status = 'IN_PROGRESS' WHERE wo_id = :wo_id"),
            {"wo_id": wo_id},
        )
        return fetch_work_order(conn, wo_id)
