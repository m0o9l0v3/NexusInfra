-- PG17 baseline step 4: delivery schema for the participant bundle (NexusMobile E2-5 §3, Infra #17).
-- Run ONCE as the cluster superuser against the application DB, after 01-roles.sql, the reviewed
-- public-api migration, grant-runtime.sql, and the Studio role/schema setup of Infra #8.
-- Review before any production use.
--
-- Ownership decision (Infra #17, Studio record 29 M1-D3): this script pre-creates the schema and
-- hands it to the Studio migrator. The migrator gets NO CREATE on the database; it can create
-- objects only inside the schemas it owns (studio, delivery).
--
-- The Studio role names are fixed by Infra #8, so they are passed as psql variables:
--   psql -X -v studio_migrator=<migrator role> -v studio_app=<runtime role> -f 02-delivery-schema.sql
-- Fails if the schema already exists; nothing is silently altered.
\set ON_ERROR_STOP on
\if :{?studio_migrator}
\else
    DO $missing$ BEGIN RAISE EXCEPTION 'Set -v studio_migrator=<Studio migrator role>'; END $missing$;
\endif
\if :{?studio_app}
\else
    DO $missing$ BEGIN RAISE EXCEPTION 'Set -v studio_app=<Studio runtime role>'; END $missing$;
\endif
BEGIN;
SET LOCAL nexus.studio_migrator = :'studio_migrator';
SET LOCAL nexus.studio_app = :'studio_app';
DO $guard$
DECLARE
    migrator text := current_setting('nexus.studio_migrator');
    app text := current_setting('nexus.studio_app');
    name text;
BEGIN
    IF current_setting('server_version_num')::int NOT BETWEEN 170000 AND 179999 THEN
        RAISE EXCEPTION 'This bootstrap is for PostgreSQL 17 only';
    END IF;
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        RAISE EXCEPTION 'Run as the cluster superuser';
    END IF;
    IF migrator = app OR migrator IN ('nexus', 'nexus_owner', 'nexus_public') OR app IN ('nexus', 'nexus_owner', 'nexus_public') THEN
        RAISE EXCEPTION 'Studio migrator and runtime roles must be two distinct Studio roles';
    END IF;
    IF to_regnamespace('delivery') IS NOT NULL THEN
        RAISE EXCEPTION 'Schema delivery already exists';
    END IF;
    IF to_regrole('nexus_public') IS NULL THEN
        RAISE EXCEPTION 'nexus_public is missing; apply 01-roles.sql first';
    END IF;
    FOREACH name IN ARRAY ARRAY[migrator, app] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name
                       AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication)) THEN
            RAISE EXCEPTION 'Role % is missing or privileged; set up the Studio roles (Infra #8) first', name;
        END IF;
        IF pg_has_role(name, 'nexus_owner', 'MEMBER') THEN
            RAISE EXCEPTION 'Role % must not be a member of nexus_owner', name;
        END IF;
        IF has_database_privilege(name, current_database(), 'CREATE') THEN
            RAISE EXCEPTION 'Role % must not have CREATE on the database', name;
        END IF;
    END LOOP;
END
$guard$;
CREATE SCHEMA delivery AUTHORIZATION :"studio_migrator";
REVOKE ALL ON SCHEMA delivery FROM PUBLIC;
GRANT USAGE ON SCHEMA delivery TO :"studio_app", nexus_public;
COMMIT;
