#!/bin/sh
# Disposable lock probe for the Issue #8 production apply review. No volume (tmpfs), no network, --rm.
# Usage: probe.sh <scratch dir> <infra dir> <mobile dir> <studio dir>
set -eu
S=$1; INFRA=$2; MOBILE=$3; STUDIO=$4
IMG=postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
C=nexus-pg17-baseline-lockprobe-20261102
W=$S/lockprobe/work; mkdir -p "$W"
LOCKQ="SELECT 'PROBE', l.locktype, CASE WHEN l.locktype = 'relation' THEN coalesce(n.nspname || '.' || c.relname, l.relation::text) WHEN l.locktype = 'object' THEN l.classid::regclass::text || '#' || l.objid ELSE coalesce(l.database::text, '') END, l.mode FROM pg_locks l LEFT JOIN pg_class c ON c.oid = l.relation LEFT JOIN pg_namespace n ON n.oid = c.relnamespace WHERE l.pid = pg_backend_pid() AND l.locktype NOT IN ('virtualxid', 'transactionid') AND NOT (l.mode = 'AccessShareLock' AND n.nspname = 'pg_catalog') ORDER BY 2, 3, 4;"

docker run -d --rm --name "$C" --network none --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=nexus -e POSTGRES_DB=nexus -e POSTGRES_PASSWORD="probe-$(date +%s)-not-a-secret" "$IMG" >/dev/null
trap 'docker stop "$C" >/dev/null 2>&1 || true' EXIT
i=0; until docker exec "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 60 ] || exit 1; sleep 1; done
sleep 2; until docker exec "$C" pg_isready -U nexus -d nexus >/dev/null 2>&1; do sleep 1; done

px() { # px <label> <role> <file> [psql args...]
  _label=$1; _role=$2; _file=$3; shift 3
  echo "== $_label"
  docker exec -i -e PGOPTIONS='-c lock_timeout=5s' -e NEXUS_PUBLIC_PASSWORD=probe-public-password-000000000000 \
    -e NEXUS_STUDIO_MIGRATOR_PASSWORD=probe-migrator-password-00000000000000 -e NEXUS_STUDIO_APP_PASSWORD=probe-app-password-0000000000000000000 \
    --user postgres "$C" psql -X -At -F "|" -v ON_ERROR_STOP=1 -U "$_role" -d nexus "$@" < "$_file"
}
single() { # rollback probe of a single-transaction script, then the real run
  label=$1; role=$2; src=$3; shift 3
  awk -v q="$LOCKQ" '{ lines[NR] = $0 } /^COMMIT;$/ { last = NR } END { for (i = 1; i <= NR; i++) print (i == last ? q "\nROLLBACK;" : lines[i]) }' "$src" > "$W/$label.probe.sql"
  px "$label (probe, rolled back)" "$role" "$W/$label.probe.sql" "$@"
  px "$label (real)" "$role" "$src" "$@" | grep -v '^PROBE' || true
}
multi() { # lock list before every COMMIT of a multi-transaction script, applied for real
  label=$1; role=$2; src=$3; shift 3
  awk -v q="$LOCKQ" '/^COMMIT;$/ { print q } { print }' "$src" > "$W/$label.probe.sql"
  px "$label (real, locks before each COMMIT)" "$role" "$W/$label.probe.sql" "$@"
}

docker exec "$C" sh -c 'grep -v "^#" "$PGDATA/pg_hba.conf" | grep -v "^$"' | sed 's/^/HBA|/'
docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c 'SHOW server_version' | sed 's/^/VERSION|/'
single 01-roles nexus "$INFRA/sql/pg17-baseline/01-roles.sql"
multi mobile-migrate nexus "$S/mobile-migrate.sql"
{ echo 'SET ROLE nexus_owner;'; cat "$MOBILE/deploy/database/grant-runtime.sql"; } > "$W/grant-runtime.asowner.sql"
single grant-runtime nexus "$W/grant-runtime.asowner.sql"

# From here on a public-api-like reader holds AccessShareLock on spots/events/visit_logs for the whole Studio phase.
docker exec --user postgres "$C" psql -X -At -U nexus_public -d nexus -c "BEGIN; SELECT count(*) FROM spots; SELECT count(*) FROM events; SELECT count(*) FROM visit_logs; SELECT pg_sleep(120); COMMIT;" >/dev/null 2>&1 &
sleep 2
docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT 'READER|' || count(*) FROM pg_locks l JOIN pg_stat_activity a USING (pid) WHERE a.usename = 'nexus_public' AND l.locktype = 'relation' AND l.mode = 'AccessShareLock'"
SYSID=$(docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c 'SELECT system_identifier FROM pg_control_system()')
single studio-roles nexus "$STUDIO/deploy/database/pg17/setup-studio-roles.sql" -v expected_system_id="$SYSID"
single 02-delivery nexus "$INFRA/sql/pg17-baseline/02-delivery-schema.sql" -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app
tail -c +4 "$S/ci-studio-877ee90/migrate.sql" > "$W/studio-migrate.nobom.sql"
multi studio-migrate nexus_studio_migrator "$W/studio-migrate.nobom.sql"
echo "== studio-migrate replay (idempotent)"
px "studio-migrate replay" nexus_studio_migrator "$W/studio-migrate.nobom.sql" >/dev/null && echo "REPLAY|ok"
single grant-studio nexus "$STUDIO/deploy/database/grant-studio-runtime.sql"
single 03-grant nexus "$INFRA/sql/pg17-baseline/03-grant-delivery.sql" -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app
docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT 'READER_STILL|' || count(*) FROM pg_stat_activity WHERE usename = 'nexus_public' AND state = 'active'"
echo "== BOM check: the CI file fed as-is"
docker exec -i --user postgres "$C" psql -X -At -v ON_ERROR_STOP=1 -U nexus_studio_migrator -d nexus < "$S/ci-studio-877ee90/migrate.sql" >/dev/null 2>"$W/bom.err" && echo "BOM|accepted" || { echo "BOM|rejected"; head -3 "$W/bom.err"; }
echo "== rollback SQL of NexusStudio README §4 (before any bundle)"
printf '%s\n' "BEGIN;" "DROP SCHEMA IF EXISTS delivery CASCADE;" "DROP SCHEMA studio CASCADE;" "REVOKE ALL ON DATABASE nexus FROM nexus_studio_migrator, nexus_studio_app;" "DROP ROLE nexus_studio_app;" "DROP ROLE nexus_studio_migrator;" "COMMIT;" > "$W/rollback.sql"
px "rollback" nexus "$W/rollback.sql" && docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT 'AFTER_ROLLBACK|' || (SELECT count(*) FROM pg_roles WHERE rolname LIKE 'nexus_studio%') || '|' || (SELECT count(*) FROM pg_namespace WHERE nspname IN ('studio','delivery')) || '|' || (SELECT count(*) FROM pg_tables WHERE schemaname = 'public')"
echo "DONE"
