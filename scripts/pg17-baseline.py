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
# Stand-ins for the Studio roles until Infra #8 fixes the real names (NexusStudio deploy/database).
STUDIO_MIGRATOR = "nexus_studio_migrator"
STUDIO_APP = "nexus_studio_app"
DELIVERY_TABLES = ["current_bundle", "participant_bundles"]
TABLE_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
# Contract B revision (E2-5 §3.2): exact table privileges per role; anything not listed must be absent.
DELIVERY_GRANTS = {
    STUDIO_APP: {"participant_bundles": {"SELECT", "INSERT"}, "current_bundle": {"SELECT", "INSERT", "UPDATE"}},
    "nexus_public": {"participant_bundles": {"SELECT"}, "current_bundle": {"SELECT"}},
    "nexus_admin_app": {},
    "nexus_verify_other": {},
}


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


DELIVERY_SQL = ["02-delivery-schema.sql", "03-grant-delivery.sql", "monitor-delivery-size.sql",
                "verification/delivery-reference-ddl.sql"]


def manifest(mobile: Path, migrate_sql: Path) -> dict:
    errors = []
    grant = mobile / "deploy/database/grant-runtime.sql"
    roles = ROOT / "sql/pg17-baseline/01-roles.sql"
    delivery = {name: ROOT / "sql/pg17-baseline" / name for name in DELIVERY_SQL}
    for path in (grant, migrate_sql, roles, *delivery.values()):
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
        "files": {"roles_sql": sha256(roles), "migrate_sql": sha256(migrate_sql), "grant_runtime_sql": sha256(grant),
                  **{name: sha256(path) for name, path in delivery.items()}},
        "visit_logs_contract": "B: INSERT + SELECT(chain_id, created_at, hash) only",
        "delivery_contract": "B rev. Infra #17: nexus_public SELECT on delivery.participant_bundles/current_bundle only",
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

    def psql(self, role, sql, *, db="nexus", host="postgres", password_env=None, input_sql=None, check=True, passthrough=(), variables=None):
        cmd = self.compose + ["exec", "-T"]
        extra = {}
        if password_env:
            cmd += ["-e", "PGPASSWORD"]
            extra["PGPASSWORD"] = password_env
        for key in passthrough:
            cmd += ["-e", key]
        cmd += ["postgres", "psql", "-h", host, "-U", role, "-d", db, "-X", "-At", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate"]
        for key, value in (variables or {}).items():
            cmd += ["-v", f"{key}={value}"]
        if input_sql is None:
            cmd += ["-c", sql]
        return self.run(cmd, stdin=input_sql, extra_env=extra, check=check)

    def ok(self, name):
        self.passed.append(name)
        print("PASS: " + name, flush=True)

    def allowed(self, name, sql, *, role="nexus_public", **kw):
        result = self.psql(role, sql, password_env=self.passwords[role], **kw)
        if result.returncode != 0:
            fail(f"Expected success: {name}\n{result.stdout}")
        self.ok(name)
        return result.stdout.strip()

    def denied(self, name, sql, *, role="nexus_public", sqlstate="42501"):
        result = self.psql(role, sql, password_env=self.passwords[role], check=False)
        if result.returncode == 0 or sqlstate not in result.stdout:
            fail(f"Expected rejection ({sqlstate}): {name}\n{result.stdout}")
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
        self.passwords = {"nexus_public": self.public_password}
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

    def verify_delivery(self):
        """Contract B revision (Infra #17, E2-5 §3.2): delivery schema owned by the Studio migrator."""
        for role in (STUDIO_MIGRATOR, STUDIO_APP, "nexus_verify_other"):
            self.passwords[role] = secrets.token_hex(24)
        p = self.passwords
        # Stand-in for the Studio setup of Infra #8: two LOGIN roles, schema studio owned by the migrator.
        self.psql("nexus", "", host="localhost", input_sql=(
            f"CREATE ROLE {STUDIO_MIGRATOR} LOGIN PASSWORD '{p[STUDIO_MIGRATOR]}';\n"
            f"CREATE ROLE {STUDIO_APP} LOGIN PASSWORD '{p[STUDIO_APP]}';\n"
            f"CREATE ROLE nexus_verify_other LOGIN PASSWORD '{p['nexus_verify_other']}';\n"
            f"GRANT CONNECT ON DATABASE nexus TO {STUDIO_MIGRATOR}, {STUDIO_APP}, nexus_verify_other;\n"
            f"CREATE SCHEMA studio AUTHORIZATION {STUDIO_MIGRATOR};\nREVOKE ALL ON SCHEMA studio FROM PUBLIC;\n"
            f"GRANT USAGE ON SCHEMA studio TO {STUDIO_APP};\n"
            f"SET ROLE {STUDIO_MIGRATOR};\nCREATE TABLE studio.releases (sequence bigint PRIMARY KEY, release_id uuid NOT NULL);\n"
            f"RESET ROLE;\nGRANT SELECT, INSERT ON studio.releases TO {STUDIO_APP};\n"))
        self.ok("stand-in Studio roles and studio schema created (real names come from Infra #8)")
        self.denied("Studio migrator CREATE SCHEMA (no CREATE on the database)", "CREATE SCHEMA forbidden", role=STUDIO_MIGRATOR)

        names = {"studio_migrator": STUDIO_MIGRATOR, "studio_app": STUDIO_APP}
        base = ROOT / "sql/pg17-baseline"
        schema_sql, grant_sql = (base / "02-delivery-schema.sql").read_text(), (base / "03-grant-delivery.sql").read_text()
        exists = "SELECT to_regnamespace('delivery') IS NOT NULL"
        unset = self.psql("nexus", "", host="localhost", input_sql=schema_sql, variables={"studio_app": STUDIO_APP}, check=False)
        if unset.returncode == 0 or self.psql("nexus", exists, host="localhost").stdout.strip() != "f":
            fail("02-delivery-schema.sql must fail without the Studio role variables")
        self.ok("02-delivery-schema.sql refuses to run without the Studio role names")
        self.psql("nexus", "", host="localhost", input_sql=schema_sql, variables=names)
        if self.psql("nexus", "", host="localhost", input_sql=schema_sql, variables=names, check=False).returncode == 0:
            fail("02-delivery-schema.sql must not be re-runnable silently")
        owner = self.psql("nexus", "SELECT nspowner::regrole FROM pg_namespace WHERE nspname = 'delivery'", host="localhost").stdout.strip()
        if owner != STUDIO_MIGRATOR:
            fail(f"delivery must be owned by the Studio migrator, got {owner}")
        self.ok("delivery schema pre-created once and handed to the Studio migrator; a second run is rejected")
        if self.psql("nexus", "", host="localhost", input_sql=grant_sql, variables=names, check=False).returncode == 0:
            fail("03-grant-delivery.sql must refuse a delivery schema without the reviewed tables")
        self.ok("03-grant-delivery.sql refuses a delivery schema without the reviewed tables")
        ddl = (base / "verification/delivery-reference-ddl.sql").read_text()
        self.psql(STUDIO_MIGRATOR, "", password_env=p[STUDIO_MIGRATOR], input_sql=ddl)
        self.ok("reference DDL (E2-5 §3.1, stand-in for NexusStudio#11) created by the Studio migrator")
        self.psql(STUDIO_MIGRATOR, "ALTER TABLE delivery.current_bundle DISABLE TRIGGER current_bundle_monotonic", password_env=p[STUDIO_MIGRATOR])
        if self.psql("nexus", "", host="localhost", input_sql=grant_sql, variables=names, check=False).returncode == 0:
            fail("03-grant-delivery.sql must refuse current_bundle without an enabled monotonic trigger")
        self.psql(STUDIO_MIGRATOR, "ALTER TABLE delivery.current_bundle ENABLE TRIGGER current_bundle_monotonic", password_env=p[STUDIO_MIGRATOR])
        self.ok("03-grant-delivery.sql refuses current_bundle while the monotonic trigger is disabled")
        self.psql("nexus", "", host="localhost", input_sql=grant_sql, variables=names)
        self.psql("nexus", "", host="localhost", input_sql=grant_sql, variables=names)
        self.ok("03-grant-delivery.sql applied, then re-applied idempotently")

        checks = ",".join(f"('{role}','{table}','{priv}')" for role in DELIVERY_GRANTS for table in DELIVERY_TABLES for priv in TABLE_PRIVILEGES)
        granted = self.psql("nexus", f"SELECT r || ':' || t || ':' || pv FROM (VALUES {checks}) AS c(r, t, pv) "
                            "WHERE has_table_privilege(r, 'delivery.' || t, pv) ORDER BY 1", host="localhost").stdout.split()
        expected = sorted(f"{role}:{table}:{priv}" for role, tables in DELIVERY_GRANTS.items() for table, privs in tables.items() for priv in privs)
        if granted != expected:
            fail(f"delivery privileges differ from contract B:\n got {granted}\n want {expected}")
        self.ok("delivery table privileges match the contract B matrix exactly")
        writers = self.psql("nexus", "SELECT string_agg(rolname, ',') FROM pg_roles WHERE NOT rolsuper AND rolname NOT LIKE 'pg\\_%' "
                            f"AND rolname <> '{STUDIO_MIGRATOR}' AND (has_table_privilege(rolname, 'delivery.participant_bundles', 'UPDATE, DELETE, TRUNCATE') "
                            "OR has_table_privilege(rolname, 'delivery.current_bundle', 'DELETE, TRUNCATE'))", host="localhost").stdout.strip()
        if writers:
            fail(f"No role but the owner may UPDATE/DELETE/TRUNCATE the delivery tables: {writers}")
        self.ok("no non-owner role can UPDATE/DELETE/TRUNCATE participant_bundles or DELETE/TRUNCATE current_bundle")

        def bundle(version):
            return ("INSERT INTO delivery.participant_bundles VALUES "
                    f"({version}, gen_random_uuid(), '2.0.0', convert_to('{{\"v\":{version}}}', 'UTF8'), "
                    f"encode(sha256(convert_to('{{\"v\":{version}}}', 'UTF8')), 'hex'), octet_length('{{\"v\":{version}}}'), now())")

        def point(version):
            return ("INSERT INTO delivery.current_bundle (bundle_version, updated_at) VALUES "
                    f"({version}, now()) ON CONFLICT (singleton) DO UPDATE SET bundle_version = EXCLUDED.bundle_version, updated_at = EXCLUDED.updated_at")

        app = {"role": STUDIO_APP}
        self.allowed("Studio runtime: SELECT both delivery tables (empty)", "SELECT count(*) FROM delivery.participant_bundles, delivery.current_bundle", **app)
        self.allowed("Studio runtime: INSERT participant_bundles", bundle(10), **app)
        self.allowed("Studio runtime: first pointer row by INSERT ... ON CONFLICT", point(10), **app)
        self.allowed("Studio runtime: INSERT a later bundle (versions may skip)", bundle(12), **app)
        self.allowed("Studio runtime: advance the pointer by ON CONFLICT DO UPDATE", point(12), **app)
        self.allowed("Studio runtime: INSERT the next bundle", bundle(13), **app)
        self.allowed("Studio runtime: advance the pointer by UPDATE", "UPDATE delivery.current_bundle SET bundle_version = 13, updated_at = now()", **app)
        for name, sql, state in [
            ("Studio runtime: pointer rollback by ON CONFLICT DO UPDATE (trigger)", point(10), "23514"),
            ("Studio runtime: pointer rollback by UPDATE (trigger)", "UPDATE delivery.current_bundle SET bundle_version = 12", "23514"),
            ("Studio runtime: second pointer row (CHECK singleton)", "INSERT INTO delivery.current_bundle VALUES (false, 13, now())", "23514"),
            ("Studio runtime: duplicate pointer INSERT (primary key)", "INSERT INTO delivery.current_bundle (bundle_version, updated_at) VALUES (13, now())", "23505"),
            ("Studio runtime: pointer to a missing bundle (foreign key)", point(99), "23503"),
            ("Studio runtime: UPDATE participant_bundles", "UPDATE delivery.participant_bundles SET schema_version = 'x'", "42501"),
            ("Studio runtime: DELETE participant_bundles", "DELETE FROM delivery.participant_bundles", "42501"),
            ("Studio runtime: TRUNCATE participant_bundles", "TRUNCATE delivery.participant_bundles", "42501"),
            ("Studio runtime: DELETE current_bundle", "DELETE FROM delivery.current_bundle", "42501"),
            ("Studio runtime: TRUNCATE current_bundle", "TRUNCATE delivery.current_bundle", "42501"),
            ("Studio runtime: CREATE TABLE in delivery", "CREATE TABLE delivery.forbidden (id integer)", "42501"),
            ("Studio runtime: ALTER TABLE participant_bundles", "ALTER TABLE delivery.participant_bundles ADD COLUMN forbidden text", "42501"),
            ("Studio runtime: disable the monotonic trigger", "ALTER TABLE delivery.current_bundle DISABLE TRIGGER current_bundle_monotonic", "42501"),
            ("Studio runtime: SELECT public.spots", "SELECT * FROM public.spots", "42501"),
            (f"Studio runtime: SET ROLE {STUDIO_MIGRATOR}", f"SET ROLE {STUDIO_MIGRATOR}", "42501"),
        ]:
            self.denied(name, sql, role=STUDIO_APP, sqlstate=state)
        self.denied("Studio migrator (owner): pointer rollback (trigger binds the owner too)",
                    "UPDATE delivery.current_bundle SET bundle_version = 10", role=STUDIO_MIGRATOR, sqlstate="23514")

        if self.allowed("nexus_public: SELECT delivery.current_bundle", "SELECT bundle_version FROM delivery.current_bundle WHERE singleton") != "13":
            fail("nexus_public must read the current pointer")
        if self.allowed("nexus_public: SELECT delivery.participant_bundles (public-api query)",
                        "SELECT encode(sha256(payload), 'hex') = rtrim(sha256) FROM delivery.participant_bundles WHERE bundle_version = 13") != "t":
            fail("Stored payload bytes must match the stored SHA-256")
        if self.allowed("nexus_public: DatabaseReadiness predicate still passes", READINESS_SQL) != "f":
            fail("nexus_public must still pass the readiness predicate")
        for name, sql in [
            ("INSERT participant_bundles", bundle(14)),
            ("UPDATE participant_bundles", "UPDATE delivery.participant_bundles SET schema_version = 'x'"),
            ("DELETE participant_bundles", "DELETE FROM delivery.participant_bundles"),
            ("TRUNCATE participant_bundles", "TRUNCATE delivery.participant_bundles"),
            ("INSERT current_bundle", point(13)),
            ("UPDATE current_bundle (pointer rollback)", "UPDATE delivery.current_bundle SET bundle_version = 10"),
            ("DELETE current_bundle", "DELETE FROM delivery.current_bundle"),
            ("TRUNCATE current_bundle", "TRUNCATE delivery.current_bundle"),
            ("CREATE TABLE in delivery", "CREATE TABLE delivery.forbidden (id integer)"),
            ("SELECT studio.releases", "SELECT * FROM studio.releases"),
            (f"SET ROLE {STUDIO_MIGRATOR}", f"SET ROLE {STUDIO_MIGRATOR}"),
        ]:
            self.denied("nexus_public: " + name, sql)
        for table in DELIVERY_TABLES:
            self.denied(f"other role: SELECT delivery.{table}", f"SELECT * FROM delivery.{table}", role="nexus_verify_other")

        monitor = (base / "monitor-delivery-size.sql").read_text()
        for variables, want in (({}, "f"), ({"max_rows": "2"}, "t"), ({"max_bytes": "1"}, "t")):
            line = self.psql("nexus_public", "", password_env=p["nexus_public"], input_sql=monitor, variables=variables).stdout.splitlines()
            row = [v for v in line if v.count("|") == 3]
            if len(row) != 1 or row[0].split("|")[0] != "3" or row[0].split("|")[3] != want:
                fail(f"monitor-delivery-size.sql reported {line} with {variables}, expected 3 rows and review_required={want}")
        self.ok("monitor-delivery-size.sql runs as nexus_public and flags the review thresholds")

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
                rehearsal.verify_delivery()
                print(f"PASS: {len(rehearsal.passed)} baseline checks (disposable only; nothing was applied to production)")
            finally:
                rehearsal.cleanup()
    else:
        fail("Usage: pg17-baseline.py preflight|manifest|run")


if __name__ == "__main__":
    main(sys.argv)
