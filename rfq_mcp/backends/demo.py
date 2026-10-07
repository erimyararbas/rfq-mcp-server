"""In-memory demo backend with fictional suppliers, RFQs and quotes.

It mirrors the behaviour of the production RFQ platform closely enough to try every tool
from Claude without access to any real system. E-mails are never sent: they are appended to
an outbox you can inspect with the `get_outbox` tool.
"""
from __future__ import annotations

import copy
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

SEED = Path(__file__).with_name("data") / "demo_seed.json"


class NotFound(KeyError):
    pass


class DemoBackend:
    def __init__(self, seed_path: Path = SEED):
        self.db: dict[str, Any] = json.loads(seed_path.read_text(encoding="utf-8"))
        self.outbox: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    def _vendor(self, vendor_id: int) -> dict:
        for v in self.db["vendors"]:
            if v["id"] == vendor_id:
                return v
        raise NotFound(f"vendor {vendor_id}")

    def _rfq(self, rfq_id: int) -> dict:
        for r in self.db["rfqs"]:
            if r["id"] == rfq_id:
                return r
        raise NotFound(f"rfq {rfq_id}")

    def _quote(self, quote_id: int) -> dict:
        for q in self.db["quotes"]:
            if q["quote_id"] == quote_id:
                return q
        raise NotFound(f"quote {quote_id}")

    def _category_names(self, ids: list[int]) -> list[str]:
        names = {c["id"]: c["title"] for g in self.db["category_groups"] for c in g["categories"]}
        return [names.get(i, str(i)) for i in ids]

    def _compliance_names(self, ids: list[int]) -> list[str]:
        names = {c["id"]: c["name"] for c in self.db["compliances"]}
        return [names.get(i, str(i)) for i in ids]

    def _vendor_view(self, v: dict) -> dict:
        return {"vendor_id": v["id"], "name": v["name"], "email": v["email"], "country": v["country"],
                "region": self.db["regions"].get(str(v["region_id"])),
                "categories": self._category_names(v["category_ids"]),
                "compliances": self._compliance_names(v["compliance_ids"]),
                "onboarding_status": v["onboarding_status"]}

    def _send_mail(self, to: list[str], subject: str, body: str) -> None:
        self.outbox.append({"to": to, "subject": subject, "body": body, "date": date.today().isoformat()})

    def _next_id(self, rows: list[dict], key: str) -> int:
        return max((r[key] for r in rows), default=0) + 1

    # ------------------------------------------------------------------ reads
    def dashboard(self) -> dict:
        open_rfqs = [r for r in self.db["rfqs"] if not r["archived"]]
        return {"open_rfqs": len(open_rfqs), "vendors": len(self.db["vendors"]),
                "quotes_received": sum(q["status"] == "quoted" for q in self.db["quotes"]),
                "recent_rfqs": [{"rfq_id": r["id"], "number": r["number"], "title": r["title"]}
                                for r in sorted(open_rfqs, key=lambda r: r["sent_date"], reverse=True)[:5]]}

    def reference_data(self, kind: str) -> Any:
        if kind == "category_groups":
            return self.db["category_groups"]
        if kind == "compliances":
            return self.db["compliances"]
        if kind == "regions":
            return [{"id": int(k), "name": v} for k, v in self.db["regions"].items()]
        raise ValueError(kind)

    def list_rfqs(self, archived: bool = False, only_with_quotes: bool = False, search: str = "") -> list[dict]:
        out = []
        for r in self.db["rfqs"]:
            if r["archived"] != archived:
                continue
            if search and search.lower() not in (r["number"] + " " + r["title"]).lower():
                continue
            quotes = [q for q in self.db["quotes"] if q["rfq_id"] == r["id"] and q["status"] == "quoted"]
            if only_with_quotes and not quotes:
                continue
            out.append({"rfq_id": r["id"], "number": r["number"], "title": r["title"],
                        "sent_date": r["sent_date"], "expiry_date": r["expiry_date"],
                        "created_by": r["created_by"], "vendors_assigned": len(r["vendor_ids"]),
                        "quotes_received": len(quotes)})
        return out

    def get_rfq(self, rfq_id: int) -> dict:
        r = copy.deepcopy(self._rfq(rfq_id))
        r["selected_vendors"] = [{"id": v, "name": self._vendor(v)["name"]} for v in r.pop("vendor_ids")]
        r["rfq_id"] = r.pop("id")
        return r

    def rfq_vendors(self, rfq_id: int) -> dict:
        r = self._rfq(rfq_id)
        by_vendor = {q["vendor_id"]: q for q in self.db["quotes"] if q["rfq_id"] == rfq_id}
        rows = []
        for vid in r["vendor_ids"]:
            q = by_vendor.get(vid)
            rows.append({"vendor_id": vid, "vendor": self._vendor(vid)["name"],
                         "status": q["status"] if q else "no_response",
                         "quote_id": q["quote_id"] if q and q["status"] == "quoted" else None})
        counts = {s: sum(x["status"] == s for x in rows) for s in ("quoted", "declined", "no_response")}
        return {"rfq_id": rfq_id, "vendor_count": len(rows), **counts, "vendors": rows}

    def get_quote(self, quote_id: int) -> dict:
        q = copy.deepcopy(self._quote(quote_id))
        items = {i["item_id"]: i for i in self._rfq(q["rfq_id"])["items"]}
        for it in q["items"]:
            src = items.get(it["item_id"], {})
            it.update(part_number=src.get("part_number"), description=src.get("description"),
                      quantity=src.get("quantity"), line_total=round(it["unit_price"] * src.get("quantity", 0), 2))
        q["vendor"] = self._vendor(q["vendor_id"])["name"]
        q["total"] = round(sum(i["line_total"] for i in q["items"]), 2)
        return q

    def compare_quotes(self, rfq_id: int) -> dict:
        self._rfq(rfq_id)
        quotes = [self.get_quote(q["quote_id"]) for q in self.db["quotes"]
                  if q["rfq_id"] == rfq_id and q["status"] == "quoted"]
        cheapest = min(quotes, key=lambda q: q["total"], default=None)
        fastest = min(quotes, key=lambda q: q["lead_time_days"], default=None)
        return {"rfq_id": rfq_id, "quotes": quotes,
                "lowest_total": cheapest and {"vendor": cheapest["vendor"], "total": cheapest["total"],
                                              "currency": cheapest["currency"]},
                "shortest_lead_time": fastest and {"vendor": fastest["vendor"], "days": fastest["lead_time_days"]},
                "note": "Totals in different currencies are not converted."}

    def export_quotes_csv(self, rfq_id: int, save_dir: Path) -> str:
        cmp = self.compare_quotes(rfq_id)
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"rfq_{rfq_id}_quotes.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["vendor", "part_number", "quantity", "unit_price", "line_total", "currency", "lead_time_days"])
            for q in cmp["quotes"]:
                for it in q["items"]:
                    w.writerow([q["vendor"], it["part_number"], it["quantity"], it["unit_price"],
                                it["line_total"], q["currency"], q["lead_time_days"]])
        return str(path.resolve())

    def list_vendors(self, name: str = "", category: str = "", country: str = "",
                     compliance: str = "", onboarding_status: str = "") -> list[dict]:
        def has(values, q):
            return not q or any(q.lower() in str(x).lower() for x in (values if isinstance(values, list) else [values]))
        out = []
        for v in self.db["vendors"]:
            view = self._vendor_view(v)
            if (has(view["name"], name) and has(view["categories"], category) and has(view["country"], country)
                    and has(view["compliances"], compliance)
                    and (not onboarding_status or view["onboarding_status"] == onboarding_status)):
                out.append(view)
        return out

    def get_vendor(self, vendor_id: int) -> dict:
        return self._vendor_view(self._vendor(vendor_id))

    def vendor_report(self) -> list[dict]:
        rows = []
        for v in self.db["vendors"]:
            received = [r for r in self.db["rfqs"] if v["id"] in r["vendor_ids"]]
            qs = [q for q in self.db["quotes"] if q["vendor_id"] == v["id"]]
            quoted = sum(q["status"] == "quoted" for q in qs)
            declined = sum(q["status"] == "declined" for q in qs)
            rows.append({"vendor": v["name"], "rfqs_received": len(received), "quoted": quoted,
                         "declined": declined, "no_response": len(received) - quoted - declined,
                         "response_rate": round((quoted + declined) / len(received), 2) if received else None})
        return rows

    # ------------------------------------------------------------------ writes
    def create_rfq(self, title: str, items: list[dict], vendor_ids: list[int], expiry_date: str,
                   email_message: str = "") -> dict:
        for vid in vendor_ids:
            self._vendor(vid)
        rfq_id = self._next_id(self.db["rfqs"], "id")
        next_item = max((i["item_id"] for r in self.db["rfqs"] for i in r["items"]), default=0) + 1
        rfq = {"id": rfq_id, "number": f"RFQ-{1000 + rfq_id}", "title": title, "created_by": "demo.user",
               "sent_date": date.today().isoformat(), "expiry_date": expiry_date, "archived": False,
               "email_message": email_message, "vendor_ids": list(vendor_ids),
               "items": [{"item_id": next_item + n, "part_number": str(it.get("part_number", "")),
                          "description": it.get("description", ""), "quantity": int(it.get("quantity", 1)),
                          "unit": it.get("unit", "Each"), "material": it.get("material", "")}
                         for n, it in enumerate(items)]}
        self.db["rfqs"].append(rfq)
        self._send_mail([self._vendor(v)["email"] for v in vendor_ids], f"New RFQ {rfq['number']}: {title}",
                        email_message)
        return {"ok": True, "rfq_id": rfq_id, "number": rfq["number"], "emails_sent": len(vendor_ids)}

    def update_rfq(self, rfq_id: int, title: str | None = None, expiry_date: str | None = None,
                   email_message: str | None = None, item_updates: list[dict] | None = None,
                   add_vendor_ids: list[int] | None = None, remove_vendor_ids: list[int] | None = None) -> dict:
        r = self._rfq(rfq_id)
        for up in item_updates or []:
            idx = int(up.get("index", 0))
            if not 1 <= idx <= len(r["items"]):
                raise ValueError(f"Invalid item index: {idx}")
            r["items"][idx - 1].update({k: v for k, v in up.items() if k not in ("index", "item_id")})
        for k, v in (("title", title), ("expiry_date", expiry_date), ("email_message", email_message)):
            if v is not None:
                r[k] = v
        r["vendor_ids"] = [v for v in r["vendor_ids"] if v not in set(remove_vendor_ids or [])]
        new = [v for v in (add_vendor_ids or []) if v not in r["vendor_ids"] and self._vendor(v)]
        existing = list(r["vendor_ids"])
        r["vendor_ids"] += new
        # Same behaviour as the real platform: every edit notifies every assigned supplier.
        if existing:
            self._send_mail([self._vendor(v)["email"] for v in existing], f"RFQ {r['number']} updated", r["email_message"])
        if new:
            self._send_mail([self._vendor(v)["email"] for v in new], f"New RFQ {r['number']}: {r['title']}", r["email_message"])
        return {"ok": True, "rfq_id": rfq_id, "update_emails": len(existing), "new_vendor_emails": len(new)}

    def rfq_action(self, rfq_id: int, action: str) -> dict:
        r = self._rfq(rfq_id)
        if action == "archive":
            r["archived"] = True
        elif action == "unarchive":
            r["archived"] = False
        elif action == "copy":
            new = copy.deepcopy(r)
            new["id"] = self._next_id(self.db["rfqs"], "id")
            new["number"] = f"RFQ-{1000 + new['id']}"
            new["title"] = r["title"] + " (copy)"
            new["vendor_ids"] = []
            self.db["rfqs"].append(new)
            return {"ok": True, "new_rfq_id": new["id"]}
        else:
            raise ValueError(action)
        return {"ok": True, "rfq_id": rfq_id, "archived": r["archived"]}

    def select_quote(self, quote_id: int, selected: bool) -> dict:
        q = self._quote(quote_id)
        if q["status"] != "quoted":
            raise ValueError("Only submitted quotes can be selected.")
        q["selected"] = selected
        return {"ok": True, "quote_id": quote_id, "selected": selected}

    def set_markup(self, quote_item_id: int, markup_price: float) -> dict:
        for q in self.db["quotes"]:
            for it in q["items"]:
                if it["quote_item_id"] == quote_item_id:
                    it["markup_price"] = markup_price
                    return {"ok": True, "quote_item_id": quote_item_id, "unit_price": it["unit_price"],
                            "markup_price": markup_price}
        raise NotFound(f"quote item {quote_item_id}")

    def create_vendor(self, name: str, email: str, country: str, region_id: int,
                      category_ids: list[int], compliance_ids: list[int]) -> dict:
        if any(v["name"].lower() == name.lower() for v in self.db["vendors"]):
            raise ValueError(f"A vendor named '{name}' already exists.")
        vid = self._next_id(self.db["vendors"], "id")
        self.db["vendors"].append({"id": vid, "name": name, "email": email, "country": country,
                                   "region_id": region_id, "category_ids": category_ids,
                                   "compliance_ids": compliance_ids, "onboarding_status": "Pending"})
        return {"ok": True, "vendor_id": vid}

    def update_vendor(self, vendor_id: int, changes: dict) -> dict:
        v = self._vendor(vendor_id)
        allowed = {"name", "email", "country", "region_id", "category_ids", "compliance_ids", "onboarding_status"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown fields: {sorted(unknown)}")
        v.update(changes)
        return {"ok": True, "vendor": self._vendor_view(v)}
