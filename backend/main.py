from pathlib import Path
from functools import lru_cache
from io import BytesIO
import os
import json

import pandas as pd
from fastapi import FastAPI, Query, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.engine.decision import decide_thread
from backend.llm_service import explain_decision
from backend.memory_service import split_conversation, retrieve_historical_cases


ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(
    title="Procurement Desk API",
    version="0.2.0",
    description="Backend API for supplier negotiation analysis"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            "http://localhost:5500,http://127.0.0.1:5500"
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MANUAL_REQUIRED_COLUMNS = {
    "date",
    "supplier",
    "category",
    "quantity",
    "round",
    "supplier_message",
}
MANUAL_OPTIONAL_COLUMNS = {"thread_id", "our_last_counter"}
MANUAL_MAX_BYTES = 5 * 1024 * 1024
MANUAL_MAX_ROWS = 1000


@lru_cache(maxsize=1)
def find_threads_file():
    configured_path = os.getenv("DATASET_PATH")
    if configured_path:
        file_path = Path(configured_path).expanduser()
        return file_path if file_path.is_file() else None

    search_roots = [ROOT / "data", ROOT / "extracted"]
    for folder in search_roots:
        if folder.exists():
            matches = list(folder.rglob("threads.csv"))
            if matches:
                return matches[0]
    return None


@lru_cache(maxsize=1)
def load_threads():
    file_path = find_threads_file()
    if file_path is None:
        return None
    return pd.read_csv(file_path)


@app.get("/")
def home():
    return {
        "message": "Procurement Desk API is running",
        "docs": "/docs"
    }


@app.get("/api/health")
def health():
    file_path = find_threads_file()
    return {
        "status": "online",
        "dataset_loaded": file_path is not None,
        "dataset_path": str(file_path) if file_path else None
    }


@app.get("/api/summary")
def summary():
    df = load_threads()
    if df is None:
        return {
            "dataset_loaded": False,
            "total_threads": 0,
            "suppliers": 0,
            "categories": 0
        }

    return {
        "dataset_loaded": True,
        "total_threads": len(df),
        "suppliers": (
            int(df["supplier"].nunique())
            if "supplier" in df.columns else 0
        ),
        "categories": (
            int(df["category"].nunique())
            if "category" in df.columns else 0
        )
    }


@app.get("/api/threads")
def get_threads(
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default="")
):
    df = load_threads()
    if df is None:
        return {
            "dataset_loaded": False,
            "message": "Dataset unavailable.",
            "total": 0,
            "limit": limit,
            "offset": offset,
            "threads": []
        }

    filtered = df
    if search.strip():
        query = search.strip().lower()
        searchable_columns = [
            col for col in ["thread_id", "supplier", "category"]
            if col in df.columns
        ]
        if searchable_columns:
            mask = pd.Series(False, index=df.index)
            for col in searchable_columns:
                mask |= df[col].astype(str).str.lower().str.contains(
                    query, regex=False, na=False
                )
            filtered = df[mask]

    total = len(filtered)
    selected = filtered.iloc[offset:offset + limit].copy()
    records = json.loads(selected.to_json(orient="records"))

    return {
        "dataset_loaded": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "threads": records
    }


@app.get("/api/threads/{thread_id}/explain")
def explain_thread(thread_id: str):
    df = load_threads()
    if df is None:
        raise HTTPException(status_code=503, detail="Dataset unavailable")

    matches = df[df["thread_id"].astype(str) == thread_id]
    if matches.empty:
        raise HTTPException(status_code=404, detail="Thread not found")

    row = matches.iloc[0]
    decision = decide_thread(row)

    conversation = split_conversation(
        row.get("supplier_message", "")
    )
    historical_cases = retrieve_historical_cases(df, row)

    result = explain_decision({
        "decision": decision,
        "supplier": row["supplier"],
        "category": row["category"],
        "date": row["date"],
        "quantity": row["quantity"],
        "round": row["round"],
        "supplier_message": row.get("supplier_message", ""),
        "conversation_history": conversation["history"],
        "latest_supplier_message": conversation["latest_message"],
        "conversation_history_available": conversation["history_available"],
        "our_last_counter": row.get("our_last_counter", ""),
        "historical_cases": historical_cases,
    })

    return {
        "thread_id": thread_id,
        "decision": decision,
        "explanation": result["explanation"],
        "llm_used": result["llm_used"],
        "reason": result["reason"],
        "conversation_history_available": conversation["history_available"],
        "conversation_history": conversation["history"],
        "latest_supplier_message": conversation["latest_message"],
        "historical_cases": historical_cases,
        "memory_count": len(historical_cases),
    }


def read_manual_upload(filename, content):
    suffix = Path(filename or "").suffix.lower()

    if suffix not in {".csv", ".xlsx"}:
        raise HTTPException(
            status_code=400,
            detail="Upload a CSV or Excel .xlsx file."
        )

    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    if len(content) > MANUAL_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File is too large. Maximum upload size is 5 MB."
        )

    try:
        if suffix == ".csv":
            df = pd.read_csv(BytesIO(content))
        else:
            df = pd.read_excel(
                BytesIO(content),
                engine="openpyxl"
            )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Could not read this file. Check that it is a valid CSV or .xlsx file."
        )

    df.columns = [str(column).strip().lower() for column in df.columns]

    if df.columns.duplicated().any():
        raise HTTPException(
            status_code=400,
            detail="The file contains duplicate column names."
        )

    missing = sorted(MANUAL_REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise HTTPException(
            status_code=400,
            detail="Missing required columns: " + ", ".join(missing)
        )

    if df.empty:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file contains no data rows."
        )

    if len(df) > MANUAL_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MANUAL_MAX_ROWS} rows per upload."
        )

    return df


