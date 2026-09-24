import pathlib
import re
import subprocess

tracked = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], text=True).splitlines()
for name in tracked:
    path = pathlib.Path(name)
    if path.name in {"production.env", "postgres_password", "admin_password", "public_password", "migrator_password", "backup_cipher", "studio_connection"}:
        raise SystemExit(f"Private file appears in repository: {name}")
    if not path.is_file():
        continue
    data = path.read_bytes()
    if re.search(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", data):
        raise SystemExit(f"Private key appears in repository: {name}")
    if re.search(rb"glpat-[A-Za-z0-9_-]{20,}", data):
        raise SystemExit(f"GitLab token appears in repository: {name}")
print("Basic tracked-file secret checks passed")
