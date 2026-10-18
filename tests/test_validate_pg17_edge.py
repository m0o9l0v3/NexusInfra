"""Exercise the static deployment boundary through the validator's public CLI."""
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
SCRIPT = ROOT / "scripts/validate-pg17-edge.py"
RAW = yaml.safe_load((ROOT / "compose.pg17-edge.yaml").read_text())
ISO = yaml.safe_load((ROOT / "compose.pg17-edge-isolation.yml").read_text())
CADDY = (ROOT / "Caddyfile.pg17-api").read_text()
DIGEST = "example/image@sha256:" + "0" * 64
ENV = {
    "PG17_EDGE_PUBLIC_API_IMAGE": DIGEST, "PG17_EDGE_CADDY_IMAGE": DIGEST,
    "PG17_EDGE_SUBNET": "172.30.251.0/28", "PG17_EDGE_API_DOMAIN": "api.example.invalid",
    "PG17_EDGE_ACME_EMAIL": "ops@example.invalid", "PG17_EDGE_BACKEND_NETWORK": "reviewed-pg17-network",
    "PG17_EDGE_SECRET_DIR": "/private/disposable-secrets",
    "PG17_EDGE_ISO_PROJECT": "nexus-pg17-edge-iso-ci",
    "PG17_EDGE_ISO_NETWORK": "nexus-pg17-edge-iso-ci-backend",
    "PG17_EDGE_ISO_VOLUME": "nexus-pg17-edge-iso-ci-db",
    "PG17_EDGE_ISO_DB_IMAGE": DIGEST,
    "PG17_EDGE_ISO_PASSWORD_FILE": "/private/disposable-secrets/isolation_password",
}
count = 0


def run(data=None, *, resolved=None, caddy=CADDY, env=None, isolation=False, accept=False, error=None):
    global count
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        compose = folder / "compose.yml"
        compose.write_text(yaml.safe_dump(data if data is not None else (ISO if isolation else RAW)))
        caddyfile = folder / "Caddyfile.pg17-api"
        caddyfile.write_text(caddy)
        cmd = [sys.executable, str(SCRIPT), "--compose", str(compose), "--caddyfile", str(caddyfile)]
        if resolved is not None:
            resolved = copy.deepcopy(resolved)
            # A concrete fixture uses the repository's source path. Relocate it
            # with this temporary Compose project, without rewriting other paths.
            for service in resolved.get("services", {}).values():
                mounts = service.get("volumes", [])
                for i, mount in enumerate(mounts):
                    if mount == f"{ROOT / 'Caddyfile.pg17-api'}:/etc/caddy/Caddyfile:ro":
                        mounts[i] = f"{caddyfile}:/etc/caddy/Caddyfile:ro"
            path = folder / "resolved.json"
            path.write_text(json.dumps(resolved))
            cmd += ["--resolved", str(path)]
        if isolation:
            cmd.append("--isolation")
        if env is not None:
            cmd.append("--env")
        result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ | ENV | (env or {}))
        assert (result.returncode == 0) == accept, result.stdout + result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        if error:
            assert error in result.stderr, result.stderr
        count += 1


def changed(path, value, data=RAW):
    obj = copy.deepcopy(data)
    target = obj
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return obj


def resolved_fixture(data=RAW):
    # Concrete input exercises the resolved path independently of Docker availability.
    text = yaml.safe_dump(data)
    text = re.sub(r"\$\{([A-Z0-9_]+)(?::\?[^}]+)?\}", lambda m: ENV[m[1]], text)
    doc = yaml.safe_load(text)
    if data is RAW:
        doc["services"]["caddy"]["volumes"][0] = f"{ROOT / 'Caddyfile.pg17-api'}:/etc/caddy/Caddyfile:ro"
        for kind in ("networks", "volumes"):
            for key, val in doc[kind].items():
                if "name" not in val:
                    val["name"] = "nexus-edge-pg17_" + key
    return doc


