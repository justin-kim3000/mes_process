"""품목·BOM API (API 1~4)."""
from fastapi import APIRouter, Depends
from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from app.db import get_conn
from app.errors import duplicate_code, invalid_item_type, is_duplicate_key, not_found
from app.schemas import BomChildOut, BomCreate, BomOut, ItemCreate, ItemOut

router = APIRouter(tags=["기준정보: 품목·BOM"])


def fetch_item(conn: Connection, item_id: int) -> RowMapping:
    """item 1건 조회. 없으면 NOT_FOUND. 다른 라우터에서도 사용."""
    row = conn.execute(
        text(
            "SELECT item_id, item_code, item_name, item_type, unit, created_at "
            "FROM item WHERE item_id = :item_id"
        ),
        {"item_id": item_id},
    ).mappings().first()
    if row is None:
        raise not_found(f"품목 item_id={item_id} 없음")
    return row


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemCreate, conn: Connection = Depends(get_conn)):
    try:
        with conn.begin():
            result = conn.execute(
                text(
                    "INSERT INTO item (item_code, item_name, item_type, unit) "
                    "VALUES (:item_code, :item_name, :item_type, :unit)"
                ),
                body.model_dump(),
            )
            return fetch_item(conn, result.lastrowid)
    except IntegrityError as e:
        if is_duplicate_key(e):
            raise duplicate_code(f"item_code {body.item_code} 이미 존재") from e
        raise


@router.get("/items", response_model=list[ItemOut])
def list_items(conn: Connection = Depends(get_conn)):
    return conn.execute(
        text(
            "SELECT item_id, item_code, item_name, item_type, unit, created_at "
            "FROM item ORDER BY item_id"
        )
    ).mappings().all()


@router.post("/boms", response_model=BomOut, status_code=201)
def create_bom(body: BomCreate, conn: Connection = Depends(get_conn)):
    try:
        with conn.begin():
            parent = fetch_item(conn, body.parent_item_id)
            child = fetch_item(conn, body.child_item_id)
            # 2단계 BOM만 다룬다: 완제품 = 원자재 조합
            if parent["item_type"] != "FINISHED":
                raise invalid_item_type(f"BOM 모품목 {parent['item_code']}은 FINISHED여야 함")
            if child["item_type"] != "RAW":
                raise invalid_item_type(f"BOM 자품목 {child['item_code']}은 RAW여야 함")
            result = conn.execute(
                text(
                    "INSERT INTO bom (parent_item_id, child_item_id, qty_per) "
                    "VALUES (:parent_item_id, :child_item_id, :qty_per)"
                ),
                body.model_dump(),
            )
            return conn.execute(
                text(
                    "SELECT bom_id, parent_item_id, child_item_id, qty_per "
                    "FROM bom WHERE bom_id = :bom_id"
                ),
                {"bom_id": result.lastrowid},
            ).mappings().one()
    except IntegrityError as e:
        if is_duplicate_key(e):
            raise duplicate_code(
                f"BOM {parent['item_code']} → {child['item_code']} 이미 존재"
            ) from e
        raise


@router.get("/items/{item_id}/bom", response_model=list[BomChildOut])
def get_item_bom(item_id: int, conn: Connection = Depends(get_conn)):
    fetch_item(conn, item_id)
    return conn.execute(
        text(
            "SELECT c.item_id AS child_item_id, c.item_code, c.item_name, c.unit, b.qty_per "
            "FROM bom b JOIN item c ON c.item_id = b.child_item_id "
            "WHERE b.parent_item_id = :item_id ORDER BY b.bom_id"
        ),
        {"item_id": item_id},
    ).mappings().all()
