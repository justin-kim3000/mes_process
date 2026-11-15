"""DB 연결. ORM 없이 SQLAlchemy Core engine + text()만 사용한다."""
import os
from collections.abc import Iterator

from sqlalchemy import URL, Connection, create_engine


def make_url(
    user: str | None = None,
    password: str | None = None,
    database: str | None = "",
) -> URL:
    """환경변수 기반 접속 URL. database=None이면 DB를 지정하지 않는다."""
    return URL.create(
        "mysql+pymysql",
        username=user or os.environ.get("DB_USER", "mes"),
        password=password if password is not None else os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        database=os.environ.get("DB_NAME", "mini_mes") if database == "" else database,
        query={"charset": "utf8mb4"},
    )


engine = create_engine(make_url(), pool_pre_ping=True)


def get_conn() -> Iterator[Connection]:
    """요청마다 connection 1개를 빌려준다.

    쓰기 엔드포인트는 `with conn.begin():`으로 트랜잭션을 직접 연다.
    커밋되지 않은 작업은 connection 반납 시 롤백된다.
    """
    with engine.connect() as conn:
        yield conn
