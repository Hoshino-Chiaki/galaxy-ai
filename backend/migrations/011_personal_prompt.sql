INSERT IGNORE INTO permissions(code) VALUES ('ai:prompt:configure');
INSERT IGNORE INTO role_permissions(role_code,permission_code) VALUES ('admin','ai:prompt:configure');
