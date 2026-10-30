"""Phase 5 installation and traceability: batteries delivered to a site go into a bank, the bank is
installed, commissioned and handed over by visits that meet SMET's checks and evidence policy, bank
changes keep their history, and a battery's QR label and timeline tell its story without leaking it."""

import io
from datetime import date, timedelta

import pytest

from app.config import get_settings
from tests.conftest import API, new_client
from tests.ops import ALL, item, key, ok, stock_in, supplier, warehouse

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
PDF = b"%PDF-1.4\n" + b"0" * 200
TODAY = str(date.today())


@pytest.fixture
def site(user_with, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "files_dir", str(tmp_path))
    pm, boss = (
        user_with(perms=ALL, email="coord@example.test"),
        user_with(perms=ALL, email="boss@example.test"),
    )
    tech = user_with(
        perms=["installation.view", "installation.record", "stock.view"], email="tech@example.test"
    )
    other = user_with(
        perms=["installation.view", "installation.record"], email="other@example.test"
    )
    wh, sup = warehouse(pm), supplier(pm)
    cust = ok(
        pm.post(
            f"{API}/parties", json={"code": "TEL", "name": "Desert Telecom", "kinds": ["customer"]}
        ),
        201,
    )
    unit = item(pm, "UNIT-2V", kind="battery_unit", tracking="serial")
    serials = [f"B{i}" for i in range(1, 7)]
    stock_in(pm, boss, wh, sup, unit, 6, serials=serials)
    pr = ok(
        pm.post(f"{API}/projects", json={"name": "Tower backup", "customer_id": cust["id"]}), 201
    )
    s = ok(
        pm.post(f"{API}/projects/{pr['id']}/sites", json={"name": "Tower A", "address": "Al Quoz"}),
        201,
    )
    qt = ok(pm.post(f"{API}/quotations", json={"customer_id": cust["id"], "project_id": pr["id"], "lines": [
        {"line_type": "battery", "item_id": unit["id"], "qty": 6, "unit_price": 500}]}), 201)  # fmt: skip
    v = lambda q: q["revisions"][0]["version"]  # noqa: E731
    qt = ok(pm.post(f"{API}/quotations/{qt['id']}/submit", json={"version": v(qt)}))
    qt = ok(boss.post(f"{API}/quotations/{qt['id']}/approve", json={"version": v(qt)}))
    qt = ok(
        pm.post(
            f"{API}/quotations/{qt['id']}/answer",
            json={"version": v(qt), "accepted": True, "by": "Buyer", "on": TODAY},
        )
    )
    so = ok(pm.post(f"{API}/quotations/{qt['id']}/convert", json={"version": v(qt)}), 201)
    d = ok(pm.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"], "site_id": s["id"],
              "lines": [{"order_line_id": so["lines"][0]["id"], "qty": 6, "serials": serials}]}), 201)  # fmt: skip
    ok(pm.post(f"{API}/dispatches/{d['id']}/send", json={"version": d["version"], "key": key()}))
    d = ok(pm.get(f"{API}/dispatches/{d['id']}"))
    ok(pm.post(f"{API}/dispatches/{d['id']}/deliver", json={"version": d["version"], "key": key(),
               "received_by": "Site guard", "delivered_on": TODAY}))  # fmt: skip
    tech_id = ok(tech.get(f"{API}/auth/me"))["user"]["id"]
    return {"pm": pm, "boss": boss, "tech": tech, "other": other, "tech_id": tech_id, "project": pr,
            "site": s, "cust": cust, "unit": unit, "serials": serials, "wh": wh}  # fmt: skip


def bank(x, name="Bank 1", n=4):
    b = ok(
        x["pm"].post(
            f"{API}/sites/{x['site']['id']}/banks", json={"name": name, "configuration": "24 × 2 V"}
        ),
        201,
    )
    return b


def visit(x, stage, banks, techs=None):
    return ok(x["pm"].post(f"{API}/visits", json={"site_id": x["site"]["id"], "stage": stage, "bank_ids": [b["id"] for b in banks],
              "scheduled_on": TODAY, "technician_ids": techs if techs is not None else [x["tech_id"]],
              "contacts": [{"name": "Omar", "phone": "+971 50 000 0000"}]}), 201)  # fmt: skip


def photo(c, v, kind="photo", data=JPEG, name="rack.jpg"):
    return c.post(
        f"{API}/visits/{v['id']}/files",
        files={"file": (name, io.BytesIO(data), "image/jpeg")},
        data={"kind": kind},
    )


