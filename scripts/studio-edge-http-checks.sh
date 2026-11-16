#!/bin/sh
# Studio on the PG17 edge (#9): HTTP checks through Caddy. Read-only unless LOGIN_EMAIL is set; run from the Mac.
# Usage: STUDIO=https://<Studio domain> API=https://<API domain> SITE=https://<site domain> sh scripts/studio-edge-http-checks.sh
#   Optional login/logout check (rehearsal, or a dedicated check account): LOGIN_EMAIL=<email> LOGIN_PASSWORD_FILE=<file>.
#   The password is read from the file and never printed. In production the owner checks login in the browser instead.
#   Rehearsal only: CURL_EXTRA="--connect-to ... --cacert ...". EXPECT_STUDIO=absent checks the rollback state.
set -u
STUDIO=${STUDIO:?} API=${API:?} SITE=${SITE:?}
X=${CURL_EXTRA:-}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }
hdr() { grep -i "^$2:" "$1" | head -1 | cut -d: -f2- | sed 's/^ *//; s/\r$//'; }
cookie() { grep -i '^set-cookie:' "$1" | grep -i "$2=" | head -1 | tr -d '\r'; }
req() { # $1 name, $2 url, rest curl args; prints the status code
  n=$1 u=$2; shift 2
  # shellcheck disable=SC2086
  curl -s $X --max-time 15 -D "$T/$n.h" -o "$T/$n.b" "$@" "$u" || true
  sed -n '1s/\r$//p' "$T/$n.h" 2>/dev/null | awk '{print $2}'
}
secure_cookie() { # $1 header file, $2 cookie name, $3 label
  c=$(cookie "$1" "$2")
  if echo "$c" | grep -qi '; secure' && echo "$c" | grep -qi '; httponly' && echo "$c" | grep -qi '; samesite=strict'; then
    pass "$3: $2 is Secure; HttpOnly; SameSite=Strict"
  else fail "$3: $2 cookie attributes: ${c:-<none>}"; fi
}

# www and api must be unchanged by the Studio site block.
s=$(req www "$SITE/")
[ "$s" = 200 ] && pass "www: / 200" || fail "www: / $s"
s=$(req health "$API/health" -H "Origin: $SITE")
[ "$s" = 200 ] && [ "$(hdr "$T/health.h" Access-Control-Allow-Origin)" = "$SITE" ] && pass "api: /health 200, CORS for the site origin" || fail "api: /health $s / ACAO $(hdr "$T/health.h" Access-Control-Allow-Origin)"
s=$(req health2 "$API/health" -H "Origin: $STUDIO")
[ "$s" = 200 ] && [ -z "$(hdr "$T/health2.h" Access-Control-Allow-Origin)" ] && pass "api: /health has no CORS for the Studio origin" || fail "api: CORS for Studio origin $(hdr "$T/health2.h" Access-Control-Allow-Origin)"
s=$(req bundle "$API/api/v2/bundle" -H "Origin: $SITE")
[ -z "$(hdr "$T/bundle.h" Access-Control-Allow-Origin)" ] && pass "api: /api/v2/bundle ($s) has no CORS" || fail "api: bundle got CORS"

if [ "${EXPECT_STUDIO:-present}" = absent ]; then
  s=$(req gone "$STUDIO/")
  case "$s" in 2*|3*) fail "rollback: Studio still answers ($s)";; *) pass "rollback: Studio is not served (${s:-no TLS/connection})";; esac
  echo "RESULT: $fails failure(s)"; [ "$fails" -eq 0 ]; exit
fi

# Studio web (SPA) through Caddy, path kept, direct URLs fall back to index.html.
s=$(req root "$STUDIO/")
[ "$s" = 200 ] && hdr "$T/root.h" Content-Type | grep -q '^text/html' && pass "studio: / 200 text/html" || fail "studio: / $s"
[ "$(hdr "$T/root.h" Strict-Transport-Security)" = "max-age=31536000" ] && pass "studio: HSTS max-age=31536000 (no includeSubDomains)" || fail "studio: HSTS $(hdr "$T/root.h" Strict-Transport-Security)"
[ "$(hdr "$T/root.h" X-Frame-Options)" = DENY ] && [ "$(hdr "$T/root.h" X-Content-Type-Options)" = nosniff ] && pass "studio: X-Frame-Options DENY, nosniff" || fail "studio: frame/nosniff headers"
[ "$(hdr "$T/root.h" X-Robots-Tag)" = "noindex, nofollow" ] && pass "studio: X-Robots-Tag noindex" || fail "studio: X-Robots-Tag"
[ -z "$(hdr "$T/root.h" Server)" ] && pass "studio: no Server header" || fail "studio: Server header present"
s=$(req deep "$STUDIO/releases/history")
[ "$s" = 200 ] && cmp -s "$T/root.b" "$T/deep.b" && pass "studio: direct URL /releases/history 200 = index.html (SPA fallback)" || fail "studio: direct URL $s"
asset=$(grep -o '/assets/[^"]*\.js' "$T/root.b" | head -1)
s=$(req asset "$STUDIO$asset")
[ -n "$asset" ] && [ "$s" = 200 ] && hdr "$T/asset.h" Content-Type | grep -q javascript && pass "studio: $asset 200 javascript" || fail "studio: asset ${asset:-<none>} $s"
s=$(req plain "$(echo "$STUDIO" | sed 's#^https://#http://#')/" )
case "$s" in 301|308) echo "$(hdr "$T/plain.h" Location)" | grep -q '^https://' && pass "studio: http -> $s https" || fail "studio: http redirect target";; *) fail "studio: http $s";; esac

