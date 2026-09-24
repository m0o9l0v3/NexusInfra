import os
import re

for key in ("NEXUS_POSTGRES_IMAGE", "PUBLIC_API_IMAGE", "ADMIN_API_IMAGE", "STUDIO_API_IMAGE", "STUDIO_WEB_IMAGE", "CADDY_IMAGE"):
    value = os.environ.get(key, "")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", value):
        raise SystemExit(f"{key} must be an immutable image digest")

db = os.environ.get("NEXUS_DB_VOLUME", "")
backup = os.environ.get("NEXUS_BACKUP_VOLUME", "")
if not db or not backup or db == backup:
    raise SystemExit("Distinct, explicitly identified DB and backup volumes are required")

secret_dir = os.environ.get("NEXUS_SECRET_DIR", "")
if not secret_dir.startswith("/"):
    raise SystemExit("NEXUS_SECRET_DIR must be absolute")

print("Release references and volume names passed static checks")
