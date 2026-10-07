import pytest

from rfq_mcp import server
from rfq_mcp.backends.demo import DemoBackend
from rfq_mcp.safety import GuardError


@pytest.fixture(autouse=True)
def fresh_backend(monkeypatch):
    monkeypatch.setattr(server, "backend", DemoBackend())
    monkeypatch.delenv("RFQ_ALLOW_WRITE", raising=False)


def enable_writes(monkeypatch):
    monkeypatch.setenv("RFQ_ALLOW_WRITE", "true")


def test_writes_are_off_by_default():
    with pytest.raises(GuardError, match="disabled"):
        server.select_quote(501)


def test_email_sending_tools_need_confirmation(monkeypatch):
    enable_writes(monkeypatch)
    with pytest.raises(GuardError, match="confirm"):
        server.create_rfq("Test", [{"part_number": "X"}], [101], "2026-12-01")
    assert server.get_outbox()["count"] == 0


def test_vendor_status_distinguishes_declined_from_silent():
    res = server.list_rfq_vendors(1)
    status = {v["vendor"]: v["status"] for v in res["vendors"]}
    assert status == {"Brightmill Machining": "quoted", "Kestrel CNC Works": "declined",
                      "Anadolu Talaşlı İmalat": "quoted"}
    assert (res["quoted"], res["declined"], res["no_response"]) == (2, 1, 0)


def test_compare_quotes_finds_cheapest_and_fastest():
    res = server.compare_quotes(1)
    assert res["lowest_total"] == {"vendor": "Anadolu Talaşlı İmalat", "total": 1945.0, "currency": "EUR"}
    assert res["shortest_lead_time"] == {"vendor": "Brightmill Machining", "days": 21}


def test_find_vendors_by_capability_and_certificate():
    names = [v["name"] for v in server.find_vendors_for_rfq(category="milling", compliance="AS9100")["vendors"]]
    assert names == ["Brightmill Machining"]


def test_create_rfq_sends_one_email_per_supplier(monkeypatch):
    enable_writes(monkeypatch)
    res = server.create_rfq("Brackets", [{"part_number": "B-1", "quantity": 10}], [103, 105],
                            "2026-12-01", "Please quote", confirm=True)
    assert res["emails_sent"] == 2
    assert server.get_outbox()["emails"][0]["to"] == ["quotes@harborline.example", "teklif@anadolu-talasli.example"]
    assert server.list_rfq_vendors(res["rfq_id"])["no_response"] == 2


def test_update_rfq_notifies_existing_and_new_suppliers(monkeypatch):
    enable_writes(monkeypatch)
    res = server.update_rfq(2, expiry_date="2026-10-30", add_vendor_ids=[101], confirm=True)
    assert (res["update_emails"], res["new_vendor_emails"]) == (2, 1)


def test_markup_and_selection(monkeypatch):
    enable_writes(monkeypatch)
    server.set_quote_markup(9003, 40.0)
    server.select_quote(502)
    q = server.get_quote(502)
    assert q["selected"] and q["items"][0]["markup_price"] == 40.0


def test_cannot_select_a_declined_quote(monkeypatch):
    enable_writes(monkeypatch)
    with pytest.raises(ValueError):
        server.select_quote(503)


def test_export_csv(tmp_path):
    path = server.export_quotes_csv(1, str(tmp_path))["file"]
    lines = open(path, encoding="utf-8").read().splitlines()
    assert lines[0].startswith("vendor,part_number") and len(lines) == 5
