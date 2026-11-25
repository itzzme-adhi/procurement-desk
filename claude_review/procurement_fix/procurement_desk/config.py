"""Playbook-driven configuration.

Every number (category values, supplier status/contracts, dated amendments,
currency table) is read from the official playbook markdown files, so the
configuration cannot drift from the playbook.  Public API kept from the
original project: ``normalize_category`` and ``get_rules``.
"""
from __future__ import annotations

import os
import re
from datetime import date
from fractions import Fraction
from pathlib import Path

PLAYBOOK_ENV = "PROCUREMENT_PLAYBOOK"


def _find_playbook() -> Path:
    here = Path(__file__).resolve()
    candidates = []
    if os.environ.get(PLAYBOOK_ENV):
        candidates.append(Path(os.environ[PLAYBOOK_ENV]))
    for base in [here.parents[1], here.parents[2]]:
        candidates += [base / "playbook", base / "playbook" / "playbook",
                       base / "data" / "playbook", base / "extracted" / "playbook"]
    for c in candidates:
        if (c / "policies" / "decision-procedure.md").exists():
            return c
    raise FileNotFoundError(
        "Playbook folder not found; set %s to the folder that contains policies/" % PLAYBOOK_ENV)


def _num(text: str) -> Fraction:
    """'₹1,19,990' -> 119990 ; '0.05' -> 1/20 (exact)."""
    return Fraction(re.sub(r"[^\d.]", "", text))


def _field(text: str, label: str):
    m = re.search(r"\*\*%s:\*\*\s*(.+)" % re.escape(label), text)
    return m.group(1).strip() if m else None


def _load(root: Path):
    categories, titles = {}, {}
    for f in sorted((root / "categories").glob("*.md")):
        t = f.read_text(encoding="utf-8")
        title = re.search(r"#\s*Category:\s*(.+)", t).group(1).strip()
        slug = f.stem
        gst = _field(t, "GST rate")
        pack = re.search(r"\*\*Pack conversion:\*\*\s*(.+)", t)
        rec = {
            "title": title,
            "gst": Fraction(re.sub(r"[^\d.]", "", gst)) / 100,
            "target": _num(_field(t, "Target price")),
            "walkaway": _num(_field(t, "Walk-away price")),
            "rounds": int(_field(t, "Negotiation rounds (R)")),
            "beta": int(_field(t, "Concession exponent (β)")),
            "approval": _num(_field(t, "Approval limit")),
            "unit": _field(t, "Normalized unit"),
            "pack_note": pack.group(1).strip() if pack else None,
        }
        unit = rec["unit"].lower()
        rec["period"] = "monthly" if "per month" in unit else ("yearly" if "per year" in unit else None)
        categories[slug] = rec
        titles[title.lower()] = slug

    amendments = []
    for f in sorted((root / "amendments").glob("*.md")):
        t = f.read_text(encoding="utf-8")
        cat = titles[re.search(r"#\s*AMD-\d+:\s*(.+)", t).group(1).strip().lower()]
        eff = date.fromisoformat(_field(t, "Effective from"))
        ch = _field(t, "Change")
        m = re.search(r"the (walk-away price|target price|approval limit) for .*? is now\s*(₹?[\d,\.]+)", ch)
        field = {"walk-away price": "walkaway", "target price": "target",
                 "approval limit": "approval"}[m.group(1)]
        amendments.append({"id": f.stem, "category": cat, "date": eff, "field": field,
                           "value": _num(m.group(2))})
    # later *effective date* wins; ties broken by amendment id
    amendments.sort(key=lambda a: (a["date"], a["id"]))

    suppliers = {}
    for f in sorted((root / "suppliers").glob("*.md")):
        t = f.read_text(encoding="utf-8")
        name = re.search(r"#\s*Supplier:\s*(.+)", t).group(1).strip()
        status_raw = _field(t, "Status") or ""
        status = ("BLACKLISTED" if status_raw.upper().startswith("BLACKLISTED")
                  else "PROBATION" if status_raw.upper().startswith("PROBATION") else "Approved")
        caps, rates = {}, {}
        contract = _field(t, "Contract") or ""
        m = re.search(r"price cap for (.+?) of\s*(₹?[\d,\.]+)", contract)
        if m:
            caps[titles[m.group(1).strip().lower()]] = _num(m.group(2))
        m = re.search(r"invoices in ([A-Z]{3}) are converted at\s*₹?\s*([\d,\.]+)", contract)
        if m:
            rates[m.group(1)] = _num(m.group(2))
        suppliers[name] = {"status": status, "price_caps": caps, "rates": rates}

    units = (root / "policies" / "units-and-currency.md").read_text(encoding="utf-8")
    rates = {m.group(1): Fraction(m.group(2))
             for m in re.finditer(r"^\|\s*([A-Z]{3})\s*\|\s*([\d.]+)\s*\|", units, re.M)}
    rates["INR"] = Fraction(1)
    return categories, amendments, suppliers, rates


PLAYBOOK_DIR = _find_playbook()
CATEGORIES, AMENDMENTS, SUPPLIERS, CURRENCY_RATES = _load(PLAYBOOK_DIR)


def normalize_category(category: str) -> str:
    c = str(category).strip().lower().replace("_", "-").replace(" ", "-")
    if c == "security":
        c = "security-guards"
    return c


def get_rules(category: str, supplier: str, thread_date) -> dict:
    """Category rules after dated amendments and supplier contract overrides."""
    cat = normalize_category(category)
    if cat not in CATEGORIES:
        raise KeyError("Unknown category: %s" % category)
    if isinstance(thread_date, str):
        thread_date = date.fromisoformat(thread_date[:10])
    rules = dict(CATEGORIES[cat])
    rules["category"] = cat
    for a in AMENDMENTS:                       # sorted by effective date
        if a["category"] == cat and a["date"] <= thread_date:
            rules[a["field"]] = a["value"]
    sup = SUPPLIERS.get(supplier, {"status": "Approved", "price_caps": {}, "rates": {}})
    if cat in sup["price_caps"]:               # contract cap replaces walk-away
        rules["walkaway"] = sup["price_caps"][cat]
    rules["supplier_status"] = sup["status"]
    rules["supplier_known"] = supplier in SUPPLIERS
    rules["currency_rates"] = {**CURRENCY_RATES, **sup["rates"]}
    return rules
