#!/bin/sh
# Post-publish HTTP checks for GET /api/v2/bundle through Caddy (docs/first-bundle-publish.md §6.1). Read-only; run from the Mac.
# Usage: API=https://<API domain> SITE_ORIGIN=https://<site domain> [EXPECT_VERSION=<n>] [CURL_EXTRA="--resolve ... -k" for rehearsal] sh scripts/first-bundle-http-checks.sh
set -u
API=${API:?} SITE_ORIGIN=${SITE_ORIGIN:?}
X=${CURL_EXTRA:-}
# Caddy encode does not compress bodies shorter than this (minimum_length default). Larger bodies must be compressed.
MIN_COMPRESS=${MIN_COMPRESS:-512}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }
hdr() { grep -i "^$2:" "$1" | head -1 | cut -d: -f2- | sed 's/^ *//; s/\r$//'; }
get() { # $1 name, rest curl args
  n=$1; shift
  # shellcheck disable=SC2086
  curl -s $X -D "$T/$n.h" -o "$T/$n.b" "$@" "$API/api/v2/bundle"
  sed -n '1s/\r$//p' "$T/$n.h" | awk '{print $2}'
}

s=$(get id -H 'Accept-Encoding: identity')
[ "$s" = 200 ] && pass "identity: 200" || fail "identity: status $s (expected 200)"
sha=$(hdr "$T/id.h" Nexus-Bundle-Sha256); len=$(hdr "$T/id.h" Nexus-Bundle-Length); ver=$(hdr "$T/id.h" Nexus-Bundle-Version)
schema=$(hdr "$T/id.h" Nexus-Bundle-Schema); recheck=$(hdr "$T/id.h" Nexus-Recheck-After); etag=$(hdr "$T/id.h" ETag)
echo "INFO: version=$ver schema=$schema length=$len sha256=$sha recheck=$recheck etag=$etag"
[ -n "$sha" ] && [ -n "$len" ] && [ -n "$ver" ] && [ -n "$schema" ] && [ -n "$recheck" ] && pass "identity: Nexus-Bundle-* and Nexus-Recheck-After present" || fail "identity: missing Nexus headers"
[ "$etag" = "\"sha256-$sha\"" ] && pass "identity: ETag is \"sha256-<Nexus-Bundle-Sha256>\"" || fail "identity: ETag $etag"
body_sha=$(shasum -a 256 "$T/id.b" | cut -d' ' -f1); body_len=$(wc -c < "$T/id.b" | tr -d ' ')
[ "$body_sha" = "$sha" ] && [ "$body_len" = "$len" ] && pass "identity: body SHA-256 and length match headers ($body_len bytes)" || fail "identity: body $body_sha/$body_len vs $sha/$len"
[ -z "${EXPECT_VERSION:-}" ] || { [ "$ver" = "$EXPECT_VERSION" ] && pass "version is $EXPECT_VERSION" || fail "version $ver (expected $EXPECT_VERSION)"; }
python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); m=d["nexus_bundle"]; print("INFO: body nexus_bundle", m["schema_version"], m["bundle_version"], "spots", len(d["spots"]), "map", "null" if d["map"] is None else "present", "categories", len(d.get("categories",[])), "occurrences", len(d.get("occurrences",[])), "events", len(d.get("events",[])))' "$T/id.b" || fail "body is not a bundle JSON"

s=$(get id304 -H 'Accept-Encoding: identity' -H "If-None-Match: $etag")
[ "$s" = 304 ] && pass "identity: ETag sent back -> 304" || fail "identity: If-None-Match -> $s"

for enc in gzip zstd; do
  s=$(get $enc -H "Accept-Encoding: $enc")
  e=$(hdr "$T/$enc.h" ETag); ce=$(hdr "$T/$enc.h" Content-Encoding)
  if [ "$s" = 200 ] && [ "$ce" = "$enc" ] && [ "$e" = "\"sha256-$sha-$enc\"" ]; then pass "$enc: 200, Content-Encoding $enc, ETag $e"
  elif [ "$s" = 200 ] && [ -z "$ce" ] && [ "$e" = "$etag" ] && [ "$body_len" -lt "$MIN_COMPRESS" ]; then
    echo "NOTE: $enc: 200 without compression ($body_len bytes < Caddy's $MIN_COMPRESS-byte minimum); suffix check not applicable"
  else fail "$enc: status $s, Content-Encoding '$ce', ETag $e"; fi
  [ "$(hdr "$T/$enc.h" Nexus-Bundle-Sha256)" = "$sha" ] && [ "$(hdr "$T/$enc.h" Nexus-Bundle-Version)" = "$ver" ] && pass "$enc: Nexus-Bundle-Sha256/Version preserved" || fail "$enc: Nexus headers changed"
  s=$(get ${enc}304 -H "Accept-Encoding: $enc" -H "If-None-Match: $e")
  [ "$s" = 304 ] && pass "$enc: its ETag sent back -> 304" || fail "$enc: If-None-Match -> $s"
  s=$(get ${enc}x -H 'Accept-Encoding: identity' -H "If-None-Match: $e")
  if [ "$e" = "$etag" ]; then :; else [ "$s" = 200 ] && pass "$enc ETag with identity -> 200 (Accept-Encoding changed)" || fail "$enc ETag with identity -> $s"; fi
done
s=$(get stale -H 'If-None-Match: "sha256-0000000000000000000000000000000000000000000000000000000000000000"')
[ "$s" = 200 ] && pass "stale ETag -> 200" || fail "stale ETag -> $s"

# CORS stays on /health only (DEC-3 plan B, NexusMobile E8-7).
curl -s $X -D "$T/c1.h" -o /dev/null -H "Origin: $SITE_ORIGIN" "$API/health"
[ "$(hdr "$T/c1.h" Access-Control-Allow-Origin)" = "$SITE_ORIGIN" ] && pass "CORS: /health allows $SITE_ORIGIN" || fail "CORS: /health ACAO '$(hdr "$T/c1.h" Access-Control-Allow-Origin)'"
curl -s $X -D "$T/c2.h" -o /dev/null -H "Origin: $SITE_ORIGIN" "$API/api/v2/bundle"
[ -z "$(hdr "$T/c2.h" Access-Control-Allow-Origin)" ] && pass "CORS: /api/v2/bundle has no Access-Control-Allow-Origin" || fail "CORS: /api/v2/bundle ACAO '$(hdr "$T/c2.h" Access-Control-Allow-Origin)'"
curl -s $X -D "$T/c3.h" -o /dev/null -H "Origin: https://evil.example" "$API/health"
[ -z "$(hdr "$T/c3.h" Access-Control-Allow-Origin)" ] && pass "CORS: /health rejects another origin" || fail "CORS: other origin got ACAO"

echo "RESULT: $fails failure(s)"
[ "$fails" = 0 ]
