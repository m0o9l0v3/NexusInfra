#!/bin/sh
# NexusInfra #17 permission checks after a real Studio publish (rehearsal DB only).
# Each statement runs as the named role over the network (scram), in its own psql call.
set -u
: "${DOCKER_CONTEXT:?}"
W=$(cd "$(dirname "$0")" && pwd)
NET=nexus-fbp-20261115-backend
IMG=postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
fails=0
run() { # role sql -> prints output, returns psql rc
  docker run --rm -i --network "$NET" -v "$W/secrets:/run/s:ro" -e ROLE="$1" --entrypoint sh "$IMG" -c \
    'PGPASSWORD="$(cat /run/s/${ROLE}_password)" exec psql -X -At -h pg17-db -U "$ROLE" -d nexus -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate' <<EOF
$2
EOF
}
allow() { out=$(run "$2" "$3" 2>&1); rc=$?; if [ $rc = 0 ]; then echo "PASS: allowed: $1 -> $(echo "$out" | tr '\n' ' ')"; else echo "FAIL: expected success: $1 -> $out"; fails=$((fails+1)); fi; }
deny() { out=$(run "$2" "$3" 2>&1); rc=$?; if [ $rc != 0 ] && echo "$out" | grep -q "$4"; then echo "PASS: denied ($4): $1"; else echo "FAIL: expected $4: $1 -> rc=$rc $out"; fails=$((fails+1)); fi; }

state="SELECT 'bundles', string_agg(bundle_version::text || ':' || byte_length || ':' || left(sha256, 12), ',' ORDER BY bundle_version) FROM delivery.participant_bundles; SELECT 'current', bundle_version FROM delivery.current_bundle;"
echo "== state before"; run nexus_studio_migrator "$state"

echo "== nexus_public (public-api)"
allow "SELECT participant_bundles" nexus_public "SELECT count(*) FROM delivery.participant_bundles"
allow "SELECT current_bundle" nexus_public "SELECT bundle_version FROM delivery.current_bundle"
deny "INSERT participant_bundles" nexus_public "INSERT INTO delivery.participant_bundles SELECT * FROM delivery.participant_bundles LIMIT 0" 42501
deny "UPDATE participant_bundles" nexus_public "UPDATE delivery.participant_bundles SET byte_length = byte_length" 42501
deny "DELETE participant_bundles" nexus_public "DELETE FROM delivery.participant_bundles" 42501
deny "UPDATE current_bundle (pointer back)" nexus_public "UPDATE delivery.current_bundle SET bundle_version = 1" 42501
deny "DELETE current_bundle" nexus_public "DELETE FROM delivery.current_bundle" 42501
deny "SELECT studio.releases" nexus_public "SELECT count(*) FROM studio.releases" 42501

echo "== nexus_studio_app (Studio runtime)"
allow "SELECT both tables" nexus_studio_app "SELECT (SELECT count(*) FROM delivery.participant_bundles), (SELECT bundle_version FROM delivery.current_bundle)"
deny "UPDATE participant_bundles" nexus_studio_app "UPDATE delivery.participant_bundles SET byte_length = byte_length" 42501
deny "DELETE participant_bundles" nexus_studio_app "DELETE FROM delivery.participant_bundles WHERE bundle_version = 1" 42501
deny "TRUNCATE participant_bundles" nexus_studio_app "TRUNCATE delivery.participant_bundles" 42501
deny "DELETE current_bundle" nexus_studio_app "DELETE FROM delivery.current_bundle" 42501
deny "pointer backwards 2 -> 1 (trigger)" nexus_studio_app "UPDATE delivery.current_bundle SET bundle_version = 1" 23514
deny "second pointer row (CHECK/PK)" nexus_studio_app "INSERT INTO delivery.current_bundle (bundle_version, updated_at) VALUES (2, now())" 23
deny "pointer to a missing version (FK)" nexus_studio_app "UPDATE delivery.current_bundle SET bundle_version = 999" 23503
allow "INSERT a bundle and move the pointer forward (then ROLLBACK)" nexus_studio_app "BEGIN;
INSERT INTO delivery.participant_bundles (bundle_version, release_id, schema_version, payload, sha256, byte_length, created_at)
  SELECT 99, gen_random_uuid(), schema_version, payload, sha256, byte_length, now() FROM delivery.participant_bundles WHERE bundle_version = 2;
UPDATE delivery.current_bundle SET bundle_version = 99, updated_at = now();
SELECT 'inside_txn_current', bundle_version FROM delivery.current_bundle;
ROLLBACK;"
deny "ALTER TABLE DISABLE TRIGGER" nexus_studio_app "ALTER TABLE delivery.current_bundle DISABLE TRIGGER current_bundle_monotonic" 42501

echo "== nexus_studio_migrator (owner)"
deny "owner: pointer backwards 2 -> 1 (trigger applies to the owner too)" nexus_studio_migrator "UPDATE delivery.current_bundle SET bundle_version = 1" 23514

echo "== state after (must equal before)"; run nexus_studio_migrator "$state"
echo "RESULT: $fails failure(s)"
[ "$fails" = 0 ]
