# mini-mes — 초미니 MES 작업 지시서

> 이 파일은 Claude Code가 읽고 단계별로 구현하기 위한 지시서다.
> 프로젝트 루트(`mini-mes/`)에 `CLAUDE.md`로 둔다.

---

## 0. 작업 규칙 (반드시 지킬 것)

1. **한 번에 한 단계만** 진행한다. 각 단계의 "완료 확인"을 모두 통과하면 멈추고, 결과를 보고한 뒤 다음 단계 지시를 기다린다.
2. **범위 밖 기능은 만들지 않는다.** (1-3 참고) 필요해 보여도 제안만 하고 구현하지 않는다.
3. **ORM을 쓰지 않는다.** SQLAlchemy 2.x `engine` + `text()`로 SQL을 직접 작성한다. ORM 모델 클래스는 만들지 않는다.
4. **스키마는 이 문서의 DDL을 그대로 쓴다.** 테이블·컬럼을 바꿔야 하면 이유를 먼저 보고한다.
5. **수량은 전부 `Decimal`** 로 다룬다. `float` 변환 금지.
6. 실적 등록은 **DB 트랜잭션 1개**로 처리한다. 중간 실패 시 전부 롤백.
7. 코드 주석·커밋 메시지·README는 한국어로 쓴다.

---

## 1. 프로젝트 개요

### 1-1. 목적
MES의 기본 개념(기준정보, 작업지시, 생산실적, LOT 추적, 재고 원장)을 테이블 7개와 REST API로 직접 구현해, 데이터가 어디서 생기고 어디로 흐르는지 설명할 수 있게 한다.

### 1-2. 가상 공장
- 볼트 가공 라인 1개
- 원자재: 강봉(EA), 도금액(L) → 같은 공정의 가공 설비 2대(CNC-01, CNC-02) → 완제품: 볼트(EA)
- BOM: 볼트 1EA = 강봉 1EA + 도금액 0.01L
- 위 공장은 **시드(002)와 테스트·7장 시나리오의 기준**이다. 테스트는 이 기준정보만 쓴다.

#### 1-2-1. 데모 공장 (운영 DB 데모 데이터, 2026-11-15 사용자 승인)
- `scripts/seed_demo.py`가 운영 DB를 "직원 100여 명 회사가 한 달 사용한 상태"로 채운다. 테이블·컬럼 변경 없음, 행만 추가.
- 품목 13종: 완제품 7 (볼트·볼트 M8·볼트 M12·너트 M10·너트 M12·평와셔 M10·평와셔 M12), 원자재 6 (강봉·강봉 소경·강봉 대경·육각 블랭크·강판 코일(KG)·도금액)
- 설비 8대: CNC-01~04(볼트), TAP-01~02(너트), PRS-01~02(와셔)
- 운영: 어제까지 31일, 평일 주·야 2교대, 토요일 주간, 일요일 휴무, 생산직 4개 조 × 24명
- 기존 기준정보(FG-BOLT, RM-ROD, RM-PLATING, CNC-01·02)는 그대로 두고 추가만 한다.
- 과거 날짜 기록을 위해 API 대신 SQL로 넣으며, 넣은 뒤 정합성(재고 음수·BOM 수량·상태·번호·LOT 추적 등)을 검증한다.

### 1-3. 범위 밖 (만들지 않음)
- ERP 기능(수주, 구매 발주, 원가, 회계). 생산계획은 ERP에서 내려왔다고 가정한다.
- 설비 PLC 실시간 연동
- 다중 공정 라우팅, 공정 간 재공(WIP)
- 로그인·권한
- 실적 수정·삭제·취소
- 별도 프론트엔드 프레임워크·빌드 도구 (Swagger UI `/docs` + 6단계 HTML 1장 현장 화면 `/ui`로 대체)
  - 2026-11-15 사용자 승인으로 6단계 화면 범위 확장: 실적 입력만 → 입고·작업지시·실적 입력·조회 4개 탭 (HTML 1장 유지)

### 1-4. 설계 원칙
1. 기준정보(item, bom, equipment)와 실적 데이터(work_order, production_result, lot, inventory_tx)를 분리한다.
2. **재고 테이블은 없다.** 재고 = inventory_tx의 IN 합계 − OUT 합계.
3. 모든 완제품은 LOT로 추적한다. 완제품 LOT → 실적 → 같은 실적의 CONSUME 이동 → 원자재 LOT.

---

## 2. 기술 스택

