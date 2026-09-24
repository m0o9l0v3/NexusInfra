#!/bin/sh
set -eu
umask 077

# Transitional export only. This script never changes the running PostgreSQL volume.
: "${ALLOW_PG17_EXPORT:?Set ALLOW_PG17_EXPORT=1 after reviewing the source and destination}"
[ "$ALLOW_PG17_EXPORT" = 1 ] || { echo 'PG17 export was not approved' >&2; exit 2; }
: "${PG17_CONTAINER:?Set the inspected source container name}"
: "${PG17_DB_USER:?Set the inspected source database superuser}"
: "${PG17_DB_NAME:?Set the inspected Nexus database name}"
: "${PG17_BACKUP_DIR:?Set an existing root-owned private backup directory}"
case "$PG17_CONTAINER" in *[!A-Za-z0-9_.-]*|'') echo 'Invalid container name' >&2; exit 2 ;; esac
case "$PG17_BACKUP_DIR" in /*) ;; *) echo 'PG17_BACKUP_DIR must be absolute' >&2; exit 2 ;; esac
[ "$(id -u)" = 0 ] || { echo 'Run as root so the backup files stay private' >&2; exit 2; }
[ -d "$PG17_BACKUP_DIR" ] || { echo 'Backup directory does not exist' >&2; exit 2; }
[ "$(stat -c %u "$PG17_BACKUP_DIR")" = 0 ] && [ "$(stat -c %a "$PG17_BACKUP_DIR")" = 700 ] || {
    echo 'Backup directory must be root-owned with mode 700' >&2; exit 2;
}
image=$(docker inspect --type container --format '{{.Config.Image}}' "$PG17_CONTAINER")
mount=$(docker inspect --type container --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$PG17_CONTAINER")
[ "$image" = 'postgres:17-alpine' ] && [ "$mount" = 'nexus_postgres_data' ] || {
    echo 'Source image or data volume differs from the reviewed PostgreSQL 17 state; stop' >&2; exit 2;
}
[ "$(docker inspect --type container --format '{{.State.Running}}' "$PG17_CONTAINER")" = true ] &&
[ "$(docker inspect --type container --format '{{.State.Health.Status}}' "$PG17_CONTAINER")" = healthy ] || {
    echo 'Source container is not healthy' >&2; exit 2;
}
version=$(docker exec --user postgres "$PG17_CONTAINER" postgres --version)
case "$version" in 'postgres (PostgreSQL) 17.'*) ;; *) echo 'Source PostgreSQL major version is not 17' >&2; exit 2 ;; esac

stamp=$(date -u +%Y%m%dT%H%M%SZ)
partial="$PG17_BACKUP_DIR/pg17-$stamp.partial"
finished="$PG17_BACKUP_DIR/pg17-$stamp"
[ ! -e "$partial" ] && [ ! -e "$finished" ] || { echo 'Backup generation already exists' >&2; exit 2; }
mkdir -m 700 "$partial"
docker exec --user postgres "$PG17_CONTAINER" pg_dumpall -U "$PG17_DB_USER" --globals-only --no-role-passwords > "$partial/globals.sql"
docker exec --user postgres "$PG17_CONTAINER" pg_dumpall -U "$PG17_DB_USER" --no-role-passwords > "$partial/cluster.sql"
docker exec --user postgres "$PG17_CONTAINER" pg_dump -U "$PG17_DB_USER" -d "$PG17_DB_NAME" --format=custom --no-owner --no-acl > "$partial/nexus.dump"
[ -s "$partial/globals.sql" ] && [ -s "$partial/cluster.sql" ] && [ -s "$partial/nexus.dump" ] || {
    echo 'Incomplete export; partial directory retained for inspection' >&2; exit 1;
}
docker exec -i --user postgres "$PG17_CONTAINER" pg_restore -l < "$partial/nexus.dump" > /dev/null
(cd "$partial" && sha256sum globals.sql cluster.sql nexus.dump > SHA256SUMS)
mv "$partial" "$finished"
echo "PG17 export created: $finished"
echo 'This is not a verified backup until an encrypted off-VPS copy and isolated restore both succeed.'
