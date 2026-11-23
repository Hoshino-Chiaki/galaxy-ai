CREATE TABLE IF NOT EXISTS permission_policies (
 workspace_id VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
 subject_type ENUM('department','user') NOT NULL,
 subject_id CHAR(36) CHARACTER SET ascii NOT NULL,
 payload JSON NOT NULL,
 revision BIGINT NOT NULL DEFAULT 1,
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
 PRIMARY KEY(workspace_id,subject_type,subject_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS permission_audit (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 workspace_id VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
 actor_user_id CHAR(36) CHARACTER SET ascii NOT NULL,
 subject_type ENUM('department','user') NOT NULL,
 subject_id CHAR(36) CHARACTER SET ascii NOT NULL,
 changed_permissions JSON NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 INDEX(workspace_id,created_at)
) ENGINE=InnoDB;
INSERT IGNORE INTO permissions(code) VALUES('permissions:manage');
INSERT IGNORE INTO role_permissions(role_code,permission_code) VALUES('admin','permissions:manage');

INSERT IGNORE INTO permissions(code) VALUES('feishu:create'),('feishu:delete'),('feishu:update');
INSERT IGNORE INTO role_permissions(role_code,permission_code)
 SELECT rp.role_code,p.code FROM role_permissions rp JOIN permissions p ON p.code IN ('feishu:create','feishu:delete','feishu:update') WHERE rp.permission_code='feishu:write';
