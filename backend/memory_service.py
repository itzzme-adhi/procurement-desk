import pandas as pd

from backend.engine.decision import decide_thread
from backend.engine.parser import get_latest_message


def split_conversation(message):
    """Separate embedded earlier messages from the latest supplier message."""
    message = str(message or "")
    latest = get_latest_message(message)

    if latest == message:
        return {
            "history": "",
            "latest_message": message,
            "history_available": False,
        }

    position = message.rfind(latest)
    return {
        "history": message[:position].strip(),
        "latest_message": latest.strip(),
        "history_available": True,
    }


def retrieve_historical_cases(df, current_row, limit=4):
    """Retrieve similar records strictly earlier than the current case."""
    if df is None or df.empty:
        return []

    current_date = pd.to_datetime(
        current_row.get("date"), errors="coerce"
    )
    if pd.isna(current_date):
        return []

    current_id = str(current_row.get("thread_id", ""))
    current_supplier = str(current_row.get("supplier", "")).strip().lower()
    current_category = str(current_row.get("category", "")).strip().lower()

    try:
        current_quantity = float(current_row.get("quantity", 0))
    except (TypeError, ValueError):
        current_quantity = 0

    try:
        current_round = int(current_row.get("round", 0))
    except (TypeError, ValueError):
        current_round = 0

    candidates = []

    for _, row in df.iterrows():
        if str(row.get("thread_id", "")) == current_id:
            continue

        case_date = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(case_date) or case_date >= current_date:
            continue

        supplier = str(row.get("supplier", "")).strip().lower()
        category = str(row.get("category", "")).strip().lower()

        score = 0
        if category and category == current_category:
            score += 5
        if supplier and supplier == current_supplier:
            score += 3

        try:
            quantity = float(row.get("quantity", 0))
            if current_quantity > 0 and quantity > 0:
                ratio = min(quantity, current_quantity) / max(
                    quantity, current_quantity
                )
                score += round(2 * ratio, 2)
        except (TypeError, ValueError):
            pass

        try:
            if int(row.get("round", 0)) == current_round:
                score += 1
        except (TypeError, ValueError):
            pass

        if score < 3:
            continue

        try:
            decision = decide_thread(row)
        except Exception:
            decision = "UNAVAILABLE"

        candidates.append({
            "thread_id": str(row.get("thread_id", "")),
            "date": str(row.get("date", "")),
            "supplier": str(row.get("supplier", "")),
            "category": str(row.get("category", "")),
            "quantity": str(row.get("quantity", "")),
            "round": str(row.get("round", "")),
            "decision": decision,
            "similarity_score": score,
        })

    candidates.sort(
        key=lambda case: (
            case["similarity_score"],
            case["date"],
        ),
        reverse=True,
    )

    return candidates[:limit]