def test_mount_identity():
    """The file inspected must be the file mounted, including with --caddyfile."""
    global count
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        checked = folder / "Caddyfile.pg17-api"
        checked.write_text(CADDY)
        other = folder / "other" / "Caddyfile.pg17-api"
        other.parent.mkdir()
        other.write_text(CADDY + "\n{$STUDIO_DOMAIN} { reverse_proxy studio-api:8080 }\n")
        compose = folder / "compose.yml"
        def invoke(source, inspected, accept):
            data = changed(["services", "caddy", "volumes"], [
                source + ":/etc/caddy/Caddyfile:ro", "caddy_data:/data", "caddy_config:/config"])
            compose.write_text(yaml.safe_dump(data))
            result = subprocess.run([sys.executable, str(SCRIPT), "--compose", str(compose),
                                     "--caddyfile", str(inspected)], capture_output=True, text=True)
            assert (result.returncode == 0) == accept, result.stderr
            assert "Traceback" not in result.stderr, result.stderr
        invoke("./Caddyfile.pg17-api", checked, True)
        invoke(str(other), checked, False)
        invoke("./other/Caddyfile.pg17-api", checked, False)
        invoke(str(other), other, False) # Inspecting the actual mounted file rejects its extra route.
        other.write_text(CADDY)
        invoke(str(other), other, True) # An explicitly selected file must still be the mounted file.
        link = folder / "link"
        link.symlink_to(other.parent, target_is_directory=True)
        invoke("./link/Caddyfile.pg17-api", checked, False)
        count += 6


