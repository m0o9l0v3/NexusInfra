from pathlib import Path
import yaml

path = Path("compose.pg17-restore.yml")
text = path.read_text()
compose = yaml.safe_load(text)
assert set(compose["services"]) == {"postgres"}
postgres = compose["services"]["postgres"]
assert "ports" not in postgres
assert postgres["networks"] == ["restore"]
assert postgres["volumes"] == ["restore_db:/var/lib/postgresql/data"]
assert compose["networks"]["restore"]["internal"] is True
assert compose["volumes"]["restore_db"]["external"] is True
assert "nexus_postgres_data" not in text
assert "PG17_RESTORE_VOLUME" in compose["volumes"]["restore_db"]["name"]
assert "PG17_RESTORE_PROJECT" in compose["name"]
print("PG17 restore isolation checks passed")
