INSERT IGNORE INTO permissions(code) VALUES('youzan:order:read');
INSERT IGNORE INTO role_permissions(role_code,permission_code)
 SELECT code,'youzan:order:read' FROM roles WHERE code IN ('admin','employee');