| 영역 | 선택 |
| --- | --- |
| DB | MariaDB 11 (Docker) |
| API | Python 3.12, FastAPI, Pydantic v2, Uvicorn |
| DB 접근 | SQLAlchemy 2.x Core (`text()`만 사용) + PyMySQL |
| 테스트 | pytest, FastAPI TestClient |
| 실행 | Docker Compose (db + api) |

---

## 3. 폴더 구조

```
mini-mes/
├── CLAUDE.md
├── README.md
├── docker-compose.yml
├── .env.example
├── Dockerfile
├── requirements.txt
├── db/
│   └── migrations/
│       ├── 001_init_schema.sql
│       └── 002_seed_master.sql
├── app/
│   ├── main.py            # FastAPI 앱, 라우터 등록, 에러 핸들러
│   ├── db.py              # engine 생성, get_conn 의존성
│   ├── errors.py          # BusinessError(code, message, status)
│   ├── schemas.py         # Pydantic 요청·응답 모델
│   ├── static/
│   │   └── index.html     # 6단계 현장 화면 (/ui)
│   ├── routers/
│   │   ├── items.py       # /items, /boms, /items/{id}/bom
│   │   ├── equipment.py   # /equipment
│   │   ├── receipts.py    # /receipts
│   │   ├── work_orders.py # /work-orders, /start, /results
│   │   └── reports.py     # /inventory, /lots/{lot_no}/trace, /reports/yield
│   └── services/
│       ├── lot_no.py      # LOT 번호 생성
│       └── production.py  # 실적 등록 트랜잭션 (5-3)
├── scripts/
│   └── seed_demo.py       # 운영 DB 데모 데이터 (1-2-1)
└── tests/
    ├── conftest.py        # 테스트 DB 생성·초기화, TestClient
    ├── test_master.py
    ├── test_results.py    # 실적 등록 검증 실패 케이스
    └── test_scenario.py   # 7장 시나리오 정답 검증
```

---

## 4. DB 스키마

### 4-1. `db/migrations/001_init_schema.sql`

```sql
-- 001_init_schema.sql

-- 기준정보
CREATE TABLE item (
  item_id     INT AUTO_INCREMENT PRIMARY KEY,
  item_code   VARCHAR(30)  NOT NULL UNIQUE,
  item_name   VARCHAR(100) NOT NULL,
  item_type   ENUM('RAW','FINISHED') NOT NULL,
  unit        VARCHAR(10)  NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bom (
  bom_id          INT AUTO_INCREMENT PRIMARY KEY,
  parent_item_id  INT NOT NULL,
  child_item_id   INT NOT NULL,
  qty_per         DECIMAL(12,4) NOT NULL,
  UNIQUE KEY uq_bom (parent_item_id, child_item_id),
  FOREIGN KEY (parent_item_id) REFERENCES item(item_id),
  FOREIGN KEY (child_item_id)  REFERENCES item(item_id),
  CHECK (qty_per > 0),
  CHECK (parent_item_id <> child_item_id)
);

CREATE TABLE equipment (
  equipment_id    INT AUTO_INCREMENT PRIMARY KEY,
  equipment_code  VARCHAR(30)  NOT NULL UNIQUE,
  equipment_name  VARCHAR(100) NOT NULL,
  is_active       TINYINT(1)   NOT NULL DEFAULT 1
);

-- 실적
CREATE TABLE work_order (
  wo_id       INT AUTO_INCREMENT PRIMARY KEY,
  wo_no       VARCHAR(30) NOT NULL UNIQUE,
  item_id     INT NOT NULL,
  plan_qty    DECIMAL(12,4) NOT NULL,
  plan_date   DATE NOT NULL,
  status      ENUM('PLANNED','IN_PROGRESS','DONE') NOT NULL DEFAULT 'PLANNED',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (item_id) REFERENCES item(item_id),
  CHECK (plan_qty > 0)
);

CREATE TABLE production_result (
  result_id     INT AUTO_INCREMENT PRIMARY KEY,
  wo_id         INT NOT NULL,
  equipment_id  INT NOT NULL,
  good_qty      DECIMAL(12,4) NOT NULL DEFAULT 0,
  defect_qty    DECIMAL(12,4) NOT NULL DEFAULT 0,
  defect_code   VARCHAR(30) NULL,
  worker_name   VARCHAR(50) NOT NULL,
  start_at      DATETIME NOT NULL,
  end_at        DATETIME NOT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (wo_id)        REFERENCES work_order(wo_id),
  FOREIGN KEY (equipment_id) REFERENCES equipment(equipment_id),
  CHECK (good_qty >= 0 AND defect_qty >= 0),
  CHECK (good_qty + defect_qty > 0),
  CHECK (end_at > start_at)
);

CREATE TABLE lot (
  lot_id       INT AUTO_INCREMENT PRIMARY KEY,
  lot_no       VARCHAR(40) NOT NULL UNIQUE,
  item_id      INT NOT NULL,
  initial_qty  DECIMAL(12,4) NOT NULL,
  source_type  ENUM('RECEIPT','PRODUCTION') NOT NULL,
  result_id    INT NULL,            -- 생산 LOT만 값이 있음
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (item_id)   REFERENCES item(item_id),
  FOREIGN KEY (result_id) REFERENCES production_result(result_id),
  CHECK (initial_qty > 0)
);

CREATE TABLE inventory_tx (
  tx_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
  lot_id      INT NOT NULL,
  tx_type     ENUM('IN','OUT') NOT NULL,
  qty         DECIMAL(12,4) NOT NULL,
  reason      ENUM('RECEIPT','PRODUCE','CONSUME') NOT NULL,
  result_id   INT NULL,             -- PRODUCE·CONSUME만 값이 있음
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (lot_id)    REFERENCES lot(lot_id),
  FOREIGN KEY (result_id) REFERENCES production_result(result_id),
  INDEX idx_tx_result (result_id),
  CHECK (qty > 0)
);
```

