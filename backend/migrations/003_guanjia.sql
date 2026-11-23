CREATE TABLE IF NOT EXISTS guanjia_connections (record_id VARCHAR(191) COLLATE utf8mb4_bin PRIMARY KEY, payload JSON NOT NULL) ENGINE=InnoDB;
INSERT IGNORE INTO repository_locks(name) VALUES ('guanjia');
INSERT IGNORE INTO permissions(code) VALUES ('guanjia:configure');
INSERT IGNORE INTO role_permissions(role_code,permission_code) VALUES ('admin','guanjia:configure'),('employee','guanjia:configure');
