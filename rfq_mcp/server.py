"""RFQ MCP server.

Lets Claude work with a procurement / RFQ (request for quotation) platform: find suppliers,
create and update RFQs, track who has responded, compare quotes and mark the winner.

This public version runs on an in-memory demo backend (fictional data, simulated e-mails).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from rfq_mcp.backends.demo import DemoBackend
from rfq_mcp.safety import DANGER, READ, WRITE, write_guard

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

mcp = FastMCP("rfq", instructions=(
    "Procurement RFQ platform. rfq_id is the internal id from list_rfqs (not the RFQ-xxxx number). "
    "quote_id comes from list_rfq_vendors or compare_quotes. Several write tools e-mail real suppliers: "
    "always explain what will happen and get the user's explicit approval before passing confirm=true."))

backend = DemoBackend()
DOWNLOAD_DIR = Path(os.getenv("RFQ_DOWNLOAD_DIR") or Path.home() / "Downloads" / "rfq")


# =========================================================================== #
# Overview & reference data
# =========================================================================== #
@mcp.tool(annotations=READ)
def get_dashboard() -> dict[str, Any]:
    """Summary counts (open RFQs, suppliers, quotes received) and the most recent RFQs."""
    return backend.dashboard()


@mcp.tool(annotations=READ)
def get_reference_data(kind: Literal["category_groups", "compliances", "regions"]) -> Any:
    """Category groups (with their categories), compliance certificates or regions, with ids.
    Use it to look up ids before creating suppliers or RFQs."""
    return backend.reference_data(kind)


# =========================================================================== #
# RFQs
# =========================================================================== #
@mcp.tool(annotations=READ)
def list_rfqs(search: str = "", only_with_quotes: bool = False, archived: bool = False) -> dict[str, Any]:
    """List RFQs with dates, creator, number of suppliers assigned and quotes received."""
    rows = backend.list_rfqs(archived=archived, only_with_quotes=only_with_quotes, search=search)
    return {"count": len(rows), "rfqs": rows}


@mcp.tool(annotations=READ)
def get_rfq(rfq_id: int) -> dict[str, Any]:
    """Full RFQ: title, line items (part no, description, qty, material), message, deadline, suppliers."""
    return backend.get_rfq(rfq_id)


@mcp.tool(annotations=READ)
def list_rfq_vendors(rfq_id: int) -> dict[str, Any]:
    """Suppliers assigned to an RFQ and whether each one quoted, declined or has not responded."""
    return backend.rfq_vendors(rfq_id)


@mcp.tool(annotations=DANGER)
def create_rfq(title: str, items: list[dict[str, Any]], vendor_ids: list[int], expiry_date: str,
               email_message: str = "", confirm: bool = False) -> dict[str, Any]:
    """Create an RFQ and E-MAIL IT TO EVERY SELECTED SUPPLIER.
    items: [{part_number, description, quantity, unit ('Each'|'Set'), material}]
    vendor_ids: ids from find_vendors_for_rfq. expiry_date: 'YYYY-MM-DD'.
    Requires confirm=true after the user has approved the supplier list and message."""
    write_guard(confirm)
    if not items:
        raise ValueError("At least one item is required.")
    if not vendor_ids:
        raise ValueError("At least one supplier is required.")
    return backend.create_rfq(title, items, vendor_ids, expiry_date, email_message)


@mcp.tool(annotations=DANGER)
def update_rfq(rfq_id: int, title: str | None = None, expiry_date: str | None = None,
               email_message: str | None = None, item_updates: list[dict[str, Any]] | None = None,
               add_vendor_ids: list[int] | None = None, remove_vendor_ids: list[int] | None = None,
               confirm: bool = False) -> dict[str, Any]:
    """Update an RFQ; fields you leave out stay unchanged.
    item_updates: [{index: 1, quantity: 50}] where index is the item's position in get_rfq.
    WARNING: every update e-mails ALL assigned suppliers, even if only the deadline changes, and
    add_vendor_ids sends the RFQ to the new suppliers. Requires confirm=true."""
    write_guard(confirm)
    return backend.update_rfq(rfq_id, title, expiry_date, email_message, item_updates,
                              add_vendor_ids, remove_vendor_ids)


@mcp.tool(annotations=DANGER)
def rfq_action(rfq_id: int, action: Literal["copy", "archive", "unarchive"], confirm: bool = False) -> dict[str, Any]:
    """Copy, archive or unarchive an RFQ. Requires confirm=true."""
    write_guard(confirm)
    return backend.rfq_action(rfq_id, action)


# =========================================================================== #
# Quotes
# =========================================================================== #
@mcp.tool(annotations=READ)
def get_quote(quote_id: int) -> dict[str, Any]:
    """One supplier's quote: unit prices and line totals, currency, lead time, payment and shipping terms."""
    return backend.get_quote(quote_id)


