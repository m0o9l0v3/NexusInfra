from pathlib import Path
import yaml

text = Path("compose.pg18-rehearsal.yml").read_text()
compose = yaml.safe_load(text)
assert set(compose["services"]) == {"postgres"}
postgres = compose["services"]["postgres"]
assert "ports" not in postgres
assert postgres["networks"] == ["rehearsal"]
assert postgres["volumes"] == ["database:/var/lib/postgresql", "backup:/backup"]
assert compose["networks"]["rehearsal"]["internal"] is True
assert all(compose["volumes"][name]["external"] is True for name in ("database", "backup"))
assert "nexus_postgres_data" not in text
assert "PG18_REHEARSAL_DB_VOLUME" in compose["volumes"]["database"]["name"]
assert "PG18_REHEARSAL_BACKUP_VOLUME" in compose["volumes"]["backup"]["name"]
print("PG18 rehearsal isolation checks passed")
