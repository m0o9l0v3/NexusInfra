"""Release configuration must never mount the live PostgreSQL 17 source volume."""
import os
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/validate-release.py"
DIGEST = "example/image@sha256:" + "0" * 64
BASE = {
    "NEXUS_POSTGRES_IMAGE": DIGEST,
    "PUBLIC_API_IMAGE": DIGEST,
    "ADMIN_API_IMAGE": DIGEST,
    "STUDIO_API_IMAGE": DIGEST,
    "STUDIO_WEB_IMAGE": DIGEST,
    "CADDY_IMAGE": DIGEST,
    "NEXUS_DB_VOLUME": "reviewed-db-volume",
    "NEXUS_BACKUP_VOLUME": "reviewed-backup-volume",
    "NEXUS_EDGE_SUBNET": "172.30.250.0/28",
    "NEXUS_SECRET_DIR": "/private/secrets",
    "NEXUS_REPO_DIR": "/private/nexus",
}


def validate(overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=os.environ | BASE | (overrides or {}),
        capture_output=True,
        text=True,
    )


def test_distinct_disposable_volumes_are_accepted() -> None:
    result = validate()
    assert result.returncode == 0, result.stderr


def test_live_postgres17_volume_is_rejected_as_database_volume() -> None:
    result = validate({"NEXUS_DB_VOLUME": "nexus_postgres_data"})
    assert result.returncode != 0
    assert "live PostgreSQL 17 source volume" in result.stderr


def test_live_postgres17_volume_is_rejected_as_backup_volume() -> None:
    result = validate({"NEXUS_BACKUP_VOLUME": "nexus_postgres_data"})
    assert result.returncode != 0
    assert "live PostgreSQL 17 source volume" in result.stderr


if __name__ == "__main__":
    test_distinct_disposable_volumes_are_accepted()
    test_live_postgres17_volume_is_rejected_as_database_volume()
    test_live_postgres17_volume_is_rejected_as_backup_volume()
    print("Release volume safety checks passed")