def test_a_bank_goes_from_delivery_to_handover_one_stage_at_a_time(site):
    pm, tech, other = site["pm"], site["tech"], site["other"]
    ok(
        pm.post(
            f"{API}/stage-checks",
            json={"stage": "installation", "kind": "check", "text": "Torque checked"},
        ),
        201,
    )
    float_v = ok(pm.post(f"{API}/stage-checks", json={"stage": "commissioning", "kind": "reading", "text": "Float voltage",
                 "unit": "V", "min_value": 52, "max_value": 55, "confirmed": True}), 201)  # fmt: skip
    b = bank(site)
    v = visit(site, "installation", [b])
    # Placing batteries: a technician only on their own visit.
    add = {"version": b["version"], "visit_id": v["id"],
           "batteries": [{"serial": s, "position": str(i)} for i, s in enumerate(site["serials"][:4], 1)]}  # fmt: skip
    assert other.post(f"{API}/banks/{b['id']}/batteries", json=add).status_code == 403
    assert (
        tech.post(f"{API}/banks/{b['id']}/batteries", json=add | {"visit_id": None}).status_code
        == 403
    )
    b = ok(tech.post(f"{API}/banks/{b['id']}/batteries", json=add))
    assert [p["serial"] for p in b["positions"]] == site["serials"][:4]
    again = tech.post(f"{API}/banks/{b['id']}/batteries", json={"version": b["version"], "visit_id": v["id"],
                      "batteries": [{"serial": "B1", "position": "9"}]})  # fmt: skip
    assert again.status_code == 422 and "already in a bank" in again.text
    # Completion is refused until the checks, the receipt of every battery and the photo are there.
    no = tech.post(
        f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
    )
    assert (
        no.status_code == 422 and "Torque checked" in no.text and "Photographs: 0 of 1" in no.text
    )
    check = ok(tech.get(f"{API}/visits/{v['id']}"))["checks"][0]
    v = ok(tech.put(f"{API}/visits/{v['id']}/record", json={"version": v["version"] , "received_serials": site["serials"][:3],
              "checklist": [{"check_id": check["id"], "done": True}], "issues": [{"text": "Cable tray loose"}]}))  # fmt: skip
    assert v["status"] == "started"
    ok(photo(tech, v), 201)
    no = tech.post(
        f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
    )
    assert no.status_code == 422 and "Confirm receipt of B4" in no.text
    v = ok(tech.put(f"{API}/visits/{v['id']}/record", json={"version": v["version"], "received_serials": site["serials"][:4],
              "checklist": [{"check_id": check["id"], "done": True}], "issues": [{"text": "Cable tray loose"}]}))  # fmt: skip
    future = tech.post(
        f"{API}/visits/{v['id']}/complete",
        json={"version": v["version"], "done_on": str(date.today() + timedelta(days=1))},
    )
    assert future.status_code == 422
    v = ok(
        tech.post(
            f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
        )
    )
    assert v["status"] == "completed" and v["banks"][0]["status"] == "installed"
    assert (
        tech.put(f"{API}/visits/{v['id']}/record", json={"version": v["version"]}).status_code
        == 409
    )
    # Commissioning can't skip ahead to handover, and a reading outside the confirmed limits stops it.
    early = visit(site, "handover", [b])
    early = ok(tech.put(f"{API}/visits/{early['id']}/record", json={"version": early["version"],
               "acknowledgement": {"name": "Eng. Saeed", "on": TODAY}}))  # fmt: skip
    r = tech.post(
        f"{API}/visits/{early['id']}/complete", json={"version": early["version"], "done_on": TODAY}
    )
    assert r.status_code == 409 and "needs it commissioned" in r.text
    c = visit(site, "commissioning", [b])
    c = ok(
        tech.put(
            f"{API}/visits/{c['id']}/record",
            json={"version": c["version"], "readings": [{"check_id": float_v["id"], "value": 58}]},
        )
    )
    assert c["readings"][0]["within"] is False
    r = tech.post(
        f"{API}/visits/{c['id']}/complete", json={"version": c["version"], "done_on": TODAY}
    )
    assert r.status_code == 422 and "outside the confirmed limits" in r.text
    c = ok(
        tech.put(
            f"{API}/visits/{c['id']}/record",
            json={
                "version": c["version"],
                "readings": [{"check_id": float_v["id"], "value": 54.2}],
            },
        )
    )
    c = ok(
        tech.post(
            f"{API}/visits/{c['id']}/complete", json={"version": c["version"], "done_on": TODAY}
        )
    )
    # Handover needs the customer's acknowledgement, and no open issue on its visit.
    early = ok(tech.get(f"{API}/visits/{early['id']}"))
    early = ok(tech.put(f"{API}/visits/{early['id']}/record", json={"version": early["version"], "issues": [{"text": "Label missing"}],
               "acknowledgement": {"name": "Eng. Saeed", "role": "Site engineer", "on": TODAY}}))  # fmt: skip
    r = tech.post(
        f"{API}/visits/{early['id']}/complete", json={"version": early["version"], "done_on": TODAY}
    )
    assert r.status_code == 422 and "Open issues" in r.text
    early = ok(tech.put(f"{API}/visits/{early['id']}/record", json={"version": early["version"], "issues": [{"text": "Label missing", "action": "Fitted", "status": "closed"}],
               "acknowledgement": {"name": "Eng. Saeed", "role": "Site engineer", "on": TODAY}}))  # fmt: skip
    ok(
        tech.post(
            f"{API}/visits/{early['id']}/complete",
            json={"version": early["version"], "done_on": TODAY},
        )
    )
    b = ok(pm.get(f"{API}/banks/{b['id']}"))
    assert (b["status"], b["installed_on"], b["commissioned_on"], b["handed_over_on"]) == (
        "handed_over",
        TODAY,
        TODAY,
        TODAY,
    )
    assert {p["status"] for p in b["positions"]} == {"in_service"}
    # The issue found at installation is still outstanding work until someone closes it.
    inst = ok(pm.get(f"{API}/projects/{site['project']['id']}/installation"))
    assert [o["text"] for o in inst["outstanding"]] == ["Cable tray loose"]
    first = inst["visits"][0]
    ok(
        tech.post(
            f"{API}/visits/{first['id']}/issues/0/close",
            json={"version": first["version"], "action": "Re-fixed"},
        )
    )
    assert ok(pm.get(f"{API}/projects/{site['project']['id']}/installation"))["outstanding"] == []
    # Every bank of the project has reached each stage, so the milestones are done.
    ms = {
        m["kind"]: m["done_at"]
        for m in ok(pm.get(f"{API}/projects/{site['project']['id']}"))["milestones"]
    }
    assert (ms["installation"], ms["commissioning"], ms["handover"]) == (TODAY, TODAY, TODAY)