### 4-2. `db/migrations/002_seed_master.sql` (기준정보만)

```sql
-- 002_seed_master.sql
INSERT INTO item (item_code, item_name, item_type, unit) VALUES
  ('FG-BOLT',    '볼트',   'FINISHED', 'EA'),
  ('RM-ROD',     '강봉',   'RAW',      'EA'),
  ('RM-PLATING', '도금액', 'RAW',      'L');

INSERT INTO bom (parent_item_id, child_item_id, qty_per)
SELECT p.item_id, c.item_id, v.qty_per
FROM (SELECT 'RM-ROD' AS code, 1.0000 AS qty_per
      UNION ALL SELECT 'RM-PLATING', 0.0100) v
JOIN item p ON p.item_code = 'FG-BOLT'
JOIN item c ON c.item_code = v.code;

INSERT INTO equipment (equipment_code, equipment_name) VALUES
  ('CNC-01', '가공기 1호'),
  ('CNC-02', '가공기 2호');
```

> 입고·작업지시·실적은 시드에 넣지 않는다. 7장 시나리오를 API로 실행해 만든다.

---

## 5. API 명세

### 5-1. 공통
- 기본 경로 `/`, Swagger UI `/docs`
- 에러 응답 형식 (모든 업무 에러 공통):
  ```json
  {"detail": {"code": "INSUFFICIENT_STOCK", "message": "LOT RM-20261116-001 재고 부족 (남은 170.0000, 요청 500.0000)"}}
  ```
- 에러 코드

| code | HTTP | 언제 |
| --- | --- | --- |
| NOT_FOUND | 404 | 대상 id·코드 없음 |
| DUPLICATE_CODE | 409 | item_code, equipment_code, lot_no 등 중복 |
| WO_INVALID_STATUS | 409 | 상태 전이 규칙 위반 (예: PLANNED에 실적 등록, DONE에 실적 등록, IN_PROGRESS를 다시 start) |
| DEFECT_CODE_REQUIRED | 400 | defect_qty > 0인데 defect_code 없음 |
| BOM_ITEM_MISMATCH | 400 | 투입 LOT 품목이 BOM에 없거나, BOM 원자재 중 빠진 것이 있음 |
| BOM_QTY_MISMATCH | 400 | 원자재별 투입 합계 ≠ qty_per × (good + defect) |
| INSUFFICIENT_STOCK | 409 | 투입 LOT의 남은 재고 < 투입량 |
| INVALID_ITEM_TYPE | 400 | 입고에 FINISHED 품목, 작업지시에 RAW 품목 등 |

- 입력 형식 검증(음수, 누락 등)은 Pydantic 기본 422를 그대로 쓴다.
- 수량은 `Decimal`. JSON 응답에서 문자열(`"170.0000"`)로 나와도 된다.

### 5-2. 엔드포인트

