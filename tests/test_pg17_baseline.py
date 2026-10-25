"""Static guards for the isolated PG17 baseline. No Docker, no database."""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pg17_baseline", ROOT / "scripts/pg17-baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)

DIGEST = "example/postgres17@sha256:" + "0" * 64
GOOD_ENV = {
    "PG17_BASELINE_PROJECT": "nexus-pg17-baseline-ci", "PG17_BASELINE_VOLUME": "nexus-pg17-baseline-ci-db",
    "PG17_BASELINE_IMAGE": DIGEST, "PG17_BASELINE_SECRET_DIR": "/private/baseline-secrets",
}


def rejected(label, function, *args):
    try:
        function(*args)
    except SystemExit as error:
        assert str(error) not in ("", "0", "None"), label
        return
    raise AssertionError("Expected rejection: " + label)


def settings(**changes):
    return dict(GOOD_ENV, **changes)


def good_sql():
    lines = ["\\set ON_ERROR_STOP on", "SET ROLE nexus_owner;", "SELECT pg_advisory_lock(1);",
             "RAISE EXCEPTION 'Existing public schema detected.';"]
    for migration in baseline.EXPECTED_MIGRATIONS:
        lines.append(f"INSERT INTO \"__EFMigrationsHistory\" (\"MigrationId\", \"ProductVersion\")\n    VALUES ('{migration}', '8.0.6');")
    return "\n".join(lines)


# --- isolation settings
assert baseline.check_settings(GOOD_ENV)["volume"] == "nexus-pg17-baseline-ci-db"
rejected("live volume", baseline.check_settings, settings(PG17_BASELINE_VOLUME="nexus_postgres_data"))
rejected("production volume", baseline.check_settings, settings(NEXUS_DB_VOLUME="nexus-pg17-baseline-ci-db"))
rejected("volume prefix", baseline.check_settings, settings(PG17_BASELINE_VOLUME="scratch-db"))
rejected("live project", baseline.check_settings, settings(PG17_BASELINE_PROJECT="nexus"))
rejected("bare prefix project", baseline.check_settings, settings(PG17_BASELINE_PROJECT="nexus-pg17-baseline-"))
rejected("tag only image", baseline.check_settings, settings(PG17_BASELINE_IMAGE="postgres:17-alpine"))
rejected("relative secret dir", baseline.check_settings, settings(PG17_BASELINE_SECRET_DIR="secrets"))
rejected("missing project", baseline.check_settings, {k: v for k, v in GOOD_ENV.items() if k != "PG17_BASELINE_PROJECT"})
cli = subprocess.run([sys.executable, str(ROOT / "scripts/pg17-baseline.py"), "preflight"], env=dict(os.environ, **GOOD_ENV), capture_output=True, text=True)
assert cli.returncode == 0, cli.stderr
cli = subprocess.run([sys.executable, str(ROOT / "scripts/pg17-baseline.py"), "preflight"], env=dict(os.environ, **settings(PG17_BASELINE_VOLUME="nexus_postgres_data")), capture_output=True, text=True)
assert cli.returncode != 0

# --- generated migration SQL review
assert baseline.check_migration_sql(good_sql()) == []
assert baseline.check_migration_sql(good_sql() + "\nCREATE ROLE x;")
assert baseline.check_migration_sql(good_sql() + "\nGRANT SELECT ON spots TO nexus_public;")
assert baseline.check_migration_sql(good_sql() + "\nCREATE ROLE nexus_public_readonly;")
assert baseline.check_migration_sql(good_sql() + "\nDROP TABLE spots;")
assert baseline.check_migration_sql(good_sql() + "\nINSERT INTO \"__EFMigrationsHistory\" (\"MigrationId\", \"ProductVersion\")\n    VALUES ('20261001000000_Extra', '8.0.6');")
assert baseline.check_migration_sql(good_sql().replace("SET ROLE nexus_owner;", "SELECT 1;"))
assert baseline.check_migration_sql(good_sql().replace("Existing public schema detected.", "ok"))

# --- public-api must not migrate or seed on startup
with tempfile.TemporaryDirectory() as tmp:
    mobile = Path(tmp)
    (mobile / "apps/public-api").mkdir(parents=True)
    program = mobile / "apps/public-api/Program.cs"
    program.write_text("await DatabaseReadiness.CheckAsync(db, publicApi: true);\napp.Run();\n")
    assert baseline.check_public_api_startup(mobile) == []
    program.write_text("await DatabaseReadiness.CheckAsync(db, publicApi: true);\nawait db.Database.MigrateAsync();\n")
    assert baseline.check_public_api_startup(mobile)
    program.write_text("db.Database.EnsureCreated();\nawait DatabaseReadiness.CheckAsync(db, publicApi: true);\n")
    assert baseline.check_public_api_startup(mobile)
    program.write_text("app.Run();\n")
    assert baseline.check_public_api_startup(mobile)

