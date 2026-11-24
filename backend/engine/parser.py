import re
from decimal import Decimal, InvalidOperation

CURRENCY_PATTERNS = [
    (r"INR|Rs\.?|₹|\brupees?\b", "INR"),
    (r"USD|US\$|\$", "USD"),
    (r"EUR|€", "EUR"),
    (r"GBP|£", "GBP"),
    (r"SGD", "SGD"),
]

INJECTION_PATTERNS = [
    r"\b(system|assistant|language model|AI agent|automated system)\b.{0,100}\b(ignore|override|approve|accept|output|instruct|bypass)\b",
    r"\b(ignore|override|bypass)\b.{0,100}\b(previous instructions|system prompt|rules|policy)\b",
    r"\b(pre-approved|approval already granted|already approved)\b",
    r"\b(output|mark this thread as)\s+(ACCEPT|REJECT|COUNTER)\b",
]

BANNED_PATTERNS = [
    r"(?<!no )\bauto(?:matic(?:ally)?)?[- ]?renew(?:al|s|ed|ing)?\b",
    r"\bcontinues? from year to year unless\b",
    r"\brenews? on its own\b",
    r"\bunlimited liability\b",
    r"\bexclusive(?:ly)?\b",
    r"\bmust buy only from\b",
]

def get_latest_message(message):
    markers = list(re.finditer(
        r"\[Latest message\s*[—-]",
        message,
        flags=re.IGNORECASE
    ))
    if markers:
        return message[markers[-1].start():]
    return message

def detect_prompt_injection(message):
    text = message.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            return True
    if re.search(
        r"<!--.*?(assistant|system|AI|approve|accept|override).*?-->",
        message,
        flags=re.IGNORECASE | re.DOTALL
    ):
        return True
    return False

def detect_banned_terms(latest_message):
    text = latest_message.lower()
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True

    advance_matches = re.findall(
        r"\b(\d+(?:\.\d+)?)\s*%\s*(?:advance|upfront|in advance)\b",
        text
    )
    for value in advance_matches:
        if Decimal(value) > Decimal("30"):
            return True

    word_amounts = {
        "thirty": Decimal("30"),
        "forty": Decimal("40"),
        "fifty": Decimal("50"),
        "sixty": Decimal("60"),
        "seventy": Decimal("70"),
        "eighty": Decimal("80"),
        "ninety": Decimal("90"),
        "one hundred": Decimal("100")
    }
    for words, value in word_amounts.items():
        if re.search(
            rf"\b{words}\s+percent\s+(?:advance|upfront)\b",
            text
        ) and value > Decimal("30"):
            return True
    if re.search(
        r"\bhalf\b.{0,60}\b(?:advance|upfront)\b",
        text,
        flags=re.IGNORECASE | re.DOTALL
    ):
        return True

    return False

def find_currency(text):
    for pattern, currency in CURRENCY_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return currency
    return None


def extract_amounts(text):
    small = {
        "zero": 0, "one": 1, "two": 2, "three": 3,
        "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
        "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17,
        "eighteen": 18, "nineteen": 19,
        "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90
    }

    scales = {
        "thousand": 1000,
        "lakh": 100000,
        "lakhs": 100000,
        "million": 1000000,
        "crore": 10000000
    }

    def words_to_number(words):
        total = 0
        current = 0

        for word in re.split(r"[\s-]+", words.lower()):
            if word == "and":
                continue
            if word in small:
                current += small[word]
            elif word == "hundred":
                current = max(current, 1) * 100
            elif word in scales:
                total += max(current, 1) * scales[word]
                current = 0
            else:
                return None

        return Decimal(total + current)

    found = []

    currency_pattern = r"INR|Rs\.?|₹|USD|US\$|\$|EUR|€|GBP|£|SGD"
    numeric_pattern = rf"(?i)(?:{currency_pattern})\s*(\d[\d,]*(?:\.\d+)?)"

    for match in re.finditer(numeric_pattern, text):
        try:
            found.append((match.start(), Decimal(match.group(1).replace(",", ""))))
        except InvalidOperation:
            pass

    number_words = (
        r"zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
        r"seventeen|eighteen|nineteen|twenty|thirty|forty|"
        r"fifty|sixty|seventy|eighty|ninety|hundred|"
        r"thousand|lakh|lakhs|million|crore|and"
    )

    word_pattern = (
        rf"\b((?:{number_words})(?:[\s-]+(?:{number_words}))*)"
        rf"\s+rupees?\b"
    )

    for match in re.finditer(word_pattern, text, flags=re.IGNORECASE):
        value = words_to_number(match.group(1))
        if value is not None:
            found.append((match.start(), value))

    found.sort(key=lambda item: item[0])
    return [amount for _, amount in found]


