from pathlib import Path

from bs4 import BeautifulSoup

from rfq_mcp.scraping import flatten_form, form_options, pagination, parse_form, parse_tables

SOUP = BeautifulSoup((Path(__file__).parent / "fixtures" / "vendor_list.html").read_text(encoding="utf-8"),
                     "html.parser")


def test_parse_tables_rows_ids_links_and_actions():
    t = parse_tables(SOUP)[0]
    assert t["headers"] == ["Vendor Name", "Country", "Note", "Action"]
    first, second = t["rows"]
    assert first["_row_id"] == 101
    assert first["_links"] == ["/suppliers/edit/101"]
    assert first["_actions"] == ["openArchive(101)"]
    assert first["Note"] == "Full note that the UI truncates on screen"  # full text from title=
    assert second["Vendor Name"] == "Kestrel CNC Works"  # whitespace collapsed


def test_placeholder_rows_are_skipped():
    assert parse_tables(SOUP)[1]["rows"] == []


def test_parse_form_reads_current_values_without_csrf():
    data = parse_form(SOUP.find("form"))
    assert "csrfmiddlewaretoken" not in data
    assert data["name"] == "Brightmill Machining"
    assert data["categories"] == ["11", "21"]
    assert data["status"] == "Approved"
    assert data["active"] == "on" and data["newsletter"] is None
    assert data["notes"] == "Line one"
    assert "logo" not in data


def test_form_options_and_pagination():
    opts = form_options(SOUP.find("form"))
    assert {"value": "12", "label": "CNC Turning"} in opts["categories"]
    assert pagination(SOUP) == [2, 3]


def test_flatten_form_repeats_list_keys_and_drops_none():
    pairs = list(flatten_form({"a": [1, 2], "b": None, "c": True, "d": False, "e": "x"}))
    assert pairs == [("a", "1"), ("a", "2"), ("c", "on"), ("e", "x")]
