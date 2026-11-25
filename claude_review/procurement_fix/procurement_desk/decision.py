"""Decision engine: first rule that fires wins (policies/decision-procedure.md)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fractions import Fraction
from math import floor

from .config import get_rules
from .parser import (detect_banned_terms, detect_prompt_injection, get_latest_message,
                     process_offer)


def round_half_up(x: Fraction) -> int:
    """Nearest rupee, x.5 rounds up (exact, no float)."""
    return floor(Fraction(x) + Fraction(1, 2))


def calculate_walkaway(rules: dict, current_round: int) -> Fraction:
    """L(r) = target + (walkaway - target) * ((r-1)/(R-1))**beta ; PROBATION -> L = target."""
    target = Fraction(rules["target"])
    if rules["supplier_status"] == "PROBATION":
        return target
    R = int(rules["rounds"])
    r = min(int(current_round), R)
    frac = Fraction(r - 1, R - 1)
    return target + (Fraction(rules["walkaway"]) - target) * frac ** int(rules["beta"])


def decide_thread(row) -> str:
    message = row["supplier_message"]
    supplier, category = row["supplier"], row["category"]
    try:
        q = Decimal(str(row["quantity"]))
        if not q.is_finite() or q <= 0 or q != q.to_integral_value():
            return "ESCALATE:MISSING_INFO:"
        quantity = int(q)
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return "ESCALATE:MISSING_INFO:"

    rnd = int(row["round"])
    rules = get_rules(category, supplier, str(row["date"]))

    offer = process_offer(message, category, supplier, str(row["date"]), quantity)
    if not offer["success"]:                                          # 1 missing info
        return "ESCALATE:MISSING_INFO:"

    quoted_quantity = offer.get("quoted_quantity")
    if quoted_quantity is not None and quoted_quantity != quantity:
        return "ESCALATE:MISSING_INFO:"
    if rules["supplier_status"] == "BLACKLISTED":                     # 2 blacklisted
        return "REJECT:BLACKLISTED_SUPPLIER:"
    if detect_prompt_injection(message):                              # 3 injection (whole thread)
        return "ESCALATE:PROMPT_INJECTION:"
    if detect_banned_terms(get_latest_message(message)):              # 4 banned terms (latest msg)
        return "ESCALATE:BANNED_TERM:"
    p = offer["effective_price_inr"]                                  # 5 price
    L = calculate_walkaway(rules, rnd)
    if p <= L:
        if p * quantity > rules["approval"]:                          # 6 approval limit
            return "ESCALATE:OVER_APPROVAL_LIMIT:"
        return "ACCEPT:ACCEPTABLE_PRICE:"
    if rnd >= rules["rounds"]:
        return "REJECT:ABOVE_WALKAWAY:"
    return "COUNTER:COUNTER_OFFER:%d" % round_half_up(L)
