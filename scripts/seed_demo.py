"""데모 데이터 생성: 직원 100여 명 규모 회사가 한 달 동안 mini-mes를 사용한 상태로 운영 DB를 채운다.

사용법 (프로젝트 루트, DB 컨테이너 실행 중):
    .venv/bin/python scripts/seed_demo.py --yes

- 실적 테이블 4개(work_order, production_result, lot, inventory_tx)를 비우고 다시 만든다.
- 기준정보(품목·BOM·설비)는 코드 기준으로 없으면 추가한다 (기존 행은 유지).
- 기간: 어제까지 31일. 평일 주·야 2교대, 토요일 주간만, 일요일 휴무. 생산직 4개 조(조당 24명).
- API와 같은 규칙을 지킨다: BOM 소요량(양품+불량 기준), LOT 재고 음수 금지, 번호 규칙, 작업지시 상태.
- 과거 날짜로 기록해야 하므로 API 대신 SQL(text())로 직접 넣고, 넣은 뒤 정합성을 검증한다.
- 난수 시드가 고정이라 같은 날 다시 실행하면 같은 데이터가 나온다.
"""
import argparse
import itertools
import math
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from sqlalchemy import Connection, create_engine, text  # noqa: E402

from app.db import make_url  # noqa: E402

Q4 = Decimal("0.0001")


