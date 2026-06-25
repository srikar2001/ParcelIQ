"""
ParcelIQ FastAPI application.

Run with:
  uvicorn app.main:app --reload --port 8000
"""
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.routers import narrative, report, search, batch, share

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    _limiter = Limiter(key_func=get_remote_address)
    _RATE_LIMIT_OK = True
except ImportError:
    _limiter = None
    _RATE_LIMIT_OK = False

# frontend/ lives two levels above this file (repo root → frontend/)
_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_HTML = _FRONTEND_DIR / "index.html"

app = FastAPI(
    title="ParcelIQ API",
    description="Property due diligence platform — Florida statewide",
    version="2.0.0",
)

if _RATE_LIMIT_OK and _limiter:
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(report.router)
app.include_router(narrative.router)
app.include_router(batch.router)
app.include_router(share.router)


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