| # | 메서드 · 경로 | 요청 본문 | 응답 |
| --- | --- | --- | --- |
| 1 | POST /items | item_code, item_name, item_type, unit | 생성된 item |
| 2 | GET /items | — | item 목록 |
| 3 | POST /boms | parent_item_id, child_item_id, qty_per | 생성된 bom |
| 4 | GET /items/{item_id}/bom | — | 자품목 목록 (child item_code, item_name, unit, qty_per) |
| 5 | POST /equipment | equipment_code, equipment_name | 생성된 equipment |
| 6 | GET /equipment | — | equipment 목록 |
| 7 | POST /receipts | item_id, qty | 생성된 lot (lot_no 포함). RAW 품목만 허용 |
| 8 | POST /work-orders | item_id, plan_qty, plan_date | 생성된 work_order (wo_no 자동, status PLANNED). FINISHED 품목만 허용 |
| 9 | GET /work-orders?status= | — | 목록 (status 선택 필터) |
| 10 | PATCH /work-orders/{wo_id}/start | — | PLANNED → IN_PROGRESS |
| 11 | POST /work-orders/{wo_id}/results | 아래 5-3 | result_id, product_lot_no(양품 0이면 null), wo_status |
| 12 | GET /inventory | — | 품목별 재고 (item_code, item_name, unit, stock_qty) |
| 13 | GET /lots/{lot_no}/trace | — | 완제품 LOT 추적 (5-5) |
| 14 | GET /reports/yield?by=equipment\|work_order | — | 그룹별 good, defect, yield_pct |
| 15 | GET /lots?item_type=&in_stock= | — | LOT 목록 + LOT별 남은 재고 (6단계 화면의 투입 LOT 선택용, 2026-11-15 추가) |

### 5-3. 실적 등록 요청 예시 (API 11)

```json
{
  "equipment_id": 1,
  "good_qty": "480",
  "defect_qty": "20",
  "defect_code": "SCRATCH",
  "worker_name": "김작업",
  "start_at": "2026-11-16T09:00:00",
  "end_at": "2026-11-16T12:00:00",
  "inputs": [
    {"lot_id": 1, "qty": "500"},
    {"lot_id": 2, "qty": "5"}
  ]
}
```

### 5-4. 실적 등록 처리 순서 (`services/production.py`, 트랜잭션 1개)

1. `work_order`를 `SELECT … FOR UPDATE`로 잠그고 읽는다. 없으면 NOT_FOUND, status ≠ IN_PROGRESS면 WO_INVALID_STATUS.
2. equipment 존재 확인 (없으면 NOT_FOUND).
3. defect_qty > 0이고 defect_code가 비어 있으면 DEFECT_CODE_REQUIRED.
4. 투입 LOT들을 `SELECT … FROM lot WHERE lot_id IN (…) FOR UPDATE`로 잠근다. 없는 LOT이면 NOT_FOUND.
5. BOM 검증
   - 완제품의 BOM 자품목 집합 = 투입 LOT 품목 집합이어야 함. 아니면 BOM_ITEM_MISMATCH.
   - 자품목별 투입 합계 == qty_per × (good_qty + defect_qty) (소수 4자리로 맞춘 뒤 정확히 비교). 아니면 BOM_QTY_MISMATCH.
6. LOT별 남은 재고 = IN 합계 − OUT 합계. 투입량보다 작으면 INSUFFICIENT_STOCK.
7. `production_result` INSERT.
8. good_qty > 0이면:
   - 완제품 `lot` INSERT (source_type PRODUCTION, result_id = 새 실적, initial_qty = good_qty, lot_no는 5-6 규칙)
   - `inventory_tx` INSERT (IN, reason PRODUCE, result_id = 새 실적)
9. 투입 LOT마다 `inventory_tx` INSERT (OUT, reason CONSUME, result_id = 새 실적).
10. 해당 작업지시의 양품 누계 ≥ plan_qty면 status = DONE.
11. 커밋. 어느 단계든 예외면 롤백 후 에러 응답.

### 5-5. 핵심 조회 SQL (그대로 사용)

