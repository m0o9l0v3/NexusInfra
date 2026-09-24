import pathlib
import yaml

compose = yaml.safe_load(pathlib.Path("compose.yaml").read_text())
services = compose["services"]
assert set(services) == {"postgres", "public-api", "admin-api", "studio-api", "studio-web", "caddy"}
assert not any("ports" in services[name] for name in services if name != "caddy")
assert services["caddy"]["ports"] == ["80:80", "443:443"]
assert services["postgres"]["networks"] == ["db"]
assert "db" not in services["caddy"]["networks"]
assert compose["networks"]["db"]["internal"] is True
assert compose["networks"]["edge"]["internal"] is True
assert compose["volumes"]["database"]["external"] is True
assert all(s.get("restart") == "unless-stopped" for s in services.values())
assert all("healthcheck" in s for s in services.values())
print("Topology checks passed")
