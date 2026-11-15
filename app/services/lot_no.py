"""번호 생성 (5-6 규칙).

형식: {접두어}-{YYYYMMDD}-{순번 3자리}
순번 = 같은 접두어·날짜의 기존 건수 + 1. UNIQUE 위반 시 1회 재시도.
"""
from collections.abc import Callable
from datetime import date
from typing import TypeVar

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.errors import duplicate_code, is_duplicate_key

T = TypeVar("T")

# SQL에 들어가는 테이블·컬럼명은 이 목록에서만 고른다 (사용자 입력 금지)
_TARGETS = {
    "lot": ("lot", "lot_no"),
    "work_order": ("work_order", "wo_no"),
}


def next_number(
    conn: Connection, target: str, prefix: str, on_date: date, locking: bool = False
) -> str:
    """다음 번호를 만든다.

    locking=True면 잠금 읽기로 다른 트랜잭션이 방금 커밋한 행까지 센다.
    (REPEATABLE READ의 일반 SELECT는 트랜잭션 시작 시점 스냅샷만 보므로
     재시도 때 같은 번호가 다시 나오는 것을 막기 위함)
    """
    table, column = _TARGETS[target]
    base = f"{prefix}-{on_date:%Y%m%d}-"
    sql = f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE :pattern"
    if locking:
        sql += " LOCK IN SHARE MODE"
    count = conn.execute(text(sql), {"pattern": base + "%"}).scalar_one()
    return f"{base}{count + 1:03d}"


def insert_with_number(
    conn: Connection,
    target: str,
    prefix: str,
    on_date: date,
    insert: Callable[[str], T],
) -> tuple[str, T]:
    """번호를 만들어 insert(번호)를 실행한다. 반드시 열린 트랜잭션 안에서 호출.

    UNIQUE 위반이면 SAVEPOINT까지만 되돌리고 번호를 다시 만들어 1회 재시도한다.
    재시도도 실패하면 DUPLICATE_CODE.
    """
    for attempt in range(2):
        no = next_number(conn, target, prefix, on_date, locking=attempt > 0)
        try:
            with conn.begin_nested():
                return no, insert(no)
        except IntegrityError as e:
            if not is_duplicate_key(e):
                raise
            if attempt == 1:
                raise duplicate_code(f"번호 {no} 생성 중 중복이 발생함. 다시 시도하세요") from e
    raise AssertionError("도달할 수 없음")
