#!/bin/sh
# #9 isolated rehearsal of docs/studio-hosting.md §5–§9 with the production compose/Caddy files.
# Prerequisite: rehearse-studio-edge.sh db + env, and an ssh tunnel from the Mac loopback 18443/18080 to the colima VM 443/80.
set -eu
: "${DOCKER_CONTEXT:?use the disposable colima context}"
: "${W:?private work dir}" "${REPO:?nexus-infra worktree}"
cd "$REPO"
set -a; . "$W/pg17-edge.env"; . "$W/pg17-studio.env"; set +a
BASE="docker-compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml"
DC="$BASE -f compose.pg17-studio.yaml"
CA=$W/caddy-root.crt
checks() { # $1 present|absent, $2 login email (optional). Tunnelled to the VM; TLS verified against Caddy's internal root.
  EXPECT_STUDIO=$1 LOGIN_EMAIL=${2:-} LOGIN_PASSWORD_FILE="$W/secrets/admin_password" KEEP_JAR="$W/jar" STUDIO=https://studio.localhost API=https://api.localhost SITE=https://www.localhost \
  CURL_EXTRA="--cacert $CA --connect-to studio.localhost:80:127.0.0.1:18080 --connect-to :443:127.0.0.1:18443" \
  sh scripts/studio-edge-http-checks.sh || true
}
# The rehearsal public-api is a local arm64 build (no registry digest); only that one message is tolerated.
v() { out=$("$@" 2>&1) && { echo "$out"; return 0; }
  rest=$(echo "$out" | grep -v -E 'public-api image must be a required digest|PG17_EDGE_PUBLIC_API_IMAGE must be an immutable' || true)
  [ -z "$rest" ] && echo "PASS (rehearsal): only the local public-api image ID differs from a registry digest" || { echo "$rest"; return 1; }; }
wait_healthy() { for _ in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Health.Status}}' "$1")" = healthy ] && return 0; sleep 3; done; echo "not healthy: $1" >&2; return 1; }
members() { for n in edge studio; do printf '%s: ' "$n"; docker network inspect "nexus-edge-pg17_$n" --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | tr ' ' '\n' | sed 's/nexus-edge-pg17[-_]//; s/[-_]1$//' | sort | tr '\n' ' '; echo; done; }

echo "== 0. today's production shape: base edge only"
umask 077; r=$(mktemp)
$BASE config --format json > "$r"; v python3 scripts/validate-pg17-edge.py --resolved "$r" --env
$BASE up -d
wait_healthy "$($BASE ps -q public-api)"; wait_healthy "$($BASE ps -q caddy)"
sleep 3
docker cp "$($BASE ps -q caddy)":/data/caddy/pki/authorities/local/root.crt "$CA"
checks absent
CADDY_BEFORE=$($BASE ps -q caddy); API_BEFORE=$($BASE ps -q public-api)

echo "== S-1 validate the merged config"
$DC config --format json > "$r"
v python3 scripts/validate-pg17-studio-edge.py --resolved "$r" --env && v python3 scripts/validate-pg17-edge.py --env
rm "$r"

echo "== S-2 studio-api + studio-web only"
$DC up -d --no-deps studio-api studio-web
wait_healthy "$($DC ps -q studio-api)"; wait_healthy "$($DC ps -q studio-web)"
[ "$($BASE ps -q caddy)" = "$CADDY_BEFORE" ] && [ "$($BASE ps -q public-api)" = "$API_BEFORE" ] && echo "PASS: caddy and public-api not recreated by S-2"
docker exec -i --user postgres nexus-pg17-edge-iso-studio-pg17 psql -X -At -U nexus -d nexus < sql/pg17-baseline/studio-runtime-privileges.sql
printf '%s\n' "$(cat "$W/secrets/admin_password")" | $DC run --rm --no-deps -T studio-api admin add --email rehearsal-admin@example.invalid --name rehearsal 2>&1 | tail -1
checks absent