def main():
    test_mount_identity()
    run(accept=True)
    run(env={}, accept=True)
    run(isolation=True, env={}, accept=True)
    resolved = resolved_fixture()
    run(resolved=resolved, accept=True)
    with_defaults = copy.deepcopy(resolved)
    for svc in with_defaults["services"].values():
        svc.update(command=None, entrypoint=None)
    for net in ("backend", "public"):
        with_defaults["networks"][net]["ipam"] = {}
    run(resolved=with_defaults, accept=True)
    run(resolved=changed(["services", "public-api", "command"], ["/bin/sh"], with_defaults))
    run(resolved=changed(["networks", "backend", "ipam"], {"driver": "custom"}, with_defaults))
    run(isolation=True, resolved=resolved_fixture(ISO), accept=True)
    mutations = [
        (["name"], "nexus-production"),
        (["services", "postgres"], {"image": DIGEST}),
        (["services", "studio-api"], {"image": DIGEST}),
        (["services", "public-api", "ports"], ["8080:8080"]),
        (["services", "public-api", "network_mode"], "host"),
        (["services", "public-api", "privileged"], True),
        (["services", "public-api", "command"], ["/bin/sh"]),
        (["services", "public-api", "env_file"], ["/private/override.env"]),
        (["services", "public-api", "profiles"], ["hidden"]),
        (["services", "public-api", "environment", "Cors__AllowedOrigins__1"], "https://other.example.invalid"),
        (["services", "public-api", "environment", "Cors__AllowedOrigins__0"], "*"),
        (["services", "public-api", "environment", "RateLimiting__KnownNetworks__0"], "172.30.250.0/28"),
        (["services", "public-api", "environment", "RateLimiting__KnownProxies__0"], "172.30.251.2"),
        (["services", "caddy", "ports"], ["80:80", "443:443", "5432:5432"]),
        (["services", "caddy", "networks"], ["edge", "public", "backend"]),
        (["services", "caddy", "volumes"], ["nexus_postgres_data:/var/lib/postgresql/data"]),
        (["services", "caddy", "volumes"], ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy_data:/data", "caddy_config:/config"]),
        (["services", "caddy", "volumes"], ["/private/tmp/other/Caddyfile.pg17-api:/etc/caddy/Caddyfile:ro", "caddy_data:/data", "caddy_config:/config"]),
        (["services", "caddy", "volumes"], ["./Caddyfile.pg17-api:/etc/caddy/Caddyfile", "caddy_data:/data", "caddy_config:/config"]),
        (["networks", "backend", "external"], False),
        (["networks", "edge", "internal"], False),
        (["networks", "edge", "name"], "shared-edge"),
        (["volumes", "database"], {}),
        (["volumes", "caddy_data", "name"], "shared-acme"),
        (["secrets", "audit_key", "file"], "/private/unreviewed/audit_key"),
        (["include"], ["compose.yaml"]),
    ]
    for path, value in mutations:
        run(changed(path, value))
    for key in ("RateLimiting__KnownNetworks__0", "Cors__AllowedOrigins__0"):
        data = copy.deepcopy(RAW)
        del data["services"]["public-api"]["environment"][key]
        run(data)
    for path, value in [
        (["services", "public-api", "environment", "RateLimiting__KnownNetworks__0"], "172.30.250.0/28"),
        (["services", "public-api", "environment", "Cors__AllowedOrigins__0"], "https://lp.example.invalid"),
        (["networks", "edge", "ipam", "config"], [{"subnet": "8.8.8.0/28"}]),
        (["networks", "backend", "name"], "nexus-production_db"),
        (["services", "caddy", "image"], "caddy:2"),
        (["services", "caddy", "volumes"], ["/private/tmp/other/Caddyfile.pg17-api:/etc/caddy/Caddyfile:ro", "caddy_data:/data", "caddy_config:/config"]),
    ]:
        run(resolved=changed(path, value, resolved))
    for key, value in [
        ("PG17_EDGE_PUBLIC_API_IMAGE", "public:latest"), ("PG17_EDGE_CADDY_IMAGE", "caddy:2"),
        ("PG17_EDGE_SUBNET", "8.8.8.0/28"), ("PG17_EDGE_SUBNET", "10.0.0.0/16"),
        ("PG17_EDGE_SUBNET", "172.30.251.1/28"), ("PG17_EDGE_SUBNET", "fc00::/24"),
        ("PG17_EDGE_SECRET_DIR", "relative"), ("PG17_EDGE_BACKEND_NETWORK", "nexus-production_db"),
        ("PG17_EDGE_API_DOMAIN", "127.0.0.1"), ("PG17_EDGE_API_DOMAIN", "api.example.invalid/path"),
        ("PG17_EDGE_API_DOMAIN", "*.example.invalid"), ("PG17_EDGE_ACME_EMAIL", ""),
    ]:
        run(env={key: value})
    for extra in ("\n{$STUDIO_DOMAIN} { reverse_proxy studio-api:8080 }", "\nimport /private/override"):
        run(caddy=CADDY + extra)
    run(caddy=CADDY.replace("reverse_proxy public-api:8080", "reverse_proxy public-api:8080 { trusted_proxies private_ranges }"))
    run(caddy=CADDY.replace("max-age=31536000", "max-age=31536000; includeSubDomains"))
    run(caddy=CADDY.replace('"DENY"', '"D E N Y"'))
    run(caddy=CADDY.replace('"DENY"', '"DE\nNY"'))
    run(caddy=CADDY.replace('"DENY"', '"DENY#invalid"'))
    run(caddy=CADDY.replace('"DENY"', '"DENY'))
    run(caddy=CADDY.replace('"max-age=31536000"', '"max-age =31536000"'))
    run(caddy=CADDY.replace("X-Frame-Options", "X -Frame-Options"))
    run(caddy=CADDY.replace("    ", "\t").replace("import headers", "import   headers # formatting only"), accept=True)
    merged = yaml.safe_load((ROOT / "compose.yaml").read_text())
    for key in ("services", "networks", "volumes", "secrets"):
        merged.setdefault(key, {}).update(copy.deepcopy(RAW[key]))
    run(merged)
    for path, value in [
        (["services", "postgres", "ports"], ["5432:5432"]),
        (["volumes", "isolation_db", "name"], "nexus_postgres_data"),
        (["networks", "backend", "external"], True),
        (["services", "postgres", "volumes"], ["/srv/nexus:/var/lib/postgresql/data"]),
    ]:
        run(changed(path, value, ISO), isolation=True)
    for key in ("PG17_EDGE_ISO_PROJECT", "PG17_EDGE_ISO_NETWORK", "PG17_EDGE_ISO_VOLUME"):
        run(isolation=True, env={key: "nexus"})
    run(isolation=True, env={"PG17_EDGE_ISO_DB_IMAGE": "postgres:17-alpine"})
    for invalid in ([], "invalid", {"services": None}, {"services": {"public-api": None}}):
        run(invalid)
    print(f"PG17 edge safety checks passed ({count} cases)")


if __name__ == "__main__":
    main()