```sql
-- 품목별 현재 재고
SELECT i.item_code, i.item_name, i.unit,
       SUM(CASE t.tx_type WHEN 'IN' THEN t.qty ELSE -t.qty END) AS stock_qty
FROM inventory_tx t
JOIN lot  l ON l.lot_id  = t.lot_id
JOIN item i ON i.item_id = l.item_id
GROUP BY i.item_id, i.item_code, i.item_name, i.unit;

-- 완제품 LOT 추적
SELECT fg.lot_no AS product_lot, e.equipment_code,
       raw.lot_no AS input_lot, ri.item_name AS input_item, t.qty AS input_qty
FROM lot fg
JOIN production_result pr ON pr.result_id = fg.result_id
JOIN equipment e          ON e.equipment_id = pr.equipment_id
JOIN inventory_tx t       ON t.result_id = pr.result_id AND t.reason = 'CONSUME'
JOIN lot raw              ON raw.lot_id = t.lot_id
JOIN item ri              ON ri.item_id = raw.item_id
WHERE fg.lot_no = :lot_no;

-- 설비별 양품률
SELECT e.equipment_code,
       SUM(pr.good_qty) AS good, SUM(pr.defect_qty) AS defect,
       ROUND(SUM(pr.good_qty) / SUM(pr.good_qty + pr.defect_qty) * 100, 1) AS yield_pct
FROM production_result pr
JOIN equipment e ON e.equipment_id = pr.equipment_id
GROUP BY e.equipment_id, e.equipment_code;
```

추적 API 응답 형식:
```json
{
  "product_lot": "FG-20261116-001",
  "equipment_code": "CNC-01",
  "inputs": [
    {"input_lot": "RM-20261116-001", "input_item": "강봉", "input_qty": "500.0000"},
    {"input_lot": "RM-20261116-002", "input_item": "도금액", "input_qty": "5.0000"}
  ]
}
```
완제품 LOT이 아니거나 없으면 NOT_FOUND.

### 5-6. 번호 규칙
- 원자재 LOT: `RM-YYYYMMDD-001` (입고일 기준, 같은 날 순번 3자리)
- 완제품 LOT: `FG-YYYYMMDD-001`
- 작업지시: `WO-YYYYMMDD-001` (plan_date 기준)
- 순번은 같은 접두어·날짜의 기존 건수 + 1. UNIQUE 제약 위반 시 1회 재시도.

---

## 6. 단계별 작업

### 1단계 — 환경과 스키마
**작업**
- `docker-compose.yml`: `db`(mariadb:11, `db/migrations`를 `/docker-entrypoint-initdb.d`에 마운트, 볼륨 영속화, healthcheck), `api`(Dockerfile 빌드, db healthy 이후 기동, 8000 포트)
- `.env.example`: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, MARIADB_ROOT_PASSWORD
- `001_init_schema.sql`, `002_seed_master.sql` (4장 그대로)
- `Dockerfile`, `requirements.txt`, `app/main.py`에 `GET /health`만

**완료 확인**
- `docker compose up -d` 후 `SHOW TABLES;` → 7개
- item 3행, bom 2행, equipment 2행
- 5-5 조회 SQL 3개가 에러 없이 실행됨 (빈 결과 정상)
- `GET /health` → 200

### 2단계 — 기준정보·입고·작업지시 API
**작업**
- `db.py`(engine, 요청마다 connection 의존성), `errors.py`(BusinessError + 핸들러로 5-1 형식 응답)
- API 1~10 구현 (번호 규칙 5-6 포함)
- `tests/conftest.py`: 테스트용 DB `mini_mes_test`를 매 테스트 세션마다 DROP/CREATE 후 001·002 실행, 각 테스트 전 실적 테이블 4개 비우기, `db` 의존성 override
- `tests/test_master.py`

**완료 확인**
- 품목·설비 코드 중복 등록 → 409 DUPLICATE_CODE
- FINISHED 품목 입고 → 400 INVALID_ITEM_TYPE
- 입고 후 lot 1건 + inventory_tx IN 1건 생성
- 작업지시 start 두 번 → 두 번째 409 WO_INVALID_STATUS
- `pytest tests/test_master.py` 전부 통과

### 3단계 — 실적 등록 트랜잭션
**작업**
- `services/production.py` (5-4 순서 그대로), API 11
- `tests/test_results.py`: 아래 실패 케이스마다 **에러 코드 확인 + 실적 4개 테이블 행 수가 요청 전과 같음(롤백) 확인**

