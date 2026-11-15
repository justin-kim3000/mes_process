"""Pydantic 요청·응답 모델. 수량은 전부 Decimal (DB DECIMAL(12,4)과 같은 자릿수)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# DECIMAL(12,4): 전체 12자리, 소수 4자리, 0보다 커야 함
Qty = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]

ItemType = Literal["RAW", "FINISHED"]
WoStatus = Literal["PLANNED", "IN_PROGRESS", "DONE"]


# ---- 품목 ----
class ItemCreate(BaseModel):
    item_code: str = Field(min_length=1, max_length=30)
    item_name: str = Field(min_length=1, max_length=100)
    item_type: ItemType
    unit: str = Field(min_length=1, max_length=10)


class ItemOut(BaseModel):
    item_id: int
    item_code: str
    item_name: str
    item_type: ItemType
    unit: str
    created_at: datetime


# ---- BOM ----
class BomCreate(BaseModel):
    parent_item_id: int
    child_item_id: int
    qty_per: Qty

    @model_validator(mode="after")
    def check_not_self(self) -> "BomCreate":
        if self.parent_item_id == self.child_item_id:
            raise ValueError("parent_item_id와 child_item_id가 같을 수 없음")
        return self


class BomOut(BaseModel):
    bom_id: int
    parent_item_id: int
    child_item_id: int
    qty_per: Decimal


class BomChildOut(BaseModel):
    child_item_id: int
    item_code: str
    item_name: str
    unit: str
    qty_per: Decimal


# ---- 설비 ----
class EquipmentCreate(BaseModel):
    equipment_code: str = Field(min_length=1, max_length=30)
    equipment_name: str = Field(min_length=1, max_length=100)


class EquipmentOut(BaseModel):
    equipment_id: int
    equipment_code: str
    equipment_name: str
    is_active: bool


# ---- 입고 / LOT ----
class ReceiptCreate(BaseModel):
    item_id: int
    qty: Qty


class LotOut(BaseModel):
    lot_id: int
    lot_no: str
    item_id: int
    initial_qty: Decimal
    source_type: Literal["RECEIPT", "PRODUCTION"]
    result_id: int | None
    created_at: datetime


# ---- 작업지시 ----
class WorkOrderCreate(BaseModel):
    item_id: int
    plan_qty: Qty
    plan_date: date


class WorkOrderOut(BaseModel):
    wo_id: int
    wo_no: str
    item_id: int
    plan_qty: Decimal
    plan_date: date
    status: WoStatus
    created_at: datetime


# ---- 생산실적 ----
# 양품·불량 수량은 0 허용 (둘의 합은 0보다 커야 함)
QtyZeroOk = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]


class ResultInput(BaseModel):
    lot_id: int
    qty: Qty


class ResultCreate(BaseModel):
    equipment_id: int
    good_qty: QtyZeroOk = Decimal("0")
    defect_qty: QtyZeroOk = Decimal("0")
    defect_code: str | None = Field(default=None, max_length=30)
    worker_name: str = Field(min_length=1, max_length=50)
    start_at: datetime
    end_at: datetime
    inputs: list[ResultInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_basic(self) -> "ResultCreate":
        # DB CHECK 제약과 같은 규칙을 입력 단계에서 422로 막는다
        if self.good_qty + self.defect_qty <= 0:
            raise ValueError("good_qty + defect_qty는 0보다 커야 함")
        if self.end_at <= self.start_at:
            raise ValueError("end_at은 start_at보다 뒤여야 함")
        return self


class ResultOut(BaseModel):
    result_id: int
    product_lot_no: str | None
    wo_status: WoStatus


# ---- 조회 ----
class InventoryOut(BaseModel):
    item_code: str
    item_name: str
    unit: str
    stock_qty: Decimal


class TraceInput(BaseModel):
    input_lot: str
    input_item: str
    input_qty: Decimal


class TraceOut(BaseModel):
    product_lot: str
    equipment_code: str
    inputs: list[TraceInput]


class YieldByEquipment(BaseModel):
    equipment_code: str
    good: Decimal
    defect: Decimal
    yield_pct: Decimal


class YieldByWorkOrder(BaseModel):
    wo_no: str
    good: Decimal
    defect: Decimal
    yield_pct: Decimal


class LotStockOut(BaseModel):
    lot_id: int
    lot_no: str
    item_id: int
    item_code: str
    item_name: str
    unit: str
    source_type: Literal["RECEIPT", "PRODUCTION"]
    initial_qty: Decimal
    stock_qty: Decimal
    created_at: datetime
