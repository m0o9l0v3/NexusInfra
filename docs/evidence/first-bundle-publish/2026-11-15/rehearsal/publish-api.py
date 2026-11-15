"""Rehearsal only: drive the one-off Studio through its HTTP API (the owner uses the Studio screens instead).

Usage: python3 -I publish-api.py <base https url> <email> <password file> <step: first|second>
Content is FICTIONAL rehearsal data (category names and an occurrence marked as rehearsal).
"""
import http.cookiejar, json, ssl, sys, urllib.request, uuid

base, email, pw_file, step = sys.argv[1:5]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE  # internal CA of the one-off Caddy
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=ctx))
csrf = None


def call(method, path, body=None, ok=(200, 201, 204)):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-XSRF-TOKEN", csrf)
    try:
        with opener.open(req) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    if status not in ok:
        sys.exit(f"{method} {path} -> {status}: {raw[:800].decode(errors='replace')}")
    return json.loads(raw) if raw else None


def refresh_csrf():
    global csrf
    csrf = None
    csrf = call("GET", "/api/auth/csrf")["token"]


refresh_csrf()
call("POST", "/api/auth/login", {"email": email, "password": open(pw_file).read().strip()})
refresh_csrf()  # the antiforgery token is bound to the signed-in identity
me = call("GET", "/api/auth/me")
print("signed in as", me["displayName"], "| environment label:", me.get("environmentLabel"))

entries = []
if step == "first":
    cats = call("GET", "/api/categories")
    saved = call("PUT", "/api/categories", {"operationId": str(uuid.uuid4()), "rowVersion": cats["rowVersion"], "items": [
        {"id": str(uuid.uuid4()), "name": "リハーサル用カテゴリA", "selectable": True},
        {"id": str(uuid.uuid4()), "name": "リハーサル用カテゴリB", "selectable": True}]})
    entries.append({"targetKind": "categories", "targetId": "categories", "action": "publish", "revisionId": saved["revisionId"]})
    occ = call("POST", "/api/occurrences", {"operationId": str(uuid.uuid4()), "draft": {
        "name": "リハーサル用の開催回（架空）", "sourceNote": "first-bundle-publish rehearsal",
        "days": [{"id": str(uuid.uuid4()), "date": "2027-08-01", "publicStart": "10:00", "publicEnd": "15:00", "status": "normal", "cancelNote": None}]}})
    entries.append({"targetKind": "occurrence", "targetId": occ["id"], "action": "publish", "revisionId": occ["revisionId"]})
    message = "最初の bundle（リハーサル）"
elif step == "third":
    cats = call("GET", "/api/categories")
    items = [{"id": c["id"], "name": c["name"], "selectable": c["selectable"]} for c in cats["items"]]
    items.append({"id": str(uuid.uuid4()), "name": "リハーサル用カテゴリC", "selectable": True})
    saved = call("PUT", "/api/categories", {"operationId": str(uuid.uuid4()), "rowVersion": cats["rowVersion"], "items": items})
    entries.append({"targetKind": "categories", "targetId": "categories", "action": "publish", "revisionId": saved["revisionId"]})
    message = "停止後の再公開（リハーサル）"
elif step == "withdraw":
    occ_id = call("GET", "/api/occurrences")[0]["id"]
    entries.append({"targetKind": "occurrence", "targetId": occ_id, "action": "withdraw", "revisionId": None})
    message = "開催回を取り下げ（リハーサルの切り戻し）"
else:
    occ_id = call("GET", "/api/occurrences")[0]["id"]
    detail = call("GET", f"/api/occurrences/{occ_id}")
    draft = detail["draft"]
    draft["days"].append({"id": str(uuid.uuid4()), "date": "2027-08-02", "publicStart": "10:00", "publicEnd": "14:00", "status": "normal", "cancelNote": None})
    saved = call("PUT", f"/api/occurrences/{occ_id}", {"operationId": str(uuid.uuid4()), "rowVersion": detail["rowVersion"], "draft": draft})
    entries.append({"targetKind": "occurrence", "targetId": occ_id, "action": "publish", "revisionId": saved["revisionId"]})
    message = "開催日を追加（リハーサル）"

preview = call("POST", "/api/releases/preview", {"entries": entries})
v = preview["validation"]
print("preview: status", v["status"], "blocking", v["blockingCount"], "warning", v["warningCount"])
for f in v["findings"]:
    print("  finding:", f.get("severity"), f.get("code"), f.get("message"))
if v["status"] != "ok" or v["blockingCount"]:
    sys.exit("blocked; not publishing")
op = str(uuid.uuid4())
result = call("POST", "/api/releases", {"operationId": op, "entries": entries, "confirmedValidationId": v["id"], "message": message})
print("publish:", result["status"], "release sequence", (result.get("release") or {}).get("sequence"), "detail", result.get("detail"))
again = call("POST", "/api/releases", {"operationId": op, "entries": entries, "confirmedValidationId": v["id"], "message": message})
print("resend same operationId:", again["status"], "sequence", (again.get("release") or {}).get("sequence"), "(no new release)")
