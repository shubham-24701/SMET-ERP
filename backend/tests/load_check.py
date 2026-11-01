"""Performance and concurrency check on the test database (wiped first and after).

    uv run python -m tests.load_check

Seeds a few years' volume for a business of SMET's size (synthetic data), then times the busiest
screens one at a time and with 8 people at once, and checks two races. Prints the results; numbers
go to docs/TEST_RESULTS.md. Not part of the normal test run (it takes about a minute).
"""

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from tests.conftest_env import use_test_database

use_test_database()
os.environ |= {"RUN_WORKER_IN_API": "false", "ALLOWED_HOSTS": '["testserver"]'}

from alembic import command  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from tests.conftest import ADMIN, API, PASSWORD, alembic_config, owner_engine, wipe  # noqa: E402

VOLUME = {"items": 400, "purchase_orders": 3000, "serials": 20000, "stock_moves": 60000,
          "service_requests": 3000, "notifications": 30000}  # fmt: skip


def client() -> TestClient:
    from app.main import app  # noqa: PLC0415

    c = TestClient(app)
    c.headers["X-CSRF-Token"] = c.get(f"{API}/auth/csrf").json()["csrf_token"]
    return c


def seed() -> TestClient:
    admin = client()
    admin.post(f"{API}/auth/setup", json=ADMIN).raise_for_status()
    with owner_engine.begin() as c:
        uid = c.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        # One role with everything, held by the administrator for this check.
        c.execute(text("INSERT INTO role_permissions SELECT r.id, p FROM roles r, unnest(CAST(:perms AS text[])) p "
                       "WHERE r.template_key = 'operations_manager' ON CONFLICT DO NOTHING"),
                  {"perms": ["reports.export", "parties.view", "service.view", "items.edit"]})  # fmt: skip
        c.execute(
            text(
                "INSERT INTO user_roles SELECT :u, id FROM roles WHERE template_key = 'operations_manager'"
            ),
            {"u": uid},
        )
        c.execute(text("INSERT INTO parties (id, code, name, kinds) SELECT gen_random_uuid(), 'P' || g, 'Party ' || g, "
                       "ARRAY['supplier','customer'] FROM generate_series(1, 200) g"))  # fmt: skip
        c.execute(text("INSERT INTO items (id, code, name, kind, unit_code, tracking, min_stock) SELECT gen_random_uuid(), "
                       "'IT' || g, 'Item ' || g, CASE WHEN g % 4 = 0 THEN 'battery_unit' ELSE 'component' END, 'PCS', "
                       "CASE WHEN g % 4 = 0 THEN 'serial' ELSE 'none' END, g % 7 FROM generate_series(1, :n) g"), {"n": VOLUME["items"]})  # fmt: skip
        c.execute(
            text("INSERT INTO warehouses (id, code, name) VALUES (gen_random_uuid(), 'W1', 'Main')")
        )
        c.execute(text("INSERT INTO locations (id, warehouse_id, code, name, purpose) SELECT gen_random_uuid(), id, 'STORE', "
                       "'Main store', 'available' FROM warehouses"))  # fmt: skip
        c.execute(text("INSERT INTO purchase_orders (id, number, supplier_id, status, currency, created_by, created_at, expected_arrival) "
                       "SELECT gen_random_uuid(), 'PO-L-' || g, (SELECT id FROM parties ORDER BY code OFFSET g % 200 LIMIT 1), "
                       "(ARRAY['draft','submitted','approved','partly_received','closed'])[1 + g % 5], 'AED', :u, "
                       "now() - (g || ' hours')::interval, current_date - (g % 30) FROM generate_series(1, :n) g"),
                  {"u": uid, "n": VOLUME["purchase_orders"]})  # fmt: skip
        c.execute(text("INSERT INTO purchase_order_lines (id, po_id, item_id, qty_ordered, unit_price) SELECT gen_random_uuid(), "
                       "po.id, i.id, 10, 5 FROM (SELECT id, row_number() OVER () AS rn FROM purchase_orders) po JOIN "
                       "(SELECT id, row_number() OVER (ORDER BY code) - 1 AS rn FROM items) i ON i.rn = po.rn % :n"), {"n": VOLUME["items"]})  # fmt: skip
        c.execute(text("INSERT INTO serials (id, serial, item_id, status, location_id) SELECT gen_random_uuid(), 'SN' || g, "
                       "(SELECT id FROM items WHERE tracking = 'serial' LIMIT 1), 'available', (SELECT id FROM locations LIMIT 1) "
                       "FROM generate_series(1, :n) g"), {"n": VOLUME["serials"]})  # fmt: skip
        c.execute(text("SET LOCAL smet.audit_maintenance = 'on'"))
        c.execute(text("INSERT INTO stock_moves (item_id, qty, to_location_id, kind, user_id, at) SELECT "
                       "(SELECT id FROM items WHERE tracking = 'none' LIMIT 1), 1, (SELECT id FROM locations LIMIT 1), 'receipt', :u, "
                       "now() - (g || ' minutes')::interval FROM generate_series(1, :n) g"), {"u": uid, "n": VOLUME["stock_moves"]})  # fmt: skip
        c.execute(text("INSERT INTO service_requests (id, number, source, issue, status, contact_name, created_at) SELECT "
                       "gen_random_uuid(), 'SR-L-' || g, 'public', 'Alarm', (ARRAY['new','scheduled','closed'])[1 + g % 3], "
                       "'Contact ' || g, now() - (g || ' hours')::interval FROM generate_series(1, :n) g"), {"n": VOLUME["service_requests"]})  # fmt: skip
        c.execute(text("INSERT INTO notifications (id, user_id, event, title, key, created_at) SELECT gen_random_uuid(), :u, "
                       "'stock.low', 'Item low', 'k' || g, now() - (g || ' minutes')::interval FROM generate_series(1, :n) g"),
                  {"u": uid, "n": VOLUME["notifications"]})  # fmt: skip
    admin.post(
        f"{API}/auth/login", json={"email": ADMIN["email"], "password": PASSWORD}
    ).raise_for_status()
    admin.headers["X-CSRF-Token"] = admin.get(f"{API}/auth/csrf").json()["csrf_token"]
    return admin


