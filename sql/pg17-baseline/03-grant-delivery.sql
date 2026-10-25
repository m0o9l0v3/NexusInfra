-- PG17 baseline step 5: delivery privileges (NexusMobile E2-5 §3.2, Infra #17 contract B revision).
-- Run as the cluster superuser after each reviewed Studio migration that touches the delivery schema.
-- Re-runnable: it first revokes, then grants exactly the contract below. Intentionally no default
-- privileges, so a table added later stays closed until this script and contract B are revised.
--
--   role                  participant_bundles   current_bundle
--   Studio runtime role   SELECT, INSERT        SELECT, INSERT, UPDATE
--   nexus_public          SELECT                SELECT
--   everyone else         none                  none
--
-- Nobody is granted UPDATE, DELETE or TRUNCATE on participant_bundles, or DELETE/TRUNCATE on
-- current_bundle. The owner (Studio migrator) keeps its implicit owner rights; E2-5 §3.5 reserves
-- them for a reviewed manual procedure.
--   psql -X -v studio_migrator=<migrator role> -v studio_app=<runtime role> -f 03-grant-delivery.sql
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
DO $shape$
DECLARE
    migrator text := current_setting('nexus.studio_migrator');
    tables text;
BEGIN
    IF current_setting('server_version_num')::int NOT BETWEEN 170000 AND 179999 THEN
        RAISE EXCEPTION 'This grant is for PostgreSQL 17 only';
    END IF;
    IF (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'delivery') IS DISTINCT FROM migrator THEN
        RAISE EXCEPTION 'Schema delivery must exist and be owned by %', migrator;
    END IF;
    SELECT string_agg(relname || ':' || relowner::regrole::text, ',' ORDER BY relname) INTO tables
      FROM pg_class WHERE relnamespace = 'delivery'::regnamespace AND relkind IN ('r', 'p', 'v', 'm', 'f');
    IF tables IS DISTINCT FROM format('current_bundle:%s,participant_bundles:%s', migrator, migrator) THEN
        RAISE EXCEPTION 'delivery must hold exactly current_bundle and participant_bundles owned by % (found %)', migrator, tables;
    END IF;
    -- E2-5 §3.2: the reviewed trigger refuses a decreasing bundle_version. Match it by name and
    -- function, so another BEFORE UPDATE trigger (audit, timestamp) cannot stand in for it.
    -- tgtype bits: ROW = 1, BEFORE = 2, UPDATE = 16.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc f ON f.oid = t.tgfoid
                   WHERE t.tgrelid = 'delivery.current_bundle'::regclass AND t.tgname = 'current_bundle_monotonic'
                   AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A') AND t.tgtype & 19 = 19
                   AND f.pronamespace = 'delivery'::regnamespace AND f.proname = 'reject_bundle_version_decrease'
                   AND f.proowner::regrole::text = migrator) THEN
        RAISE EXCEPTION 'delivery.current_bundle lacks the enabled current_bundle_monotonic trigger (delivery.reject_bundle_version_decrease)';
    END IF;
END
$shape$;
REVOKE ALL ON ALL TABLES IN SCHEMA delivery FROM PUBLIC, :"studio_app", nexus_public;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA delivery FROM PUBLIC, :"studio_app", nexus_public;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA delivery FROM PUBLIC, :"studio_app", nexus_public;
GRANT SELECT, INSERT ON delivery.participant_bundles TO :"studio_app";
GRANT SELECT, INSERT, UPDATE ON delivery.current_bundle TO :"studio_app";
GRANT SELECT ON delivery.participant_bundles, delivery.current_bundle TO nexus_public;
COMMIT;