echo "== S-3 swap the Caddy config"
$DC up -d --no-deps caddy
wait_healthy "$($DC ps -q caddy)"
[ "$($BASE ps -q public-api)" = "$API_BEFORE" ] && echo "PASS: public-api not recreated by S-3"
members
docker port "$($DC ps -q caddy)"
for s in studio-api studio-web public-api; do printf '%s ports: [%s]\n' "$s" "$(docker port "$($DC ps -q "$s")" | tr '\n' ' ')"; done

echo "== 6.1 HTTP checks with login/logout"
checks present rehearsal-admin@example.invalid
echo "== DB least privilege while Studio is in use"
docker exec -i --user postgres nexus-pg17-edge-iso-studio-pg17 psql -X -At -U nexus -d nexus < sql/pg17-baseline/studio-runtime-privileges.sql

echo "== X-Forwarded-Proto from outside the trusted subnet (direct to studio-api on backend) is not trusted"
for proto in https http; do
  printf 'backend, X-Forwarded-Proto %s -> ' "$proto"
  docker run --rm --network "$PG17_EDGE_BACKEND_NETWORK" --entrypoint curl "$PG17_STUDIO_API_IMAGE" -s -o /dev/null -w '%{http_code}\n' -H "X-Forwarded-Proto: $proto" http://studio-api:8080/api/auth/csrf || true
done
printf 'studio network (trusted), X-Forwarded-Proto https -> '
docker run --rm --network nexus-edge-pg17_studio --entrypoint curl "$PG17_STUDIO_API_IMAGE" -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-Proto: https' http://studio-api:8080/api/auth/csrf || true
printf 'public network (internet side) -> studio-api resolvable? '
docker run --rm --network nexus-edge-pg17_public --entrypoint curl "$PG17_STUDIO_API_IMAGE" -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://studio-api:8080/health || echo unreachable

echo "== session survives a studio-api restart (DataProtection keys on the volume)"
$DC restart studio-api >/dev/null; wait_healthy "$($DC ps -q studio-api)"
printf 'me with the pre-restart cookie -> '
curl -s -o /dev/null -w '%{http_code}\n' --cacert "$CA" --connect-to :443:127.0.0.1:18443 -b "$W/jar" https://studio.localhost/api/auth/me
docker run --rm -v nexus-edge-pg17_studio_dataprotection_keys:/k --entrypoint sh "$PG17_STUDIO_API_IMAGE" -c 'printf "keys: %s files, mode %s\n" "$(ls /k | wc -l)" "$(stat -c %a /k)"'

echo "== R1 restore the pre-Studio Caddy config, then confirm www/api"
$BASE up -d --no-deps caddy 2>&1 | grep -v -i orphan || true
wait_healthy "$($BASE ps -q caddy)"
[ "$($BASE ps -q public-api)" = "$API_BEFORE" ] && echo "PASS: public-api not recreated by R1"
checks absent
members

echo "== R2 stop only the Studio services (no down -v, no volume rm)"
$DC stop studio-api studio-web
$DC ps --all --format '{{.Service}} {{.State}}' 2>/dev/null || $DC ps
docker volume ls --format '{{.Name}}' | grep nexus-edge-pg17_ | sort
docker exec --user postgres nexus-pg17-edge-iso-studio-pg17 psql -X -At -U nexus -d nexus -c "SELECT 'after_rollback', (SELECT count(*) FROM studio.\"__EFMigrationsHistory\"), (SELECT count(*) FROM studio.admins), (SELECT count(*) FROM delivery.participant_bundles)"

echo "== re-apply after rollback (S-2, S-3 again)"
$DC up -d --no-deps studio-api studio-web
wait_healthy "$($DC ps -q studio-api)"; wait_healthy "$($DC ps -q studio-web)"
$DC up -d --no-deps caddy; wait_healthy "$($DC ps -q caddy)"
checks present
echo REHEARSAL_DONE
