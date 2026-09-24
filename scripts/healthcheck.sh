#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
: "${NEXUS_ENV_FILE:=/etc/nexus/production.env}"
test -r "$NEXUS_ENV_FILE" || { echo "Private deployment environment file is missing" >&2; exit 2; }
set -a
# shellcheck source=/dev/null
. "$NEXUS_ENV_FILE"
set +a
docker info >/dev/null
docker compose ps
docker compose exec -T postgres pg_isready -U postgres -d nexus_admin
: "${API_DOMAIN:?set domain}"
: "${STUDIO_DOMAIN:?set domain}"
curl --fail --silent --show-error "https://$API_DOMAIN/health" >/dev/null
curl --fail --silent --show-error "https://$STUDIO_DOMAIN/health" >/dev/null
curl --fail --silent --show-error "https://$STUDIO_DOMAIN/" >/dev/null
df -h / /srv/nexus
free -h
uptime
test -n "${BACKUP_DIR:-}" && find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -mtime -2 | grep -q .
