INSERT IGNORE INTO permissions(code) VALUES ('excel:process');
INSERT IGNORE INTO role_permissions(role_code,permission_code) VALUES
    ('admin','excel:process'), ('employee','excel:process');