**완료 확인 (테스트 케이스)**
1. PLANNED 작업지시에 실적 → 409 WO_INVALID_STATUS
2. 불량 20, defect_code 없음 → 400 DEFECT_CODE_REQUIRED
3. 도금액 LOT 빠뜨림 → 400 BOM_ITEM_MISMATCH
4. 강봉 투입 499 (필요 500) → 400 BOM_QTY_MISMATCH
5. 재고보다 많이 투입 → 409 INSUFFICIENT_STOCK
6. 정상 등록 → 실적 1, 완제품 lot 1, inventory_tx 3 (IN 1, OUT 2)
7. 양품 0, 불량 10 → 완제품 lot 없음, product_lot_no null, OUT만 생성
8. DONE 작업지시에 실적 → 409 WO_INVALID_STATUS

### 4단계 — 조회 API와 시나리오 검증
**작업**
- API 12~14 (5-5 SQL 사용)
- `tests/test_scenario.py`: 7장 시나리오를 API만으로 처음부터 실행하고 정답 검증

**완료 확인**
- `pytest` 전체 통과
- 7장 "정답" 항목이 모두 일치

### 5단계 — README와 마무리
**작업**
- `README.md`: 프로젝트 목적, ERD(mermaid `erDiagram`), 설계 원칙 3가지, 시나리오와 정답, 실행 방법(`docker compose up`, `/docs` 접속, `pytest`), API 목록, 범위 밖 항목과 확장 계획(8장)
- 불필요한 코드·print 제거, `requirements.txt` 버전 고정

**완료 확인**
- 새 환경에서 README 순서대로 따라 하면 `/docs`가 열리고 `pytest`가 통과함

### 6단계 — (선택, 사용자 승인 시에만) 현장 화면
> 2026-11-15 사용자 승인. 원래 범위(실적 입력 화면)를 4개 탭으로 확장.

- `app/static/index.html` 1장, 탭 4개
  - ① 원자재 입고: RAW 품목·수량 입력 → API 7, 원자재 LOT 현황(남은 재고)
  - ② 작업지시: 생성(API 8), 목록·상태 필터(API 9), [시작](API 10)
  - ③ 실적 입력: 진행 중 작업지시 선택 → 설비·작업자·시간·양품·불량·불량코드 → BOM 기준 투입 LOT 자동 구성(소요량 자동 계산, LOT 분할 투입) → API 11 → 결과 표시
  - ④ 조회: 재고(API 12), 양품률(API 14), 완제품 LOT 추적(API 13)
- 투입 LOT 선택을 위해 API 15 `GET /lots` 추가 (조회 전용)
- FastAPI `StaticFiles`로 `/ui`에 연결. 프레임워크 없이 HTML + fetch만 사용.

---

## 7. 테스트 시나리오와 정답

| 순서 | API 호출 | 기대 결과 |
| --- | --- | --- |
| 1 | POST /receipts (RM-ROD, 1200) | lot `RM-<오늘>-001` |
| 2 | POST /receipts (RM-PLATING, 20) | lot `RM-<오늘>-002` |
| 3 | POST /work-orders (FG-BOLT, 1000, 오늘) | status PLANNED |
| 4 | PATCH /work-orders/{id}/start | status IN_PROGRESS |
| 5 | POST results: CNC-01, 양품 480, 불량 20(SCRATCH), 강봉 500 + 도금액 5 | product_lot `FG-<오늘>-001`, wo_status IN_PROGRESS |
| 6 | POST results: CNC-02, 양품 520, 불량 10(DIMENSION), 강봉 530 + 도금액 5.3 | product_lot `FG-<오늘>-002`, wo_status DONE |

**정답**
- GET /inventory: RM-ROD **170**, RM-PLATING **9.7**, FG-BOLT **1000**
- GET /reports/yield?by=equipment: CNC-01 **96.0**, CNC-02 **98.1**
- GET /reports/yield?by=work_order: 해당 작업지시 **97.1**
- GET /lots/FG-<오늘>-001/trace: equipment CNC-01, inputs = 강봉 RM-…-001 500, 도금액 RM-…-002 5
- 원자재 소요량은 양품이 아니라 투입량(양품 + 불량) 기준이다.

---

## 8. 확장 계획 (지금은 만들지 않음, README에만 기록)

| 순서 | 확장 | 추가 |
| --- | --- | --- |
| 1 | 불량코드 표준화 | defect_code 테이블 |
| 2 | 공정 라우팅 | process, routing 테이블 |
| 3 | 실적 취소 | 반대 방향 inventory_tx (삭제 없는 정정) |
| 4 | 설비 데이터 수집 | equipment_reading 테이블 (시뮬레이션) |
| 5 | 불량 예측 | production_result + equipment_reading 피처 분류 모델 |
