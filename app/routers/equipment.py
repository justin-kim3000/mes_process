"""설비 API (API 5~6)."""
from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.db import get_conn
from app.errors import duplicate_code, is_duplicate_key
from app.schemas import EquipmentCreate, EquipmentOut

router = APIRouter(tags=["기준정보: 설비"])

_SELECT = "SELECT equipment_id, equipment_code, equipment_name, is_active FROM equipment"


@router.post("/equipment", response_model=EquipmentOut, status_code=201)
def create_equipment(body: EquipmentCreate, conn: Connection = Depends(get_conn)):
    try:
        with conn.begin():
            result = conn.execute(
                text(
                    "INSERT INTO equipment (equipment_code, equipment_name) "
                    "VALUES (:equipment_code, :equipment_name)"
                ),
                body.model_dump(),
            )
            return conn.execute(
                text(f"{_SELECT} WHERE equipment_id = :id"), {"id": result.lastrowid}
            ).mappings().one()
    except IntegrityError as e:
        if is_duplicate_key(e):
            raise duplicate_code(f"equipment_code {body.equipment_code} 이미 존재") from e
        raise


@router.get("/equipment", response_model=list[EquipmentOut])
def list_equipment(conn: Connection = Depends(get_conn)):
    return conn.execute(text(f"{_SELECT} ORDER BY equipment_id")).mappings().all()
