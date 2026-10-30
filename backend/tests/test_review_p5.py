"""Regressions for the independent review of Phase 5 (2026-10-30). Each test failed before its fix."""

from app.auth import _attempts
from tests import test_installation
from tests.conftest import API, new_client
from tests.ops import ok
from tests.test_installation import TODAY, bank, photo, visit

site = test_installation.site  # batteries B1–B6 delivered to Tower A; pm, tech (assigned), other


def place(x, b, serials, start=1):
    return ok(x["pm"].post(f"{API}/banks/{b['id']}/batteries", json={"version": b["version"],
              "batteries": [{"serial": sn, "position": str(i)} for i, sn in enumerate(serials, start)]}))  # fmt: skip


def install(x, b):
    v = visit(x, "installation", [b])
    v = ok(x["tech"].put(f"{API}/visits/{v['id']}/record", json={"version": v["version"],
           "received_serials": [p["serial"] for p in b["positions"]]}))  # fmt: skip
    ok(photo(x["tech"], v), 201)
    ok(
        x["tech"].post(
            f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
        )
    )
    return ok(x["pm"].get(f"{API}/banks/{b['id']}"))


def test_a_technician_moves_batteries_only_into_banks_their_visit_covers(site):  # H1
    b1, b2 = place(site, bank(site), ["B1"]), bank(site, "Bank 2")
    v = visit(site, "other", [b1])
    r = site["tech"].post(f"{API}/banks/{b1['id']}/relocate", json={"version": b1["version"], "visit_id": v["id"],
                          "serial": "B1", "to_bank_id": b2["id"], "position": "1", "reason": "Rebalance"})  # fmt: skip
    assert r.status_code == 422 and "doesn't cover" in r.text


def test_no_battery_skips_a_stage(site):  # H2
    pm, tech = site["pm"], site["tech"]
    b1 = install(site, place(site, bank(site), ["B1", "B2"]))
    planned = place(site, bank(site, "Bank 2"), ["B3"])
    # Not added to a bank that is already installed, and not moved into one from a planned bank.
    r = pm.post(
        f"{API}/banks/{b1['id']}/batteries",
        json={"version": b1["version"], "batteries": [{"serial": "B4", "position": "3"}]},
    )
    assert r.status_code == 409 and "replacement" in r.text
    r = pm.post(f"{API}/banks/{planned['id']}/relocate", json={"version": planned["version"], "serial": "B3",
                "to_bank_id": b1["id"], "position": "3", "reason": "Moved over"})  # fmt: skip
    assert r.status_code == 409 and "same stage" in r.text
    # A replacement into a live bank is recorded on a visit, where its evidence goes.
    r = pm.post(
        f"{API}/banks/{b1['id']}/replace",
        json={
            "version": b1["version"],
            "serial": "B2",
            "new_serial": "B4",
            "reason": "Low voltage",
        },
    )
    assert r.status_code == 422 and "on a visit" in r.text
    v = visit(site, "other", [b1])
    b1 = ok(tech.post(f"{API}/banks/{b1['id']}/replace", json={"version": b1["version"], "visit_id": v["id"],
            "serial": "B2", "new_serial": "B4", "reason": "Low voltage"}))  # fmt: skip
    assert {p["serial"]: p["status"] for p in b1["positions"]} == {
        "B1": "installed",
        "B4": "installed",
    }


def test_saving_a_visit_never_drops_or_quietly_closes_an_issue(site):  # M3
    tech = site["tech"]
    v = visit(site, "installation", [bank(site)])
    v = ok(
        tech.put(
            f"{API}/visits/{v['id']}/record",
            json={
                "version": v["version"],
                "issues": [{"text": "Tray loose"}, {"text": "Door lock"}],
            },
        )
    )
    v = ok(
        tech.post(
            f"{API}/visits/{v['id']}/issues/0/close",
            json={"version": v["version"], "action": "Re-fixed"},
        )
    )
    r = tech.put(f"{API}/visits/{v['id']}/record", json={"version": v["version"], "issues": []})
    assert r.status_code == 422 and "can't be deleted" in r.text
    v = ok(tech.put(f"{API}/visits/{v['id']}/record", json={"version": v["version"],
           "issues": [{"text": "changed"}, {"text": "Door lock (rear)", "status": "closed"}]}))  # fmt: skip
    assert [(i["text"], i["status"], i.get("closed_by")) for i in v["issues"]] == [
        ("Tray loose", "closed", "tech"),
        ("Door lock (rear)", "open", None),
    ]


def test_the_public_check_tells_installed_from_in_service_and_is_rate_limited(
    site,
):  # L5, checkpoint 1
    pm = site["pm"]
    b = install(site, place(site, bank(site), ["B1", "B2"]))
    ok(
        pm.post(
            f"{API}/banks/{b['id']}/remove",
            json={"version": b["version"], "serial": "B2", "reason": "Swollen case"},
        )
    )
    tokens = {}
    for sn in ("B1", "B2"):
        sid = ok(pm.get(f"{API}/trace/scan?code={sn}"))["id"]
        tokens[sn] = ok(pm.get(f"{API}/serial-labels?ids={sid}"))["items"][0]["url"].rsplit("/", 1)[
            1
        ]
    anyone = new_client()
    assert ok(anyone.get(f"{API}/public/verify/{tokens['B1']}"))["status"] == "installed"
    assert ok(anyone.get(f"{API}/public/verify/{tokens['B2']}"))["status"] == "removed"
    _attempts.clear()
    codes = [anyone.get(f"{API}/public/verify/{'f' * 32}").status_code for _ in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429
    _attempts.clear()