def extract_offer(message):
    latest = get_latest_message(message)
    currency = find_currency(latest)
    amounts = extract_amounts(latest)
    includes_gst = bool(re.search(
        r"\b(incl\.?\s*GST|including\s+GST|GST\s+included|inclusive of GST)\b",
        latest,
        flags=re.IGNORECASE
    ))
    is_range = bool(re.search(
        r"\b(between|range|ranging)\b",
        latest,
        flags=re.IGNORECASE
    ))

    if not currency or not amounts:
        return {
            "latest_message": latest,
            "currency": currency,
            "amount": None,
            "amounts": amounts,
            "missing_info": True,
            "includes_gst": includes_gst,
            "is_range": is_range
        }

    is_correction = bool(re.search(
        r"\b(correction|corrected|should read)\b",
        latest,
        flags=re.IGNORECASE
    ))

    if is_range:
        amount = max(amounts)
    elif is_correction:
        amount = amounts[0]
    else:
        amount = amounts[-1]

    return {
        "latest_message": latest,
        "currency": currency,
        "amount": amount,
        "amounts": amounts,
        "missing_info": False,
        "includes_gst": includes_gst,
        "is_range": is_range
    }
def extract_discount(text):
    """
    Extract percentage discounts and their conditions.
    """
    text_lower = text.lower()

    discount_matches = re.findall(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:discount|off)",
        text_lower
    )

    discounts = [Decimal(value) for value in discount_matches]

    threshold_match = re.search(
    r"orders?\s+of\s+(\d+)\b.*?\b(?:or more|or above|and above)",
    text_lower
    )

    return {
        "discount_percent": discounts[-1] if discounts else Decimal("0"),
        "has_discount": bool(discounts),
        "quantity_threshold": (
            int(threshold_match.group(1))
            if threshold_match else None
        ),
        "is_previous_quote_discount": bool(
            re.search(r"off\s+(?:our\s+)?previous quote", text_lower)
        )
    }
def calculate_discounted_price(
    base_price,
    discount_info,
    quantity
):
    """
    Calculate the price after applying an eligible discount.
    """
    base_price = Decimal(str(base_price))

    discount = discount_info["discount_percent"]
    threshold = discount_info["quantity_threshold"]

    # Do not apply a discount if the order misses its threshold.
    if threshold is not None and quantity < threshold:
        return base_price

    multiplier = Decimal("1") - (discount / Decimal("100"))

    return base_price * multiplier
def normalize_price(price, currency, rules, includes_gst=False):
    """
    Convert supplier price into INR, excluding GST.
    """
    price = Decimal(str(price))

    rates = rules["currency_rates"]

    if currency not in rates:
        return None

    # Convert to INR
    price = price * Decimal(str(rates[currency]))

    # Remove GST when the quoted price includes it
    if includes_gst:
        gst = Decimal(str(rules["gst"]))
        price = price / (Decimal("1") + gst)

    return price


def process_offer(message, category, supplier, thread_date, quantity):
    from backend.engine.config import get_rules

    offer = extract_offer(message)
    rules = get_rules(category, supplier, thread_date)

    latest = offer["latest_message"]
    currency = offer["currency"]
    includes_gst = offer["includes_gst"]

    total_info = extract_total_order_price(latest)

    if total_info:
        price = total_info["unit_price"]
        quantity = total_info["quantity"]

    else:
        discount_info = extract_discount(latest)

        if discount_info["is_previous_quote_discount"]:
            previous_info = extract_previous_supplier_price(message)

            if previous_info is None:
                return {
                    "success": False,
                    "reason": "MISSING_INFO"
                }

            price = previous_info["amount"]
            currency = previous_info["currency"]

        else:
            tiered_price = extract_tiered_price(latest, quantity)

            if tiered_price is not None:
                price = tiered_price

            elif offer["missing_info"]:
                return {
                    "success": False,
                    "reason": "MISSING_INFO"
                }

            else:
                price = offer["amount"]

        pack_size = extract_pack_size(latest)

        if pack_size:
            price = calculate_pack_unit_price(price, pack_size)

        price = calculate_discounted_price(
            price,
            discount_info,
            quantity
        )

        free_unit_ratio = extract_free_unit_ratio(latest)

        if free_unit_ratio is not None:
            price = price * free_unit_ratio

    annual_categories = {
        "cloud-servers",
        "crm-licenses",
        "design-software"
    }

    monthly_categories = {
        "cleaning",
        "security-guards"
    }

    normalized_category = category.strip().lower().replace("_", "-")

    if normalized_category in annual_categories | monthly_categories:
        billing_period = extract_billing_period(latest)

        if billing_period is None:
            return {
                "success": False,
                "reason": "MISSING_INFO"
            }

        if normalized_category in annual_categories and billing_period == "monthly":
            price = price * Decimal("12")

        elif normalized_category in monthly_categories and billing_period == "yearly":
            price = price / Decimal("12")

    if currency not in rules["currency_rates"]:
        return {
            "success": False,
            "reason": "MISSING_INFO"
        }

    normalized_price = normalize_price(
        price,
        currency,
        rules,
        includes_gst
    )

    if normalized_price is None:
        return {
            "success": False,
            "reason": "MISSING_INFO"
        }

    return {
        "success": True,
        "category": category,
        "supplier": supplier,
        "currency": currency,
        "original_price": str(price),
        "effective_price_inr": str(normalized_price),
        "includes_gst": includes_gst,
        "quantity": quantity
    }


