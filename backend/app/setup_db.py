"""One-time database setup on this computer (and in CI).

    uv run python -m app.setup_db            # asks for the PostgreSQL superuser password
    uv run python -m app.setup_db --admin-url postgresql://postgres:PW@127.0.0.1:5432/postgres

Creates two logins with random passwords and two databases, then writes their addresses to
backend/.env (other lines in .env are kept):

  smet_owner  owns the schema; used only by migrations and test clean-up
  smet_app    the app's login: reads and writes rows; can't change the schema or the audit log
  smet        the working database      smet_test  for the automated tests (wiped by them)

Safe to run again: existing logins get new passwords and .env is updated to match.
"""

import argparse
import getpass
import secrets

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.config import BACKEND_DIR

ENV = BACKEND_DIR / ".env"
OWNER, APP = "smet_owner", "smet_app"
DATABASES = {"smet": ("DATABASE_URL", "MIGRATION_DATABASE_URL"),
             "smet_test": ("TEST_DATABASE_URL", "TEST_MIGRATION_DATABASE_URL")}  # fmt: skip


def url(user: str, password: str, host: str, port: int, db: str) -> str:
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def setup(admin_url: str, host: str, port: int) -> dict[str, str]:
    pw = {OWNER: secrets.token_urlsafe(24), APP: secrets.token_urlsafe(24)}
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for role, password in pw.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)))  # fmt: skip
        for db in DATABASES:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                    sql.Identifier(db), sql.Identifier(OWNER)))  # fmt: skip
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db))
            )
            conn.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
                sql.Identifier(db), sql.Identifier(APP)))  # fmt: skip
    for db in DATABASES:  # privileges are per database: connect to each one
        with psycopg.connect(make_conninfo(admin_url, dbname=db), autocommit=True) as conn:
            conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(OWNER)))
            conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP)))
            for stmt in (
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
                "GRANT USAGE, SELECT ON SEQUENCES TO {app}",
            ):
                conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public " + stmt).format(
                    owner=sql.Identifier(OWNER), app=sql.Identifier(APP)))  # fmt: skip
            # Tables made before this ran (a re-run on an existing database)
            conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                sql.Identifier(APP)))  # fmt: skip
            conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                sql.Identifier(APP)))  # fmt: skip
            if conn.execute("SELECT to_regclass('public.audit_events')").fetchone()[0]:
                conn.execute(sql.SQL("REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {}").format(
                    sql.Identifier(APP)))  # fmt: skip
    values = {}
    for db, (app_key, owner_key) in DATABASES.items():
        values[app_key] = url(APP, pw[APP], host, port, db)
        values[owner_key] = url(OWNER, pw[OWNER], host, port, db)
    return values


def write_env(values: dict[str, str]) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in values]
    ENV.write_text("\n".join([*kept, *(f"{k}={v}" for k, v in values.items())]) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--admin-url", help="superuser connection string (default: ask for the postgres password)"
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5432)
    args = ap.parse_args()
    admin_url = args.admin_url or make_conninfo(  # any characters in the password are fine
        host=args.host,
        port=args.port,
        user="postgres",
        dbname="postgres",
        password=getpass.getpass("PostgreSQL superuser (postgres) password: "),
    )
    write_env(setup(admin_url, args.host, args.port))
    print(f"Done. Databases smet and smet_test are ready; their addresses are in {ENV}.")
    print("Next: uv run alembic upgrade head")


if __name__ == "__main__":
    main()
