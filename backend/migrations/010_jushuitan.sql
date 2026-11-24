CREATE TABLE IF NOT EXISTS jushuitan_connections (record_id VARCHAR(191) COLLATE utf8mb4_bin PRIMARY KEY, payload JSON NOT NULL) ENGINE=InnoDB;
INSERT IGNORE INTO repository_locks(name) VALUES ('jushuitan');
INSERT IGNORE INTO permissions(code) VALUES ('jushuitan:configure'),('jushuitan:order:read');
INSERT IGNORE INTO role_permissions(role_code,permission_code) VALUES ('admin','jushuitan:configure'),('admin','jushuitan:order:read'),('employee','jushuitan:configure'),('employee','jushuitan:order:read');