# --- role bootstrap SQL
roles = (ROOT / "sql/pg17-baseline/01-roles.sql").read_text()
code = "\n".join(line for line in roles.splitlines() if not line.lstrip().startswith("--"))
assert "\\getenv public_password NEXUS_PUBLIC_PASSWORD" in code and "PASSWORD :'public_password'" in code
assert not re.search(r"PASSWORD\s+'", code), "literal password"
role_statements = " ".join(re.findall(r"CREATE ROLE[^;]*;", code))
assert role_statements.count("CREATE ROLE") == 3
assert not re.search(r"SUPERUSER|CREATEDB|CREATEROLE|BYPASSRLS|REPLICATION|INHERIT", role_statements, re.IGNORECASE)
assert not re.search(r"WITH ADMIN|GRANT\s+nexus_owner\s+TO", code, re.IGNORECASE)
assert "nexus_public_readonly" not in code and "nexus_postgres_data" not in code
assert re.search(r"CREATE ROLE nexus_admin_app NOLOGIN", code) and re.search(r"CREATE ROLE nexus_owner NOLOGIN", code)
assert "CREATE ROLE nexus_migrator" not in code
assert "170000 AND 179999" in code and "ON_ERROR_STOP" in code

# --- delivery schema and grants (contract B revision, Infra #17)
def sql_code(name):
    text = (ROOT / "sql/pg17-baseline" / name).read_text()
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


schema_sql = sql_code("02-delivery-schema.sql")
assert 'CREATE SCHEMA delivery AUTHORIZATION :"studio_migrator";' in schema_sql
assert "REVOKE ALL ON SCHEMA delivery FROM PUBLIC;" in schema_sql
assert 'GRANT USAGE ON SCHEMA delivery TO :"studio_app", nexus_public;' in schema_sql
assert not re.search(r"GRANT\s+(CREATE|ALL)[^;]*ON\s+DATABASE", schema_sql, re.IGNORECASE), "migrator must not get CREATE on the database"
assert not re.search(r"CREATE ROLE|ALTER ROLE|DEFAULT PRIVILEGES|\bstudio\.", schema_sql, re.IGNORECASE)
assert "170000 AND 179999" in schema_sql and "ON_ERROR_STOP" in schema_sql and "Schema delivery already exists" in schema_sql

grant_sql = sql_code("03-grant-delivery.sql")
grants = re.findall(r"GRANT ([^;]+);", grant_sql)
assert sorted(grants) == sorted([
    'SELECT, INSERT ON delivery.participant_bundles TO :"studio_app"',
    'SELECT, INSERT, UPDATE ON delivery.current_bundle TO :"studio_app"',
    "SELECT ON delivery.participant_bundles, delivery.current_bundle TO nexus_public",
]), grants
assert not re.search(r"DEFAULT PRIVILEGES|ON ALL TABLES[^;]*\bTO\b|\bstudio\.", grant_sql, re.IGNORECASE)
assert "tgtype & 19 = 19" in grant_sql and "170000 AND 179999" in grant_sql

for name in ("02-delivery-schema.sql", "03-grant-delivery.sql"):
    code = sql_code(name)
    assert code.count("RAISE EXCEPTION 'Set -v") == 2 and "\\quit" not in code, name

monitor_sql = sql_code("monitor-delivery-size.sql")
assert "BEGIN READ ONLY;" in monitor_sql and "\\set max_bytes 1073741824" in monitor_sql and "\\set max_rows 500" in monitor_sql
assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|GRANT|CREATE)\b", monitor_sql)

reference = (ROOT / "sql/pg17-baseline/verification/delivery-reference-ddl.sql").read_text()
assert "VERIFICATION ONLY" in reference and "BEFORE UPDATE ON delivery.current_bundle" in reference
assert "payload         bytea" in reference and "CHECK (singleton)" in reference
assert set(baseline.DELIVERY_GRANTS["nexus_public"]) == set(baseline.DELIVERY_TABLES)
assert all(privs == {"SELECT"} for privs in baseline.DELIVERY_GRANTS["nexus_public"].values())

# --- disposable compose file
compose = yaml.safe_load((ROOT / "compose.pg17-baseline.yml").read_text())
assert set(compose["services"]) == {"postgres"}
postgres = compose["services"]["postgres"]
assert "ports" not in postgres and "expose" not in postgres
assert postgres["volumes"] == ["baseline_db:/var/lib/postgresql/data"]
assert compose["networks"]["baseline"]["internal"] is True
assert compose["volumes"]["baseline_db"]["external"] is True
assert "PG17_BASELINE_VOLUME" in compose["volumes"]["baseline_db"]["name"]
assert "PG17_BASELINE_PROJECT" in compose["name"]
assert "nexus_postgres_data" not in (ROOT / "compose.pg17-baseline.yml").read_text()
print("PG17 baseline tests passed")