def test_bank_changes_keep_every_battery_s_history(site):
    pm, tech = site["pm"], site["tech"]
    b1, b2 = bank(site), bank(site, "Bank 2")
    b1 = ok(pm.post(f"{API}/banks/{b1['id']}/batteries", json={"version": b1["version"],
            "batteries": [{"serial": "B1", "position": "1"}, {"serial": "B2", "position": "2"}]}))  # fmt: skip
    taken = pm.post(
        f"{API}/banks/{b2['id']}/batteries",
        json={
            "version": b2["version"],
            "batteries": [{"serial": "B3", "position": "1"}, {"serial": "B4", "position": "1"}],
        },
    )
    assert taken.status_code == 422
    b1 = ok(
        pm.post(
            f"{API}/banks/{b1['id']}/replace",
            json={
                "version": b1["version"],
                "serial": "B2",
                "new_serial": "B5",
                "reason": "Low voltage",
            },
        )
    )
    assert [p["serial"] for p in b1["positions"]] == ["B1", "B5"]
    b1 = ok(pm.post(f"{API}/banks/{b1['id']}/relocate", json={"version": b1["version"], "serial": "B1", "to_bank_id": b2["id"],
                    "position": "4", "reason": "Rebalanced strings"}))  # fmt: skip
    b1 = ok(
        pm.post(
            f"{API}/banks/{b1['id']}/remove",
            json={"version": b1["version"], "serial": "B5", "reason": "Swollen case"},
        )
    )
    assert b1["positions"] == []
    hist = {(h["serial"], h["start_reason"], h["end_reason"]) for h in b1["history"]}
    assert hist == {
        ("B1", "added", "relocated"),
        ("B2", "added", "replaced"),
        ("B5", "replacement", "removed"),
    }
    assert [h["replaces"] for h in b1["history"] if h["serial"] == "B5"] == ["B2"]
    assert [p["serial"] for p in ok(pm.get(f"{API}/banks/{b2['id']}"))["positions"]] == ["B1"]
    # A removal needs a reason; a technician changes a bank only on their visit.
    assert (
        pm.post(
            f"{API}/banks/{b1['id']}/remove",
            json={"version": b1["version"], "serial": "B5", "reason": ""},
        ).status_code
        == 422
    )
    assert tech.post(
        f"{API}/banks/{b2['id']}/remove", json={"version": 1, "serial": "B1", "reason": "Loose"}
    ).status_code in (403, 409)
    # The removed battery's timeline tells the whole story.
    sid = ok(pm.get(f"{API}/trace/scan?code=B2"))["id"]
    t = ok(pm.get(f"{API}/serials/{sid}/timeline"))
    titles = [e["title"] for e in t["events"]]
    assert (
        titles[0] == "Received from the supplier"
        and "Dispatched to the customer" in titles
        and "Delivered" in titles
    )
    assert titles[-2:] == ["Placed in a battery bank", "Replaced in the bank"]
    assert (
        t["now"]["status"] == "removed"
        and "bank" not in t["now"]
        and t["now"]["site"]["name"] == "Tower A"
    )
    t5 = ok(pm.get(f"{API}/serials/{ok(pm.get(f'{API}/trace/scan?code=B5'))['id']}/timeline"))
    assert "Replaced B2" in [e["title"] for e in t5["events"]]


