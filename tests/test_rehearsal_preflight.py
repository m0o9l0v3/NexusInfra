import os
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/preflight-rehearsal.py"
BASE = {
    "PG18_REHEARSAL_PROJECT": "nexus-pg18-rehearsal-ci",
    "PG18_REHEARSAL_DB_VOLUME": "isolated-db-ci",
    "PG18_REHEARSAL_BACKUP_VOLUME": "isolated-backup-ci",
    "NEXUS_POSTGRES_IMAGE": "example/postgres@sha256:" + "0" * 64,
    "PG18_REHEARSAL_SECRET_DIR": "/private/disposable-secrets",
}


def check(values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), "pg18"], env=os.environ | values, capture_output=True, text=True)


assert check(BASE).returncode == 0
assert check(BASE | {"PG18_REHEARSAL_DB_VOLUME": "nexus_postgres_data"}).returncode != 0
assert check(BASE | {"PG18_REHEARSAL_BACKUP_VOLUME": "isolated-db-ci"}).returncode != 0
assert check(BASE | {"NEXUS_POSTGRES_IMAGE": "postgres:18"}).returncode != 0
print("Rehearsal preflight safety checks passed")

BASE17 = {
    "PG17_RESTORE_PROJECT": "nexus-pg17-restore-ci",
    "PG17_RESTORE_VOLUME": "isolated-pg17-ci",
    "PG17_RESTORE_IMAGE": "example/postgres17@sha256:" + "0" * 64,
    "PG17_DB_NAME": "nexus_admin",
    "PG17_RESTORE_PASSWORD_FILE": "/private/restore-password",
}
result = subprocess.run([sys.executable, str(SCRIPT), "pg17"], env=os.environ | BASE17, capture_output=True, text=True)
assert result.returncode == 0, result.stderr
result = subprocess.run([sys.executable, str(SCRIPT), "pg17"], env=os.environ | BASE17 | {"PG17_RESTORE_VOLUME": "nexus_postgres_data"}, capture_output=True, text=True)
assert result.returncode != 0
