"""Static PG17 edge guards. Reads files/settings only; never contacts Docker or the DB."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "nexus-edge-pg17"
ISO_PREFIX = "nexus-pg17-edge-iso-"
DIGEST = r"[^\s]+@sha256:[0-9a-f]{64}"
PRIVATE = [ipaddress.ip_network(s) for s in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
# Deliberately a closed API-only Caddy contract: header rewrites/trusted_proxies/imports
# can invalidate DEC-1 even when reverse_proxy is still present.
CADDY_CONTRACT = '''
{ email {$ACME_EMAIL} log { output stdout format json } }
(headers) { header {
Strict-Transport-Security "max-age=31536000"
X-Content-Type-Options "nosniff"
Referrer-Policy "strict-origin-when-cross-origin"
X-Frame-Options "DENY" -Server
} }
{$API_DOMAIN} { import headers encode zstd gzip reverse_proxy public-api:8080 }
'''


def variable(value, name, suffix=""):
    return isinstance(value, str) and re.fullmatch(
        r"\$\{" + re.escape(name) + r":\?[^}]+\}" + re.escape(suffix), value) is not None


def private_subnet(value):
    try:
        net = ipaddress.ip_network(value, strict=True)
        return (isinstance(net, ipaddress.IPv4Network) and 24 <= net.prefixlen <= 28
                and any(net.subnet_of(parent) for parent in PRIVATE))
    except (ValueError, TypeError):
        return False


def domain(value):
    if not isinstance(value, str) or len(value) > 253:
        return False
    try:
        ipaddress.ip_address(value)
        return False
    except ValueError:
        pass
    return bool(re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
                             r"[a-zA-Z](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", value))


def networks_of(service):
    return set(service.get("networks", {}))


def environment_of(service):
    env = service.get("environment", {})
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return env


def secrets_of(service):
    items = service.get("secrets", [])
    return {item if isinstance(item, str) else item["source"] for item in items}


def mounts_of(service):
    mounts = []
    for item in service.get("volumes", []):
        if isinstance(item, str):
            parts = item.split(":")
            mounts.append((parts[0], parts[1], len(parts) == 3 and parts[2] == "ro"))
        else:
            mounts.append((item["source"], item["target"], item.get("read_only", False)))
    return mounts


def ports_of(service):
    result = []
    for item in service.get("ports", []):
        if isinstance(item, str):
            parts = item.split(":")
            if len(parts) != 2:
                raise ValueError("ports must use plain published:target form")
            result.append((parts[0], parts[1], "tcp"))
        else:
            result.append((str(item["published"]), str(item["target"]), item.get("protocol", "tcp")))
    return result


def caddy_tokens(text):
    # Keep quoted literals intact (including spaces and '#'); only normalize
    # whitespace/comment formatting between tokens. This is a closed contract,
    # not a general-purpose Caddyfile parser.
    lexer = shlex.shlex(text, posix=False)
    lexer.whitespace_split = True
    lexer.quotes = '"'
    return list(lexer)


def same_file(a: Path, b: Path) -> bool:
    """File identity, not spelling: macOS reports non-ASCII paths in a different Unicode form than Compose."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a.resolve() == b.resolve()


