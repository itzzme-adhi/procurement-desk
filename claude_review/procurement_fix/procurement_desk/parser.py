"""Message parsing: latest message, offer extraction/normalisation,
prompt-injection and banned-term detection.

All money arithmetic uses exact ``fractions.Fraction`` (text is read with
``decimal.Decimal`` first), so nothing is rounded before the final comparison.
"""
from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction

from .config import CATEGORIES, get_rules

# --------------------------------------------------------------------------
# thread structure
# --------------------------------------------------------------------------
_HDR = re.compile(r"^\[(Earlier|Latest) message(?:\s*[—–-]\s*(.*?))?\]\s*$", re.M)


def split_messages(message: str):
    """Return [(kind, header_text, body)] ; kind in Earlier/Latest/Acme/Single."""
    message = message.replace("\r\n", "\n")
    heads = list(_HDR.finditer(message))
    if not heads:
        return [("Latest", "", message.strip())]
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(message)
        meta = (h.group(2) or "")
        kind = h.group(1)
        if kind == "Earlier" and meta.strip().lower().startswith("acme"):
            kind = "Acme"
        out.append((kind, meta, message[h.end():end].strip()))
    return out


def get_latest_message(message: str) -> str:
    """Body of the supplier's latest message (the whole text if there is no marker)."""
    msgs = split_messages(message)
    latest = [m for m in msgs if m[0] == "Latest"]
    return (latest[-1] if latest else msgs[-1])[2]


def get_previous_supplier_message(message: str):
    """Most recent *supplier* message before the latest one (Acme messages ignored)."""
    msgs = split_messages(message)
    earlier = [m for m in msgs if m[0] == "Earlier"]
    return earlier[-1][2] if earlier else None


# --------------------------------------------------------------------------
# numbers, currencies
# --------------------------------------------------------------------------
_CUR_PATTERN = r"(?<![A-Za-z])(?:INR|Rs\.?|₹|US\$|USD|\$|EUR|€|GBP|£|(?-i:[A-Z]{3}))"
_CUR_MAP = {"INR": "INR", "RS": "INR", "RS.": "INR", "₹": "INR", "$": "USD", "US$": "USD",
            "USD": "USD", "EUR": "EUR", "€": "EUR", "GBP": "GBP", "£": "GBP"}
_SCALE = {"k": 1000, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "lac": 100000,
          "crore": 10000000, "crores": 10000000, "cr": 10000000}

_AMOUNT = r"\d[\d,]*(?:\.\d+)?"
_SUFFIX = r"(?:\s?(?:k|lakhs?|lac|crores?|cr|thousand)\b)?"
MONEY_RE = re.compile(r"(?P<cur>%s)\s?(?P<amt>%s)(?P<scale>%s)" % (_CUR_PATTERN, _AMOUNT, _SUFFIX), re.I)

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_WORD_SCALE = {"thousand": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10000000, "crores": 10000000}
_NUMWORD = "|".join(list(_UNITS) + list(_TENS) + ["hundred", "and"] + list(_WORD_SCALE))
WORDS_RE = re.compile(r"\b(?P<w>(?:(?:%s)(?:[\s-]+|(?=\s*rupees?)))+)\s*rupees?\b" % _NUMWORD, re.I)


