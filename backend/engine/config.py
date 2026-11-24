from datetime import date

CATEGORIES = {
    "catering": {
        "gst": 0.05, "target": 280, "walkaway": 340,
        "rounds": 3, "beta": 1, "approval": 600000
    },
    "cleaning": {
        "gst": 0.18, "target": 85000, "walkaway": 100000,
        "rounds": 4, "beta": 2, "approval": 900000
    },
    "cloud-servers": {
        "gst": 0.18, "target": 96000, "walkaway": 115000,
        "rounds": 5, "beta": 3, "approval": 6000000
    },
    "courier": {
        "gst": 0.18, "target": 160, "walkaway": 200,
        "rounds": 4, "beta": 2, "approval": 500000
    },
    "crm-licenses": {
        "gst": 0.18, "target": 18000, "walkaway": 22000,
        "rounds": 4, "beta": 2, "approval": 2500000
    },
    "design-software": {
        "gst": 0.18, "target": 30000, "walkaway": 36000,
        "rounds": 4, "beta": 2, "approval": 1500000
    },
    "headsets": {
        "gst": 0.18, "target": 3500, "walkaway": 4200,
        "rounds": 3, "beta": 1, "approval": 400000
    },
    "laptops": {
        "gst": 0.18, "target": 62000, "walkaway": 70000,
        "rounds": 4, "beta": 2, "approval": 4000000
    },
    "led-lighting": {
        "gst": 0.18, "target": 1800, "walkaway": 2200,
        "rounds": 3, "beta": 1, "approval": 350000
    },
    "mobile-phones": {
        "gst": 0.18, "target": 24000, "walkaway": 28000,
        "rounds": 4, "beta": 2, "approval": 2000000
    },
    "monitors": {
        "gst": 0.18, "target": 14000, "walkaway": 16500,
        "rounds": 3, "beta": 1, "approval": 1200000
    },
    "office-chairs": {
        "gst": 0.18, "target": 9000, "walkaway": 11000,
        "rounds": 3, "beta": 1, "approval": 700000
    },
    "printer-paper": {
        "gst": 0.12, "target": 2200, "walkaway": 2600,
        "rounds": 3, "beta": 1, "approval": 250000
    },
    "security-guards": {
        "gst": 0.18, "target": 21000, "walkaway": 25000,
        "rounds": 4, "beta": 2, "approval": 1000000
    },
    "training": {
        "gst": 0.18, "target": 6000, "walkaway": 7500,
        "rounds": 3, "beta": 1, "approval": 900000
    },
}

# Supplier names must match the CSV supplier column.
SUPPLIERS = {
    "Arvind Office Supplies": {"status": "BLACKLISTED"},
    "Everbright Lighting": {"status": "BLACKLISTED"},
    "Saffron Kitchens": {"status": "BLACKLISTED"},

    "Banyan Guarding": {"status": "PROBATION"},
    "GreenLeaf Facility Care": {"status": "PROBATION"},
    "Jade Express": {"status": "PROBATION"},
    "Vertex Mobile": {"status": "PROBATION"},

    "Deccan Logistics": {
        "status": "Approved",
        "price_caps": {"monitors": 17700}
    },
    "Indus Paper Mills": {
        "status": "Approved",
        "price_caps": {"courier": 200}
    },
    "Jupiter Devices": {
        "status": "Approved",
        "price_caps": {"design-software": 37500}
    },
    "Monsoon Foods": {
        "status": "Approved",
        "price_caps": {"training": 8100}
    },
    "Northstar Facilities": {
        "status": "Approved",
        "price_caps": {"mobile-phones": 29000}
    },
    "Quartz Software": {
        "status": "Approved",
        "price_caps": {"mobile-phones": 29400}
    },

    "Fjord Design Tools": {"status": "Approved", "rates": {"GBP": 105}},
    "Nimbus Hosting": {"status": "Approved", "rates": {"USD": 83}},
    "Redwood Couriers": {"status": "Approved", "rates": {"EUR": 90.5}},
    "Xenon Displays": {"status": "Approved", "rates": {"EUR": 90.5}},
}

# Changes apply only on or after their effective dates.
AMENDMENTS = [
    {"date": date(2026, 10, 14), "category": "courier",
     "field": "approval", "value": 620000},
    {"date": date(2026, 11, 1), "category": "monitors",
     "field": "target", "value": 13790},
    {"date": date(2026, 9, 10), "category": "printer-paper",
     "field": "walkaway", "value": 2720},
    {"date": date(2026, 9, 18), "category": "cleaning",
     "field": "approval", "value": 680000},
    {"date": date(2026, 9, 19), "category": "mobile-phones",
     "field": "approval", "value": 2500000},
    {"date": date(2026, 10, 18), "category": "security-guards",
     "field": "walkaway", "value": 26550},
    {"date": date(2026, 12, 8), "category": "crm-licenses",
     "field": "approval", "value": 3120000},
    {"date": date(2026, 12, 5), "category": "crm-licenses",
     "field": "approval", "value": 1880000},
    {"date": date(2026, 12, 1), "category": "printer-paper",
     "field": "walkaway", "value": 2770},
]

# Standard exchange rates: INR per unit of foreign currency.
CURRENCY_RATES = {
    "INR": 1,
    "USD": 84.5,
    "EUR": 91,
    "GBP": 106,
}


def normalize_category(category):
    """Normalize category names from the CSV."""
    aliases = {
        "security": "security-guards",
    }
    value = str(category).strip().lower().replace("_", "-")
    return aliases.get(value, value)


def get_rules(category, supplier, thread_date):
    """Return category rules after dated amendments and supplier overrides."""
    category = normalize_category(category)
    supplier_info = SUPPLIERS.get(
        str(supplier).strip(),
        {"status": "Approved"}
    )

    if category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category}")

    rules = CATEGORIES[category].copy()

    if isinstance(thread_date, str):
        thread_date = date.fromisoformat(thread_date)

    applicable = [
        amendment for amendment in AMENDMENTS
        if amendment["category"] == category
        and amendment["date"] <= thread_date
    ]

    # Later effective amendments replace earlier values.
    for amendment in sorted(applicable, key=lambda item: item["date"]):
        rules[amendment["field"]] = amendment["value"]

    supplier_cap = supplier_info.get("price_caps", {}).get(category)
    if supplier_cap is not None:
        rules["walkaway"] = supplier_cap

    rules["supplier_status"] = supplier_info["status"]
    rules["currency_rates"] = {
        **CURRENCY_RATES,
        **supplier_info.get("rates", {})
    }

    return rules