def validate(data, resolved=False, isolation=False, *, compose_dir=ROOT, caddyfile=ROOT / "Caddyfile.pg17-api"):
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    def keys(value, allowed, label):
        check(isinstance(value, dict) and not set(value) - set(allowed), label + " has unsupported keys")

    check("nexus_postgres_data" not in json.dumps(data), "live PG17 volume must never appear")
    keys(data, {"name", "services", "networks", "volumes", "secrets"}, "compose")
    if isolation:
        validate_isolation(data, resolved, check, keys)
        return errors

    check(data.get("name") == PROJECT, "project name must be nexus-edge-pg17")
    services = data.get("services", {})
    check(set(services) == {"public-api", "caddy"}, "services must be exactly public-api and caddy")
    common = {"image", "restart", "environment", "networks", "healthcheck", "logging"}
    for name, svc in services.items():
        keys(svc, common | ({"command", "entrypoint"} if resolved else set())
             | ({"secrets"} if name == "public-api" else {"ports", "volumes", "depends_on"}), name)
        check(svc.get("command") is None and svc.get("entrypoint") is None, name + " command/entrypoint overrides forbidden")
        check(svc.get("restart") == "unless-stopped", name + " restart must be unless-stopped")
        health = svc.get("healthcheck", {})
        command = (["CMD", "/app/healthcheck"] if name == "public-api"
                   else ["CMD", "caddy", "validate", "--config", "/etc/caddy/Caddyfile"])
        check(health.get("test") == command and not health.get("disable"), name + " healthcheck contract differs")
        check(svc.get("logging") == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
              name + " logging must be bounded json-file")
        image = svc.get("image", "")
        image_env = "PG17_EDGE_PUBLIC_API_IMAGE" if name == "public-api" else "PG17_EDGE_CADDY_IMAGE"
        check(bool(re.fullmatch(DIGEST, image)) if resolved else variable(image, image_env),
              name + " image must be a required digest env reference / resolved digest")
        if isinstance(svc.get("networks"), dict):
            check(all(not config for config in svc["networks"].values()), name + " network overrides are forbidden")

    api, caddy = services.get("public-api", {}), services.get("caddy", {})
    check(networks_of(api) == {"backend", "edge"}, "public-api networks must be backend and edge")
    check(networks_of(caddy) == {"edge", "public"}, "caddy networks must be edge and public")
    check(ports_of(caddy) == [("80", "80", "tcp"), ("443", "443", "tcp")], "only caddy TCP 80/443 may be published")
    check(not api.get("ports") and not api.get("volumes") and not api.get("depends_on"),
          "public-api must not own ports, mounts or database dependencies")
    check(caddy.get("depends_on", {}).get("public-api", {}).get("condition") == "service_healthy"
          and set(caddy.get("depends_on", {})) == {"public-api"}, "caddy must depend only on healthy public-api")
    mounts = mounts_of(caddy)
    check(len(mounts) == 3 and {m[1] for m in mounts} == {"/etc/caddy/Caddyfile", "/data", "/config"},
          "caddy mounts must be exactly the API config and two ACME volumes")
    for source, target, read_only in mounts:
        if target == "/etc/caddy/Caddyfile":
            source_path = Path(source)
            check(not resolved or source_path.is_absolute(), "resolved Caddyfile mount source must be absolute")
            if not source_path.is_absolute():
                source_path = compose_dir / source_path
            check(source_path.name == "Caddyfile.pg17-api" and read_only and same_file(source_path, caddyfile),
                  "Caddyfile mount must be the exact file validated, mounted read-only")
        else:
            check(source == {"/data": "caddy_data", "/config": "caddy_config"}.get(target) and not read_only,
                  "ACME mount source must be project-scoped")
    volumes = data.get("volumes", {})
    check(set(volumes) == {"caddy_data", "caddy_config"}, "only ACME volumes are allowed")
    for name, config in volumes.items():
        check(not config if not resolved else config == {"name": PROJECT + "_" + name},
              "ACME volumes must have default project-scoped names")

    networks = data.get("networks", {})
    check(set(networks) == {"backend", "edge", "public"}, "network set differs")
    backend, edge, public = (networks.get(n, {}) for n in ("backend", "edge", "public"))
    keys(backend, {"external", "name"} | ({"ipam"} if resolved else set()), "backend network")
    check(not backend.get("ipam"), "backend IPAM must remain externally managed")
    check(backend.get("external") is True, "backend must be external")
    backend_name = backend.get("name", "")
    check(bool(backend_name) and not backend_name.startswith("nexus-production_") if resolved
          else variable(backend_name, "PG17_EDGE_BACKEND_NETWORK"), "backend network must be explicitly identified")
    keys(edge, {"internal", "ipam", "name"} if resolved else {"internal", "ipam"}, "edge network")
    keys(public, {"name", "ipam"} if resolved else set(), "public network")
    check(not public.get("ipam"), "public network must use default IPAM")
    check(edge.get("internal") is True, "edge must be internal")
    if resolved:
        for name in ("edge", "public"):
            check(networks.get(name, {}).get("name") == PROJECT + "_" + name, "edge/public network must be project-scoped")
    ipam = edge.get("ipam", {})
    keys(ipam, {"config", "driver"} if resolved else {"config"}, "edge IPAM")
    check(ipam.get("driver", "default") == "default", "edge IPAM driver must be default")
    configs = ipam.get("config", [])
    check(len(configs) == 1 and set(configs[0]) == {"subnet"}, "edge needs one subnet with no gateway/ip-range overrides")
    subnet = configs[0].get("subnet", "") if len(configs) == 1 else ""
    check(private_subnet(subnet) if resolved else variable(subnet, "PG17_EDGE_SUBNET"), "edge subnet must be private IPv4 /24 to /28")

    env = environment_of(api)
    expected = {"ASPNETCORE_ENVIRONMENT": "Production", "DatabaseProvider": "postgresql",
                "ConnectionStrings__PublicApiReadOnlyFile": "/run/secrets/public_connection",
                "AuditLog__HashKeyFile": "/run/secrets/audit_key"}
    check(set(env) == set(expected) | {"RateLimiting__KnownNetworks__0", "Cors__AllowedOrigins__0"},
          "public-api environment keys differ (extra CORS/proxy/config overrides forbidden)")
    check(all(env.get(k) == v for k, v in expected.items()), "public-api runtime/secret configuration differs")
    check(env.get("RateLimiting__KnownNetworks__0") == subnet, "KnownNetworks must equal the edge subnet (DEC-1)")
    caddy_env = environment_of(caddy)
    check(set(caddy_env) == {"API_DOMAIN", "ACME_EMAIL"}, "caddy environment keys differ")
    api_domain = caddy_env.get("API_DOMAIN", "")
    origin = env.get("Cors__AllowedOrigins__0", "")
    check(origin == "https://" + api_domain, "CORS must equal the API HTTPS origin (DEC-3)")
    check(domain(api_domain) if resolved else variable(api_domain, "PG17_EDGE_API_DOMAIN"), "API domain is invalid")
    email = caddy_env.get("ACME_EMAIL", "")
    check(bool(email) and "@" in email if resolved else variable(email, "PG17_EDGE_ACME_EMAIL"), "ACME contact is required")
    check(secrets_of(api) == {"public_connection", "audit_key"}, "public-api secret set differs")
    secrets = data.get("secrets", {})
    check(set(secrets) == {"public_connection", "audit_key"}, "only public_connection and audit_key secrets allowed")
    directories = set()
    for name, config in secrets.items():
        keys(config, {"file"} | ({"name"} if resolved else set()), "secret")
        file = config.get("file", "")
        check(Path(file).is_absolute() and Path(file).name == name if resolved
              else variable(file, "PG17_EDGE_SECRET_DIR", "/" + name), "secret file must come from the private directory")
        directories.add(str(Path(file).parent))
    check(len(directories) == 1, "secrets must share the selected private directory")
    return errors


