"""mini-mes FastAPI 앱 진입점."""
from fastapi import FastAPI

app = FastAPI(title="mini-mes", description="초미니 MES (볼트 가공 라인)")


@app.get("/health")
def health() -> dict:
    """헬스 체크."""
    return {"status": "ok"}
