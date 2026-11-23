INSERT IGNORE INTO permissions(code) VALUES ('feishu:chat');
INSERT IGNORE INTO role_permissions(role_code,permission_code)
 SELECT code,'feishu:chat' FROM roles WHERE code IN ('admin','employee');
