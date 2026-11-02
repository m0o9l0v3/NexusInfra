#!/bin/bash
# Runs the operator commands of docs/pg17-issue-8-production-apply.md §5.2-5.4 verbatim against a disposable PG17.
set -eu
S=$1; INFRA=$2; MOBILE=$3; STUDIO=$4
IMGREF=postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
NET=nexus-pg17-baseline-opcheck-net
C=nexus-pg17-baseline-opcheck-20261102
WORK=$HOME/nexus-pg17-baseline-opcheck-20261102/work; PRIV=$HOME/nexus-pg17-baseline-opcheck-20261102/priv
rm -rf "$WORK" "$PRIV"; mkdir -p "$WORK/studio-pg17"; mkdir -m 700 -p "$PRIV"
cp "$INFRA/sql/pg17-baseline/01-roles.sql" "$INFRA/sql/pg17-baseline/02-delivery-schema.sql" "$INFRA/sql/pg17-baseline/03-grant-delivery.sql" "$WORK/"
cp "$S/mobile-migrate.sql" "$WORK/mobile-migrate.sql"; cp "$MOBILE/deploy/database/grant-runtime.sql" "$WORK/"
cp "$STUDIO/deploy/database/pg17/setup-studio-roles.sh" "$STUDIO/deploy/database/pg17/setup-studio-roles.sql" "$WORK/studio-pg17/"
cp "$S/ci-studio-877ee90/migrate.sql" "$WORK/studio-migrate.sql"; cp "$STUDIO/deploy/database/grant-studio-runtime.sql" "$WORK/"
cat > "$WORK/SHA256SUMS" <<'EOF'
0ec65dce204f27fd01db974cc9508f2e36594cfe1708b0c84af0aca80fd4d65b  01-roles.sql
7cf58c307611ebec07e617add3f18d7fa65eee30c6de16dc2d989d5e4a9da763  mobile-migrate.sql
456bbf9836b5fede04fa0d914beaca743456ee84452c63db13479667c8e9c134  grant-runtime.sql
951f09c85e58df6d8e013451044b6ef8b747c1041ea618b275799fb39afbf62f  studio-pg17/setup-studio-roles.sh
769fd80e26624598d7e5f42b2db674175ac7cf4a9ba731f94d4eeb1490b4a53f  studio-pg17/setup-studio-roles.sql
b71231e47693c3957a04b48fe339fbf769778789fa6fd02068edbe8b3cd65b93  02-delivery-schema.sql
a1eb2bbc314144487fd5ba48e89841ec2c9e2960190eb1e8c83bd193fe60c835  studio-migrate.sql
d662f59548e90615d16b416a208ce8b5813eeb529960cad75fd4b1ef3050ce4a  grant-studio-runtime.sql
f7d516a8ac9488bfa0c3dc1d674b836d54363cff966e3376ae14faceadc590a2  03-grant-delivery.sql
EOF
cd "$WORK"; shasum -a 256 -c SHA256SUMS
SUPW=$(openssl rand -hex 24)
for f in nexus_public_password nexus_studio_migrator_password nexus_studio_app_password; do (umask 077; openssl rand -hex 24 > "$PRIV/$f"); done
(umask 077; echo "db:5432:nexus:nexus:$SUPW" > "$PRIV/pgpass")
docker network create --internal "$NET" >/dev/null
trap 'docker stop "$C" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true' EXIT
docker run -d --rm --name "$C" --network "$NET" --network-alias db --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=nexus -e POSTGRES_DB=nexus -e POSTGRES_PASSWORD="$SUPW" "$IMGREF" >/dev/null
sleep 3; until docker exec "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1; do sleep 1; done; sleep 3
until docker exec "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1; do sleep 1; done

echo "== P2"; docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT current_database(), current_user, current_setting('server_version_num'), current_setting('password_encryption'), system_identifier FROM pg_control_system()" | sed -E 's/[0-9]{12,}$/<sysid>/'
SYSID=$(docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c 'SELECT system_identifier FROM pg_control_system()')
echo "== P3"; docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT 'roles', string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' UNION ALL SELECT 'schemas', string_agg(nspname, ',' ORDER BY nspname) FROM pg_namespace WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema' UNION ALL SELECT 'user_tables', count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p','v','m','f','S') AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema' UNION ALL SELECT 'databases', string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE NOT datistemplate UNION ALL SELECT 'nexus_owner', datdba::regrole::text FROM pg_database WHERE datname = 'nexus' UNION ALL SELECT 'extensions', string_agg(extname, ',' ORDER BY extname) FROM pg_extension"
echo "== P5"; docker exec "$C" sh -c 'grep -Ev "^[[:space:]]*(#|$)" "$PGDATA/pg_hba.conf"'