def q4(value) -> Decimal:
    return Decimal(value).quantize(Q4, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# 기준정보
# ---------------------------------------------------------------------------
RAW_ITEMS = [
    ("RM-ROD", "강봉", "EA"),
    ("RM-PLATING", "도금액", "L"),
    ("RM-ROD-S", "강봉(소경 Ø8)", "EA"),
    ("RM-ROD-L", "강봉(대경 Ø12)", "EA"),
    ("RM-HEX", "육각 블랭크", "EA"),
    ("RM-COIL", "강판 코일", "KG"),
]
FINISHED_ITEMS = [
    ("FG-BOLT", "볼트", "EA"),
    ("FG-BOLT-M8", "볼트 M8×30", "EA"),
    ("FG-BOLT-M12", "볼트 M12×50", "EA"),
    ("FG-NUT-M10", "너트 M10", "EA"),
    ("FG-NUT-M12", "너트 M12", "EA"),
    ("FG-WASHER-M10", "평와셔 M10", "EA"),
    ("FG-WASHER-M12", "평와셔 M12", "EA"),
]
BOMS = {
    "FG-BOLT": [("RM-ROD", "1.0000"), ("RM-PLATING", "0.0100")],
    "FG-BOLT-M8": [("RM-ROD-S", "1.0000"), ("RM-PLATING", "0.0080")],
    "FG-BOLT-M12": [("RM-ROD-L", "1.0000"), ("RM-PLATING", "0.0150")],
    "FG-NUT-M10": [("RM-HEX", "1.0000"), ("RM-PLATING", "0.0040")],
    "FG-NUT-M12": [("RM-HEX", "1.0000"), ("RM-PLATING", "0.0060")],
    "FG-WASHER-M10": [("RM-COIL", "0.0040"), ("RM-PLATING", "0.0020")],
    "FG-WASHER-M12": [("RM-COIL", "0.0060"), ("RM-PLATING", "0.0030")],
}
# 설비 코드, 이름, 제품군, 기본 불량률
EQUIPMENT = [
    ("CNC-01", "가공기 1호", "BOLT", 0.030),
    ("CNC-02", "가공기 2호", "BOLT", 0.020),
    ("CNC-03", "가공기 3호", "BOLT", 0.025),
    ("CNC-04", "가공기 4호", "BOLT", 0.045),   # 노후 설비
    ("TAP-01", "너트 탭핑기 1호", "NUT", 0.018),
    ("TAP-02", "너트 탭핑기 2호", "NUT", 0.022),
    ("PRS-01", "와셔 프레스 1호", "WASHER", 0.010),
    ("PRS-02", "와셔 프레스 2호", "WASHER", 0.014),
]
FAMILIES = {
    "BOLT": {
        "products": [("FG-BOLT", 50), ("FG-BOLT-M8", 30), ("FG-BOLT-M12", 20)],
        "plan": (3000, 8000, 500),
        "daily_cap": 9500,
        "codes": [("SCRATCH", 30), ("DIMENSION", 30), ("THREAD", 25), ("CRACK", 5), ("PLATING", 10)],
    },
    "NUT": {
        "products": [("FG-NUT-M10", 60), ("FG-NUT-M12", 40)],
        "plan": (5000, 15000, 1000),
        "daily_cap": 8500,
        "codes": [("THREAD", 45), ("DIMENSION", 25), ("BURR", 20), ("PLATING", 10)],
    },
    "WASHER": {
        "products": [("FG-WASHER-M10", 55), ("FG-WASHER-M12", 45)],
        "plan": (20000, 60000, 5000),
        "daily_cap": 42000,
        "codes": [("BURR", 40), ("DENT", 30), ("DIMENSION", 20), ("PLATING", 10)],
    },
}
# 시간당 생산량 (EA)
RATE_PER_HOUR = {
    "FG-BOLT": 175, "FG-BOLT-M8": 200, "FG-BOLT-M12": 135,
    "FG-NUT-M10": 320, "FG-NUT-M12": 280,
    "FG-WASHER-M10": 1600, "FG-WASHER-M12": 1400,
}
# 원자재 입고 규칙: (최소, 최대, 단위) 입고량, 재주문점
RAW_RECEIPT = {
    "RM-ROD": (8000, 12000, 1000, 7000),
    "RM-ROD-S": (5000, 8000, 1000, 4500),
    "RM-ROD-L": (3000, 5000, 500, 3000),
    "RM-HEX": (15000, 25000, 5000, 13000),
    "RM-COIL": (900, 1300, None, 350),       # 코일은 계근 중량 (소수 1자리)
    "RM-PLATING": (400, 800, 200, 450),      # 200L 드럼 단위
}

SURNAMES = list("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나")
GIVEN = (
    "민준 서준 도윤 예준 시우 하준 지호 주원 지후 준우 준서 건우 현우 우진 선우 서진 민재 현준 "
    "연우 유준 정우 승우 승현 시윤 준혁 지훈 동현 성민 영수 영호 상철 정훈 재현 태호 경민 수빈 "
    "지은 미영 은정 혜진 수진 지영 서연 민지 유진 하은 지현 은지 소연 예린 다은 영희 미숙 경희 "
    "정숙 명수 광수 철민 용호 성호 대호 병철 종민 기현 상우 형준 진우 태민 재훈 승민 혜원 보람"
).split()
FOREIGN_NAMES = ["응우옌 반 훙", "쩐 티 마이", "팜 반 득", "라흐마트 하디"]

# 교대 시간대: (시작, 종료, 종료가 다음날인지)
SHIFT_BLOCKS = {
    "DAY": [((8, 0), (12, 0), 0), ((13, 0), (17, 0), 0)],
    "NIGHT": [((20, 0), (23, 50), 0), ((1, 0), (5, 0), 1)],
}


@dataclass
class Lot:
    key: int
    item: str
    qty: Decimal
    source: str
    created_at: datetime
    result_key: int | None = None
    remaining: Decimal = Decimal("0")


@dataclass
class WorkOrder:
    key: int
    item: str
    family: str
    plan_qty: int
    plan_date: date
    created_at: datetime
    good: int = 0
    results: int = 0


@dataclass
class Result:
    key: int
    wo: WorkOrder
    equipment: str
    good: int
    defect: int
    defect_code: str | None
    worker: str
    start_at: datetime
    end_at: datetime
    created_at: datetime


# ---------------------------------------------------------------------------
# 시뮬레이션
# ---------------------------------------------------------------------------
class Simulator:
    def __init__(self, start_day: date, end_day: date, seed: int):
        self.rng = random.Random(seed)
        self.start_day, self.end_day = start_day, end_day
        self.seq = itertools.count(1)
        self.lots: list[Lot] = []
        self.lots_by_item: dict[str, list[Lot]] = defaultdict(list)
        self.txs: list[tuple] = []   # (lot_key, tx_type, qty, reason, result_key, created_at, seq)
        self.results: list[Result] = []
        self.wos: list[WorkOrder] = []
        self.qty_per = {fg: {c: Decimal(q) for c, q in kids} for fg, kids in BOMS.items()}
        self.crews = self._make_crews()
        self.shift_no = 0
        # CNC-04 고장 수리 2일
        self.breakdown = {("CNC-04", start_day + timedelta(days=12)), ("CNC-04", start_day + timedelta(days=13))}

    # ---- 사람 ----
    def _make_crews(self) -> list[dict[str, list[str]]]:
        names: list[str] = list(FOREIGN_NAMES)
        while len(names) < 96:
            name = self.rng.choice(SURNAMES) + self.rng.choice(GIVEN)
            if name not in names:
                names.append(name)
        self.rng.shuffle(names)
        crews = []
        for i in range(4):
            members = names[i * 24:(i + 1) * 24]
            crews.append({"BOLT": members[:12], "NUT": members[12:18], "WASHER": members[18:]})
        return crews

    # ---- 시간 ----
    def at(self, d: date, h: int, m: int = 0) -> datetime:
        return datetime.combine(d, time(h, m))

    def minutes(self, lo: int, hi: int) -> timedelta:
        return timedelta(minutes=self.rng.randint(lo, hi), seconds=self.rng.randint(0, 59))

    def weighted(self, pairs):
        values, weights = zip(*pairs)
        return self.rng.choices(values, weights=weights)[0]

    # ---- 입고 ----
    def receipt_qty(self, item: str) -> Decimal:
        lo, hi, step, _ = RAW_RECEIPT[item]
        if step is None:
            return Decimal(str(round(self.rng.uniform(lo, hi), 1)))
        return Decimal(self.rng.randrange(lo, hi + step, step))

    def receive(self, item: str, qty: Decimal, created_at: datetime) -> Lot:
        lot = Lot(next(self.seq), item, q4(qty), "RECEIPT", created_at)
        lot.remaining = lot.qty
        self.lots.append(lot)
        self.lots_by_item[item].append(lot)
        self.txs.append((lot.key, "IN", lot.qty, "RECEIPT", None, created_at, next(self.seq)))
        return lot

    def stock(self, item: str) -> Decimal:
        return sum((lot.remaining for lot in self.lots_by_item[item]), Decimal("0"))

    def morning_receipts(self, d: date) -> None:
        """자재팀 오전 입고: 재주문점 아래로 떨어진 원자재를 입고."""
        t = self.at(d, 8, 30) + self.minutes(0, 40)
        for item, (_, _, _, reorder) in RAW_RECEIPT.items():
            while self.stock(item) < reorder:
                self.receive(item, self.receipt_qty(item), t)
                t += self.minutes(4, 25)

    def emergency_time(self, need_at: datetime) -> datetime:
        """긴급 입고 시각: 근무 시간이면 직전, 야간이면 전날 17시대."""
        if 9 <= need_at.hour < 18:
            return need_at - self.minutes(15, 45)
        base = need_at.date() if need_at.hour >= 18 else need_at.date() - timedelta(days=1)
        return self.at(base, 17, 0) + self.minutes(0, 40)

    def consume(self, item: str, need: Decimal, at: datetime) -> list[tuple[Lot, Decimal]]:
        """선입선출로 투입 LOT을 고른다. 모자라면 긴급 입고."""
        def available():
            return sorted(
                (lot for lot in self.lots_by_item[item] if lot.remaining > 0 and lot.created_at <= at),
                key=lambda lot: (lot.created_at, lot.key),
            )

        if sum((lot.remaining for lot in available()), Decimal("0")) < need:
            self.receive(item, max(self.receipt_qty(item), need), self.emergency_time(at))
        picks, left = [], need
        for lot in available():
            take = min(lot.remaining, left)
            lot.remaining -= take
            picks.append((lot, take))
            left -= take
            if left == 0:
                break
        assert left == 0
        return picks

    # ---- 작업지시 (ERP에서 내려옴) ----
    def new_work_order(self, family: str, plan_date: date, created_at: datetime) -> WorkOrder:
        lo, hi, step = FAMILIES[family]["plan"]
        wo = WorkOrder(
            next(self.seq), self.weighted(FAMILIES[family]["products"]), family,
            self.rng.randrange(lo, hi + step, step), plan_date, created_at,
        )
        self.wos.append(wo)
        return wo

    def open_orders(self, family: str, d: date) -> list[WorkOrder]:
        return [w for w in self.wos if w.family == family and w.good < w.plan_qty and w.plan_date <= d]

    def ensure_work_orders(self, d: date, prev_workday: date | None) -> None:
        for family, spec in FAMILIES.items():
            remaining = sum(w.plan_qty - w.good for w in self.wos if w.family == family and w.good < w.plan_qty)
            while remaining < 1.6 * spec["daily_cap"]:
                created = (
                    self.at(prev_workday, 14, 0) + self.minutes(0, 220) if prev_workday
                    else self.at(d, 7, 20) + self.minutes(0, 30)
                )
                remaining += self.new_work_order(family, d, created).plan_qty

    # ---- 생산 ----
    def run_block(self, start: datetime, end: datetime, plan_day: date, operators: dict[str, str]) -> None:
        pending = []
        for code, _, family, base_rate in EQUIPMENT:
            if (code, plan_day) in self.breakdown or self.rng.random() < 0.04:
                continue   # 고장·점검·자재 대기
            orders = self.open_orders(family, plan_day)
            if not orders:
                continue
            wo = min(orders, key=lambda w: (w.plan_date, w.created_at))
            s = start + self.minutes(0, 15)
            e = end - self.minutes(0, 25)
            hours = (e - s).total_seconds() / 3600
            capacity = max(1, int(RATE_PER_HOUR[wo.item] * hours * self.rng.uniform(0.85, 1.0)))

            if self.rng.random() < 0.005:
                # 셋업(시운전) 실패: 양품 없이 불량만
                good, defect, defect_code = 0, self.rng.randint(5, 30), "SETUP"
                e = s + self.minutes(20, 45)
            else:
                zero_chance = 0.15 if base_rate <= 0.02 else 0.05
                rate = 0.0 if self.rng.random() < zero_chance else base_rate * self.rng.uniform(0.4, 1.8)
                defect = round(capacity * rate)
                good = capacity - defect
                remaining = wo.plan_qty - wo.good
                if good >= remaining:
                    # 계획 수량 달성: 남은 양만 만들고 일찍 끝낸다
                    good = remaining
                    total = math.ceil(good / (1 - rate))
                    defect = total - good
                    e = max(s + (e - s) * (total / capacity), s + timedelta(minutes=20))
                defect_code = self.weighted(FAMILIES[family]["codes"]) if defect > 0 else None

            wo.good += good
            wo.results += 1
            reg = e + self.minutes(2, 15)   # 작업 종료 후 실적 등록
            pending.append((reg, wo, code, good, defect, defect_code, operators[code], s, e))

        for reg, wo, code, good, defect, defect_code, worker, s, e in sorted(pending, key=lambda p: p[0]):
            result = Result(next(self.seq), wo, code, good, defect, defect_code, worker, s, e, reg)
            self.results.append(result)
            if good > 0:
                lot = Lot(next(self.seq), wo.item, Decimal(good), "PRODUCTION", reg, result.key, Decimal(good))
                self.lots.append(lot)
                self.lots_by_item[wo.item].append(lot)
                self.txs.append((lot.key, "IN", lot.qty, "PRODUCE", result.key, reg, next(self.seq)))
            total = Decimal(good + defect)
            for child, per in self.qty_per[wo.item].items():
                for lot, qty in self.consume(child, q4(per * total), reg):
                    self.txs.append((lot.key, "OUT", qty, "CONSUME", result.key, reg, next(self.seq)))

    def run_shift(self, d: date, shift: str) -> None:
        crew = self.crews[self.shift_no % 4]
        self.shift_no += 1
        operators = {}
        for family in FAMILIES:
            machines = [c for c, _, f, _ in EQUIPMENT if f == family]
            for machine, person in zip(machines, self.rng.sample(crew[family], len(machines))):
                operators[machine] = person
        for (sh, sm), (eh, em), next_day in SHIFT_BLOCKS[shift]:
            block_day = d + timedelta(days=next_day)
            self.run_block(self.at(block_day, sh, sm), self.at(block_day, eh, em), d, operators)

    def run(self) -> None:
        prev_workday = None
        d = self.start_day
        while d <= self.end_day:
            shifts = ["DAY", "NIGHT"] if d.weekday() < 5 else (["DAY"] if d.weekday() == 5 else [])
            if shifts:
                self.ensure_work_orders(d, prev_workday)
                self.morning_receipts(d)
                for shift in shifts:
                    self.run_shift(d, shift)
                prev_workday = d
            d += timedelta(days=1)

        # 다음 근무일 2일치 계획 (ERP에서 미리 내려온 작업지시)
        upcoming, d = [], self.end_day + timedelta(days=1)
        while len(upcoming) < 2:
            if d.weekday() != 6:
                upcoming.append(d)
            d += timedelta(days=1)
        office_day = max(x for x in (self.end_day - timedelta(days=i) for i in range(3)) if x.weekday() < 5)
        for plan_day in upcoming:
            for family in FAMILIES:
                if self.rng.random() < 0.8:
                    self.new_work_order(family, plan_day, self.at(office_day, 15, 0) + self.minutes(0, 150))


# ---------------------------------------------------------------------------
# DB 반영
# ---------------------------------------------------------------------------
TX_TABLES = ["inventory_tx", "lot", "production_result", "work_order"]


def upsert_master(conn: Connection, setup_at: datetime) -> tuple[dict[str, int], dict[str, int]]:
    for code, name, unit in RAW_ITEMS + FINISHED_ITEMS:
        item_type = "RAW" if code.startswith("RM-") else "FINISHED"
        conn.execute(
            text(
                "INSERT INTO item (item_code, item_name, item_type, unit) "
                "VALUES (:code, :name, :type, :unit) "
                "ON DUPLICATE KEY UPDATE item_code = item_code"
            ),
            {"code": code, "name": name, "type": item_type, "unit": unit},
        )
    # 시스템 도입 시점으로 품목 등록일을 맞춘다
    conn.execute(text("UPDATE item SET created_at = :at"), {"at": setup_at})
    items = dict(conn.execute(text("SELECT item_code, item_id FROM item")).all())

    for parent, kids in BOMS.items():
        for child, qty_per in kids:
            conn.execute(
                text(
                    "INSERT INTO bom (parent_item_id, child_item_id, qty_per) VALUES (:p, :c, :q) "
                    "ON DUPLICATE KEY UPDATE qty_per = qty_per"
                ),
                {"p": items[parent], "c": items[child], "q": qty_per},
            )
    for code, name, _, _ in EQUIPMENT:
        conn.execute(
            text(
                "INSERT INTO equipment (equipment_code, equipment_name) VALUES (:code, :name) "
                "ON DUPLICATE KEY UPDATE equipment_code = equipment_code"
            ),
            {"code": code, "name": name},
        )
    equipment = dict(conn.execute(text("SELECT equipment_code, equipment_id FROM equipment")).all())
    return items, equipment


def write(engine, sim: Simulator) -> None:
    # 1) 실적 테이블 비우기 + AUTO_INCREMENT 초기화 (ALTER는 자동 커밋되므로 따로 실행)
    with engine.begin() as conn:
        for table in TX_TABLES:
            conn.execute(text(f"DELETE FROM {table}"))
    with engine.begin() as conn:
        for table in TX_TABLES:
            conn.execute(text(f"ALTER TABLE {table} AUTO_INCREMENT = 1"))

    # 2) 한 트랜잭션으로 기준정보 + 한 달치 실적 입력
    with engine.begin() as conn:
        items, equipment = upsert_master(conn, sim.at(sim.start_day - timedelta(days=2), 10, 0))

        # 작업지시: 생성 순서대로 id, 번호는 plan_date별 순번
        wo_id, wo_rows, wo_seq = {}, [], defaultdict(int)
        for i, wo in enumerate(sorted(sim.wos, key=lambda w: (w.created_at, w.key)), start=1):
            wo_seq[wo.plan_date] += 1
            wo_id[wo.key] = i
            status = "DONE" if wo.good >= wo.plan_qty else ("IN_PROGRESS" if wo.results else "PLANNED")
            wo_rows.append({
                "id": i, "no": f"WO-{wo.plan_date:%Y%m%d}-{wo_seq[wo.plan_date]:03d}",
                "item": items[wo.item], "qty": wo.plan_qty, "date": wo.plan_date,
                "status": status, "at": wo.created_at,
            })
        conn.execute(
            text(
                "INSERT INTO work_order (wo_id, wo_no, item_id, plan_qty, plan_date, status, created_at) "
                "VALUES (:id, :no, :item, :qty, :date, :status, :at)"
            ),
            wo_rows,
        )

        result_id, result_rows = {}, []
        for i, r in enumerate(sorted(sim.results, key=lambda r: (r.created_at, r.key)), start=1):
            result_id[r.key] = i
            result_rows.append({
                "id": i, "wo": wo_id[r.wo.key], "eq": equipment[r.equipment], "good": r.good,
                "defect": r.defect, "code": r.defect_code, "worker": r.worker,
                "s": r.start_at, "e": r.end_at, "at": r.created_at,
            })
        conn.execute(
            text(
                "INSERT INTO production_result (result_id, wo_id, equipment_id, good_qty, defect_qty, "
                "defect_code, worker_name, start_at, end_at, created_at) "
                "VALUES (:id, :wo, :eq, :good, :defect, :code, :worker, :s, :e, :at)"
            ),
            result_rows,
        )

        # LOT: 생성 순서대로 id, 번호는 접두어·날짜별 순번
        lot_id, lot_rows, lot_seq = {}, [], defaultdict(int)
        for i, lot in enumerate(sorted(sim.lots, key=lambda x: (x.created_at, x.key)), start=1):
            prefix = "RM" if lot.source == "RECEIPT" else "FG"
            day = lot.created_at.date()
            lot_seq[(prefix, day)] += 1
            lot_id[lot.key] = i
            lot_rows.append({
                "id": i, "no": f"{prefix}-{day:%Y%m%d}-{lot_seq[(prefix, day)]:03d}",
                "item": items[lot.item], "qty": lot.qty, "src": lot.source,
                "rid": result_id.get(lot.result_key), "at": lot.created_at,
            })
        conn.execute(
            text(
                "INSERT INTO lot (lot_id, lot_no, item_id, initial_qty, source_type, result_id, created_at) "
                "VALUES (:id, :no, :item, :qty, :src, :rid, :at)"
            ),
            lot_rows,
        )

        tx_rows = [
            {"id": i, "lot": lot_id[lk], "type": tt, "qty": qty, "reason": reason,
             "rid": result_id.get(rk), "at": at}
            for i, (lk, tt, qty, reason, rk, at, _) in enumerate(
                sorted(sim.txs, key=lambda t: (t[5], t[6])), start=1)
        ]
        conn.execute(
            text(
                "INSERT INTO inventory_tx (tx_id, lot_id, tx_type, qty, reason, result_id, created_at) "
                "VALUES (:id, :lot, :type, :qty, :reason, :rid, :at)"
            ),
            tx_rows,
        )


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------
CHECKS = {
    "LOT 재고가 시간순으로 한 번도 음수가 되지 않음": """
        SELECT COUNT(*) FROM (
          SELECT SUM(CASE tx_type WHEN 'IN' THEN qty ELSE -qty END)
                 OVER (PARTITION BY lot_id ORDER BY created_at, tx_id) AS running
          FROM inventory_tx) x
        WHERE running < 0""",
    "투입 품목별 합계 = qty_per × (양품+불량)": """
        SELECT COUNT(*) FROM production_result pr
        JOIN work_order w ON w.wo_id = pr.wo_id
        JOIN bom b ON b.parent_item_id = w.item_id
        LEFT JOIN (SELECT t.result_id, l.item_id, SUM(t.qty) AS s
                   FROM inventory_tx t JOIN lot l ON l.lot_id = t.lot_id
                   WHERE t.reason = 'CONSUME' GROUP BY t.result_id, l.item_id) c
          ON c.result_id = pr.result_id AND c.item_id = b.child_item_id
        WHERE c.s IS NULL OR c.s <> ROUND(b.qty_per * (pr.good_qty + pr.defect_qty), 4)""",
    "BOM에 없는 품목 투입 없음": """
        SELECT COUNT(*) FROM inventory_tx t
        JOIN lot l ON l.lot_id = t.lot_id
        JOIN production_result pr ON pr.result_id = t.result_id
        JOIN work_order w ON w.wo_id = pr.wo_id
        LEFT JOIN bom b ON b.parent_item_id = w.item_id AND b.child_item_id = l.item_id
        WHERE t.reason = 'CONSUME' AND b.bom_id IS NULL""",
    "양품 > 0 실적마다 완제품 LOT 1개, 수량 = 양품": """
        SELECT (SELECT COUNT(*) FROM production_result WHERE good_qty > 0)
             - (SELECT COUNT(*) FROM lot fg JOIN production_result pr ON pr.result_id = fg.result_id
                WHERE fg.source_type = 'PRODUCTION' AND fg.initial_qty = pr.good_qty)""",
    "작업지시 상태 = 양품 누계 규칙": """
        SELECT COUNT(*) FROM work_order w
        LEFT JOIN (SELECT wo_id, SUM(good_qty) AS g, COUNT(*) AS n
                   FROM production_result GROUP BY wo_id) r ON r.wo_id = w.wo_id
        WHERE (w.status = 'DONE' AND COALESCE(r.g, 0) < w.plan_qty)
           OR (w.status <> 'DONE' AND COALESCE(r.g, 0) >= w.plan_qty)
           OR (w.status = 'PLANNED' AND r.n IS NOT NULL)
           OR (w.status = 'IN_PROGRESS' AND r.n IS NULL)""",
    "불량 > 0이면 불량코드 있음": """
        SELECT COUNT(*) FROM production_result WHERE defect_qty > 0 AND defect_code IS NULL""",
    "LOT 생성 이후에만 재고 이동": """
        SELECT COUNT(*) FROM inventory_tx t JOIN lot l ON l.lot_id = t.lot_id
        WHERE t.created_at < l.created_at""",
    "같은 설비 작업 시간 겹침 없음": """
        SELECT COUNT(*) FROM production_result a JOIN production_result b
          ON a.equipment_id = b.equipment_id AND a.result_id < b.result_id
         AND a.start_at < b.end_at AND b.start_at < a.end_at""",
}


def verify(engine) -> bool:
    ok = True
    with engine.connect() as conn:
        for name, sql in CHECKS.items():
            bad = conn.execute(text(sql)).scalar_one()
            print(f"  {'OK ' if bad == 0 else 'NG '} {name}" + ("" if bad == 0 else f" (위반 {bad}건)"))
            ok &= bad == 0
        # 번호 순번이 접두어·날짜별로 1부터 빈틈없이 이어지는지
        for table, col, key in (("lot", "lot_no", "lot_id"), ("work_order", "wo_no", "wo_id")):
            nos = [r[0] for r in conn.execute(text(f"SELECT {col} FROM {table} ORDER BY {key}"))]
            seen: dict[str, int] = defaultdict(int)
            broken = 0
            for no in nos:
                head, num = no.rsplit("-", 1)
                seen[head] += 1
                broken += int(num) != seen[head]
            print(f"  {'OK ' if broken == 0 else 'NG '} {table} 번호 순번 연속" + ("" if broken == 0 else f" ({broken}건)"))
            ok &= broken == 0
    return ok


def summary(engine) -> None:
    with engine.connect() as conn:
        one = lambda sql: conn.execute(text(sql)).scalar_one()  # noqa: E731
        print(f"  기간          {one('SELECT MIN(start_at) FROM production_result')} ~ "
              f"{one('SELECT MAX(end_at) FROM production_result')}")
        print(f"  품목 / 설비   {one('SELECT COUNT(*) FROM item')} / {one('SELECT COUNT(*) FROM equipment')}")
        print(f"  작업자        {one('SELECT COUNT(DISTINCT worker_name) FROM production_result')}명")
        status = ", ".join(f"{s} {n}" for s, n in conn.execute(text(
            "SELECT status, COUNT(*) FROM work_order GROUP BY status ORDER BY status")))
        print(f"  작업지시      {one('SELECT COUNT(*) FROM work_order')}건 ({status})")
        print(f"  생산실적      {one('SELECT COUNT(*) FROM production_result')}건")
        raw_lots = one("SELECT COUNT(*) FROM lot WHERE source_type = 'RECEIPT'")
        fg_lots = one("SELECT COUNT(*) FROM lot WHERE source_type = 'PRODUCTION'")
        print(f"  LOT           원자재 {raw_lots} / 완제품 {fg_lots}")
        print(f"  재고 이동     {one('SELECT COUNT(*) FROM inventory_tx')}건")


def main() -> None:
    parser = argparse.ArgumentParser(description="한 달 운영 데모 데이터 생성")
    parser.add_argument("--yes", action="store_true", help="확인 없이 실적 데이터를 지우고 다시 만든다")
    parser.add_argument("--end", type=date.fromisoformat, default=date.today() - timedelta(days=1),
                        help="마지막 날짜 (기본: 어제)")
    parser.add_argument("--seed", type=int, default=20261115, help="난수 시드")
    args = parser.parse_args()

    db_name = os.environ.get("DB_NAME", "mini_mes")
    if not args.yes:
        answer = input(f"DB '{db_name}'의 실적 데이터 4개 테이블을 모두 지우고 데모 데이터로 채웁니다. 계속할까요? [y/N] ")
        if answer.strip().lower() != "y":
            print("취소했습니다.")
            return

    start = args.end - timedelta(days=30)
    print(f"[1/3] 시뮬레이션 {start} ~ {args.end}")
    sim = Simulator(start, args.end, args.seed)
    sim.run()

    engine = create_engine(make_url(), pool_pre_ping=True)
    print(f"[2/3] DB '{db_name}'에 입력")
    write(engine, sim)
    summary(engine)
    print("[3/3] 정합성 검증")
    if not verify(engine):
        print("검증 실패")
        sys.exit(1)
    print("완료")


if __name__ == "__main__":
    main()
