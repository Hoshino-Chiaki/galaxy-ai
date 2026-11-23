CREATE TABLE IF NOT EXISTS departments (
 id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
 workspace_id VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
 name VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 UNIQUE KEY workspace_name(workspace_id,name)
) ENGINE=InnoDB;
INSERT IGNORE INTO departments(id,workspace_id,name)
 SELECT UUID(),workspace_id,department COLLATE utf8mb4_bin FROM employees WHERE department<>'' GROUP BY workspace_id,department COLLATE utf8mb4_bin;
