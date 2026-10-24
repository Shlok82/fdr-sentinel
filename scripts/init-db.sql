-- scripts/init-db.sql
-- Initializes the runtime role and sets up privilege boundaries

-- 1. Create the runtime application user if it doesn't already exist
DO
$do$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'fdr_app_user') THEN
      CREATE ROLE fdr_app_user WITH LOGIN PASSWORD 'fdr_app_dev_password';
   END IF;
END
$do$;

-- 2. Grant connection rights to the fdr_sentinel database
GRANT CONNECT ON DATABASE fdr_sentinel TO fdr_app_user;

-- 3. Grant schema usage
\connect fdr_sentinel
GRANT USAGE, CREATE ON SCHEMA public TO fdr_owner;
GRANT USAGE ON SCHEMA public TO fdr_app_user;

-- 4. Set default privileges so that any table created by fdr_owner in public schema
-- automatically allows CRUD to fdr_app_user (restrictions like audit_log are revoked explicitly)
ALTER DEFAULT PRIVILEGES FOR ROLE fdr_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fdr_app_user;

ALTER DEFAULT PRIVILEGES FOR ROLE fdr_owner IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO fdr_app_user;
