"""mini-mes FastAPI 앱 진입점."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.errors import BusinessError, business_error_handler
from app.routers import equipment, items, receipts, reports, work_orders

app = FastAPI(title="mini-mes", description="초미니 MES (볼트 가공 라인)")
app.add_exception_handler(BusinessError, business_error_handler)

app.include_router(items.router)
app.include_router(equipment.router)
app.include_router(receipts.router)
app.include_router(work_orders.router)
app.include_router(reports.router)

# 현장 화면 (HTML 1장): http://localhost:8000/ui
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")


@app.get("/health", tags=["시스템"])
def health() -> dict:
    """헬스 체크."""
    return {"status": "ok"}