SCREENS = ["/dashboards", "/reports/purchase_orders?limit=50", "/reports/stock_moves?limit=50", "/reports/batteries?limit=50",
           "/reports/service_requests?flag=open&limit=50", "/stock/summary?limit=50", "/search?q=SN19", "/notifications?limit=30",
           "/purchase-orders?limit=50", "/service-requests?status=open&limit=50"]  # fmt: skip


def timed(c: TestClient, path: str) -> float:
    t = time.perf_counter()
    r = c.get(f"{API}{path}")
    assert r.status_code == 200, (path, r.status_code, r.text[:200])
    return (time.perf_counter() - t) * 1000


def main() -> None:
    command.upgrade(alembic_config(), "head")
    wipe()
    t0 = time.perf_counter()
    admin = seed()
    print(f"Seeded {VOLUME} in {time.perf_counter() - t0:.1f}s")
    print(f"{'screen':48} {'p50 ms':>8} {'p95 ms':>8}")
    for path in SCREENS:
        times = sorted(timed(admin, path) for _ in range(10))
        print(f"{path:48} {statistics.median(times):8.0f} {times[int(len(times) * 0.95) - 1]:8.0f}")
    # 8 people at once, 160 requests over the same screens.
    t = time.perf_counter()
    with ThreadPoolExecutor(8) as pool:
        times = sorted(pool.map(lambda i: timed(admin, SCREENS[i % len(SCREENS)]), range(160)))
    print(f"8 at once: 160 requests in {time.perf_counter() - t:.1f}s, p50 {statistics.median(times):.0f} ms, "
          f"p95 {times[int(len(times) * 0.95) - 1]:.0f} ms")  # fmt: skip
    # Races: two saves of the same details with the same version; one wins, one is told it changed.
    admin.post(
        f"{API}/custom-fields", json={"entity": "item", "label": "Bin", "kind": "text"}
    ).raise_for_status()
    item_id = admin.get(f"{API}/items?limit=1").json()["items"][0]["id"]
    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(lambda v: client_as_admin().put(f"{API}/custom-values/item/{item_id}",
                                                                 json={"version": 0, "values": {"bin": v}}).status_code, ["A1", "B2"]))  # fmt: skip
    print(f"Two saves at once: {codes} (expected [200, 409])")
    wipe()


def client_as_admin() -> TestClient:
    c = client()
    c.post(
        f"{API}/auth/login", json={"email": ADMIN["email"], "password": PASSWORD}
    ).raise_for_status()
    c.headers["X-CSRF-Token"] = c.get(f"{API}/auth/csrf").json()["csrf_token"]
    return c


if __name__ == "__main__":
    main()
