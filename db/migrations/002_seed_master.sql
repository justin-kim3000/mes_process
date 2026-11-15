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
