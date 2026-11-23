INSERT IGNORE INTO permissions(code) VALUES ('clawbot:configure');
INSERT IGNORE INTO role_permissions(role_code,permission_code)
 SELECT code,'clawbot:configure' FROM roles WHERE code IN ('admin','employee');
