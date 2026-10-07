"""Generic helpers for wrapping a server-rendered (Django-style) web app that has no public API.

This is the technique the production integration is built on: log in like a browser
(session cookie + CSRF token), read HTML pages, turn tables and forms into JSON, and
call the few internal JSON endpoints the UI itself uses.

Nothing here is tied to a specific application. The live adapter that maps these helpers
onto a real system's routes is intentionally not part of this public repository.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Comment, Tag


class ScrapeError(RuntimeError):
    pass


def text_of(el: Tag | None) -> str:
    """Visible text of an element with whitespace collapsed."""
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


# --------------------------------------------------------------------------- #
# HTML -> data
# --------------------------------------------------------------------------- #
def parse_tables(soup: BeautifulSoup | Tag) -> list[dict[str, Any]]:
    """Turn every <table> with a header row into {"headers", "rows"[, "caption", "footer"]}.

    Each row is a dict header -> cell text, plus:
      _row_id : numeric id taken from <tr id="something-123">, if present
      _links  : hrefs found in the row (used to recover record ids from edit/detail links)
      _actions: onclick handlers that carry ids (e.g. "openModal(42)")
    """
    out = []
    for table in soup.find_all("table"):
        head_rows = table.select("thead tr")
        header_tr = head_rows[-1] if head_rows else table.find("tr")
        if header_tr is None:
            continue
        ths = header_tr.find_all("th") or header_tr.find_all("td")
        headers = []
        for th in ths:  # tolerate malformed nested <th>: use each th's own text
            own = re.sub(r"\s+", " ", " ".join(
                x for x in th.find_all(string=True, recursive=False) if not isinstance(x, Comment))).strip()
            headers.append(own or text_of(th) if not th.find("th") else own)
        caption = text_of(head_rows[0]) if len(head_rows) > 1 else None
        body = table.find("tbody") or table
        rows = []
        for tr in body.find_all("tr", recursive=False):
            if tr is header_tr:
                continue
            cells = tr.find_all("td", recursive=False)
            if not cells or (len(cells) == 1 and cells[0].get("colspan")):
                continue  # empty / "No records found." placeholder row
            row: dict[str, Any] = {}
            for i, td in enumerate(cells):
                key = headers[i] if i < len(headers) and headers[i] else f"col{i}"
                # UIs often truncate long text and keep the full value in title=
                titled = td.find(attrs={"title": True})
                full = titled.get("title") if titled is not None and titled.name == "span" else None
                row[key] = (full or text_of(td)).strip()
            m = re.search(r"(\d+)$", tr.get("id", "") or "")
            if m:
                row["_row_id"] = int(m.group(1))
            links = [a["href"].strip() for a in tr.find_all("a", href=True) if a["href"].strip() not in ("#", "")]
            if links:
                row["_links"] = links
            actions = [re.sub(r"\s+", " ", el["onclick"]).strip()
                       for el in tr.find_all(attrs={"onclick": True}) if re.search(r"\d", el["onclick"])]
            if actions:
                row["_actions"] = actions[:6]
            rows.append(row)
        entry: dict[str, Any] = {"headers": headers, "rows": rows}
        if caption:
            entry["caption"] = caption
        tfoot = table.find("tfoot")
        if tfoot:
            entry["footer"] = [text_of(tr) for tr in tfoot.find_all("tr")]
        out.append(entry)
    return out


def parse_form(form: Tag) -> dict[str, Any]:
    """Current values of an HTML form as {name: value | [values]} (CSRF token excluded).

    Reading the form first and then posting it back with only the requested fields changed
    is how partial updates are done safely on apps that only accept full-form submissions.
    """
    data: dict[str, Any] = {}

    def put(name: str, value: Any):
        if name in data:
            if not isinstance(data[name], list):
                data[name] = [data[name]]
            data[name].append(value)
        else:
            data[name] = value

    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or name == "csrfmiddlewaretoken":
            continue
        if el.name == "select":
            sel = [o.get("value", text_of(o)) for o in el.find_all("option") if o.has_attr("selected")]
            put(name, sel if el.has_attr("multiple") else (sel[0] if sel else ""))
        elif el.name == "textarea":
            put(name, el.get_text())
        else:
            t = (el.get("type") or "text").lower()
            if t in ("file", "submit", "button"):
                continue
            if t in ("checkbox", "radio"):
                if el.has_attr("checked"):
                    put(name, el.get("value", "on"))
                elif t == "checkbox":
                    data.setdefault(name, None)
                continue
            put(name, el.get("value", ""))
    return data


def form_options(form: Tag) -> dict[str, list[dict[str, str]]]:
    """Options of every <select>: {field: [{value, label}]} — used to turn ids into names."""
    return {sel["name"]: [{"value": o.get("value", ""), "label": text_of(o)}
                          for o in sel.find_all("option") if o.get("value", "") != ""]
            for sel in form.find_all("select") if sel.get("name")}


def pagination(soup: BeautifulSoup) -> list[int]:
    """Page numbers found in ?page=N links."""
    pages = {int(m.group(1)) for a in soup.find_all("a", href=True)
             if (m := re.search(r"[?&]page=(\d+)", a["href"]))}
    return sorted(pages)


def flatten_form(d: dict[str, Any]):
    """dict -> (key, value) pairs the way a browser encodes a form (lists repeat the key)."""
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            for x in v:
                yield k, str(x)
        elif isinstance(v, bool):
            if v:
                yield k, "on"
        else:
            yield k, str(v)


# --------------------------------------------------------------------------- #
# Session client
# --------------------------------------------------------------------------- #
class SessionClient:
    """Browser-like HTTP session for a Django app: CSRF-aware login, transparent re-login
    when the session expires, HTML/JSON helpers and form submission with error collection."""

    def __init__(self, base_url: str, email: str, password: str,
                 login_path: str = "/accounts/login/", timeout: float = 60.0):
        if not base_url:
            raise ScrapeError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.email, self.password, self.login_path = email, password, login_path
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": "rfq-mcp/1.0"})
        self._logged_in = False

    def _csrf(self) -> str:
        tok = self.http.cookies.get("csrftoken")
        if not tok:
            self.http.get(self.login_path)
            tok = self.http.cookies.get("csrftoken", "")
        return tok

    def _on_login_page(self, r: httpx.Response) -> bool:
        return urlparse(str(r.url)).path.rstrip("/") == self.login_path.rstrip("/")

    def login(self) -> None:
        if not self.email or not self.password:
            raise ScrapeError("Credentials are not configured.")
        r = self.http.get(self.login_path)
        tok_el = BeautifulSoup(r.text, "html.parser").find("input", {"name": "csrfmiddlewaretoken"})
        tok = tok_el["value"] if tok_el else self.http.cookies.get("csrftoken", "")
        r = self.http.post(self.login_path, data={"csrfmiddlewaretoken": tok, "email": self.email,
                                                  "password": self.password},
                           headers={"Referer": self.base_url + self.login_path})
        if self._on_login_page(r):
            msg = text_of(BeautifulSoup(r.text, "html.parser").select_one(".alert, .error"))
            raise ScrapeError(f"Login failed. {msg}".strip())
        self._logged_in = True

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        if not self._logged_in:
            self.login()
        headers = kw.pop("headers", {}) or {}
        if method.upper() != "GET":
            headers.setdefault("X-CSRFToken", self._csrf())
            headers.setdefault("Referer", self.base_url + "/")
        r = self.http.request(method, path, headers=headers, **kw)
        if self._on_login_page(r):  # session expired -> log in again once and retry
            self.login()
            if method.upper() != "GET":
                headers["X-CSRFToken"] = self._csrf()
            r = self.http.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            raise ScrapeError(f"{method} {path} -> HTTP {r.status_code}")
        return r

    def soup(self, path: str, params: dict | None = None) -> BeautifulSoup:
        r = self.request("GET", path, params={k: v for k, v in (params or {}).items() if v not in (None, "")})
        return BeautifulSoup(r.text, "html.parser")

    def json(self, method: str, path: str, **kw) -> Any:
        headers = kw.pop("headers", {}) or {}
        headers.setdefault("Accept", "application/json")
        headers.setdefault("X-Requested-With", "XMLHttpRequest")
        r = self.request(method, path, headers=headers, **kw)
        try:
            return r.json()
        except ValueError:
            raise ScrapeError(f"{path} did not return JSON")

    def form_post(self, path: str, data: Any, files: Any = None) -> dict[str, Any]:
        """Submit a form and report what happened: final URL, flash messages, and whether the
        app re-rendered the form (which on Django usually means validation errors)."""
        page = BeautifulSoup(self.request("GET", path).text, "html.parser")
        tok_el = page.find("input", {"name": "csrfmiddlewaretoken"})
        tok = tok_el["value"] if tok_el else self._csrf()
        pairs = [("csrfmiddlewaretoken", tok)] + list(flatten_form(data) if isinstance(data, dict) else data)
        body: dict[str, Any] = {}
        for k, v in pairs:
            body.setdefault(k, [])
            body[k].append(v)
        body = {k: v[0] if len(v) == 1 else v for k, v in body.items()}
        r = self.request("POST", path, data=body, files=files, headers={"Referer": self.base_url + path})
        s = BeautifulSoup(r.text, "html.parser")
        msgs = [text_of(m) for m in s.select(".alert, .messages li, .errorlist li") if text_of(m)]
        stayed = urlparse(str(r.url)).path.rstrip("/") == path.rstrip("/")
        return {"final_url": str(r.url), "messages": msgs[:20], "stayed_on_form": stayed}
