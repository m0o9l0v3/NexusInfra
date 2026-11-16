"""Static guards for the Studio overlay on the PG17 edge (#9). Reads files/settings only.

The overlay (compose.pg17-studio.yaml) is used with the base edge file. The merged result must:
- leave the base edge exactly as validate-pg17-edge.py accepts it (public-api, DEC-1, CORS, www/api);
- add only studio-api and studio-web, on their own internal `studio` network (never on `edge`);
- make studio-api trust X-Forwarded-Proto only from that network's subnet;
- swap the one Caddy's config to Caddyfile.pg17-with-studio = the base contract + one Studio block.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import ipaddress
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("validate_pg17_edge", ROOT / "scripts/validate-pg17-edge.py")
edge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edge)

BASE_CADDYFILE = ROOT / "Caddyfile.pg17-api"
STUDIO_CADDYFILE = ROOT / "Caddyfile.pg17-with-studio"
STUDIO_SERVICES = {"studio-api", "studio-web"}
KEYS_DIR = "/var/lib/nexus-studio/dataprotection-keys"
IMAGE = edge.DIGEST + r"|sha256:[0-9a-f]{64}"  # registry digest, or a natively built local image ID
STUDIO_CONTRACT = '''
{$STUDIO_DOMAIN} {
import headers
header X-Robots-Tag "noindex, nofollow"
encode zstd gzip
handle /api/* { header Cache-Control "no-store" reverse_proxy studio-api:8080 }
handle { reverse_proxy studio-web:8080 }
}
'''
API_ENV = {
    "ASPNETCORE_ENVIRONMENT": "Production",
    "ConnectionStrings__StudioDatabaseFile": "/run/secrets/studio_connection",
    "DataProtection__KeysDirectory": KEYS_DIR,
}
HEALTH = {
    "studio-api": ["CMD", "/app/healthcheck"],
    "studio-web": ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/health"],
}
LOGS = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}


def caddyfile_errors(path: Path):
    try:
        if edge.caddy_tokens(path.read_text()) != edge.caddy_tokens(edge.CADDY_CONTRACT + STUDIO_CONTRACT):
            return ["Caddyfile.pg17-with-studio must be the base contract plus exactly the Studio block"]
    except ValueError:
        return ["Caddyfile contains an invalid quoted literal"]
    except OSError:
        return ["Caddyfile is unreadable"]
    return []


def validate_overlay(data):
    """The raw overlay: env references only, no other base service touched, no ports."""
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    v = edge.variable
    check(isinstance(data, dict) and set(data) == {"services", "networks", "volumes", "secrets"},
          "overlay top-level keys differ (no name/include)")
    services = data.get("services", {})
    check(set(services) == STUDIO_SERVICES | {"caddy"}, "overlay services must be studio-api, studio-web and caddy")
    api, web, caddy = (services.get(n, {}) for n in ("studio-api", "studio-web", "caddy"))
    check(v(api.get("image"), "PG17_STUDIO_API_IMAGE") and v(web.get("image"), "PG17_STUDIO_WEB_IMAGE"),
          "studio images must be required env references")
    check(v(edge.environment_of(api).get("ReverseProxy__TrustedNetwork"), "PG17_STUDIO_SUBNET"),
          "TrustedNetwork must reference PG17_STUDIO_SUBNET")
    check(set(caddy) == {"environment", "volumes", "networks"}, "overlay may only add env, the config mount and a network to caddy")
    check(set(edge.environment_of(caddy)) == {"STUDIO_DOMAIN"}
          and v(edge.environment_of(caddy)["STUDIO_DOMAIN"], "PG17_STUDIO_DOMAIN"), "caddy gets only STUDIO_DOMAIN")
    check(caddy.get("volumes") == ["./Caddyfile.pg17-with-studio:/etc/caddy/Caddyfile:ro"], "caddy config swap differs")
    check(caddy.get("networks") == ["studio"], "caddy joins only the studio network in addition")
    check(all("ports" not in s and "depends_on" not in s for s in services.values()), "overlay must not publish ports or add depends_on")
    net = data.get("networks", {})
    check(set(net) == {"studio"} and net["studio"].get("internal") is True
          and v(net["studio"].get("ipam", {}).get("config", [{}])[0].get("subnet"), "PG17_STUDIO_SUBNET"),
          "overlay network must be the internal studio network on PG17_STUDIO_SUBNET")
    check(data.get("volumes") == {"studio_dataprotection_keys": {}}, "overlay volumes differ")
    check(set(data.get("secrets", {})) == {"studio_connection"}
          and v(data["secrets"]["studio_connection"].get("file"), "PG17_STUDIO_SECRET_DIR", "/studio_connection"),
          "studio_connection must come from PG17_STUDIO_SECRET_DIR")
    return errors


def base_view(data, base_caddyfile):
    """The merged config with the Studio additions removed: must equal what the base validator accepts."""
    base = copy.deepcopy(data)
    for name in STUDIO_SERVICES:
        base.get("services", {}).pop(name, None)
    base.get("networks", {}).pop("studio", None)
    base.get("volumes", {}).pop("studio_dataprotection_keys", None)
    base.get("secrets", {}).pop("studio_connection", None)
    caddy = base.get("services", {}).get("caddy", {})
    edge.environment_of(caddy).pop("STUDIO_DOMAIN", None)
    if isinstance(caddy.get("networks"), dict):
        caddy["networks"].pop("studio", None)
    elif isinstance(caddy.get("networks"), list):
        caddy["networks"] = [n for n in caddy["networks"] if n != "studio"]
    for i, item in enumerate(caddy.get("volumes", [])):
        if isinstance(item, dict) and item.get("target") == "/etc/caddy/Caddyfile":
            caddy["volumes"][i] = f"{base_caddyfile}:/etc/caddy/Caddyfile:ro"
    for svc in base.get("services", {}).values():
        if isinstance(svc.get("depends_on"), dict):
            for dep in svc["depends_on"].values():
                if isinstance(dep, dict):
                    dep.pop("required", None)
    return base


def validate_merged(data, *, studio_caddyfile=STUDIO_CADDYFILE, base_caddyfile=BASE_CADDYFILE):
    """The resolved `docker compose -f compose.pg17-edge.yaml -f compose.pg17-studio.yaml config --format json`."""
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    services = data.get("services", {})
    check(set(services) == {"public-api", "caddy"} | STUDIO_SERVICES,
          "services must be exactly public-api, caddy, studio-api and studio-web")
    errors += ["base edge: " + e for e in edge.validate(base_view(data, base_caddyfile), resolved=True,
                                                        caddyfile=base_caddyfile)]

    nets = data.get("networks", {})
    studio_net = nets.get("studio", {})
    check(set(studio_net) <= {"name", "internal", "ipam"} and studio_net.get("internal") is True
          and studio_net.get("name") == edge.PROJECT + "_studio", "studio network must be internal and project-scoped")
    ipam = studio_net.get("ipam", {})
    configs = ipam.get("config", [])
    check(ipam.get("driver", "default") == "default" and len(configs) == 1 and set(configs[0]) == {"subnet"},
          "studio network needs one subnet with no overrides")
    subnet = configs[0].get("subnet", "") if len(configs) == 1 else ""
    edge_configs = nets.get("edge", {}).get("ipam", {}).get("config", [{}])
    edge_subnet = edge_configs[0].get("subnet", "") if edge_configs else ""
    check(edge.private_subnet(subnet), "studio subnet must be private IPv4 /24 to /28")
    if edge.private_subnet(subnet) and edge.private_subnet(edge_subnet):
        check(not ipaddress.ip_network(subnet).overlaps(ipaddress.ip_network(edge_subnet)),
              "studio subnet must not overlap the edge subnet")

    members = {n: {s for s, svc in services.items() if n in edge.networks_of(svc)} for n in ("edge", "studio", "backend", "public")}
    check(members["edge"] == {"caddy", "public-api"}, "only caddy and public-api may join edge (DEC-1)")
    check(members["studio"] == {"caddy"} | STUDIO_SERVICES, "only caddy and the Studio services may join studio")
    check(members["backend"] == {"public-api", "studio-api"}, "only public-api and studio-api may join backend")
    check(members["public"] == {"caddy"}, "only caddy may join public")

    caddy = services.get("caddy", {})
    cenv = edge.environment_of(caddy)
    studio_domain = cenv.get("STUDIO_DOMAIN", "")
    check(edge.domain(studio_domain) and studio_domain.lower() not in
          {cenv.get("API_DOMAIN", "").lower(), cenv.get("SITE_DOMAIN", "").lower()},
          "Studio domain must be a DNS name distinct from the API and site domains")
    mounts = [m for m in edge.mounts_of(caddy) if m[1] == "/etc/caddy/Caddyfile"]
    check(len(mounts) == 1, "caddy needs exactly one config mount")
    for source, _, read_only in mounts:
        path = Path(source)
        check(path.is_absolute() and path.name == "Caddyfile.pg17-with-studio" and read_only
              and edge.same_file(path, studio_caddyfile), "caddy must mount the validated Caddyfile.pg17-with-studio read-only")

    for name in sorted(STUDIO_SERVICES):
        svc = services.get(name, {})
        allowed = {"image", "restart", "networks", "healthcheck", "logging", "command", "entrypoint"}
        if name == "studio-api":
            allowed |= {"environment", "secrets", "volumes"}
        check(isinstance(svc, dict) and not set(svc) - allowed, name + " has unsupported keys")
        check(svc.get("command") is None and svc.get("entrypoint") is None, name + " command/entrypoint overrides forbidden")
        check(bool(re.fullmatch(IMAGE, svc.get("image", ""))), name + " image must be a digest or a local image ID")
        check(svc.get("restart") == "unless-stopped", name + " restart must be unless-stopped")
        health = svc.get("healthcheck", {})
        check(health.get("test") == HEALTH[name] and not health.get("disable"), name + " healthcheck contract differs")
        check(svc.get("logging") == LOGS, name + " logging must be bounded json-file")
        if isinstance(svc.get("networks"), dict):
            check(all(not c for c in svc["networks"].values()), name + " network overrides are forbidden")

    api = services.get("studio-api", {})
    env = edge.environment_of(api)
    check(set(env) == set(API_ENV) | {"ReverseProxy__TrustedNetwork"}, "studio-api environment keys differ")
    check(all(env.get(k) == v for k, v in API_ENV.items()), "studio-api runtime/secret configuration differs")
    check(env.get("ReverseProxy__TrustedNetwork") == subnet, "TrustedNetwork must equal the studio subnet")
    check(edge.networks_of(api) == {"backend", "studio"}, "studio-api networks must be backend and studio")
    check(edge.networks_of(services.get("studio-web", {})) == {"studio"}, "studio-web network must be studio only")
    check(edge.secrets_of(api) == {"studio_connection"}, "studio-api secret set differs")
    check([(m[0], m[1], m[2]) for m in edge.mounts_of(api)] == [("studio_dataprotection_keys", KEYS_DIR, False)],
          "studio-api mounts only its own DataProtection key volume")
    check(data.get("volumes", {}).get("studio_dataprotection_keys") == {"name": edge.PROJECT + "_studio_dataprotection_keys"},
          "DataProtection key volume must be project-scoped")
    secret = data.get("secrets", {}).get("studio_connection", {})
    file = secret.get("file", "")
    check(set(secret) <= {"file", "name"} and Path(file).is_absolute() and Path(file).name == "studio_connection",
          "studio_connection must be an absolute private file")
    return errors


def validate_environment():
    errors = []
    for key in ("PG17_STUDIO_API_IMAGE", "PG17_STUDIO_WEB_IMAGE"):
        if not re.fullmatch(IMAGE, os.environ.get(key, "")):
            errors.append(key + " must be an immutable digest or a local image ID")
    studio = os.environ.get("PG17_STUDIO_SUBNET", "")
    base = os.environ.get("PG17_EDGE_SUBNET", "")
    if not edge.private_subnet(studio):
        errors.append("PG17_STUDIO_SUBNET must be private IPv4 /24 to /28")
    elif edge.private_subnet(base) and ipaddress.ip_network(studio).overlaps(ipaddress.ip_network(base)):
        errors.append("PG17_STUDIO_SUBNET must not overlap PG17_EDGE_SUBNET")
    domain = os.environ.get("PG17_STUDIO_DOMAIN", "")
    if not edge.domain(domain) or domain.lower() in {os.environ.get(k, "").lower()
                                                    for k in ("PG17_EDGE_API_DOMAIN", "PG17_EDGE_SITE_DOMAIN")}:
        errors.append("PG17_STUDIO_DOMAIN must be a DNS name distinct from the API and site domains")
    if not Path(os.environ.get("PG17_STUDIO_SECRET_DIR", "")).is_absolute():
        errors.append("PG17_STUDIO_SECRET_DIR must be absolute")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--overlay", type=Path, default=ROOT / "compose.pg17-studio.yaml")
    parser.add_argument("--caddyfile", type=Path, default=STUDIO_CADDYFILE)
    parser.add_argument("--base-caddyfile", type=Path, default=BASE_CADDYFILE)
    parser.add_argument("--resolved", type=Path, help="merged `docker compose config --format json` output")
    parser.add_argument("--env", action="store_true")
    args = parser.parse_args()
    errors = caddyfile_errors(args.caddyfile)
    try:
        errors += validate_overlay(yaml.safe_load(args.overlay.read_text()))
        if args.resolved:
            errors += validate_merged(json.loads(args.resolved.read_text()),
                                      studio_caddyfile=args.caddyfile, base_caddyfile=args.base_caddyfile)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, yaml.YAMLError):
        errors.append("invalid Compose structure or unreadable input (values omitted)")
    if args.env:
        errors += validate_environment()
    if errors:
        raise SystemExit("\n".join(errors))
    print("PG17 Studio edge checks passed")


if __name__ == "__main__":
    main()
