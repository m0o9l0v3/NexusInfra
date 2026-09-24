#!/bin/sh
set -eu
umask 077

cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
: "${NEXUS_ENV_FILE:=/etc/nexus/production.env}"
test -r "$NEXUS_ENV_FILE" || { echo "Private deployment environment file is missing" >&2; exit 2; }
set -a
# shellcheck source=/dev/null
. "$NEXUS_ENV_FILE"
set +a
: "${BACKUP_DIR:?Set BACKUP_DIR in the private deployment environment}"
case "$BACKUP_DIR" in /*) ;; *) echo "BACKUP_DIR must be absolute" >&2; exit 2;; esac
test -d "$BACKUP_DIR" || { echo "Create the private backup directory first" >&2; exit 2; }
test "$(stat -c %a "$BACKUP_DIR")" = 700 || { echo "BACKUP_DIR must be mode 700" >&2; exit 2; }

exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9 || { echo "Another backup is running" >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tmp="$BACKUP_DIR/nexus_admin-$stamp.dump.partial"
done_file="${tmp%.partial}"
trap 'rm -f "$tmp"' EXIT HUP INT TERM
docker compose exec -T postgres sh -ceu '
  export PGPASSWORD="$(cat /run/secrets/postgres_password)"
  exec pg_dump -U postgres -d nexus_admin --format=custom --no-owner --no-acl
' > "$tmp"
test -s "$tmp"
docker compose exec -T postgres pg_restore -l < "$tmp" > /dev/null
mv "$tmp" "$done_file"
sha256sum "$done_file" > "$done_file.sha256"
trap - EXIT HUP INT TERM
echo "Backup complete: $done_file"
echo "Keep at least seven verified daily generations; remove older copies only after off-VPS copy and restore verification."
