"""Backup and restore of the database and the stored files.

    uv run python -m app.backup create [--dir D:/smet-backups]
    uv run python -m app.backup verify <backup.dump>
    uv run python -m app.backup restore <backup.dump> [--files <files.zip>] --yes

`create` writes a PostgreSQL custom-format dump (pg_dump, as the schema owner, so grants and the
audit-log protections come back with it) and a zip of the files folder, each with a SHA-256 next to
it. `verify` checks the checksum and that PostgreSQL can read the whole archive. `restore` replaces
the working database with the backup (and the files, if given); it asks for --yes because it
overwrites. CODES_KEY and SECRETS_KEY are not in the backup: keep .env backed up separately, or
reset codes and provider credentials can't be read after a restore.

Needs the PostgreSQL client tools (pg_dump, pg_restore) on the PATH.
"""

import argparse
import hashlib
import os
import shutil
import subprocess  # noqa: S404 - runs the PostgreSQL tools with fixed arguments, no shell
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.setup_db import app_rights


def libpq(url: str) -> tuple[str, dict]:
    """The address as the PostgreSQL tools take it, with the password moved to PGPASSWORD so it
    never shows in the process list."""
    u = make_url(url)
    env = os.environ | ({"PGPASSWORD": u.password} if u.password else {})
    plain = u.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    return plain, env


def tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(
            f"{name} not found: install the PostgreSQL client tools and add them to PATH"
        )
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _seal(path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(f"{sha256(path)}  {path.name}\n")


def create(
    folder: Path, url: str | None = None, files_dir: Path | None = None
) -> tuple[Path, Path | None]:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dump = folder / f"smet-{stamp}.dump"
    address, env = libpq(url or get_settings().owner_url)
    subprocess.run([tool("pg_dump"), "--format=custom", "--file", str(dump), address],  # noqa: S603
                   check=True, timeout=3600, env=env)  # fmt: skip
    _seal(dump)
    files_dir = files_dir or Path(get_settings().files_dir)
    archive = None
    if files_dir.exists():
        archive = folder / f"files-{stamp}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(files_dir.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(files_dir).as_posix())
        _seal(archive)
    return dump, archive


def verify(dump: Path) -> int:
    """Number of objects in the archive; raises if the checksum or the archive is wrong."""
    seal = dump.with_name(dump.name + ".sha256")
    if seal.exists() and seal.read_text().split()[0] != sha256(dump):
        raise SystemExit(f"{dump.name}: checksum does not match: the file changed or is damaged")
    out = subprocess.run(  # noqa: S603 - pg_restore with fixed arguments
        [tool("pg_restore"), "--list", str(dump)],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )  # noqa: S603
    return sum(1 for ln in out.stdout.splitlines() if ln and not ln.startswith(";"))


def restore(
    dump: Path, url: str | None = None, files: Path | None = None, files_dir: Path | None = None
) -> None:
    verify(dump)
    owner = url or get_settings().owner_url
    address, env = libpq(owner)
    subprocess.run([tool("pg_restore"), "--clean", "--if-exists", "--single-transaction", "--exit-on-error",  # noqa: S603
                    "--dbname", address, str(dump)], check=True, timeout=3600, env=env)  # fmt: skip
    # The restore re-creates the tables with default rights; take the history rights away again.
    with psycopg.connect(make_url(owner).set(drivername="postgresql").render_as_string(hide_password=False),
                         autocommit=True) as conn:  # fmt: skip
        app_rights(conn)
    if files:
        target = files_dir or Path(get_settings().files_dir)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(files) as z:
            for name in (
                z.namelist()
            ):  # names are relative and checked, so nothing lands outside the folder
                dest = (target / name).resolve()
                if not dest.is_relative_to(target.resolve()):
                    raise SystemExit(f"Refused: {name} would land outside the files folder")
            z.extractall(target)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--dir", default=get_settings().backup_dir)
    v = sub.add_parser("verify")
    v.add_argument("dump")
    r = sub.add_parser("restore")
    r.add_argument("dump")
    r.add_argument("--files")
    r.add_argument("--yes", action="store_true", help="confirm: the working database is replaced")
    a = ap.parse_args()
    if a.cmd == "create":
        dump, archive = create(Path(a.dir))
        print(
            f"Database: {dump}\nFiles:    {archive or 'no files folder yet'}\nBack up backend/.env too (CODES_KEY, SECRETS_KEY)."
        )
    elif a.cmd == "verify":
        print(f"{a.dump}: readable, {verify(Path(a.dump))} objects")
    else:
        if not a.yes:
            sys.exit("restore replaces the working database: add --yes to confirm")
        restore(Path(a.dump), files=Path(a.files) if a.files else None)
        print("Restored. Run `uv run alembic upgrade head` if the backup is from an older version.")


if __name__ == "__main__":
    main()
