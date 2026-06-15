"""
ParcelIQ FastAPI application.

Run with:
  uvicorn app.main:app --reload --port 8000
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import report, search

# frontend/ lives two levels above this file (repo root → frontend/)
_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_HTML = _FRONTEND_DIR / "parceliq_v5.html"

app = FastAPI(
    title="ParcelIQ API",
    description="Property due diligence platform — Hillsborough County, FL",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(report.router)


@app.get("/", include_in_schema=False)
async def serve_frontend():
    if _HTML.exists():
        return FileResponse(_HTML, media_type="text/html")
    return {
        "name": "ParcelIQ API",
        "version": "0.1.0",
        "endpoints": {
            "search": "/api/search?q=<address|folio|strap|pin>",
            "report": "/api/report/{folio}",
            "docs": "/docs",
        },
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
