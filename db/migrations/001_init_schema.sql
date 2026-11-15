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
