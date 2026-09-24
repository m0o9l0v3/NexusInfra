"""Refuse a rehearsal configuration that could name the running source volume."""
import os
import re
import sys

SOURCE_VOLUME = "nexus_postgres_data"


def required(key: str) -> str:
    value = os.environ.get(key, "")
    if not value:
        raise SystemExit(f"{key} is required")
    return value


def distinct_volumes(*volumes: str) -> None:
    blocked = {SOURCE_VOLUME, os.environ.get("NEXUS_DB_VOLUME", ""), os.environ.get("NEXUS_BACKUP_VOLUME", "")}
    if len(set(volumes)) != len(volumes) or any(name in blocked for name in volumes):
        raise SystemExit("Rehearsal volumes must be distinct from the source and production volumes")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "pg17":
        project = required("PG17_RESTORE_PROJECT")
        volume = required("PG17_RESTORE_VOLUME")
        image = required("PG17_RESTORE_IMAGE")
        required("PG17_DB_NAME")
        secret = required("PG17_RESTORE_PASSWORD_FILE")
        distinct_volumes(volume)
        prefix = "nexus-pg17-restore-"
    elif mode == "pg18":
        project = required("PG18_REHEARSAL_PROJECT")
        database = required("PG18_REHEARSAL_DB_VOLUME")
        backup = required("PG18_REHEARSAL_BACKUP_VOLUME")
        image = required("NEXUS_POSTGRES_IMAGE")
        secret = required("PG18_REHEARSAL_SECRET_DIR")
        distinct_volumes(database, backup)
        prefix = "nexus-pg18-rehearsal-"
    else:
        raise SystemExit("Usage: preflight-rehearsal.py pg17|pg18")
    if not project.startswith(prefix) or len(project) == len(prefix):
        raise SystemExit(f"Project name must begin with {prefix} and have a unique suffix")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image):
        raise SystemExit("Rehearsal image must be pinned by digest")
    if not secret.startswith("/"):
        raise SystemExit("Rehearsal secret path must be absolute")
    print(f"{mode} rehearsal settings passed static preflight; inspect volume contents before startup")


if __name__ == "__main__":
    main()
