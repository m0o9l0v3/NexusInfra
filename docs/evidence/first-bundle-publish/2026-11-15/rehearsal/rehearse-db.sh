#!/bin/sh
# Disposable PG17.11 in the production shape, rebuilt with the same commands and order as
# NexusInfra docs/pg17-issue-8-production-apply.md §5.2–5.4 (B1–B4, C1–C6). Local colima only.
set -eu
: "${DOCKER_CONTEXT:?use the disposable colima context}"
W=$(cd "$(dirname "$0")" && pwd)
P=nexus-fbp-20261115
NET=$P-backend
VOL=$P-pg17-db
C=$P-pg17
IMG=postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
SEC=$W/secrets
cd "$W/work"

for n in $(docker ps -a --format '{{.Names}}'); do case "$n" in "$P"*) echo "exists: $n" >&2; exit 1;; esac; done
docker volume inspect "$VOL" >/dev/null 2>&1 && { echo "volume exists" >&2; exit 1; }

umask 077
for f in nexus_password nexus_public_password nexus_studio_migrator_password nexus_studio_app_password; do
  openssl rand -hex 24 > "$SEC/$f"
done
printf 'pg17-db:5432:nexus:nexus:%s\n' "$(cat "$SEC/nexus_password")" > "$SEC/pgpass"

echo "== SHA256SUMS (inputs)"; shasum -a 256 *.sql studio-pg17/*

docker network create --internal "$NET" >/dev/null
docker volume create "$VOL" >/dev/null
docker run -d --name "$C" --network "$NET" --network-alias pg17-db \
  -e POSTGRES_USER=nexus -e POSTGRES_DB=nexus -e POSTGRES_PASSWORD_FILE=/run/s/nexus_password \
  -v "$SEC/nexus_password:/run/s/nexus_password:ro" -v "$VOL:/var/lib/postgresql/data" "$IMG" >/dev/null
for i in $(seq 1 60); do docker exec --user postgres "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1 && break; sleep 1; done
# The entrypoint restarts the server once after init; wait for the final one.
sleep 3; for i in $(seq 1 60); do docker exec --user postgres "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1 && break; sleep 1; done

psql_su() { docker exec -i -e PGOPTIONS='-c lock_timeout=5s' -e NEXUS_PUBLIC_PASSWORD --user postgres "$C" psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus "$@"; }

echo "== P2 (version / system_identifier redacted)"
docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT current_database(), current_user, current_setting('server_version'), current_setting('password_encryption')"
SYSID=$(docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT system_identifier FROM pg_control_system()")

echo "== B1 01-roles.sql"
export NEXUS_PUBLIC_PASSWORD="$(cat "$SEC/nexus_public_password")"
psql_su < 01-roles.sql
unset NEXUS_PUBLIC_PASSWORD
echo "== B2 mobile-migrate.sql"; psql_su -q < mobile-migrate.sql
echo "== B3 grant-runtime.sql"; { echo 'SET ROLE nexus_owner;'; cat grant-runtime.sql; } | psql_su -q
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

echo "== C1 setup-studio-roles.sh (temporary client container on the DB network)"
docker run --rm -i --network "$NET" --user root \
  -v "$W/work/studio-pg17:/w:ro" -v "$SEC:/run/s:ro" \
  -e PGOPTIONS='-c lock_timeout=5s' -e PGPASSFILE=/run/s/pgpass \
  -e NEXUS_STUDIO_TARGET_ENV=production -e ALLOW_PRODUCTION_STUDIO_PG17_ROLE_SETUP=1 \
  -e NEXUS_STUDIO_TARGET_HOST=pg17-db -e NEXUS_STUDIO_TARGET_PORT=5432 \
  -e NEXUS_STUDIO_EXPECTED_SYSTEM_ID="$SYSID" \
  --entrypoint sh "$IMG" -c 'NEXUS_STUDIO_MIGRATOR_PASSWORD="$(cat /run/s/nexus_studio_migrator_password)" NEXUS_STUDIO_APP_PASSWORD="$(cat /run/s/nexus_studio_app_password)" exec sh /w/setup-studio-roles.sh'
echo "== C2 02-delivery-schema.sql"; psql_su -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 02-delivery-schema.sql
echo "== C3 studio-migrate.sql (as nexus_studio_migrator)"
docker exec -i -e PGOPTIONS='-c lock_timeout=5s' --user postgres "$C" psql -X -q -v ON_ERROR_STOP=1 -U nexus_studio_migrator -d nexus < studio-migrate.sql
echo "== C4 grant-studio-runtime.sql"; psql_su -q < grant-studio-runtime.sql
echo "== C5 03-grant-delivery.sql"; psql_su -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 03-grant-delivery.sql
echo "== C6"
psql_su -A -t <<'SQL'
SELECT 'studio_roles', string_agg(rolname || ':' || (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication)::text, ',' ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'nexus\_studio\_%';
SELECT 'schemas', string_agg(nspname || ':' || nspowner::regrole::text, ',' ORDER BY nspname) FROM pg_namespace WHERE nspname IN ('studio', 'delivery');
SELECT 'studio_history', count(*), max("MigrationId") FROM studio."__EFMigrationsHistory";
SELECT 'delivery_tables', string_agg(tablename || ':' || tableowner, ',' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'delivery';
SELECT 'public_cannot_use_studio', NOT has_schema_privilege('nexus_public', 'studio', 'USAGE');
SELECT 'app_cannot_write_history', NOT has_table_privilege('nexus_studio_app', 'studio."__EFMigrationsHistory"', 'INSERT');
SELECT 'delivery_rows', (SELECT count(*) FROM delivery.participant_bundles) + (SELECT count(*) FROM delivery.current_bundle);
SQL
echo "DB_READY $C on $NET (alias pg17-db)"