# Studio API: same origin, cookies Secure, no CORS, forwarded headers not trusted from clients.
s=$(req csrf "$STUDIO/api/auth/csrf" -c "$T/jar")
[ "$s" = 200 ] && pass "studio: /api/auth/csrf 200" || fail "studio: /api/auth/csrf $s"
secure_cookie "$T/csrf.h" nexus_studio_csrf "csrf"
[ "$(hdr "$T/csrf.h" Cache-Control)" = no-store ] && pass "studio: /api/* Cache-Control no-store" || fail "studio: /api Cache-Control $(hdr "$T/csrf.h" Cache-Control)"
s=$(req spoof "$STUDIO/api/auth/csrf" -H 'X-Forwarded-Proto: http' -H 'X-Forwarded-Host: evil.invalid' -H 'X-Forwarded-For: 203.0.113.9' -H 'Forwarded: for=203.0.113.9;proto=http;host=evil.invalid')
[ "$s" = 200 ] && pass "studio: spoofed X-Forwarded-*/Forwarded ignored (200)" || fail "studio: spoofed headers -> $s"
secure_cookie "$T/spoof.h" nexus_studio_csrf "spoofed"
s=$(req cors "$STUDIO/api/auth/csrf" -H 'Origin: https://evil.invalid')
[ -z "$(hdr "$T/cors.h" Access-Control-Allow-Origin)" ] && pass "studio: no CORS for other origins" || fail "studio: CORS $(hdr "$T/cors.h" Access-Control-Allow-Origin)"
s=$(req me0 "$STUDIO/api/auth/me")
[ "$s" = 401 ] && pass "studio: /api/auth/me without session 401" || fail "studio: /api/auth/me $s"
s=$(req nocsrf "$STUDIO/api/auth/login" -X POST -H 'Content-Type: application/json' --data '{"email":"x@example.invalid","password":"x"}')
[ "$s" = 400 ] && grep -q csrf_invalid "$T/nocsrf.b" && pass "studio: login without CSRF token 400 csrf_invalid" || fail "studio: login without CSRF $s"
s=$(req health "$STUDIO/health")
echo "INFO: $STUDIO/health -> $s (served by studio-web; studio-api /health is not routed)"

if [ -n "${LOGIN_EMAIL:-}" ]; then
  tok=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$T/csrf.b")
  python3 -I -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": open(sys.argv[2]).read().rstrip("\n")}))' \
    "$LOGIN_EMAIL" "${LOGIN_PASSWORD_FILE:?}" > "$T/login.json"
  s=$(req bad "$STUDIO/api/auth/login" -b "$T/jar" -X POST -H "X-XSRF-TOKEN: $tok" -H 'Content-Type: application/json' --data '{"email":"nobody@example.invalid","password":"wrong-password-123"}')
  [ "$s" = 401 ] && grep -q invalid_credentials "$T/bad.b" && pass "login: wrong credentials 401" || fail "login: wrong credentials $s"
  s=$(req login "$STUDIO/api/auth/login" -b "$T/jar" -c "$T/jar" -X POST -H "X-XSRF-TOKEN: $tok" -H 'Content-Type: application/json' --data @"$T/login.json")
  rm -f "$T/login.json"
  [ "$s" = 204 ] && pass "login: 204" || fail "login: $s"
  secure_cookie "$T/login.h" nexus_studio_session "login"
  s=$(req me "$STUDIO/api/auth/me" -b "$T/jar")
  [ "$s" = 200 ] && pass "login: /api/auth/me 200" || fail "login: /api/auth/me $s"
  cp "$T/jar" "$T/jar.before-logout"
  s=$(req csrf2 "$STUDIO/api/auth/csrf" -b "$T/jar" -c "$T/jar")
  tok=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$T/csrf2.b")
  s=$(req logout "$STUDIO/api/auth/logout" -b "$T/jar" -c "$T/jar" -X POST -H "X-XSRF-TOKEN: $tok")
  [ "$s" = 204 ] && pass "logout: 204" || fail "logout: $s"
  s=$(req me2 "$STUDIO/api/auth/me" -b "$T/jar")
  [ "$s" = 401 ] && pass "logout: /api/auth/me 401 afterwards" || fail "logout: /api/auth/me $s"
  [ -n "${KEEP_JAR:-}" ] && cp "$T/jar.before-logout" "$KEEP_JAR"
fi

echo "RESULT: $fails failure(s)"
[ "$fails" -eq 0 ]