def calculate_unit_price(total_price, quantity):
    """
    Convert total order price into per-unit price.
    """
    total_price = Decimal(str(total_price))
    quantity = Decimal(str(quantity))

    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero")

    return total_price / quantity
def extract_total_order_price(text):
    """
    Detect phrases such as:
    INR 3,49,280 in total for all 25 monitors.
    """
    pattern = (
        r"(?:INR|Rs\.?|₹|USD|US\$|\$|EUR|€|GBP|£|SGD)"
        r"\s*([\d,]+(?:\.\d+)?)"
        r"\s+in total for all\s+(\d+)"
    )

    match = re.search(pattern, text, flags=re.IGNORECASE)

    if not match:
        return None

    total_price = Decimal(match.group(1).replace(",", ""))
    quantity = int(match.group(2))

    if quantity <= 0:
        return None

    return {
        "total_price": total_price,
        "quantity": quantity,
        "unit_price": total_price / Decimal(quantity)
    }

def calculate_pack_unit_price(pack_price, units_per_pack):
    """
    Convert pack price into individual unit price.
    """
    pack_price = Decimal(str(pack_price))
    units_per_pack = Decimal(str(units_per_pack))

    if units_per_pack <= 0:
        raise ValueError("Units per pack must be greater than zero")

    return pack_price / units_per_pack


def extract_pack_size(text):
    """
    Detect pack sizes such as:
    'Each pack contains 4 fixtures.'
    'Pack of 10 boxes.'
    """
    patterns = [
        r"pack\s+contains\s+(\d+)",
        r"pack\s+of\s+(\d+)",
        r"(\d+)\s+units?\s+per\s+pack",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            return int(match.group(1))

    return None

def extract_previous_supplier_price(message):
    """
    Find the most recent earlier supplier price.
    Ignore Acme Procurement messages.
    """
    blocks = re.split(
        r"(?=\[(?:Earlier|Latest) message\s*[—-])",
        message,
        flags=re.IGNORECASE
    )

    previous_price = None

    for block in blocks:
        header = block.split("]", 1)[0].lower()

        # Ignore Acme's messages and the latest supplier message.
        if "acme procurement" in header:
            continue

        if "latest message" in header:
            continue

        amounts = extract_amounts(block)

        if amounts:
            currency = find_currency(block)

            if currency:
                previous_price = {
                    "amount": amounts[-1],
                    "currency": currency
                }

    return previous_price

def extract_tiered_price(text, quantity):
    threshold_match = re.search(
        r"orders?\s+of\s+up\s+to\s+(\d+)",
        text,
        flags=re.IGNORECASE
    )

    if not threshold_match:
        return None

    amounts = extract_amounts(text)

    if len(amounts) < 2:
        return None

    threshold = int(threshold_match.group(1))
    first_price = amounts[0]
    second_price = amounts[1]

    return first_price if quantity <= threshold else second_price

def extract_free_unit_ratio(text):
    """
    Return N/(N+1) for 1 free unit for every N paid units.
    """
    match = re.search(
        r"plus\s+1\s+free\s+\w+.*?for\s+every\s+(\d+)\s+you\s+pay\s+for",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        return None

    paid_units = Decimal(match.group(1))

    if paid_units <= 0:
        return None

    return paid_units / (paid_units + Decimal("1"))


def extract_billing_period(text):
    monthly = bool(re.search(
        r"\bper\s+month\b|\bmonthly\b|/\s*month\b",
        text,
        flags=re.IGNORECASE
    ))

    yearly = bool(re.search(
        r"\bper\s+year\b|\byearly\b|\bannually\b|/\s*year\b",
        text,
        flags=re.IGNORECASE
    ))

    if monthly and yearly:
        return None
    if monthly:
        return "monthly"
    if yearly:
        return "yearly"

    return None
