"""Phase 7 dashboards, reports, exports and search: every figure is a count of rows anyone allowed
can open, every report runs with every filter it offers, and downloads are permitted and audited."""

import io
from datetime import date

from openpyxl import load_workbook
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditEvent
from app.reports import AREAS, REPORTS
from tests import test_installation
from tests.conftest import API
from tests.ops import approved_po, item, ok, supplier

site = test_installation.site  # serials B1–B6 delivered to Tower A; pm and boss hold everything
ZERO = "00000000-0000-0000-0000-000000000000"
VALUE = {"date_from": "2020-01-01", "date_to": str(date.today()), "serial": "B1", "item": ZERO, "customer": ZERO,
         "distributor": ZERO, "supplier": ZERO, "warehouse": ZERO, "project": ZERO}  # fmt: skip


def test_every_report_runs_with_every_filter_it_offers(site):
    pm = site["pm"]
    for name, r in REPORTS.items():
        full = ok(pm.get(f"{API}/reports/{name}"))
        assert [c["key"] for c in full["columns"]] == [k for k, _ in r.columns]
        assert all(set(row) >= {k for k, _ in r.columns} for row in full["items"]), name
        for st in r.statuses:
            ok(pm.get(f"{API}/reports/{name}", params={"status": st}))
        for flag in r.flags or {}:
            ok(pm.get(f"{API}/reports/{name}", params={"flag": flag}))
        allowed = {
            k for f in r.filters for k in (("date_from", "date_to") if f == "date" else (f,))
        }
        ok(pm.get(f"{API}/reports/{name}", params={k: VALUE[k] for k in allowed if k in VALUE}))
    batteries = ok(pm.get(f"{API}/reports/batteries", params={"serial": "B"}))
    assert batteries["total"] == 6 and {b["status"] for b in batteries["items"]} == {"delivered"}
    assert ok(pm.get(f"{API}/reports/batteries", params={"customer": ZERO}))["total"] == 0


def test_filters_a_report_does_not_offer_are_refused(site):
    pm = site["pm"]
    assert pm.get(f"{API}/reports/stock_position", params={"customer": ZERO}).status_code == 422
    assert pm.get(f"{API}/reports/purchase_orders", params={"status": "lost"}).status_code == 422
    assert pm.get(f"{API}/reports/purchase_orders", params={"flag": "low"}).status_code == 422
    assert pm.get(f"{API}/reports/nothing").status_code == 404


def test_dashboards_show_only_what_the_person_may_see_and_each_figure_opens_its_rows(user_with):
    buyer = user_with(perms=["purchasing.view", "purchasing.create", "items.view", "items.edit", "parties.view", "parties.edit",
                             "reports.purchase_orders", "reports.rejected_receipts"])  # fmt: skip
    approver = user_with(perms=["purchasing.view", "purchasing.approve"])
    approved_po(buyer, approver, supplier(buyer), [(item(buyer, "CELL"), 3, 2)])
    areas = ok(buyer.get(f"{API}/dashboards"))["areas"]
    assert [a["area"] for a in areas] == ["procurement", "exceptions"]
    assert {t["report"] for a in areas for t in a["tiles"]} <= {
        "purchase_orders",
        "rejected_receipts",
    }
    for t in areas[0]["tiles"]:
        assert (
            t["count"]
            == ok(buyer.get(f"{API}/reports/{t['report']}", params=t["filters"]))["total"]
        )
    assert next(t for t in areas[0]["tiles"] if t["label"] == "Expected receipts")["count"] == 1
    assert buyer.get(f"{API}/reports/stock_position").status_code == 403
    assert {r["name"] for r in ok(buyer.get(f"{API}/reports"))["items"]} == {
        "purchase_orders",
        "rejected_receipts",
    }


def test_every_dashboard_figure_points_at_a_real_report_and_filters():
    for _, tiles in AREAS.values():
        for _, rep, f in tiles:
            r = REPORTS[rep]
            assert set(f.get("status", "").split(",")) - {""} <= set(r.statuses), (rep, f)
            assert f.get("flag") is None or f["flag"] in (r.flags or {}), (rep, f)


def test_downloads_need_their_permission_and_are_audited(user_with):
    viewer = user_with(perms=["purchasing.view", "purchasing.create", "items.view", "items.edit", "parties.view", "parties.edit"])  # fmt: skip
    exporter = user_with(perms=["reports.purchase_orders", "reports.export"])
    sup = ok(viewer.post(f"{API}/parties", json={"code": "EVIL", "name": "=HYPERLINK(1)", "kinds": ["supplier"]}), 201)  # fmt: skip
    approved_po(
        viewer,
        user_with(perms=["purchasing.view", "purchasing.approve"]),
        sup,
        [(item(viewer, "CELL"), 3, 2)],
    )
    assert viewer.get(f"{API}/reports/purchase_orders/export").status_code == 403
    r = exporter.get(f"{API}/reports/purchase_orders/export", params={"status": "approved"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig").splitlines()
    assert text[0].startswith("Order,Supplier,Status") and ",'=HYPERLINK(1),approved," in text[1]
    x = exporter.get(f"{API}/reports/purchase_orders/export", params={"format": "xlsx"})
    ws = load_workbook(io.BytesIO(x.content)).active
    rows = list(ws.values)
    assert (
        rows[0][:2] == ("Order", "Supplier") and rows[1][1] == "'=HYPERLINK(1)" and rows[1][5] == 3
    )
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action.in_(("report.export", "access.denied")))
                            .order_by(AuditEvent.id)).all()  # fmt: skip
    assert [e.action for e in events] == ["access.denied", "report.export", "report.export"]
    assert (
        events[1].changes["filters"][1] == {"status": "approved"}
        and "1 rows as CSV" in events[1].summary
    )


def test_search_finds_only_what_the_person_may_see(site, user_with):
    hits = ok(site["pm"].get(f"{API}/search", params={"q": "b1"}))["groups"]
    assert [i["title"] for g in hits if g["kind"] == "battery" for i in g["items"]] == ["B1"]
    assert (
        ok(site["pm"].get(f"{API}/search", params={"q": "desert"}))["groups"][0]["items"][0][
            "title"
        ]
        == "Desert Telecom"
    )
    nobody = user_with(perms=["purchasing.view"])
    assert ok(nobody.get(f"{API}/search", params={"q": "b1"}))["groups"] == []
    assert nobody.get(f"{API}/search", params={"q": "b"}).status_code == 422
