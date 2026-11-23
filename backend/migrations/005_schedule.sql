INSERT IGNORE INTO permissions(code) VALUES ('schedule:create'),('schedule:read'),('schedule:update'),('schedule:delete');
INSERT IGNORE INTO role_permissions(role_code,permission_code)
 SELECT r.code,p.code FROM roles r JOIN permissions p ON p.code IN ('schedule:create','schedule:read','schedule:update','schedule:delete') WHERE r.code IN ('admin','employee');
