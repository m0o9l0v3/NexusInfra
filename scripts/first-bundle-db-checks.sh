#!/bin/sh
# Issue #17 permission checks for delivery after a bundle is published (docs/first-bundle-publish.md §5.3, §6.2).
# Every probe runs inside BEGIN ... ROLLBACK as the named role, through a temporary client container on the DB
# network (scram over the network, like C1 in pg17-issue-8-production-apply.md). Nothing is committed even if a
# probe is unexpectedly allowed. Run as root on the PG17 host, or against a disposable PG17 in rehearsal.
#
# Required: DB_NETWORK (DB network name), DB_HOST (DB alias), PG_IMAGE (the running PG17 image ID or digest),
#           SECRET_DIR (root-owned 0700 directory with nexus_public_password, nexus_studio_app_password,
#           nexus_studio_migrator_password). Optional: EXPECT_CURRENT (expected current bundle_version).
set -u
: "${DB_NETWORK:?}" "${DB_HOST:?}" "${PG_IMAGE:?}" "${SECRET_DIR:?}"
fails=0

as() { # role sql : prints psql output, returns its status. The password file is read inside the container.
    docker run --rm -i --network "$DB_NETWORK" -v "$SECRET_DIR:/run/s:ro" -e ROLE="$1" -e DB_HOST="$DB_HOST" \
        -e PGOPTIONS='-c lock_timeout=5s -c statement_timeout=15s' --entrypoint sh "$PG_IMAGE" -c \
        'PGPASSWORD="$(cat "/run/s/${ROLE}_password")" exec psql -X -At -h "$DB_HOST" -U "$ROLE" -d nexus -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate' <<EOF
BEGIN;
$2
ROLLBACK;
EOF
}
allow() { out=$(as "$2" "$3" 2>&1); rc=$?
    if [ "$rc" = 0 ]; then echo "PASS: allowed ($2): $1 -> $(echo "$out" | grep -v -e '^BEGIN$' -e '^ROLLBACK$' | tr '\n' ' ')"
    else echo "FAIL: expected success ($2): $1 -> $out"; fails=$((fails + 1)); fi; }
deny() { out=$(as "$2" "$3" 2>&1); rc=$?
    if [ "$rc" != 0 ] && echo "$out" | grep -q "$4"; then echo "PASS: denied $4 ($2): $1"
    else echo "FAIL: expected $4 ($2): $1 -> rc=$rc $out"; fails=$((fails + 1)); fi; }

STATE="SELECT 'bundles', count(*), coalesce(max(bundle_version), 0) FROM delivery.participant_bundles;
SELECT 'current', coalesce((SELECT bundle_version FROM delivery.current_bundle), 0);
SELECT 'sha_ok', coalesce(bool_and(encode(sha256(payload), 'hex') = sha256 AND length(payload) = byte_length), true) FROM delivery.participant_bundles;"
echo "== state (nexus_studio_app)"
before=$(as nexus_studio_app "$STATE" 2>&1) || { echo "FAIL: cannot read state: $before"; exit 1; }
echo "$before" | grep -v -e '^BEGIN$' -e '^ROLLBACK$'
cur=$(echo "$before" | sed -n 's/^current|//p')
[ -z "${EXPECT_CURRENT:-}" ] || { [ "$cur" = "$EXPECT_CURRENT" ] && echo "PASS: current bundle_version is $cur" || { echo "FAIL: current $cur (expected $EXPECT_CURRENT)"; fails=$((fails + 1)); }; }
echo "$before" | grep -q '^sha_ok|t$' && echo "PASS: every stored payload matches its sha256 and byte_length" || { echo "FAIL: payload/sha256 mismatch"; fails=$((fails + 1)); }
back=$((cur - 1))

echo "== nexus_public (public-api)"
allow "SELECT participant_bundles" nexus_public "SELECT count(*) FROM delivery.participant_bundles;"
allow "SELECT current_bundle" nexus_public "SELECT bundle_version FROM delivery.current_bundle;"
deny "INSERT participant_bundles" nexus_public "INSERT INTO delivery.participant_bundles SELECT * FROM delivery.participant_bundles WHERE false;" 42501
deny "UPDATE participant_bundles" nexus_public "UPDATE delivery.participant_bundles SET byte_length = byte_length;" 42501
deny "DELETE participant_bundles" nexus_public "DELETE FROM delivery.participant_bundles;" 42501
deny "UPDATE current_bundle" nexus_public "UPDATE delivery.current_bundle SET updated_at = updated_at;" 42501
deny "DELETE current_bundle" nexus_public "DELETE FROM delivery.current_bundle;" 42501
deny "read studio schema" nexus_public "SELECT count(*) FROM studio.releases;" 42501

echo "== nexus_studio_app (Studio runtime)"
allow "SELECT both tables" nexus_studio_app "SELECT (SELECT count(*) FROM delivery.participant_bundles), (SELECT bundle_version FROM delivery.current_bundle);"
deny "UPDATE participant_bundles" nexus_studio_app "UPDATE delivery.participant_bundles SET byte_length = byte_length;" 42501
deny "DELETE participant_bundles" nexus_studio_app "DELETE FROM delivery.participant_bundles;" 42501
deny "TRUNCATE participant_bundles" nexus_studio_app "TRUNCATE delivery.participant_bundles;" 42501
deny "DELETE current_bundle" nexus_studio_app "DELETE FROM delivery.current_bundle;" 42501
deny "disable the monotonic trigger" nexus_studio_app "ALTER TABLE delivery.current_bundle DISABLE TRIGGER current_bundle_monotonic;" 42501
if [ "$back" -ge 0 ]; then
    deny "move the pointer backwards ($cur -> $back)" nexus_studio_app "UPDATE delivery.current_bundle SET bundle_version = $back;" 23514
fi
allow "INSERT a bundle and move the pointer forward (rolled back)" nexus_studio_app \
"INSERT INTO delivery.participant_bundles (bundle_version, release_id, schema_version, payload, sha256, byte_length, created_at)
  VALUES ($cur + 1000000, gen_random_uuid(), 'probe', '\\x7b7d', encode(sha256('\\x7b7d'::bytea), 'hex'), 2, now());
INSERT INTO delivery.current_bundle (bundle_version, updated_at) VALUES ($cur + 1000000, now())
  ON CONFLICT (singleton) DO UPDATE SET bundle_version = EXCLUDED.bundle_version, updated_at = EXCLUDED.updated_at;
SELECT 'inside_txn_current', bundle_version FROM delivery.current_bundle;"

echo "== nexus_studio_migrator (owner of delivery)"
if [ "$back" -ge 0 ]; then
    deny "owner: move the pointer backwards ($cur -> $back)" nexus_studio_migrator "UPDATE delivery.current_bundle SET bundle_version = $back;" 23514
fi

echo "== state again (must be unchanged)"
after=$(as nexus_studio_app "$STATE" 2>&1)
[ "$after" = "$before" ] && echo "PASS: state unchanged" || { echo "FAIL: state changed: $after"; fails=$((fails + 1)); }
echo "RESULT: $fails failure(s)"
[ "$fails" = 0 ]
