"""Helpers for the Phase 2–3 tests: master data and documents built through the API."""

import uuid

from fastapi.testclient import TestClient

from app.rbac import PERMISSIONS
from tests.conftest import API

ALL = [p for p in PERMISSIONS if not p.startswith(("users.", "roles."))]


def key() -> str:
    return uuid.uuid4().hex


def ok(r, code=200) -> dict:
    assert r.status_code == code, r.text
    return r.json()


def item(c: TestClient, code: str, kind="component", tracking="none", **kw) -> dict:
    body = {
        "code": code,
        "name": code.title(),
        "kind": kind,
        "unit_code": "PCS",
        "tracking": tracking,
    } | kw
    return ok(c.post(f"{API}/items", json=body), 201)


def warehouse(c: TestClient, code="DXB") -> dict:
    w = ok(c.post(f"{API}/warehouses", json={"code": code, "name": f"{code} warehouse"}), 201)
    w["loc"] = {loc["purpose"]: loc for loc in w["locations"]}
    return w


def supplier(c: TestClient, code="SUP1", currency="AED") -> dict:
    return ok(c.post(f"{API}/parties", json={"code": code, "name": f"Supplier {code}", "kinds": ["supplier"],
                                             "currency": currency}), 201)  # fmt: skip


def approved_po(maker: TestClient, approver: TestClient, sup: dict, lines: list[tuple[dict, float, float]],
                currency="AED") -> dict:  # fmt: skip
    po = ok(
        maker.post(
            f"{API}/purchase-orders",
            json={
                "supplier_id": sup["id"],
                "currency": currency,
                "lines": [{"item_id": i["id"], "qty": q, "unit_price": pr} for i, q, pr in lines],
            },
        ),
        201,
    )
    po = ok(maker.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    if po["status"] == "submitted":
        po = ok(
            approver.post(
                f"{API}/purchase-orders/{po['id']}/approve", json={"version": po["version"]}
            )
        )
    return po


def stock_in(maker: TestClient, approver: TestClient, wh: dict, sup: dict, it: dict, qty: float,
             lot: str = "", serials: list[str] | None = None) -> dict:  # fmt: skip
    """Buy, receive and accept: `qty` of `it` lands in the warehouse's main store."""
    po = approved_po(maker, approver, sup, [(it, qty, 10)])
    line = po["lines"][0]
    body = {"po_line_id": line["id"], "qty": qty, "lot": lot}
    if serials:
        body |= {"units": [{"serial": s} for s in serials], "accept_serials": serials}
    else:
        body |= {"accept": qty}
    ok(maker.post(f"{API}/purchase-orders/{po['id']}/receipts",
                  json={"key": key(), "warehouse_id": wh["id"], "lines": [body]}), 201)  # fmt: skip
    return po


def summary(c: TestClient, code: str) -> dict:
    rows = ok(c.get(f"{API}/stock/summary", params={"q": code}))["items"]
    return next(r for r in rows if r["code"] == code)


def reconciled(c: TestClient) -> None:
    r = ok(c.get(f"{API}/stock/reconcile"))
    assert r == {"ledger_mismatches": [], "serial_mismatches": []}, r