def words_to_number(words: str):
    total = current = 0
    seen = False
    for tok in re.split(r"[\s-]+", words.lower().strip()):
        if not tok or tok == "and":
            continue
        seen = True
        if tok in _UNITS:
            current += _UNITS[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok == "hundred":
            current = max(current, 1) * 100
        elif tok in _WORD_SCALE:
            total += max(current, 1) * _WORD_SCALE[tok]
            current = 0
        else:
            return None
    return Fraction(total + current) if seen else None


def _to_fraction(amt: str, scale: str):
    value = Fraction(Decimal(amt.replace(",", "")))
    scale = (scale or "").strip().lower()
    return value * _SCALE.get(scale, 1)


def find_money(text: str):
    """All monetary amounts in order: [(start, end, currency, Fraction)]."""
    found = []
    for m in MONEY_RE.finditer(text):
        cur_raw = m.group("cur").upper()
        cur = _CUR_MAP.get(cur_raw, cur_raw)
        found.append((m.start(), m.end(), cur, _to_fraction(m.group("amt"), m.group("scale"))))
    # Also support suffix currency notation, e.g. "5,000 INR".
    suffix_re = re.compile(
        r"(?P<amt>%s)(?P<scale>%s)\s*(?P<cur>%s)" %
        (_AMOUNT, _SUFFIX, _CUR_PATTERN),
        re.I
    )

    for m in suffix_re.finditer(text):
        if any(s < m.end() and m.start() < e
               for s, e, _, _ in found):
            continue

        cur_raw = m.group("cur").upper()
        cur = _CUR_MAP.get(cur_raw, cur_raw)
        found.append((
            m.start(), m.end(), cur,
            _to_fraction(m.group("amt"), m.group("scale"))
        ))

    taken = [(s, e) for s, e, _, _ in found]
    for m in WORDS_RE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        v = words_to_number(m.group("w"))
        if v is not None and v > 0:
            found.append((m.start(), m.end(), "INR", v))
    return sorted(found, key=lambda x: x[0])


# --------------------------------------------------------------------------
# line level helpers
# --------------------------------------------------------------------------
_GST_INCL = re.compile(r"\b(?:incl\.?|including|inclusive\s+of)\s+GST\b|\bGST\s+(?:included|inclusive)\b"
                       r"|\(GST included\)", re.I)
_GST_EXCL = re.compile(r"\b(?:excl\.?|excluding|exclusive\s+of|plus|\+)\s*GST\b|\bGST\s+extra\b", re.I)
_PER_MONTH = re.compile(r"\bper\s+month\b|\bmonthly\b|/\s*month\b|\ba\s+month\b", re.I)
_PER_YEAR = re.compile(r"\bper\s+(?:year|annum)\b|\byearly\b|\bannually\b|/\s*(?:year|yr)\b|\ba\s+year\b", re.I)
_WORDNUM = {"dozen": 12, "ten": 10, "hundred": 100, "thousand": 1000}


def detect_period(text: str):
    m, y = _PER_MONTH.search(text), _PER_YEAR.search(text)
    if m and y:                       # use whichever comes first after the price
        return "monthly" if m.start() < y.start() else "yearly"
    return "monthly" if m else "yearly" if y else None


def _unit_divisor(tail: str, category: str):
    """Per-pack / per-dozen / per-N-units / per-ream divisor ( >1 shrinks the price)."""
    t = tail.lower()
    m = re.search(r"\bper\s+(?:a\s+)?dozen\b", t)
    if m:
        return Fraction(12)
    m = re.search(r"\bper\s+pack\s+of\s+(\d+)\b", t)
    if m:
        return Fraction(int(m.group(1)))
    m = re.search(r"\bper\s+(\d+)\s+[a-z]+", t)
    if m and not re.match(r"\bper\s+\d+\s+(?:days?|weeks?|months?|years?)", t[m.start():]):
        return Fraction(int(m.group(1)))
    if re.search(r"\bper\s+pack\b", t):
        pack = CATEGORIES[category].get("pack_note") or ""
        n = re.search(r"holds\s+(\d+)", pack)
        return Fraction(int(n.group(1))) if n else Fraction(1)
    if re.search(r"\bper\s+ream\b", t) and category == "printer-paper":
        return Fraction(1, 10)          # one box = 10 reams -> box price = ream price x 10
    return Fraction(1)


class OfferError(Exception):
    """Raised when the latest offer has no usable price (-> MISSING_INFO)."""


def _norm_money(cur, amount, rules, gst_incl, period, divisor, subscription):
    """One money amount -> INR excl. GST per category unit."""
    if cur not in rules["currency_rates"]:
        raise OfferError("unlisted currency %s" % cur)
    price = amount * rules["currency_rates"][cur] / divisor
    if gst_incl:
        price = price / (1 + rules["gst"])
    if subscription:
        want = rules["period"]
        if period is None:
            raise OfferError("subscription price without billing period")
        if period != want:
            price = price * 12 if (period == "monthly" and want == "yearly") else price / 12
    return price


_PREV_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*off\s+(?:of\s+)?(?:our|my|the)\s+previous\s+quote", re.I)
_LIST_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*discount\s+on\s+(?:our|the)\s+list\s+price", re.I)
_VOL_PCT = re.compile(r"(?:further\s+)?(\d+(?:\.\d+)?)\s*%\s*off\s+for\s+orders?\s+of\s+(\d+)\s+\w+\s+or\s+(?:more|above)", re.I)
_TIER = re.compile(r"orders?\s+of\s+up\s+to\s+(\d+)", re.I)
_FREE = re.compile(r"plus\s+(\d+)\s+free\s+\w+\s+for\s+every\s+(\d+)\s+you\s+pay\s+for", re.I)
_TOTAL = re.compile(r"in\s+total\s+for\s+all\s+(\d+)\s+\w+", re.I)
_RANGE = re.compile(r"\b(between|ranging)\b", re.I)
_CORR = re.compile(r"should\s+read", re.I)


def _price_lines(text: str):
    """Lines that carry an offer (contain a money amount)."""
    return [ln for ln in text.split("\n") if find_money(ln)]


def extract_offer_price(text: str, category: str, rules: dict, quantity, prev_resolver=None):
    """Normalise the offer in one message body to Fraction INR excl. GST per unit.

    Raises OfferError when there is no usable price.
    """
    subscription = rules["period"] is not None
    lines = _price_lines(text)
    pct_only = [ln for ln in text.split("\n") if _PREV_PCT.search(ln)]
    # --- "D% off our previous quote" (no money in the line) ---------------
    if not lines and pct_only:
        pct = Fraction(Decimal(_PREV_PCT.search(pct_only[0]).group(1)))
        if prev_resolver is None:
            raise OfferError("no previous quote")
        prev = prev_resolver()
        return prev * (1 - pct / 100)
    if not lines:
        raise OfferError("no price")

    # a correction replaces the price it corrects
    corr = [ln for ln in lines if _CORR.search(ln)]
    line = corr[0] if corr else lines[0]
    if corr:
        pre = line[_CORR.search(line).end():]
        money = find_money(pre)[:1]
        base_line = pre
    else:
        money = find_money(line)
        base_line = line
    # GST may be stated on the immediately following line.
    gst_context = line
    all_lines = text.splitlines()

    try:
        line_index = all_lines.index(line)
        if line_index + 1 < len(all_lines):
            next_line = all_lines[line_index + 1]
            if re.search(r"\bGST\b", next_line, re.I):
                gst_context += " " + next_line
    except ValueError:
        pass

    gst_incl = (
        bool(_GST_INCL.search(gst_context))
        and not _GST_EXCL.search(gst_context)
    )
    period = detect_period(line)

    def one(cur, amt, tail):
        return _norm_money(cur, amt, rules, gst_incl, period, _unit_divisor(tail, category), subscription)

    # --- list-price discount ----------------------------------------------
    m = _LIST_PCT.search(line)
    if m:
        _, _, cur, amt = find_money(line)[0]
        p = one(cur, amt, line)
        return p * (1 - Fraction(Decimal(m.group(1))) / 100)

    # --- price range: evaluate at the dearer end (P-01) --------------------
    if _RANGE.search(line) and not corr:
        ps = [one(c, a, line) for _, _, c, a in find_money(line)]
        return max(ps)

    # --- tiers -------------------------------------------------------------
    t = _TIER.search(line)
    if t and len(money) >= 2:
        limit = int(t.group(1))
        first, second = money[0], money[1]
        s, e, cur, amt = first if quantity <= limit else second
        return one(cur, amt, line)

    # --- total order price ---------------------------------------------------
    tot = _TOTAL.search(line)
    if tot:
        s, e, cur, amt = money[0]
        return one(cur, amt, line) / int(tot.group(1))

    s, e, cur, amt = money[0]
    p = one(cur, amt, line)

    # --- volume discount (only if quantity meets the threshold) -----------------
    v = _VOL_PCT.search(line)
    if v and quantity >= int(v.group(2)):
        p = p * (1 - Fraction(Decimal(v.group(1))) / 100)
    # --- free units: "plus 1 free X for every N you pay for" ----------------------
    f = _FREE.search(line)
    if f:
        free, paid = int(f.group(1)), int(f.group(2))
        p = p * paid / (paid + free)
    return p



def _quoted_total_quantity(text: str):
    """Return the quantity stated in the selected total-price offer, if any."""
    lines = _price_lines(text)
    if not lines:
        return None

    corrections = [line for line in lines if _CORR.search(line)]
    selected = corrections[0] if corrections else lines[0]
    match = _TOTAL.search(selected)
    return int(match.group(1)) if match else None


def process_offer(message: str, category: str, supplier: str, thread_date, quantity):
    """Parse the supplier's latest offer. Returns dict with success/effective_price_inr (Fraction)."""
    rules = get_rules(category, supplier, thread_date)
    cat = rules["category"]
    latest = get_latest_message(message)

    # A discount on a previous quote inherits that quote's stated total quantity.
    quote_source = latest
    if not _price_lines(latest) and any(
        _PREV_PCT.search(line) for line in latest.splitlines()
    ):
        previous = get_previous_supplier_message(message)
        if previous:
            quote_source = previous

    quoted_quantity = _quoted_total_quantity(quote_source)

    def prev_resolver():
        prev = get_previous_supplier_message(message)
        if not prev:
            raise OfferError("no previous quote")
        return extract_offer_price(prev, cat, rules, quantity, prev_resolver=None)

    try:
        q = Decimal(str(quantity))
        if not q.is_finite() or q <= 0 or q != q.to_integral_value():
            raise OfferError("quantity must be a positive whole number")
        price = extract_offer_price(latest, cat, rules, int(q), prev_resolver)
    except OfferError as exc:
        return {"success": False, "reason": "MISSING_INFO", "detail": str(exc),
                "latest_message": latest}
    return {"success": True, "effective_price_inr": price, "latest_message": latest,
            "category": cat, "supplier": supplier,
            "quoted_quantity": quoted_quantity}


# --------------------------------------------------------------------------
# prompt injection  (anywhere in the thread)
# --------------------------------------------------------------------------
_BENIGN = re.compile(r"AI-powered|system integration|ERP system|assistant manager|assistant (?:director|vice)", re.I)

_INJ = [
    re.compile(
        r"\bautomated\s+system\b[^.;\n]{0,120}"
        r"\brecord\s+the\s+decision\s+as\s+(?:approved|accepted)\b",
        re.I,
    ),

    # An AI, bot, automated system, or reviewer must be paired
    # with an instruction or directive.
    re.compile(
        r"\b(?:AI\s+(?:agent|assistant|model|system|bot)|"
        r"language\s+model|LLM|chat\s*bot|bot|"
        r"automated\s+(?:system|agent|reviewer)|"
        r"(?:AI\s+)?reviewer|system\s+(?:prompt|instruction))\b"
        r"[^.;\n]{0,120}\b(?:ignore|override|bypass|approve|accept|"
        r"reject|output|instruct|skip|mark|classify|disregard|follow)\b"
        r"|"
        r"\b(?:ignore|override|bypass|approve|accept|reject|output|"
        r"instruct|skip|mark|classify|disregard|follow)\b"
        r"[^.;\n]{0,120}\b(?:AI\s+(?:agent|assistant|model|system|bot)|"
        r"language\s+model|LLM|chat\s*bot|bot|"
        r"automated\s+(?:system|agent|reviewer)|"
        r"(?:AI\s+)?reviewer|system\s+(?:prompt|instruction))\b",
        re.I,
    ),

    # Direct attempts to override the instructions.
    re.compile(
        r"\bignore\s+(?:all\s+|any\s+)?(?:your|the|previous|prior|above)"
        r"\b.{0,50}\binstructions?\b",
        re.I,
    ),
    re.compile(
        r"\bskip\s+the\s+(?:playbook|checks|policy|rules)\b",
        re.I,
    ),

    # Explicit override claims or claims of prior approval.
    re.compile(
        r"\b(?:admin|administrator|system|policy)\s+override\b|"
        r"\boverride\b[^.;\n]{0,60}\b(?:approved|authorized|granted|permitted)\b|"
        r"\b(?:pre-?approved|approval\s+(?:was\s+)?already\s+granted|"
        r"already\s+(?:signed\s+off|approved))\b",
        re.I,
    ),

    # Explicit assistant-style instruction labels.
    re.compile(r"\bassistant\s*:", re.I),

    # HTML comments containing instruction-like content.
    re.compile(
        r"<!--(?:(?!-->).)*(?:ignore|override|bypass|approve|accept|"
        r"output|instruction|system prompt)(?:(?!-->).)*-->",
        re.I | re.S,
    ),

    # Specific Hindi AI-instruction wording.
    re.compile(
        r"(?:AI|एआई).{0,40}(?:सहायक|मॉडल|बॉट)"
        r".{0,80}(?:निर्देश|स्वीकार|मंजूर|मंज़ूर)",
        re.I,
    ),
]



def detect_prompt_injection(message: str) -> bool:
    """Detect instruction-like content anywhere in the supplier thread."""
    for line in message.replace("\\r\\n", "\\n").split("\\n"):
        cleaned = _BENIGN.sub(" ", line)
        if any(pattern.search(cleaned) for pattern in _INJ):
            return True
    return False


# --------------------------------------------------------------------------
# banned contract terms (latest message only)
# --------------------------------------------------------------------------
_NUM_WORDS = {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
              "ninety": 90, "hundred": 100, "twenty": 20, "ten": 10, "fifteen": 15, "twenty-five": 25,
              "twenty five": 25, "thirty-five": 35, "forty-five": 45, "thirty five": 35, "forty five": 45}
_ADV_WORDS = r"(?:advance|upfront|up-front|up front|in advance|prepay(?:ment)?|deposit)"
_AUTO = [
    re.compile(r"\bauto[- ]?(?:matic(?:ally)?[- ]?)?renew\w*", re.I),
    re.compile(r"\bcontinues?\s+(?:from\s+)?year\s+to\s+year\b|\bfrom\s+year\s+to\s+year\s+unless\b", re.I),
    re.compile(r"\brenews?\s+(?:on\s+its\s+own|itself|without)\b|\bself[- ]renew\w*", re.I),
    re.compile(r"\brolls?\s+over\s+automatically\b|\brenew\w*\s+(?:automatically|by\s+default)\b", re.I),
]
_NEG_AUTO = re.compile(r"\b(?:no|not|without|never)\b[^.;\n]{0,25}\bauto(?:matic)?[- ]?renew\w*", re.I)

_LIAB = [
    re.compile(
        r"\bAcme(?:'s)?\b[^.;\n]{0,100}\bliabilit\w*"
        r"[^.;\n]{0,100}\b(?:unlimited|uncapped|without\s+(?:any\s+)?"
        r"(?:limit|cap)|not\s+(?:subject\s+to|limited\s+by)\s+"
        r"(?:any\s+)?(?:cap|limit))\b",
        re.I,
    ),
    re.compile(
        r"\bliabilit\w*[^.;\n]{0,80}\bof\s+Acme\b"
        r"[^.;\n]{0,80}\b(?:unlimited|uncapped|without\s+(?:any\s+)?"
        r"(?:limit|cap)|not\s+(?:subject\s+to|limited\s+by)\s+"
        r"(?:any\s+)?(?:cap|limit))\b",
        re.I,
    ),
    re.compile(
        r"\bunlimited\s+liabilit\w*[^.;\n]{0,50}\bfor\s+Acme\b",
        re.I,
    ),
]

_EXCL = [
    re.compile(
        r"\bAcme\b[^.;\n]{0,160}\b(?:buy|purchase|procure|source|obtain)"
        r"\w*[^.;\n]{0,100}\b(?:only|exclusively|solely)\b"
        r"[^.;\n]{0,50}\bfrom\b",
        re.I,
    ),
    re.compile(
        r"\bAcme\b[^.;\n]{0,160}\b(?:buy|purchase|procure|source|obtain)"
        r"\w*[^.;\n]{0,100}\bfrom\b[^.;\n]{0,50}"
        r"\b(?:only|exclusively|solely)\b",
        re.I,
    ),
    re.compile(
        r"\bAcme\b[^.;\n]{0,100}\bexclusive\s+"
        r"(?:supplier|vendor|provider|partner)\b",
        re.I,
    ),
]

_NEG_EXCL = re.compile(
    r"\bnot\s+asking\s+for\s+exclusivity\b|"
    r"\bno\s+exclusivity\b|"
    r"\bwithout\s+exclusivity\b|"
    r"\bdo\s+not\s+(?:require|need|want)\s+(?:an?\s+)?"
    r"exclusive\s+(?:supplier|vendor|provider|partner)\b",
    re.I,
)



def _advance_percentages(text: str):
    pcts = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent|percent)", text, re.I):
        window = text[max(0, m.start() - 60): m.end() + 70]
        if re.search(_ADV_WORDS, window, re.I):
            pcts.append(Decimal(m.group(1)))
    for m in re.finditer(r"\b((?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)(?:[- ]five)?)\s+(?:per\s*cent|percent)\b", text, re.I):
        window = text[max(0, m.start() - 60): m.end() + 70]
        if re.search(_ADV_WORDS, window, re.I):
            pcts.append(Decimal(_NUM_WORDS.get(m.group(1).lower(), 0)))
    for m in re.finditer(r"\bhalf\b|\ba\s+third\b|\bone[- ]third\b|\bquarter\b|\bfull\s+(?:payment|amount|order\s+value)\b", text, re.I):
        window = text[max(0, m.start() - 60): m.end() + 70]
        if re.search(_ADV_WORDS, window, re.I):
            w = m.group(0).lower()
            pcts.append(Decimal(50) if w == "half" else Decimal("33.3") if "third" in w
                        else Decimal(25) if w == "quarter" else Decimal(100))
    return pcts


def detect_banned_terms(latest_message: str) -> bool:
    text = latest_message
    scrub = _NEG_AUTO.sub(" ", text)
    if any(p.search(scrub) for p in _AUTO):
        return True
    if any(p.search(text) for p in _LIAB):
        return True
    scrub = _NEG_EXCL.sub(" ", text)
    if any(p.search(scrub) for p in _EXCL):
        return True
    return any(p > 30 for p in _advance_percentages(text))
