"""테스트 공통 설정.

- 세션 시작 시 테스트 DB(mini_mes_test)를 DROP/CREATE 후 001·002 마이그레이션 실행
- 각 테스트 전 실적 테이블 4개 비우기
- 앱의 get_conn 의존성을 테스트 DB로 교체
"""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
TEST_DB = "mini_mes_test"


def _load_dotenv() -> None:
    """프로젝트 루트 .env를 읽어 환경변수에 없는 값만 채운다 (호스트 실행용)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from app.db import get_conn, make_url  # noqa: E402  (.env 로드 후 import)
from app.main import app  # noqa: E402

# 비우는 순서: FK 자식 → 부모
TX_TABLES = ["inventory_tx", "lot", "production_result", "work_order"]


def _split_sql(path: Path) -> list[str]:
    """SQL 파일을 문장 단위로 나눈다. '--'로 시작하는 주석 줄은 버린다."""
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.strip().startswith("--")
    ]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


@pytest.fixture(scope="session")
def engine() -> Engine:
    # 테스트 DB 생성은 root로, 앱 접속은 일반 계정(DB_USER)으로
    admin = create_engine(
        make_url(user="root", password=os.environ["MARIADB_ROOT_PASSWORD"], database=None)
    )
    db_user = os.environ.get("DB_USER", "mes")
    with admin.begin() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB}"))
        conn.execute(
            text(f"CREATE DATABASE {TEST_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )
        conn.execute(text(f"GRANT ALL PRIVILEGES ON {TEST_DB}.* TO '{db_user}'@'%'"))
    admin.dispose()

    eng = create_engine(make_url(database=TEST_DB), pool_pre_ping=True)
    with eng.begin() as conn:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for stmt in _split_sql(path):
                conn.execute(text(stmt))
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_tx_tables(engine: Engine) -> None:
    """각 테스트 전 실적 테이블 4개 비우기 (기준정보는 유지)."""
    with engine.begin() as conn:
        for table in TX_TABLES:
            conn.execute(text(f"DELETE FROM {table}"))


@pytest.fixture
def client(engine: Engine):
    def override_get_conn():
        with engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_conn] = override_get_conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def item_ids(client: TestClient) -> dict[str, int]:
    """item_code → item_id."""
    return {i["item_code"]: i["item_id"] for i in client.get("/items").json()}


def count_rows(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