def test_a_battery_not_delivered_here_cannot_go_into_the_bank(site, user_with):
    pm = site["pm"]
    other = ok(
        pm.post(f"{API}/projects/{site['project']['id']}/sites", json={"name": "Tower B"}), 201
    )
    b = ok(pm.post(f"{API}/sites/{other['id']}/banks", json={"name": "Bank 1"}), 201)
    r = pm.post(
        f"{API}/banks/{b['id']}/batteries",
        json={"version": b["version"], "batteries": [{"serial": "B1", "position": "1"}]},
    )
    assert r.status_code == 422 and "not been delivered to this site" in r.text
    v = visit(site, "installation", [bank(site)])
    r = site["tech"].put(
        f"{API}/visits/{v['id']}/record",
        json={"version": v["version"], "received_serials": ["NOPE"]},
    )
    assert r.status_code == 422 and "Not delivered to this site" in r.text
    # A visit is scheduled only for banks of its own site and people who can record the work.
    r = pm.post(
        f"{API}/visits",
        json={
            "site_id": site["site"]["id"],
            "stage": "installation",
            "bank_ids": [b["id"]],
            "scheduled_on": TODAY,
        },
    )
    assert r.status_code == 422
    sales = ok(user_with("sales_officer").get(f"{API}/auth/me"))["user"]["id"]
    r = pm.post(
        f"{API}/visits",
        json={
            "site_id": site["site"]["id"],
            "stage": "other",
            "scheduled_on": TODAY,
            "technician_ids": [sales],
        },
    )
    assert r.status_code == 422 and "record installation work" in r.text


def test_qr_labels_lead_to_a_public_check_that_reveals_nothing_private(site):
    pm = site["pm"]
    sid = ok(pm.get(f"{API}/trace/scan?code=B1"))["id"]
    lab = ok(pm.get(f"{API}/serial-labels?ids={sid}"))["items"][0]
    assert lab["qr"].startswith("data:image/svg+xml") and lab["url"].startswith(
        get_settings().public_base_url
    )
    token = lab["url"].rsplit("/", 1)[1]
    assert sid.replace("-", "") not in lab["url"] and len(token) == 32
    assert (
        ok(pm.get(f"{API}/trace/scan", params={"code": lab["url"]}))["id"] == sid
    )  # a scanned label finds it
    anyone = new_client()
    v = ok(anyone.get(f"{API}/public/verify/{token}"))
    assert v == {"serial": "B1", "product": "UNIT-2V", "status": "not_in_service"} or set(v) == {
        "serial",
        "product",
        "status",
    }
    assert "Desert Telecom" not in str(v) and "Tower" not in str(v)
    assert anyone.get(f"{API}/public/verify/{'0' * 32}").status_code == 404
    assert anyone.get(f"{API}/public/verify/../serials").status_code == 404
    assert anyone.get(f"{API}/serials/{sid}/timeline").status_code == 401


def test_evidence_files_are_checked_by_content_and_kept_safely(site):
    pm, tech, other = site["pm"], site["tech"], site["other"]
    v = visit(site, "installation", [bank(site)])
    assert (
        photo(tech, v, data=b"<script>alert(1)</script>", name="x.jpg").status_code == 422
    )  # named .jpg, isn't
    assert photo(tech, v, data=PDF, name="r.pdf").status_code == 422  # a photo must be an image
    assert photo(other, v).status_code == 403  # not on this visit
    f = ok(photo(tech, v, kind="test_report", data=PDF, name="../../etc/report.pdf"), 201)
    assert f["name"] == "report.pdf" and f["content_type"] == "application/pdf"
    r = pm.get(f"{API}/files/{f['id']}")
    assert (
        r.status_code == 200
        and r.content == PDF
        and r.headers["content-disposition"].startswith("attachment")
    )
    old = get_settings().max_upload_mb
    get_settings().max_upload_mb = 0
    try:
        assert photo(tech, v).status_code == 413
    finally:
        get_settings().max_upload_mb = old
    assert new_client().get(f"{API}/files/{f['id']}").status_code == 401
    ok(
        pm.post(
            f"{API}/visits/{v['id']}/cancel",
            json={
                "version": ok(pm.get(f"{API}/visits/{v['id']}"))["version"],
                "reason": "Access refused",
            },
        )
    )
    assert photo(tech, v).status_code == 409
