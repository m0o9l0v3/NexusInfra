"""Rehearsal only: drive the one-off Studio API (the owner uses the screens).

python3 -I publish-stages.py <base> <email> <pw file> a        -> occurrence only (2026-11-14, past, real schedule frame)
python3 -I publish-stages.py <base> <email> <pw file> map      -> later bundle: test-fixture map + 2 spots + categories + 1 event
The map stage uses the Studio test fixture (docs/schemas/examples/map-dataset-geojson-v2.example.json), not campus data.
"""
import http.cookiejar, json, ssl, sys, urllib.request, uuid

base, email, pw_file, step = sys.argv[1:5]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.HTTPSHandler(context=ctx))
csrf = None


def call(method, path, body=None, ok=(200, 201, 204)):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), method=method)
    req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-XSRF-TOKEN", csrf)
    try:
        with opener.open(req) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    if status not in ok:
        sys.exit(f"{method} {path} -> {status}: {raw[:1500].decode(errors='replace')}")
    return json.loads(raw) if raw else None


def login():
    global csrf
    csrf = call("GET", "/api/auth/csrf")["token"]
    call("POST", "/api/auth/login", {"email": email, "password": open(pw_file).read().strip()})
    csrf = call("GET", "/api/auth/csrf")["token"]


def publish(entries, message):
    preview = call("POST", "/api/releases/preview", {"entries": entries})
    v = preview["validation"]
    print("preview:", v["status"], "blocking", v["blockingCount"], "warning", v["warningCount"])
    for f in v["findings"]:
        print("  finding:", f.get("severity"), f.get("code"), f.get("message"))
    if v["status"] != "ok" or v["blockingCount"]:
        sys.exit("blocked; not publishing")
    r = call("POST", "/api/releases", {"operationId": str(uuid.uuid4()), "entries": v["entries"], "confirmedValidationId": v["id"], "message": message})
    print("publish:", r["status"], "release", (r.get("release") or {}).get("sequence"), r.get("detail"))


login()
if step == "a":
    occ = call("POST", "/api/occurrences", {"operationId": str(uuid.uuid4()), "draft": {
        "name": "11月オープンキャンパス（午前の部）", "sourceNote": "2026年11月 AM version タイムスケジュール（オーナー提供）",
        "days": [{"id": str(uuid.uuid4()), "date": "2026-11-14", "publicStart": "10:00", "publicEnd": "13:30", "status": "normal", "cancelNote": None}]}})
    publish([{"targetKind": "occurrence", "targetId": occ["id"], "action": "publish", "revisionId": occ["revisionId"]}], "最初の bundle（開催回のみ）")
elif step == "map":
    datasets = call("GET", "/api/map/datasets", ok=(200, 404)) if False else None
    # map: the imported draft version (map import CLI ran before this step)
    diag = call("GET", "/api/releases/preview-targets", ok=(200, 404)) if False else None
    import subprocess  # noqa: F401 (kept simple: the map revision id is passed in the environment)
    import os
    map_rev = os.environ["MAP_DATASET_ID"]
    entries = [{"targetKind": "map", "targetId": "map", "action": "publish", "revisionId": map_rev}]
    for sid in ("mb", "mb_1f_cl_001"):
        d = call("GET", f"/api/spots/item?id={sid}")
        entries.append({"targetKind": "spot", "targetId": sid, "action": "publish", "revisionId": d["revisionId"]})
    cats = call("GET", "/api/categories")
    cat_id = str(uuid.uuid4())
    saved = call("PUT", "/api/categories", {"operationId": str(uuid.uuid4()), "rowVersion": cats["rowVersion"], "items": [{"id": cat_id, "name": "体験授業（試験）", "selectable": True}]})
    entries.append({"targetKind": "categories", "targetId": "categories", "action": "publish", "revisionId": saved["revisionId"]})
    occ = call("POST", "/api/occurrences", {"operationId": str(uuid.uuid4()), "draft": {
        "name": "架空の次回オープンキャンパス（試験）", "sourceNote": "rehearsal",
        "days": [{"id": (day_id := str(uuid.uuid4())), "date": "2027-08-01", "publicStart": "10:00", "publicEnd": "15:00", "status": "normal", "cancelNote": None}]}})
    entries.append({"targetKind": "occurrence", "targetId": occ["id"], "action": "publish", "revisionId": occ["revisionId"]})
    ev = call("POST", "/api/events", {"operationId": str(uuid.uuid4()), "draft": {
        "schemaVersion": "studio.event/1", "title": "架空の模擬授業（試験）", "description": None, "categoryId": cat_id, "occurrenceId": occ["id"],
        "slots": [{"slotId": str(uuid.uuid4()), "ocDayId": day_id, "timeMode": "fixed", "fixed": {"start": "10:30", "end": "11:15"},
                   "participation": "atStart", "status": "normal", "cancelNote": None, "venues": [{"canonicalSpotId": "mb_1f_cl_001", "note": None}]}]}})
    entries.append({"targetKind": "event", "targetId": ev["id"], "action": "publish", "revisionId": ev["revisionId"]})
    publish(entries, "地図ありの後続 bundle（試験）")
elif step == "map-retry":
    import os
    entries = [{"targetKind": "map", "targetId": "map", "action": "publish", "revisionId": os.environ["MAP_DATASET_ID"]}]
    for sid in ("mb", "mb_1f_cl_001"):
        entries.append({"targetKind": "spot", "targetId": sid, "action": "publish", "revisionId": call("GET", f"/api/spots/item?id={sid}")["revisionId"]})
    entries.append({"targetKind": "categories", "targetId": "categories", "action": "publish", "revisionId": call("GET", "/api/categories")["revisionId"]})
    occ = [o for o in call("GET", "/api/occurrences") if "試験" in o["name"]][0]
    entries.append({"targetKind": "occurrence", "targetId": occ["id"], "action": "publish", "revisionId": call("GET", f"/api/occurrences/{occ['id']}")["revisionId"]})
    evs = call("GET", "/api/events")
    ev_id = (evs[0] if isinstance(evs, list) else evs["items"][0])["id"]
    ev = call("GET", f"/api/events/{ev_id}")
    draft = ev["draft"]; draft["description"] = "架空の説明（試験）"
    saved = call("PUT", f"/api/events/{ev_id}", {"operationId": str(uuid.uuid4()), "rowVersion": ev["rowVersion"], "draft": draft})
    entries.append({"targetKind": "event", "targetId": ev_id, "action": "publish", "revisionId": saved["revisionId"]})
    publish(entries, "地図ありの後続 bundle（試験）")