def validate_isolation(data, resolved, check, keys):
    def setting(value, env):
        return (isinstance(value, str) and value.startswith(ISO_PREFIX) and len(value) > len(ISO_PREFIX)
                if resolved else variable(value, env))

    check(setting(data.get("name", ""), "PG17_EDGE_ISO_PROJECT"), "isolation project needs disposable prefix")
    services = data.get("services", {})
    check(set(services) == {"postgres"}, "isolation allows only postgres")
    pg = services.get("postgres", {})
    keys(pg, {"image", "restart", "environment", "secrets", "networks", "volumes", "healthcheck"}
         | ({"command", "entrypoint"} if resolved else set()), "isolation postgres")
    check(pg.get("command") is None and pg.get("entrypoint") is None, "isolation command/entrypoint overrides forbidden")
    check(pg.get("restart") == "no", "isolation must not restart automatically")
    image = pg.get("image", "")
    check(bool(re.fullmatch(DIGEST, image)) if resolved else variable(image, "PG17_EDGE_ISO_DB_IMAGE"), "isolation image must be digest pinned")
    check(environment_of(pg) == {"POSTGRES_USER": "nexus", "POSTGRES_DB": "nexus",
                                "POSTGRES_PASSWORD_FILE": "/run/secrets/isolation_password"}, "isolation PG17 baseline contract differs")
    check(pg.get("networks") == {"backend": {"aliases": ["pg17-isolation-db"]}}, "isolation DB network/alias differs")
    check(mounts_of(pg) == [("isolation_db", "/var/lib/postgresql/data", False)], "isolation mounts differ")
    check(secrets_of(pg) == {"isolation_password"}, "isolation secret set differs")
    check(pg.get("healthcheck", {}).get("test") == ["CMD-SHELL", "pg_isready -U nexus -d nexus"], "isolation healthcheck differs")
    nets = data.get("networks", {})
    check(set(nets) == {"backend"}, "isolation network set differs")
    net = nets.get("backend", {})
    keys(net, {"name", "internal"} | ({"ipam"} if resolved else set()), "isolation backend")
    check(not net.get("ipam"), "isolation IPAM must use defaults")
    check(net.get("internal") is True and setting(net.get("name", ""), "PG17_EDGE_ISO_NETWORK"), "isolation backend must be disposable and internal")
    volumes = data.get("volumes", {})
    check(set(volumes) == {"isolation_db"}, "isolation volume set differs")
    vol = volumes.get("isolation_db", {})
    keys(vol, {"name", "external"}, "isolation volume")
    check(vol.get("external") is True and setting(vol.get("name", ""), "PG17_EDGE_ISO_VOLUME"), "isolation volume must have disposable prefix and be explicitly created")
    secrets = data.get("secrets", {})
    check(set(secrets) == {"isolation_password"}, "isolation top-level secrets differ")
    secret = secrets.get("isolation_password", {})
    keys(secret, {"file", "name"} if resolved else {"file"}, "isolation password")
    file = secret.get("file", "")
    check(Path(file).is_absolute() if resolved else variable(file, "PG17_EDGE_ISO_PASSWORD_FILE"), "isolation password file must be absolute")