def prepare_manual_row(raw, row_number):
    row = {}
    for key, value in raw.items():
        if pd.isna(value):
            row[key] = ""
        elif isinstance(value, pd.Timestamp):
            row[key] = value.isoformat()
        else:
            row[key] = value

    for field in ("supplier", "category", "supplier_message"):
        value = str(row.get(field, "")).strip()
        if not value:
            raise ValueError(f"{field} is required.")
        row[field] = value

    try:
        parsed_date = pd.to_datetime(row.get("date"), errors="raise")
        row["date"] = parsed_date.date().isoformat()
    except Exception:
        raise ValueError("date must be a valid date.")

    for field in ("quantity", "round"):
        try:
            number = float(row.get(field))
            if not number.is_integer() or number <= 0:
                raise ValueError
            row[field] = int(number)
        except Exception:
            raise ValueError(f"{field} must be a positive whole number.")

    row["thread_id"] = str(
        row.get("thread_id") or f"MANUAL-{row_number:04d}"
    ).strip()

    row["our_last_counter"] = str(
        row.get("our_last_counter", "")
    ).strip()

    return row


def evaluate_manual_row(raw, row_number):
    try:
        row = prepare_manual_row(raw, row_number)

        # The existing deterministic engine remains authoritative.
        decision = decide_thread(row)

        return {
            **row,
            "row_number": row_number,
            "valid": True,
            "decision": decision,
            "status": decision.split(":", 1)[0],
            "reason": decision.split(":")[1] if ":" in decision else "",
            "counter_offer": (
                decision.split(":")[2]
                if decision.count(":") >= 2 else ""
            ),
            "error": ""
        }
    except Exception as exc:
        return {
            **{
                key: (
                    "" if pd.isna(value) else str(value)
                )
                for key, value in raw.items()
            },
            "row_number": row_number,
            "valid": False,
            "decision": "",
            "status": "INVALID",
            "reason": "",
            "counter_offer": "",
            "error": str(exc)
        }


async def receive_manual_file(file):
    content = await file.read()
    return read_manual_upload(file.filename, content)


@app.post("/api/manual-analysis/preview")
async def preview_manual_analysis(file: UploadFile = File(...)):
    df = await receive_manual_file(file)
    records = df.to_dict(orient="records")

    evaluated = [
        evaluate_manual_row(record, index + 1)
        for index, record in enumerate(records)
    ]

    valid_count = sum(1 for row in evaluated if row["valid"])

    return {
        "filename": file.filename,
        "total_rows": len(evaluated),
        "valid_rows": valid_count,
        "invalid_rows": len(evaluated) - valid_count,
        "required_columns": sorted(MANUAL_REQUIRED_COLUMNS),
        "optional_columns": sorted(MANUAL_OPTIONAL_COLUMNS),
        "columns": list(df.columns),
        "preview": evaluated[:10]
    }


@app.post("/api/manual-analysis/analyze")
async def analyze_manual_upload(file: UploadFile = File(...)):
    df = await receive_manual_file(file)
    records = df.to_dict(orient="records")

    results = [
        evaluate_manual_row(record, index + 1)
        for index, record in enumerate(records)
    ]

    valid_count = sum(1 for row in results if row["valid"])

    return {
        "filename": file.filename,
        "total_rows": len(results),
        "analysed_rows": valid_count,
        "invalid_rows": len(results) - valid_count,
        "results": results
    }


@app.post("/api/manual-analysis/explain")
def explain_manual_result(payload: dict):
    raw_row = payload.get("row")

    if not isinstance(raw_row, dict):
        raise HTTPException(
            status_code=400,
            detail="A valid row is required."
        )

    checked = evaluate_manual_row(raw_row, 1)
    if not checked["valid"]:
        raise HTTPException(
            status_code=400,
            detail=checked["error"]
        )

    # Recalculate the decision on the server. Never trust a
    # decision value sent by the browser.
    decision = checked["decision"]

    result = explain_decision({
        "decision": decision,
        "supplier": checked["supplier"],
        "category": checked["category"],
        "date": checked["date"],
        "quantity": checked["quantity"],
        "round": checked["round"],
        "supplier_message": checked["supplier_message"],
    })

    return {
        "decision": decision,
        "explanation": result["explanation"],
        "llm_used": result["llm_used"],
        "reason": result["reason"]
    }


@app.post("/api/manual-analysis/export")
def export_manual_analysis(payload: dict):
    results = payload.get("results")

    if not isinstance(results, list) or not results:
        raise HTTPException(
            status_code=400,
            detail="No analysis results available to export."
        )

    if len(results) > MANUAL_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail="Maximum 1000 rows can be exported."
        )

    if not all(isinstance(row, dict) for row in results):
        raise HTTPException(
            status_code=400,
            detail="Invalid result format."
        )

    columns = [
        "row_number", "thread_id", "date", "supplier",
        "category", "quantity", "round", "status",
        "decision", "counter_offer", "error"
    ]

    output = BytesIO()
    pd.DataFrame(results).reindex(columns=columns).to_excel(
        output,
        index=False,
        engine="openpyxl"
    )
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition":
                'attachment; filename="manual-analysis-results.xlsx"'
        }
    )