psql_su() { docker exec -i -e PGOPTIONS='-c lock_timeout=5s' -e NEXUS_PUBLIC_PASSWORD --user postgres "$C" psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus "$@"; }
echo "== B1 empty password (expect exit 0, nothing created)"
export NEXUS_PUBLIC_PASSWORD=""
psql_su < 01-roles.sql; echo "exit=$?"; unset NEXUS_PUBLIC_PASSWORD
docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT 'after_empty', count(*) FROM pg_roles WHERE rolname = 'nexus_public'"
echo "== B1"; export NEXUS_PUBLIC_PASSWORD="$(cat "$PRIV/nexus_public_password")"
psql_su < 01-roles.sql > /dev/null; unset NEXUS_PUBLIC_PASSWORD
echo "== B2"; psql_su < mobile-migrate.sql > /dev/null
echo "== B3"; { echo 'SET ROLE nexus_owner;'; cat grant-runtime.sql; } | psql_su > /dev/null 2>&1
echo "== B4"
psql_su -A -t <<'SQL'
SELECT 'roles', string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname NOT LIKE 'pg\_%';
SELECT 'db_owner', datdba::regrole FROM pg_database WHERE datname = 'nexus';
SELECT 'public_acl_on_db', count(*) FROM pg_database d, aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a WHERE d.datname = 'nexus' AND a.grantee = 0;
SELECT 'history', string_agg("MigrationId", ',' ORDER BY "MigrationId") FROM public."__EFMigrationsHistory";
SELECT 'public_tables', count(*) FROM pg_tables WHERE schemaname = 'public';
SET ROLE nexus_public;
SELECT 'readiness_unfit', r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls OR pg_has_role(current_user, 'nexus_owner', 'MEMBER') OR has_schema_privilege(current_user, 'public', 'CREATE') FROM pg_roles r WHERE r.rolname = current_user;
RESET ROLE;
SQL

echo "== C1 wrong system id (expect refusal)"
IMG="$(docker inspect --type container --format '{{.Image}}' "$C")"
c1() { docker run --rm -i --network "$NET" --user root \
  -v "$PWD/studio-pg17:/w:ro" -v "$PRIV":/run/s:ro \
  -e PGOPTIONS='-c lock_timeout=5s' -e PGPASSFILE=/run/s/pgpass \
  -e NEXUS_STUDIO_TARGET_ENV=production -e ALLOW_PRODUCTION_STUDIO_PG17_ROLE_SETUP=1 \
  -e NEXUS_STUDIO_TARGET_HOST=db -e NEXUS_STUDIO_TARGET_PORT=5432 \
  -e NEXUS_STUDIO_EXPECTED_SYSTEM_ID="$1" \
  --entrypoint sh "$IMG" -c 'NEXUS_STUDIO_MIGRATOR_PASSWORD="$(cat /run/s/nexus_studio_migrator_password)" NEXUS_STUDIO_APP_PASSWORD="$(cat /run/s/nexus_studio_app_password)" exec sh /w/setup-studio-roles.sh'; }
c1 1 && echo "UNEXPECTED success" || echo "refused exit=$?"
echo "== C1"; c1 "$SYSID" > /dev/null; echo "exit=$?"
echo "== C2"; psql_su -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 02-delivery-schema.sql > /dev/null
echo "== R1 precheck before C3 (delivery tables absent)"; psql_su -A -t -c "SELECT 'delivery_rows', (CASE WHEN to_regclass('delivery.participant_bundles') IS NULL THEN 0 ELSE (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM delivery.participant_bundles', false, true, '')))[1]::text::bigint END) + (CASE WHEN to_regclass('delivery.current_bundle') IS NULL THEN 0 ELSE (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM delivery.current_bundle', false, true, '')))[1]::text::bigint END)"
echo "== C3"; docker exec -i -e PGOPTIONS='-c lock_timeout=5s' --user postgres "$C" psql -X -v ON_ERROR_STOP=1 -U nexus_studio_migrator -d nexus < studio-migrate.sql > /dev/null
echo "== C4"; psql_su < grant-studio-runtime.sql > /dev/null
echo "== C5"; psql_su -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 03-grant-delivery.sql > /dev/null
echo "== C6"
psql_su -A -t <<'SQL'
SELECT 'studio_roles', string_agg(rolname || ':' || (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication)::text, ',' ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'nexus\_studio\_%';
SELECT 'schemas', string_agg(nspname || ':' || nspowner::regrole::text, ',' ORDER BY nspname) FROM pg_namespace WHERE nspname IN ('studio', 'delivery');
SELECT 'studio_history', count(*), max("MigrationId") FROM studio."__EFMigrationsHistory";
SELECT 'delivery_tables', string_agg(tablename || ':' || tableowner, ',' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'delivery';
SELECT 'public_cannot_use_studio', NOT has_schema_privilege('nexus_public', 'studio', 'USAGE');
SELECT 'app_cannot_write_history', NOT has_table_privilege('nexus_studio_app', 'studio."__EFMigrationsHistory"', 'INSERT');
SELECT 'migrator_no_db_create', NOT has_database_privilege('nexus_studio_migrator', 'nexus', 'CREATE');
SELECT 'public_tables_unchanged', count(*) FROM pg_tables WHERE schemaname = 'public';
SELECT 'delivery_rows', (CASE WHEN to_regclass('delivery.participant_bundles') IS NULL THEN 0 ELSE (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM delivery.participant_bundles', false, true, '')))[1]::text::bigint END) + (CASE WHEN to_regclass('delivery.current_bundle') IS NULL THEN 0 ELSE (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM delivery.current_bundle', false, true, '')))[1]::text::bigint END);
SQL
echo "== 5.5 history query"; docker exec -i --user postgres "$C" psql -X -At -U nexus_studio_migrator -d nexus -c 'SELECT "MigrationId" FROM studio."__EFMigrationsHistory" ORDER BY 1' | wc -l
echo "DONE"
