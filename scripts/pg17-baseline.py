"""Isolated PostgreSQL 17 baseline: review package and rehearsal for the public-api runtime role.

Never touches the live database. Subcommands:
  preflight  static checks of the isolation settings (no Docker access)
  manifest   verify a NexusMobile checkout + generated migration SQL and print a review manifest
  run        build a disposable PG17 from an empty volume, apply roles/migration/grants, verify
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE_VOLUME = "nexus_postgres_data"
LIVE_CONTAINER = "nexus-postgres-1"
LIVE_PROJECT = "nexus"
PREFIX = "nexus-pg17-baseline-"
EXPECTED_MIGRATIONS = ["20260905120000_AddMapDatasets", "20260919120000_AddInitialPostgreSqlSchema"]
APP_TABLES = ["departments", "events", "exhibits", "issued_tokens", "map_datasets", "oc_days", "one_time_login_codes",
              "open_campus_timeslots", "qr_issues", "revoked_jti", "spots", "timeslot_exhibits", "visit_logs"]
ADMIN_ONLY_TABLES = [t for t in APP_TABLES if t not in ("spots", "events", "visit_logs")]
# Mirrors apps/admin-api/Data/DatabaseReadiness.cs (read-only startup gate).
READINESS_SQL = ("SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls "
                 "OR pg_has_role(current_user, 'nexus_owner', 'MEMBER') "
                 "OR has_schema_privilege(current_user, 'public', 'CREATE') "
                 "FROM pg_roles r WHERE r.rolname = current_user")


class Failure(SystemExit):
    pass


def fail(message: str) -> None:
    raise Failure(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- preflight


def check_settings(env: dict) -> dict:
    def need(key: str) -> str:
        value = env.get(key, "")
        if not value:
            fail(f"{key} is required")
        return value

    project, volume = need("PG17_BASELINE_PROJECT"), need("PG17_BASELINE_VOLUME")
    image, secret_dir = need("PG17_BASELINE_IMAGE"), need("PG17_BASELINE_SECRET_DIR")
    if project == LIVE_PROJECT or not project.startswith(PREFIX) or len(project) == len(PREFIX):
        fail(f"Project name must begin with {PREFIX} and have a unique suffix")
    if volume in {LIVE_VOLUME, env.get("NEXUS_DB_VOLUME", ""), env.get("NEXUS_BACKUP_VOLUME", "")} or not volume.startswith(PREFIX):
        fail(f"Volume must begin with {PREFIX} and differ from the live and production volumes")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image):
        fail("Baseline image must be pinned by digest")
    if not secret_dir.startswith("/"):
        fail("Secret directory must be an absolute path")
    return {"project": project, "volume": volume, "image": image, "secret_dir": Path(secret_dir)}


# ---------------------------------------------------------------- manifest


def migration_ids_in_source(mobile: Path) -> list:
    """Migrations EF can discover: classes carrying both [DbContext] and [Migration("id")]."""
    found = []
    for path in sorted((mobile / "apps/admin-api/Migrations").glob("*.cs")):
        text = path.read_text()
        match = re.search(r'\[Migration\("([^"]+)"\)\]', text)
        if match and "[DbContext(" in text:
            found.append(match.group(1))
    return found


def check_migration_sql(sql: str) -> list:
    errors = []
    ids = sorted(set(re.findall(r"INSERT INTO \"__EFMigrationsHistory\" \(\"MigrationId\", \"ProductVersion\"\)\s+VALUES \('([^']+)'", sql)))
    if ids != sorted(EXPECTED_MIGRATIONS):
        errors.append(f"Generated SQL migrations differ from the reviewed set: {ids}")
    first = [line for line in sql.splitlines() if line.strip()][:2]
    if first != ["\\set ON_ERROR_STOP on", "SET ROLE nexus_owner;"]:
        errors.append("Generated SQL must start with ON_ERROR_STOP and SET ROLE nexus_owner")
    for pattern in (r"\bCREATE\s+ROLE\b", r"\bALTER\s+ROLE\b", r"\bDROP\s+(TABLE|SCHEMA|DATABASE|ROLE)\b",
                    r"\bTRUNCATE\b", r"\bGRANT\b", r"\bREVOKE\b", r"nexus_public_readonly", r"\bCOPY\b"):
        if re.search(pattern, sql, re.IGNORECASE):
            errors.append(f"Generated SQL contains a forbidden statement: {pattern}")
    if "Existing public schema detected" not in sql or "pg_advisory_lock" not in sql:
        errors.append("Generated SQL lacks the legacy-schema guard or the advisory lock")
    return errors


def check_public_api_startup(mobile: Path) -> list:
    errors = []
    pattern = re.compile(r"\bMigrate(Async)?\s*\(|EnsureCreated|DbSeeder|\.SeedAsync")
    for path in (mobile / "apps/public-api").rglob("*.cs"):
        if "/obj/" in str(path) or "/bin/" in str(path):
            continue
        if pattern.search(path.read_text()):
            errors.append(f"public-api source runs migration or seeding: {path.relative_to(mobile)}")
    program = (mobile / "apps/public-api/Program.cs").read_text()
    if "DatabaseReadiness.CheckAsync" not in program:
        errors.append("public-api Program.cs no longer runs the read-only readiness gate")
    return errors


def manifest(mobile: Path, migrate_sql: Path) -> dict:
    errors = []
    grant = mobile / "deploy/database/grant-runtime.sql"
    roles = ROOT / "sql/pg17-baseline/01-roles.sql"
    for path in (grant, migrate_sql, roles):
        if not path.is_file():
            fail(f"Missing review input: {path}")
    discoverable = migration_ids_in_source(mobile)
    if discoverable != EXPECTED_MIGRATIONS:
        errors.append(f"Discoverable migrations differ from the reviewed chain: {discoverable}")
    errors += check_migration_sql(migrate_sql.read_text())
    errors += check_public_api_startup(mobile)
    grant_text = grant.read_text()
    if "nexus_public_readonly" in grant_text or "GRANT SELECT ON ALL TABLES" in grant_text:
        errors.append("grant-runtime.sql contains the legacy broad read role contract")
    if not re.search(r"GRANT SELECT \(chain_id, created_at, hash\), INSERT ON visit_logs TO nexus_public;", grant_text):
        errors.append("grant-runtime.sql no longer matches the reviewed visit_logs contract (B)")
    commit = subprocess.run(["git", "-C", str(mobile), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if errors:
        fail("\n".join(errors))
    return {
        "nexus_mobile_commit": commit,
        "migrations": discoverable,
        "files": {"roles_sql": sha256(roles), "migrate_sql": sha256(migrate_sql), "grant_runtime_sql": sha256(grant)},
        "visit_logs_contract": "B: INSERT + SELECT(chain_id, created_at, hash) only",
    }


# ---------------------------------------------------------------- rehearsal


class Rehearsal:
    def __init__(self, cfg: dict, mobile: Path, migrate_sql: Path):
        self.cfg, self.mobile, self.migrate_sql = cfg, mobile, migrate_sql
        self.docker = shlex.split(os.environ.get("DOCKER", "docker"))
        probe = subprocess.run(self.docker + ["compose", "version"], capture_output=True)
        self.compose = self.docker + ["compose"] if probe.returncode == 0 else ["docker-compose"]
        self.compose += ["-p", cfg["project"], "-f", str(ROOT / "compose.pg17-baseline.yml")]
        self.env = dict(os.environ, PG17_BASELINE_PROJECT=cfg["project"], PG17_BASELINE_VOLUME=cfg["volume"],
                        PG17_BASELINE_IMAGE=cfg["image"], PG17_BASELINE_PASSWORD_FILE=str(cfg["secret_dir"] / "baseline_password"))
        self.passed = []
        self.started = False

    def run(self, args, *, stdin=None, extra_env=None, check=True):
        result = subprocess.run([str(a) for a in args], env=dict(self.env, **(extra_env or {})), input=stdin,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if check and result.returncode != 0:
            fail(f"Command failed ({result.returncode}): {args[0]} {args[1] if len(args) > 1 else ''}\n{result.stdout}")
        return result

    def psql(self, role, sql, *, db="nexus", host="postgres", password_env=None, input_sql=None, check=True, passthrough=()):
        cmd = self.compose + ["exec", "-T"]
        extra = {}
        if password_env:
            cmd += ["-e", "PGPASSWORD"]
            extra["PGPASSWORD"] = password_env
        for key in passthrough:
            cmd += ["-e", key]
        cmd += ["postgres", "psql", "-h", host, "-U", role, "-d", db, "-X", "-At", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate"]
        if input_sql is None:
            cmd += ["-c", sql]
        return self.run(cmd, stdin=input_sql, extra_env=extra, check=check)

    def ok(self, name):
        self.passed.append(name)
        print("PASS: " + name, flush=True)

    def allowed(self, name, sql, **kw):
        result = self.psql("nexus_public", sql, password_env=self.public_password, **kw)
        if result.returncode != 0:
            fail(f"Expected success: {name}\n{result.stdout}")
        self.ok(name)
        return result.stdout.strip()

    def denied(self, name, sql):
        result = self.psql("nexus_public", sql, password_env=self.public_password, check=False)
        if result.returncode == 0 or "42501" not in result.stdout:
            fail(f"Expected permission denied (42501): {name}\n{result.stdout}")
        self.ok("denied: " + name)

    def setup(self):
        local = subprocess.run(self.docker + ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], capture_output=True, text=True)
        if local.returncode != 0 or not local.stdout.strip().startswith("unix://"):
            fail("Only a local Unix-socket Docker engine is supported")
        names = subprocess.run(self.docker + ["ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
        volumes = subprocess.run(self.docker + ["volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True).stdout.split()
        if LIVE_CONTAINER in names or LIVE_VOLUME in volumes:
            fail("The live PostgreSQL container or volume exists on this engine; run only on an isolated machine")
        occupied = subprocess.run(self.docker + ["ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={self.cfg['project']}"],
                                  capture_output=True, text=True).stdout.split()
        if occupied:
            fail("Baseline project already has containers; use a new project name (nothing was touched)")
        if self.cfg["volume"] in volumes:
            fail("Baseline volume already exists; use a new empty volume name")
        secret_dir = self.cfg["secret_dir"]
        if secret_dir.exists():
            fail("Secret directory already exists; use a new path")
        secret_dir.mkdir(mode=0o700, parents=True)
        self.public_password = secrets.token_hex(24)
        for name, value in (("baseline_password", secrets.token_hex(24)), ("public_password", self.public_password)):
            path = secret_dir / name
            path.write_text(value)
            path.chmod(0o600)
        self.run(self.docker + ["volume", "create", self.cfg["volume"]])
        self.started = True
        self.run(self.compose + ["up", "-d", "--wait", "--wait-timeout", "120", "postgres"])
        version = self.psql("nexus", "SHOW server_version", host="localhost").stdout.strip()
        if not version.startswith("17."):
            fail(f"Baseline must be PostgreSQL 17, got {version}")
        databases = self.psql("nexus", "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE NOT datistemplate", host="localhost").stdout.strip()
        tables = self.psql("nexus", "SELECT count(*) FROM pg_tables WHERE schemaname='public'", host="localhost").stdout.strip()
        if databases != "nexus,postgres" or tables != "0":
            fail("Baseline does not match the inventoried live shape (databases nexus,postgres; no user tables)")
        self.ok(f"empty disposable PG{version} matches the inventoried live shape")

    def apply(self):
        sql = (ROOT / "sql/pg17-baseline/01-roles.sql").read_text()
        env = {"NEXUS_PUBLIC_PASSWORD": self.public_password}
        cmd = self.compose + ["exec", "-T", "-e", "NEXUS_PUBLIC_PASSWORD", "postgres", "psql", "-h", "localhost", "-U", "nexus", "-d", "nexus", "-X", "-v", "ON_ERROR_STOP=1"]
        self.run(cmd, stdin=sql, extra_env=env)
        again = self.run(cmd, stdin=sql, extra_env=env, check=False)
        if again.returncode == 0:
            fail("Role bootstrap must not be re-runnable silently")
        self.ok("role bootstrap applied once; a second run is rejected")
        readiness = self.psql("nexus", READINESS_SQL, host="localhost").stdout.strip()
        if readiness != "t":
            fail("The bootstrap superuser should be reported as unfit for runtime")
        self.ok("superuser 'nexus' fails the runtime readiness predicate (NO-GO for the live role)")
        self.guard_legacy()
        migrate = self.migrate_sql.read_text()
        self.psql("nexus", "", host="localhost", input_sql=migrate)
        self.psql("nexus", "", host="localhost", input_sql=migrate)
        self.ok("reviewed migration SQL applied, then replayed idempotently")
        grants = "SET ROLE nexus_owner;\n" + (self.mobile / "deploy/database/grant-runtime.sql").read_text()
        self.psql("nexus", "", host="localhost", input_sql=grants)
        self.ok("grant-runtime.sql applied as nexus_owner")

    def guard_legacy(self):
        self.psql("nexus", "CREATE DATABASE nexus_legacy_guard OWNER nexus_owner", db="postgres", host="localhost")
        self.psql("nexus", "CREATE TABLE existing_data(value text); INSERT INTO existing_data VALUES ('keep')", db="nexus_legacy_guard", host="localhost")
        rejected = self.psql("nexus", "", db="nexus_legacy_guard", host="localhost", input_sql=self.migrate_sql.read_text(), check=False)
        survived = self.psql("nexus", "SELECT value FROM existing_data", db="nexus_legacy_guard", host="localhost").stdout.strip()
        if rejected.returncode == 0 or survived != "keep":
            fail("Migration SQL must refuse a database with unknown tables and leave it untouched")
        self.ok("migration SQL refuses a database with unknown tables, rows preserved")

    def verify(self):
        history = self.psql("nexus", 'SELECT string_agg("MigrationId", \',\' ORDER BY "MigrationId") FROM public."__EFMigrationsHistory"', host="localhost").stdout.strip()
        if history != ",".join(sorted(EXPECTED_MIGRATIONS)):
            fail("Migration history differs from the reviewed chain")
        missing = self.psql("nexus", "SELECT count(*) FROM unnest(ARRAY['spots','events','visit_logs']) t WHERE to_regclass('public.' || t) IS NULL", host="localhost").stdout.strip()
        if missing != "0":
            fail("Required tables are missing")
        self.ok("history equals the two reviewed migrations; spots/events/visit_logs exist")
        if self.allowed("DatabaseReadiness predicate for nexus_public", READINESS_SQL) != "f":
            fail("nexus_public must pass the readiness predicate")
        self.psql("nexus", "INSERT INTO spots (id, code, name, description, is_published, updated_at) VALUES "
                  "(gen_random_uuid(), 'v-visible', 'v', 'v', true, now()), (gen_random_uuid(), 'v-hidden', 'h', 'h', false, now());"
                  "INSERT INTO events (id, title, starts_at, ends_at, is_published) VALUES "
                  "(gen_random_uuid(), 'visible', now(), now(), true), (gen_random_uuid(), 'hidden', now(), now(), false)", host="localhost")
        if self.allowed("SELECT spots/events limited to published rows", "SELECT (SELECT count(*) FROM spots) || ',' || (SELECT count(*) FROM events)") != "1,1":
            fail("Row level security must hide unpublished rows")
        self.allowed("SELECT __EFMigrationsHistory", 'SELECT count(*) FROM "__EFMigrationsHistory"')
        insert = ("INSERT INTO visit_logs (id, session_id, event_type, occurred_at, created_at, chain_id, hash, prev_hash, hash_alg) "
                  "VALUES (gen_random_uuid(), 's', 'spot_view', now(), now(), 'c1', 'h1', '', 'test')")
        self.allowed("INSERT visit_logs (the only permitted write)", insert)
        self.allowed("SELECT visit_logs(chain_id, created_at, hash) for the audit chain", "SELECT hash FROM visit_logs WHERE chain_id='c1' ORDER BY created_at DESC LIMIT 1")
        for table in ADMIN_ONLY_TABLES:
            self.denied(f"SELECT {table}", f"SELECT * FROM {table}")
        self.psql("nexus", "SET ROLE nexus_owner; CREATE TABLE verification_future (id integer)", host="localhost")
        self.denied("SELECT future table (no default privileges)", "SELECT * FROM verification_future")
        for name, sql in [
            ("SELECT visit_logs.session_id", "SELECT session_id FROM visit_logs"),
            ("SELECT visit_logs.*", "SELECT * FROM visit_logs"),
            ("INSERT spots", "INSERT INTO spots (id, code, name, description, updated_at) VALUES (gen_random_uuid(), 'x', 'x', 'x', now())"),
            ("INSERT events", "INSERT INTO events (id, title, starts_at, ends_at) VALUES (gen_random_uuid(), 'x', now(), now())"),
            ("UPDATE spots", "UPDATE spots SET name = 'x'"), ("UPDATE events", "UPDATE events SET title = 'x'"),
            ("UPDATE visit_logs", "UPDATE visit_logs SET hash = 'x'"),
            ("DELETE spots", "DELETE FROM spots"), ("DELETE events", "DELETE FROM events"), ("DELETE visit_logs", "DELETE FROM visit_logs"),
            ("TRUNCATE visit_logs", "TRUNCATE visit_logs"),
            ("INSERT migration history", 'INSERT INTO "__EFMigrationsHistory" ("MigrationId", "ProductVersion") VALUES (\'x\', \'x\')'),
            ("DELETE migration history", 'DELETE FROM "__EFMigrationsHistory"'),
            ("CREATE TABLE", "CREATE TABLE forbidden (id integer)"), ("CREATE SCHEMA", "CREATE SCHEMA forbidden"),
            ("ALTER TABLE", "ALTER TABLE visit_logs ADD COLUMN forbidden text"), ("DROP TABLE", "DROP TABLE visit_logs"),
            ("CREATE ROLE", "CREATE ROLE forbidden"), ("disable row level security", "ALTER TABLE spots DISABLE ROW LEVEL SECURITY"),
            ("SET ROLE nexus_owner", "SET ROLE nexus_owner"), ("SET ROLE nexus", "SET ROLE nexus"),
            ("COPY TO PROGRAM", "COPY (SELECT 1) TO PROGRAM 'true'"),
        ]:
            self.denied(name, sql)
        wrong = self.psql("nexus_public", "SELECT 1", password_env="not-the-password", check=False)
        if wrong.returncode == 0 or "password authentication failed" not in wrong.stdout:
            fail("A wrong password must be rejected")
        self.ok("denied: wrong password for nexus_public")
        migrate = self.migrate_sql.read_text()
        self.psql("nexus", "", host="localhost", input_sql=migrate)
        self.ok("migration replay after grants keeps history and privileges intact")
        count = self.psql("nexus", 'SELECT count(*) FROM public."__EFMigrationsHistory"', host="localhost").stdout.strip()
        if count != "2":
            fail("Migration replay changed the history")

    def cleanup(self):
        if not self.started:
            print("Nothing was started by this run; no containers were touched.")
            return
        self.run(self.compose + ["down", "--remove-orphans"], check=False)
        print(f"Containers removed. Remove the disposable volume yourself: docker volume rm {self.cfg['volume']}")
        print(f"Private secrets remain in {self.cfg['secret_dir']}; delete them when done.")


def main(argv: list) -> None:
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "preflight":
        check_settings(os.environ)
        print("PG17 baseline isolation settings passed static preflight")
    elif mode in ("manifest", "run"):
        mobile = Path(os.environ.get("NEXUS_MOBILE_DIR", ""))
        migrate_sql = Path(os.environ.get("PG17_BASELINE_MIGRATE_SQL", ""))
        if not mobile.is_dir() or not migrate_sql.is_file():
            fail("Set NEXUS_MOBILE_DIR (NexusMobile checkout) and PG17_BASELINE_MIGRATE_SQL (output of tools/database script)")
        review = manifest(mobile, migrate_sql)
        print(json.dumps(review, indent=2, ensure_ascii=False))
        if mode == "run":
            rehearsal = Rehearsal(check_settings(os.environ), mobile, migrate_sql)
            try:
                rehearsal.setup()
                rehearsal.apply()
                rehearsal.verify()
                print(f"PASS: {len(rehearsal.passed)} baseline checks (disposable only; nothing was applied to production)")
            finally:
                rehearsal.cleanup()
    else:
        fail("Usage: pg17-baseline.py preflight|manifest|run")


if __name__ == "__main__":
    main(sys.argv)
