#!/bin/sh
# #9 isolated rehearsal: the Studio overlay on the PG17 edge, with the production compose/Caddy files.
# Local colima only (DOCKER_CONTEXT must be a disposable context). Never point this at the VPS.
# W = a private work dir outside Git with work/*.sql (production SHA-256s) and an empty secrets/ (0700).
# Domains are *.localhost so the unchanged Caddyfile uses Caddy's internal CA instead of ACME.
set -eu
: "${DOCKER_CONTEXT:?use the disposable colima context}"
: "${W:?private work dir}"
: "${REPO:?nexus-infra worktree}"
: "${COLIMA_PROFILE:?colima profile that serves DOCKER_CONTEXT}"
PG=postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24   # 17.11
CADDY_IMG=caddy@sha256:0c994536bddb66445885237f1a5dcc1916bccea922661c76b4e9fc24061f9b52  # 2.11.4 arm64
P=nexus-edge-pg17
NET=nexus-pg17-edge-iso-studio-backend
VOL=nexus-pg17-edge-iso-studio-db
C=nexus-pg17-edge-iso-studio-pg17
SEC=$W/secrets
STEP=${1:-all}

for n in $(docker ps -a --format '{{.Names}}'); do case "$n" in "$P"*|"$C") [ "$STEP" = all ] && { echo "exists: $n" >&2; exit 1; };; esac; done

db() {
  umask 077
  for f in nexus_password nexus_public_password nexus_studio_migrator_password nexus_studio_app_password audit_key; do
    openssl rand -hex 24 > "$SEC/$f"
  done
  printf 'pg17-db:5432:nexus:nexus:%s\n' "$(cat "$SEC/nexus_password")" > "$SEC/pgpass"
  printf 'Host=pg17-db;Port=5432;Database=nexus;Username=nexus_public;Password=%s' "$(cat "$SEC/nexus_public_password")" > "$SEC/public_connection"
  printf 'Host=pg17-db;Port=5432;Database=nexus;Username=nexus_studio_app;Password=%s' "$(cat "$SEC/nexus_studio_app_password")" > "$SEC/studio_connection"
  chmod 0444 "$SEC/public_connection" "$SEC/studio_connection" "$SEC/audit_key"   # container UID 1654 must read them (local only)
  cd "$W/work"
  echo "== SHA256SUMS (inputs)"; shasum -a 256 ./*.sql studio-pg17/*
  docker network create --internal "$NET" >/dev/null
  docker volume create "$VOL" >/dev/null
  docker run -d --name "$C" --network "$NET" --network-alias pg17-db \
    -e POSTGRES_USER=nexus -e POSTGRES_DB=nexus -e POSTGRES_PASSWORD_FILE=/run/s/nexus_password \
    -v "$SEC/nexus_password:/run/s/nexus_password:ro" -v "$VOL:/var/lib/postgresql/data" "$PG" >/dev/null
  for _ in $(seq 1 60); do docker exec --user postgres "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1 && break; sleep 1; done
  sleep 3; for _ in $(seq 1 60); do docker exec --user postgres "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1 && break; sleep 1; done
  psql_su() { docker exec -i -e PGOPTIONS='-c lock_timeout=5s' -e NEXUS_PUBLIC_PASSWORD --user postgres "$C" psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus "$@"; }
  SYSID=$(docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT system_identifier FROM pg_control_system()")
  docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT 'server_version', current_setting('server_version')"
  echo "== B1"; NEXUS_PUBLIC_PASSWORD="$(cat "$SEC/nexus_public_password")"; export NEXUS_PUBLIC_PASSWORD
  psql_su -q < 01-roles.sql; unset NEXUS_PUBLIC_PASSWORD
  echo "== B2"; psql_su -q < mobile-migrate.sql
  echo "== B3"; { echo 'SET ROLE nexus_owner;'; cat grant-runtime.sql; } | psql_su -q
  echo "== C1"
  docker run --rm -i --network "$NET" --user root -v "$W/work/studio-pg17:/w:ro" -v "$SEC:/run/s:ro" \
    -e PGOPTIONS='-c lock_timeout=5s' -e PGPASSFILE=/run/s/pgpass \
    -e NEXUS_STUDIO_TARGET_ENV=production -e ALLOW_PRODUCTION_STUDIO_PG17_ROLE_SETUP=1 \
    -e NEXUS_STUDIO_TARGET_HOST=pg17-db -e NEXUS_STUDIO_TARGET_PORT=5432 -e NEXUS_STUDIO_EXPECTED_SYSTEM_ID="$SYSID" \
    --entrypoint sh "$PG" -c 'NEXUS_STUDIO_MIGRATOR_PASSWORD="$(cat /run/s/nexus_studio_migrator_password)" NEXUS_STUDIO_APP_PASSWORD="$(cat /run/s/nexus_studio_app_password)" exec sh /w/setup-studio-roles.sh' >/dev/null
  echo "== C2"; psql_su -q -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 02-delivery-schema.sql
  echo "== C3"; docker exec -i -e PGOPTIONS='-c lock_timeout=5s' --user postgres "$C" psql -X -q -v ON_ERROR_STOP=1 -U nexus_studio_migrator -d nexus < studio-migrate.sql
  echo "== C4"; psql_su -q < grant-studio-runtime.sql
  echo "== C5"; psql_su -q -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 03-grant-delivery.sql
  echo "== C6"
  psql_su -A -t <<'SQL'
SELECT 'studio_history', count(*), max("MigrationId") FROM studio."__EFMigrationsHistory";
SELECT 'public_cannot_use_studio', NOT has_schema_privilege('nexus_public', 'studio', 'USAGE');
SQL
  # Site root the edge bind-mounts (create_host_path: false); a stand-in page inside the colima VM.
  colima ssh -p "$COLIMA_PROFILE" -- sudo sh -c 'mkdir -p /srv/nexus-web/current && printf "<!doctype html><title>www stand-in</title>\n" > /srv/nexus-web/current/index.html'
  echo DB_READY
}

envfiles() {
  umask 077
  cat > "$W/pg17-edge.env" <<EOF
PG17_EDGE_PUBLIC_API_IMAGE=${PUBLIC_API_IMAGE:?local public-api image ID}
PG17_EDGE_CADDY_IMAGE=$CADDY_IMG
PG17_EDGE_API_DOMAIN=api.localhost
PG17_EDGE_SITE_DOMAIN=www.localhost
PG17_EDGE_ACME_EMAIL=ops@example.invalid
PG17_EDGE_BACKEND_NETWORK=$NET
PG17_EDGE_SUBNET=172.30.241.0/28
PG17_EDGE_SECRET_DIR=$SEC
EOF
  cat > "$W/pg17-studio.env" <<EOF
PG17_STUDIO_API_IMAGE=${STUDIO_API_IMAGE:?local image ID}
PG17_STUDIO_WEB_IMAGE=${STUDIO_WEB_IMAGE:?local image ID}
PG17_STUDIO_DOMAIN=studio.localhost
PG17_STUDIO_SUBNET=172.30.242.0/28
PG17_STUDIO_SECRET_DIR=$SEC
EOF
}

case "$STEP" in
  db) db ;;
  env) envfiles ;;
  *) echo "usage: $0 db|env" >&2; exit 2 ;;
esac
