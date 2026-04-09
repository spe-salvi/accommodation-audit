"""
FastAPI application entry point.

Serves both the REST API and the built React frontend as static files.
In development, React runs on its own dev server (proxied via Vite).
In production (Render), the built frontend is served from dist/.

Start with:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes.audit import router as audit_router
from api.routes.cache import router as cache_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Accommodation Audit",
    description="Canvas LMS accommodation audit system API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS — in dev the React dev server runs on a different port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(audit_router)
app.include_router(cache_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve built React frontend in production
# Vite builds to frontend/dist/ — mount at root after API routes
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
    logger.info("Serving frontend from %s", _FRONTEND_DIST)
else:
    logger.info("Frontend dist not found — running in API-only mode")
