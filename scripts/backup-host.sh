#!/bin/sh
set -eu

operation=${1:?Usage: backup-host.sh daily|full|diff|check|export}
case "$operation" in daily|full|diff|check|export) ;; *) exit 2 ;; esac
: "${NEXUS_ENV_FILE:=/etc/nexus/production.env}"
test -r "$NEXUS_ENV_FILE" || { echo 'Private deployment environment file is missing' >&2; exit 2; }
set -a
# shellcheck source=/dev/null
. "$NEXUS_ENV_FILE"
set +a
: "${NEXUS_REPO_DIR:?Set NEXUS_REPO_DIR in the private deployment environment}"
case "$NEXUS_REPO_DIR" in /*) ;; *) echo 'NEXUS_REPO_DIR must be absolute' >&2; exit 2 ;; esac
cd "$NEXUS_REPO_DIR"

if [ "$operation" = daily ]; then
    operation='diff'
    if [ "$(TZ=Asia/Tokyo date +%u)" = 7 ]; then operation='full'; fi
fi
exec docker compose exec -T --user postgres postgres nexus-backup "$operation"
