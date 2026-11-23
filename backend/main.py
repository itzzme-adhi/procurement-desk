from pathlib import Path
import json

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(
    title="Procurement Desk API",
    version="0.1.0",
    description="Backend API for supplier negotiation analysis"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def find_threads_file():
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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0)
):
    df = load_threads()

    if df is None:
        return {
            "dataset_loaded": False,
            "message": "Place the official threads.csv inside the data folder.",
            "total": 0,
            "threads": []
        }

    selected = df.iloc[offset:offset + limit].copy()

    # Convert NaN values to JSON null values.
    records = json.loads(selected.to_json(orient="records"))

    return {
        "dataset_loaded": True,
        "total": len(df),
        "limit": limit,
        "offset": offset,
        "threads": records
    }
