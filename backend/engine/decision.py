from decimal import Decimal, ROUND_HALF_UP

from backend.engine.config import get_rules
from backend.engine.parser import (
    process_offer,
    detect_prompt_injection,
    detect_banned_terms,
)


def calculate_walkaway(rules, current_round):
    target = Decimal(str(rules["target"]))
    walkaway = Decimal(str(rules["walkaway"]))
    rounds = int(rules["rounds"])
    beta = Decimal(str(rules["beta"]))

    if rules["supplier_status"] == "PROBATION":
        return target

    if rounds <= 1:
        return walkaway

    progress = Decimal(current_round - 1) / Decimal(rounds - 1)
    concession = progress ** int(beta)

    return target + (walkaway - target) * concession


def decide_thread(row):
    message = str(row["supplier_message"])
    supplier = str(row["supplier"])
    category = str(row["category"])
    thread_date = str(row["date"])
    quantity = int(row["quantity"])
    current_round = int(row["round"])

    rules = get_rules(category, supplier, thread_date)

    # 1. Missing usable price information
    offer = process_offer(
        message, category, supplier, thread_date, quantity
    )

    if not offer["success"]:
        return "ESCALATE:MISSING_INFO:"

    # 2. Blacklisted supplier
    if rules["supplier_status"] == "BLACKLISTED":
        return "REJECT:BLACKLISTED_SUPPLIER:"

    # 3. Prompt injection anywhere in the conversation
    if detect_prompt_injection(message):
        return "ESCALATE:PROMPT_INJECTION:"

    # 4. Banned terms in latest supplier message
    from backend.engine.parser import get_latest_message
    latest = get_latest_message(message)

    if detect_banned_terms(latest):
        return "ESCALATE:BANNED_TERM:"

    # 5. Calculate negotiation walk-away
    price = Decimal(offer["effective_price_inr"])
    walkaway = calculate_walkaway(rules, current_round)

    if price > walkaway:
        if current_round >= int(rules["rounds"]):
            return "REJECT:ABOVE_WALKAWAY:"

        counter = int(walkaway.quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ))
        return f"COUNTER:COUNTER_OFFER:{counter}"

    # 6. Approval limit
    total = price * Decimal(quantity)

    if total > Decimal(str(rules["approval"])):
        return "ESCALATE:OVER_APPROVAL_LIMIT:"

    # 7. Accept
    return "ACCEPT:ACCEPTABLE_PRICE:"