def validate_environment(isolation=False):
    errors = []
    if isolation:
        for key in ("PG17_EDGE_ISO_PROJECT", "PG17_EDGE_ISO_NETWORK", "PG17_EDGE_ISO_VOLUME"):
            value = os.environ.get(key, "")
            if not re.fullmatch(re.escape(ISO_PREFIX) + r"[a-z0-9][a-z0-9_-]*", value):
                errors.append(key + " must have a disposable prefix and suffix")
        images = ("PG17_EDGE_ISO_DB_IMAGE",)
        paths = ("PG17_EDGE_ISO_PASSWORD_FILE",)
    else:
        images = ("PG17_EDGE_PUBLIC_API_IMAGE", "PG17_EDGE_CADDY_IMAGE")
        paths = ("PG17_EDGE_SECRET_DIR",)
        if not private_subnet(os.environ.get("PG17_EDGE_SUBNET", "")):
            errors.append("PG17_EDGE_SUBNET must be private IPv4 /24 to /28")
        if not domain(os.environ.get("PG17_EDGE_API_DOMAIN", "")):
            errors.append("PG17_EDGE_API_DOMAIN must be a DNS name, not an IP or URL")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+", os.environ.get("PG17_EDGE_ACME_EMAIL", "")):
            errors.append("PG17_EDGE_ACME_EMAIL is required")
        backend = os.environ.get("PG17_EDGE_BACKEND_NETWORK", "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", backend) or backend.startswith("nexus-production_"):
            errors.append("PG17_EDGE_BACKEND_NETWORK must identify the inspected PG17 network, not PG18")
    for key in images:
        if not re.fullmatch(DIGEST, os.environ.get(key, "")):
            errors.append(key + " must be an immutable image digest")
    for key in paths:
        if not Path(os.environ.get(key, "")).is_absolute():
            errors.append(key + " must be absolute")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path)
    parser.add_argument("--caddyfile", type=Path, default=ROOT / "Caddyfile.pg17-api")
    parser.add_argument("--resolved", type=Path)
    parser.add_argument("--env", action="store_true")
    parser.add_argument("--isolation", action="store_true")
    args = parser.parse_args()
    path = args.compose or ROOT / ("compose.pg17-edge-isolation.yml" if args.isolation else "compose.pg17-edge.yaml")
    errors = []
    for source, resolved in ((path, False), (args.resolved, True)):
        if source is None:
            continue
        try:
            data = json.loads(source.read_text()) if resolved else yaml.safe_load(source.read_text())
            errors.extend(validate(data, resolved, args.isolation,
                                   compose_dir=path.resolve().parent, caddyfile=args.caddyfile))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, yaml.YAMLError):
            errors.append("invalid Compose structure or unreadable input (values omitted)")
    if not args.isolation:
        try:
            if caddy_tokens(args.caddyfile.read_text()) != caddy_tokens(CADDY_CONTRACT):
                errors.append("Caddyfile must match the API-only forwarding/header/TLS contract")
        except ValueError:
            errors.append("Caddyfile contains an invalid quoted literal")
        except OSError:
            errors.append("Caddyfile is unreadable")
    if args.env:
        errors.extend(validate_environment(args.isolation))
    if errors:
        raise SystemExit("\n".join(errors))
    print("PG17 edge checks passed" + (" (isolation)" if args.isolation else ""))


if __name__ == "__main__":
    main()
