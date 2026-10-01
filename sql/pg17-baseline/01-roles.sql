-- PG17 baseline step 1/3: runtime role bootstrap for an ALREADY INITIALIZED cluster.
-- Mirrors the role contract of NexusMobile deploy/database/init-roles.sh (which only runs for an
-- empty cluster, so it never runs against the live PG17). Review before any production use.
--
-- Run ONCE as the cluster superuser, inside the maintenance window, against the application DB:
--   NEXUS_PUBLIC_PASSWORD is read from the client environment (never put it in this file or argv).
-- Differences from init-roles.sh, all deliberate and subject to human approval:
--   * no nexus_migrator login: the reviewed migration SQL is applied by the superuser connection,
--     which switches to nexus_owner itself (first statement of the generated script).
--   * nexus_admin_app is NOLOGIN: grant-runtime.sql grants to it, but admin-api is not deployed
--     by the PG17 public edge. Enabling its login is a separate reviewed change.
--   * every CREATE ROLE fails if the role already exists; nothing is silently altered.
\set ON_ERROR_STOP on
\getenv public_password NEXUS_PUBLIC_PASSWORD
BEGIN;
DO $guard$
BEGIN
    IF current_setting('server_version_num')::int NOT BETWEEN 170000 AND 179999 THEN
        RAISE EXCEPTION 'This bootstrap is for PostgreSQL 17 only';
    END IF;
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RAISE EXCEPTION 'Run as the cluster superuser';
    END IF;
END
$guard$;
SELECT :'public_password' = '' AS empty_password \gset
\if :empty_password
    \echo 'NEXUS_PUBLIC_PASSWORD is empty'
    \quit
\endif
CREATE ROLE nexus_owner NOLOGIN;
CREATE ROLE nexus_admin_app NOLOGIN;
CREATE ROLE nexus_public LOGIN PASSWORD :'public_password';
SELECT format('ALTER DATABASE %I OWNER TO nexus_owner', current_database()) \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO nexus_admin_app, nexus_public', current_database()) \gexec
ALTER SCHEMA public OWNER TO nexus_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO nexus_admin_app, nexus_public;
COMMIT;
