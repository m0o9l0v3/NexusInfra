-- Read-only fingerprint of the PG17 `nexus` database after #8 B and C (roles, schemas, owners, ACLs, row counts,
-- migration histories, delivery trigger). ACLs are compared as effective ACLs (NULL = the owner default).
-- Run as the superuser on the source and on the isolated restore, and diff the two outputs (docs/first-bundle-publish.md §4.3). No secrets are printed (no password hashes).
\pset tuples_only on
\pset format unaligned
SELECT 'server_major', current_setting('server_version_num')::int / 10000;
SELECT 'db_owner', datdba::regrole FROM pg_database WHERE datname = current_database();
SELECT 'db_acl', coalesce(datacl, acldefault('d', datdba))::text FROM pg_database WHERE datname = current_database();
SELECT 'role', rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls
  FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' ORDER BY rolname;
SELECT 'member', r.rolname, m.rolname FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member
  WHERE r.rolname NOT LIKE 'pg\_%' ORDER BY 2, 3;
SELECT 'schema', nspname, nspowner::regrole, coalesce(nspacl, acldefault('n', nspowner))::text FROM pg_namespace
  WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema' ORDER BY nspname;
SELECT 'table', n.nspname, c.relname, c.relkind, c.relowner::regrole, coalesce(c.relacl, acldefault('r', c.relowner))::text, c.relrowsecurity,
       (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('public', 'studio', 'delivery') ORDER BY 2, 3;
SELECT 'sequence', n.nspname, c.relname, c.relowner::regrole, coalesce(c.relacl, acldefault('s', c.relowner))::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind = 'S' AND n.nspname IN ('public', 'studio', 'delivery') ORDER BY 2, 3;
SELECT 'policy', schemaname, tablename, policyname, roles::text, cmd FROM pg_policies ORDER BY 2, 3, 4;
SELECT 'trigger', tgrelid::regclass, tgname, tgenabled, tgfoid::regproc FROM pg_trigger WHERE NOT tgisinternal ORDER BY 2, 3;
SELECT 'function', p.oid::regprocedure, p.proowner::regrole, p.prosecdef, coalesce(p.proconfig::text, '')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname IN ('public', 'studio', 'delivery') ORDER BY 2;
SELECT 'public_history', string_agg("MigrationId", ',' ORDER BY "MigrationId") FROM public."__EFMigrationsHistory";
SELECT 'studio_history', string_agg("MigrationId", ',' ORDER BY "MigrationId") FROM studio."__EFMigrationsHistory";
SELECT 'delivery_current', coalesce((SELECT bundle_version::text FROM delivery.current_bundle), 'none');
SELECT 'delivery_payload_sha', coalesce(string_agg(bundle_version || ':' || sha256, ',' ORDER BY bundle_version), 'none') FROM delivery.participant_bundles;
