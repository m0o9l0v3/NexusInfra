"""Exercise the Studio overlay guard (#9) through its CLI, without Docker."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/validate-pg17-studio-edge.py"
BASE = yaml.safe_load((ROOT / "compose.pg17-edge.yaml").read_text())
OVERLAY = yaml.safe_load((ROOT / "compose.pg17-studio.yaml").read_text())
CADDY = (ROOT / "Caddyfile.pg17-with-studio").read_text()
BASE_CADDY = (ROOT / "Caddyfile.pg17-api").read_text()
DIGEST = "example/image@sha256:" + "0" * 64
LOCAL_ID = "sha256:" + "1" * 64
ENV = {
    "PG17_EDGE_PUBLIC_API_IMAGE": DIGEST, "PG17_EDGE_CADDY_IMAGE": DIGEST,
    "PG17_EDGE_SUBNET": "172.30.251.0/28", "PG17_EDGE_API_DOMAIN": "api.example.invalid",
    "PG17_EDGE_SITE_DOMAIN": "www.example.invalid", "PG17_EDGE_ACME_EMAIL": "ops@example.invalid",
    "PG17_EDGE_BACKEND_NETWORK": "reviewed-pg17-network", "PG17_EDGE_SECRET_DIR": "/private/disposable-secrets",
    "PG17_STUDIO_API_IMAGE": LOCAL_ID, "PG17_STUDIO_WEB_IMAGE": DIGEST,
    "PG17_STUDIO_DOMAIN": "studio.example.invalid", "PG17_STUDIO_SUBNET": "172.30.252.0/28",
    "PG17_STUDIO_SECRET_DIR": "/private/studio-secrets",
}
count = 0


def substitute(data):
    text = yaml.safe_dump(data)
    return yaml.safe_load(re.sub(r"\$\{([A-Z0-9_]+)(?::\?[^}]+)?\}", lambda m: ENV[m[1]], text))


def merged_fixture(folder):
    """What `docker compose -f base -f overlay config --format json` produces (shape checked in CI)."""
    base, over = substitute(BASE), substitute(OVERLAY)
    doc = copy.deepcopy(base)
    doc["services"].update({k: v for k, v in over["services"].items() if k != "caddy"})
    caddy = doc["services"]["caddy"]
    caddy["environment"].update(over["services"]["caddy"]["environment"])
    caddy["networks"] = caddy["networks"] + over["services"]["caddy"]["networks"]
    caddy["volumes"][0] = {"type": "bind", "source": str(folder / "Caddyfile.pg17-with-studio"),
                           "target": "/etc/caddy/Caddyfile", "read_only": True, "bind": {}}
    caddy["depends_on"]["public-api"]["required"] = True
    for kind in ("networks", "volumes", "secrets"):
        doc[kind].update(over[kind])
    for kind in ("networks", "volumes"):
        for key, val in doc[kind].items():
            if "name" not in val:
                val["name"] = "nexus-edge-pg17_" + key
    return doc


def run(*, overlay=OVERLAY, merged=None, caddy=CADDY, env=None, accept=False, error=None):
    global count
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "Caddyfile.pg17-with-studio").write_text(caddy)
        (folder / "Caddyfile.pg17-api").write_text(BASE_CADDY)
        (folder / "overlay.yml").write_text(yaml.safe_dump(overlay))
        cmd = [sys.executable, str(SCRIPT), "--overlay", str(folder / "overlay.yml"),
               "--caddyfile", str(folder / "Caddyfile.pg17-with-studio"),
               "--base-caddyfile", str(folder / "Caddyfile.pg17-api")]
        if merged is not None:
            data = merged(merged_fixture(folder)) if callable(merged) else merged
            (folder / "merged.json").write_text(json.dumps(data))
            cmd += ["--resolved", str(folder / "merged.json")]
        if env is not None:
            cmd.append("--env")
        result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ | ENV | (env or {}))
        assert (result.returncode == 0) == accept, result.stdout + result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        if error:
            assert error in result.stderr, result.stderr
        count += 1


def mutate(path, value):
    def apply(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        if value is DELETE:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return doc
    return apply


DELETE = object()


def main():
    run(accept=True)
    run(env={}, accept=True)
    run(merged=lambda d: d, accept=True)
    run(merged=lambda d: d, env={}, accept=True)

    # The base edge must stay exactly as before: CORS, DEC-1, ports, www/api.
    for path, value in [
        (["services", "public-api", "environment", "Cors__AllowedOrigins__1"], "https://studio.example.invalid"),
        (["services", "public-api", "environment", "Cors__AllowedOrigins__0"], "https://studio.example.invalid"),
        (["services", "public-api", "environment", "RateLimiting__KnownNetworks__0"], "172.30.252.0/28"),
        (["services", "caddy", "ports"], [{"published": "8443", "target": "8443"}]),
        (["services", "studio-api", "ports"], ["8080:8080"]),
        (["services", "studio-web", "networks"], {"studio": None, "edge": None}),
        (["services", "studio-api", "networks"], {"backend": None, "edge": None}),
        (["services", "studio-api", "networks"], {"studio": None}),
        (["services", "studio-web", "networks"], {"studio": None, "backend": None}),
        (["services", "studio-web", "networks"], {"studio": None, "public": None}),
        (["services", "caddy", "networks"], {"edge": None, "public": None, "studio": None, "backend": None}),
        (["services", "caddy", "networks"], {"edge": None, "public": None}),
        (["services", "studio-api", "environment", "ReverseProxy__TrustedNetwork"], "172.30.251.0/28"),
        (["services", "studio-api", "environment", "ReverseProxy__TrustedNetwork"], "172.16.0.0/12"),
        (["services", "studio-api", "environment", "ASPNETCORE_ENVIRONMENT"], "Development"),
        (["services", "studio-api", "environment", "Studio__Seed"], "true"),
        (["services", "studio-api", "image"], "nexus-studio-api:165d468"),
        (["services", "studio-web", "image"], "studio-web:latest"),
        (["services", "studio-api", "command"], ["admin", "add"]),
        (["services", "studio-api", "privileged"], True),
        (["services", "studio-api", "restart"], "no"),
        (["services", "studio-api", "healthcheck", "disable"], True),
        (["services", "studio-api", "volumes"], [{"type": "volume", "source": "caddy_data", "target": "/data"}]),
        (["services", "studio-api", "secrets"], [{"source": "public_connection"}]),
        (["services", "studio-web", "secrets"], [{"source": "studio_connection"}]),
        (["services", "studio-db"], {"image": DIGEST}),
        (["services", "studio-web"], DELETE),
        (["networks", "studio", "internal"], False),
        (["networks", "studio", "ipam", "config"], [{"subnet": "172.30.251.0/28"}]),
        (["networks", "studio", "ipam", "config"], [{"subnet": "172.30.251.0/24"}]),
        (["networks", "studio", "ipam", "config"], [{"subnet": "8.8.8.0/28"}]),
        (["networks", "studio", "name"], "shared-studio"),
        (["volumes", "studio_dataprotection_keys", "name"], "shared-keys"),
        (["secrets", "studio_connection", "file"], "relative/studio_connection"),
        (["services", "caddy", "environment", "STUDIO_DOMAIN"], "www.example.invalid"),
        (["services", "caddy", "environment", "STUDIO_DOMAIN"], "https://studio.example.invalid"),
        (["services", "caddy", "volumes", 0, "source"], str(ROOT / "Caddyfile.pg17-api")),
        (["services", "caddy", "volumes", 0, "read_only"], False),
    ]:
        run(merged=mutate(path, value))
    run(merged=mutate(["services", "public-api", "environment", "Cors__AllowedOrigins__1"], "https://x.invalid"),
        error="base edge:")

    # The Caddy config: base contract unchanged + exactly the Studio block.
    for old, new in [
        ("reverse_proxy studio-api:8080", "reverse_proxy studio-api:8080 { trusted_proxies private_ranges }"),
        ("handle /api/*", "handle_path /api/*"),
        ("reverse_proxy public-api:8080", "reverse_proxy public-api:8080 studio-api:8080"),
        ("max-age=31536000", "max-age=31536000; includeSubDomains"),
        ('header Cache-Control "no-store"', ""),
        ("{$STUDIO_DOMAIN} {", "{$STUDIO_DOMAIN} {\n    header_up X-Forwarded-Proto https"),
        ("file_server\n}", "file_server\n    reverse_proxy /api/* studio-api:8080\n}"),
    ]:
        assert old in CADDY, old
        run(caddy=CADDY.replace(old, new))
    run(caddy=BASE_CADDY)
    run(caddy=CADDY + "\nimport /private/override\n")
    run(caddy=CADDY.replace("    ", "\t"), accept=True)

    # The raw overlay.
    for path, value in [
        (["services", "public-api"], {"image": DIGEST}),
        (["services", "caddy", "ports"], ["8443:8443"]),
        (["services", "caddy", "depends_on"], ["studio-api"]),
        (["services", "caddy", "volumes"], ["./Caddyfile:/etc/caddy/Caddyfile:ro"]),
        (["services", "caddy", "networks"], ["studio", "backend"]),
        (["services", "studio-api", "image"], "nexus-studio-api:165d468"),
        (["networks", "studio", "internal"], False),
        (["volumes", "nexus_postgres_data"], {}),
        (["include"], ["compose.yaml"]),
    ]:
        run(overlay=mutate(path, value)(copy.deepcopy(OVERLAY)))

    for key, value in [
        ("PG17_STUDIO_API_IMAGE", "nexus-studio-api:165d468"), ("PG17_STUDIO_WEB_IMAGE", "sha256:abc"),
        ("PG17_STUDIO_SUBNET", "172.30.251.0/28"), ("PG17_STUDIO_SUBNET", "172.30.251.0/24"),
        ("PG17_STUDIO_SUBNET", "10.0.0.0/16"), ("PG17_STUDIO_SUBNET", "8.8.8.0/28"),
        ("PG17_STUDIO_DOMAIN", "api.example.invalid"), ("PG17_STUDIO_DOMAIN", "WWW.example.invalid"),
        ("PG17_STUDIO_DOMAIN", "1.2.3.4"), ("PG17_STUDIO_SECRET_DIR", "relative"),
    ]:
        run(env={key: value})
    for invalid in ([], "invalid", {"services": None}):
        run(overlay=invalid)
        run(merged=invalid)
    print(f"PG17 Studio edge checks passed ({count} cases)")


if __name__ == "__main__":
    main()
