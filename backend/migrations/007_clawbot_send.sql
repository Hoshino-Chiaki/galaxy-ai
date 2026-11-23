INSERT IGNORE INTO permissions(code) VALUES ('clawbot:send');
INSERT IGNORE INTO role_permissions(role_code,permission_code)
 SELECT code,'clawbot:send' FROM roles WHERE code IN ('admin','employee');
