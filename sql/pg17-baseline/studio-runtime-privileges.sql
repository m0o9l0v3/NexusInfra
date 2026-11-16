-- #9: least privilege of the Studio runtime role while Studio is hosted. Read-only (catalog functions only).
-- Run as the DB owner/superuser: psql -X -At -v ON_ERROR_STOP=1 -U nexus -d nexus < studio-runtime-privileges.sql
-- Every row must end in |t. The last row lists who is connected to the DB as a Studio role (expected: nexus_studio_app only).
SELECT 'app_no_role_attributes', NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication)
  FROM pg_roles WHERE rolname = 'nexus_studio_app';
SELECT 'app_not_member_of_migrator_or_owner', NOT pg_has_role('nexus_studio_app', 'nexus_studio_migrator', 'MEMBER')
  AND NOT pg_has_role('nexus_studio_app', 'nexus_owner', 'MEMBER');
SELECT 'app_cannot_create_in_studio_or_delivery', NOT has_schema_privilege('nexus_studio_app', 'studio', 'CREATE')
  AND NOT has_schema_privilege('nexus_studio_app', 'delivery', 'CREATE');
SELECT 'app_cannot_create_in_public', NOT has_schema_privilege('nexus_studio_app', 'public', 'CREATE');
SELECT 'app_cannot_write_history', NOT has_table_privilege('nexus_studio_app', 'studio."__EFMigrationsHistory"', 'INSERT,UPDATE,DELETE,TRUNCATE');
SELECT 'app_cannot_read_public_api_tables', NOT EXISTS (
  SELECT 1 FROM pg_tables WHERE schemaname = 'public'
   AND has_table_privilege('nexus_studio_app', format('%I.%I', schemaname, tablename), 'SELECT,INSERT,UPDATE,DELETE'));
SELECT 'app_bundles_insert_select_only', has_table_privilege('nexus_studio_app', 'delivery.participant_bundles', 'INSERT,SELECT')
  AND NOT has_table_privilege('nexus_studio_app', 'delivery.participant_bundles', 'UPDATE,DELETE,TRUNCATE');
SELECT 'app_pointer_no_delete', NOT has_table_privilege('nexus_studio_app', 'delivery.current_bundle', 'DELETE,TRUNCATE');
SELECT 'app_does_not_own_objects', NOT EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname IN ('studio', 'delivery', 'public') AND c.relowner = 'nexus_studio_app'::regrole);
SELECT 'public_cannot_use_studio', NOT has_schema_privilege('nexus_public', 'studio', 'USAGE');
SELECT 'studio_connections', coalesce(string_agg(DISTINCT usename, ','), '<none>') || '|' ||
  (coalesce(bool_and(usename = 'nexus_studio_app'), true))::text
  FROM pg_stat_activity WHERE datname = current_database() AND usename LIKE 'nexus\_studio\_%';
