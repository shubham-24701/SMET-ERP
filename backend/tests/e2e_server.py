"""The API for the browser tests: a fresh test database, then the server on 127.0.0.1:8000.

    uv run python -m tests.e2e_server      (started by frontend/playwright.config.ts)

Uses the test database (TEST_DATABASE_URL), never the working one.
"""

import os

from tests.conftest_env import use_test_database

use_test_database()

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import BACKEND_DIR  # noqa: E402

cfg = Config(str(BACKEND_DIR / "alembic.ini"))
command.downgrade(cfg, "base")  # dropping tables is allowed; rewriting audit rows is not
command.upgrade(cfg, "head")
os.environ.setdefault("RUN_WORKER_IN_API", "true")
uvicorn.run("app.main:app", host="127.0.0.1", port=8000, proxy_headers=False)
