import ipaddress
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

try:
    edge = ipaddress.ip_network(os.environ.get("NEXUS_EDGE_SUBNET", ""), strict=True)
except ValueError as error:
    raise SystemExit("A valid edge subnet is required") from error
private_ranges = [ipaddress.ip_network(value) for value in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
if (not isinstance(edge, ipaddress.IPv4Network)
        or not 24 <= edge.prefixlen <= 28
        or not any(edge.subnet_of(private) for private in private_ranges)):
    raise SystemExit("The edge network must be a private IPv4 /24 to /28 subnet")

secret_dir = os.environ.get("NEXUS_SECRET_DIR", "")
if not secret_dir.startswith("/"):
    raise SystemExit("NEXUS_SECRET_DIR must be absolute")

repo_dir = os.environ.get("NEXUS_REPO_DIR", "")
if not repo_dir.startswith("/"):
    raise SystemExit("NEXUS_REPO_DIR must be absolute")

print("Release references and volume names passed static checks")
