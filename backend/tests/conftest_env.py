import os

from app.config import BACKEND_DIR


def _from_env_file(key: str) -> str:
    env = BACKEND_DIR / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return ""


def use_test_database() -> None:
    """Point the app at the test database (created by app.setup_db). Must run before app imports."""
    for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        value = os.environ.get(f"TEST_{key}") or _from_env_file(f"TEST_{key}")
        if not value:
            raise SystemExit(f"TEST_{key} is not set: run `uv run python -m app.setup_db` first")
        os.environ[key] = value
    assert os.environ["DATABASE_URL"].rsplit("/", 1)[-1].endswith("_test"), "not a test database"
