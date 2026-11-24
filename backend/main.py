from pathlib import Path
import os
from functools import lru_cache
import json

import pandas as pd
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from backend.engine.decision import decide_thread
from backend.llm_service import explain_decision

ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(
    title="Procurement Desk API",
    version="0.1.0",
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


@lru_cache(maxsize=1)
def find_threads_file():
    configured_path = os.getenv("DATASET_PATH")

    if configured_path:
        file_path = Path(configured_path).expanduser()
        return file_path if file_path.is_file() else None

    search_roots = [
        ROOT / "data",
        ROOT / "extracted"
    ]

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

    # Deterministic engine always decides first.
    decision = decide_thread(row)

    # LLM receives explanation context only.
    result = explain_decision({
        "decision": decision,
        "supplier": row["supplier"],
        "category": row["category"],
        "date": row["date"],
        "quantity": row["quantity"],
        "round": row["round"],
        "supplier_message": row.get("supplier_message", ""),
    })

    return {
        "thread_id": thread_id,
        "decision": decision,
        "explanation": result["explanation"],
        "llm_used": result["llm_used"],
        "reason": result["reason"],
    }