@mcp.tool(annotations=READ)
def compare_quotes(rfq_id: int) -> dict[str, Any]:
    """All quotes for an RFQ side by side, plus the lowest total and the shortest lead time."""
    return backend.compare_quotes(rfq_id)


@mcp.tool(annotations=READ)
def export_quotes_csv(rfq_id: int, save_dir: str = "") -> dict[str, str]:
    """Write the quote comparison for an RFQ to a CSV file and return its path."""
    return {"file": backend.export_quotes_csv(rfq_id, Path(save_dir) if save_dir else DOWNLOAD_DIR)}


@mcp.tool(annotations=WRITE)
def select_quote(quote_id: int, selected: bool = True) -> dict[str, Any]:
    """Mark a quote as the winning / selected one (or clear the mark)."""
    write_guard()
    return backend.select_quote(quote_id, selected)


@mcp.tool(annotations=WRITE)
def set_quote_markup(quote_item_id: int, markup_price: float) -> dict[str, Any]:
    """Set the sales price (supplier price + margin) for one quote line.
    quote_item_id is in compare_quotes -> quotes[].items[].quote_item_id."""
    write_guard()
    return backend.set_markup(quote_item_id, markup_price)


# =========================================================================== #
# Suppliers
# =========================================================================== #
@mcp.tool(annotations=READ)
def find_vendors_for_rfq(name: str = "", category: str = "", country: str = "", compliance: str = "",
                         onboarding_status: Literal["", "Approved", "Pending", "Rejected"] = "Approved",
                         limit: int = 50) -> dict[str, Any]:
    """Search suppliers by name, capability (category), country or certificate.
    Filters are case-insensitive 'contains' matches. Returned ids go into create_rfq.vendor_ids."""
    rows = backend.list_vendors(name, category, country, compliance, onboarding_status)
    return {"total": len(rows), "vendors": rows[:limit]}


@mcp.tool(annotations=READ)
def get_vendor(vendor_id: int) -> dict[str, Any]:
    """Everything stored about one supplier."""
    return backend.get_vendor(vendor_id)


@mcp.tool(annotations=READ)
def vendor_report(vendor_name: str = "") -> dict[str, Any]:
    """Supplier responsiveness: RFQs received, quoted, declined, not answered, and response rate."""
    rows = [r for r in backend.vendor_report() if vendor_name.lower() in r["vendor"].lower()]
    return {"count": len(rows), "rows": rows}


@mcp.tool(annotations=WRITE)
def create_vendor(name: str, email: str, country: str, region_id: int,
                  category_ids: list[int] | None = None, compliance_ids: list[int] | None = None) -> dict[str, Any]:
    """Add a supplier (starts as 'Pending'). Look up ids with get_reference_data."""
    write_guard()
    return backend.create_vendor(name, email, country, region_id, category_ids or [], compliance_ids or [])


@mcp.tool(annotations=WRITE)
def update_vendor(vendor_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Update only the given supplier fields: name, email, country, region_id, category_ids,
    compliance_ids, onboarding_status."""
    write_guard()
    return backend.update_vendor(vendor_id, changes)


# =========================================================================== #
# Demo only
# =========================================================================== #
@mcp.tool(annotations=READ)
def get_outbox() -> dict[str, Any]:
    """Demo backend only: e-mails the platform would have sent to suppliers."""
    return {"count": len(backend.outbox), "emails": backend.outbox}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
