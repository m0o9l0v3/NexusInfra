"""Safety checks for the transitional export; Docker is never contacted."""
import os
import pathlib
import subprocess
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/backup-pg17-source.sh"


def run_export(root: pathlib.Path, image: str, volume: str) -> subprocess.CompletedProcess[str]:
    fake_bin = root / "bin"
    fake_bin.mkdir(exist_ok=True)
    for name, script in {
        "id": "#!/bin/sh\necho 0\n",
        "stat": "#!/bin/sh\ncase $2 in %u) echo 0;; %a) echo 700;; esac\n",
        "docker": """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[0] == 'inspect':
    fmt = args[args.index('--format') + 1]
    if '.Config.Image' in fmt: print(os.environ['FAKE_IMAGE'])
    elif '.Mounts' in fmt: print(os.environ['FAKE_VOLUME'])
    elif '.State.Running' in fmt: print('true')
    elif '.State.Health.Status' in fmt: print('healthy')
elif args[0] == 'exec':
    if 'postgres' in args and '--version' in args: print('postgres (PostgreSQL) 17.11')
    elif 'pg_restore' in args: sys.stdin.buffer.read(); print('archive list')
    elif 'pg_dumpall' in args: print('SQL backup fixture')
    elif 'pg_dump' in args: sys.stdout.buffer.write(b'custom archive fixture')
else: sys.exit(2)
""",
    }.items():
        path = fake_bin / name
        path.write_text(script)
        path.chmod(0o755)
    backup_dir = root / "private-backups"
    backup_dir.mkdir(exist_ok=True)
    backup_dir.chmod(0o700)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_IMAGE": image,
        "FAKE_VOLUME": volume,
        "ALLOW_PG17_EXPORT": "1",
        "PG17_CONTAINER": "postgres-source",
        "PG17_DB_USER": "postgres",
        "PG17_DB_NAME": "nexus_admin",
        "PG17_BACKUP_DIR": str(backup_dir),
    }
    return subprocess.run([str(SCRIPT)], env=env, text=True, capture_output=True, check=False)


def test_wrong_volume_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        result = run_export(root, "postgres:17-alpine", "wrong-volume")
        assert result.returncode == 2, result.stderr
        assert not list((root / "private-backups").iterdir())


def test_wrong_image_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        result = run_export(root, "postgres:18-alpine", "nexus_postgres_data")
        assert result.returncode == 2, result.stderr
        assert not list((root / "private-backups").iterdir())


def test_successful_export_is_a_complete_generation() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        result = run_export(root, "postgres:17-alpine", "nexus_postgres_data")
        assert result.returncode == 0, result.stderr
        generations = list((root / "private-backups").iterdir())
        assert len(generations) == 1
        generation = generations[0]
        assert not generation.name.endswith(".partial")
        assert {p.name for p in generation.iterdir()} == {"globals.sql", "cluster.sql", "nexus.dump", "SHA256SUMS"}
        checked = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=generation, capture_output=True, text=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr


if __name__ == "__main__":
    test_wrong_volume_is_rejected()
    test_wrong_image_is_rejected()
    test_successful_export_is_a_complete_generation()
    print("PG17 backup safety checks passed")
