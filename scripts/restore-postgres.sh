#!/bin/sh
set -eu

# Restore only into a new disposable database in the reviewed PostgreSQL instance.
cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
: "${NEXUS_ENV_FILE:=/etc/nexus/production.env}"
test -r "$NEXUS_ENV_FILE" || { echo "Private deployment environment file is missing" >&2; exit 2; }
set -a
# shellcheck source=/dev/null
. "$NEXUS_ENV_FILE"
set +a
backup=${1:?Usage: restore-postgres.sh BACKUP nexus_restore_YYYYMMDD}
target=${2:?Usage: restore-postgres.sh BACKUP nexus_restore_YYYYMMDD}
case "$backup" in /*) ;; *) echo "Backup path must be absolute" >&2; exit 2;; esac
case "$target" in nexus_restore_[0-9]*) ;; *) echo "Target must start nexus_restore_ followed by digits" >&2; exit 2;; esac
case "$target" in *[!a-zA-Z0-9_]* ) echo "Invalid target name" >&2; exit 2;; esac
test "${ALLOW_RESTORE_TEST:-}" = 1 || { echo "Set ALLOW_RESTORE_TEST=1 after reviewing target and backup" >&2; exit 2; }
test -f "$backup" && test -f "$backup.sha256"
(cd "$(dirname "$backup")" && sha256sum -c "$(basename "$backup").sha256")
docker compose exec -T postgres pg_restore -l < "$backup" > /dev/null
if docker compose exec -T postgres sh -ceu '
  export PGPASSWORD="$(cat /run/secrets/postgres_password)"
  exec psql -U postgres -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname = '\''$1'\''"
' sh "$target" | grep -q '^1$'; then
  echo "Target already exists; refusing overwrite" >&2
  exit 2
fi
docker compose exec -T postgres sh -ceu '
  export PGPASSWORD="$(cat /run/secrets/postgres_password)"
  exec createdb -U postgres "$1"
' sh "$target"
docker compose exec -T postgres sh -ceu '
  export PGPASSWORD="$(cat /run/secrets/postgres_password)"
  exec pg_restore -U postgres -d "$1" --no-owner --no-acl --exit-on-error
' sh "$target" < "$backup"
echo "Restored into disposable database $target; verify data and record the result."
