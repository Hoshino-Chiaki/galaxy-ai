CREATE TABLE IF NOT EXISTS ai_usage (
 id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
 workspace_id VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
 user_id CHAR(36) CHARACTER SET ascii NOT NULL,
 conversation_id CHAR(36) CHARACTER SET ascii NULL,
 turn_id CHAR(36) CHARACTER SET ascii NOT NULL,
 entry_point VARCHAR(24) CHARACTER SET ascii NOT NULL,
 provider_id CHAR(36) CHARACTER SET ascii NOT NULL,
 model VARCHAR(160) NOT NULL,
 started_at DOUBLE NOT NULL, finished_at DOUBLE NULL,
 status VARCHAR(24) CHARACTER SET ascii NOT NULL,
 usage_complete BOOLEAN NOT NULL DEFAULT FALSE,
 input_tokens BIGINT UNSIGNED NULL, output_tokens BIGINT UNSIGNED NULL, total_tokens BIGINT UNSIGNED NULL,
 cache_hit_tokens BIGINT UNSIGNED NULL, cache_miss_tokens BIGINT UNSIGNED NULL,
 INDEX(workspace_id,started_at),INDEX(workspace_id,user_id,started_at),INDEX(turn_id)
) ENGINE=InnoDB;
INSERT IGNORE INTO permissions(code) VALUES ('ai:usage:read');
INSERT IGNORE INTO role_permissions(role_code,permission_code) VALUES ('admin','ai:usage:read');
