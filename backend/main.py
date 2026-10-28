"""
RxTrail — FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.auth.router import router as auth_router
from backend.config import BACKEND_DIR, settings
from backend.db import ensure_indexes, get_client
from backend.routers import (
    admin, appointments, cases, dashboard, doctors, documents, fulfilments,
    imports, knowledge, llm, notifications, onboarding, patients, prescriptions, queue,
    search, settings_router, share, skus, sources, structures, visits, week,
)

# ── Global: ObjectId → str για ΟΛΑ τα responses ──────────────────────────────
# Χωρίς αυτό, κάθε endpoint που επιστρέφει raw Mongo docs με ObjectId σκάει
# με «500: ObjectId is not iterable». Το FastAPI χρησιμοποιεί το
# pydantic.json.ENCODERS_BY_TYPE για άγνωστους τύπους.
try:
    from bson import ObjectId as _BsonObjectId
    from pydantic.json import ENCODERS_BY_TYPE as _ENCODERS_BY_TYPE
    _ENCODERS_BY_TYPE[_BsonObjectId] = str
except Exception:  # pragma: no cover
    pass

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

# Rate limiting
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ensure MongoDB indexes, start scheduler + OCR worker.
    Shutdown: close Motor, stop scheduler + OCR worker."""
    await ensure_indexes()

    # Start nightly scheduler
    from backend.jobs.scheduler import start_scheduler, stop_scheduler
    start_scheduler()

    # Start OCR background worker
    from backend.ocr.engine import start_ocr_worker, stop_ocr_worker
    start_ocr_worker()

    # Warm-up τοπικού μοντέλου embeddings στο background (δωρεάν, αντικαθιστά Jina)
    import asyncio as _asyncio
    async def _warmup_embeddings():
        from backend.llm.embeddings import warmup_embeddings_model
        await _asyncio.get_event_loop().run_in_executor(None, warmup_embeddings_model)
    _asyncio.ensure_future(_warmup_embeddings())

    yield

    stop_ocr_worker()
    stop_scheduler()
    client = await get_client()
    client.close()


app = FastAPI(
    title="RxTrail",
    description="Homecare CRM — provider side, EOPYY cycle management",
    version="1.0.0",
    lifespan=lifespan,
)

# ── State for rate limiter ──────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(admin.router)
app.include_router(appointments.router)
app.include_router(cases.router)
app.include_router(dashboard.router)
app.include_router(doctors.router)
app.include_router(documents.router)
app.include_router(fulfilments.router)
app.include_router(imports.router)
app.include_router(knowledge.router)
app.include_router(llm.router)
app.include_router(notifications.router)
app.include_router(onboarding.router)
app.include_router(patients.router)
app.include_router(prescriptions.router)
app.include_router(queue.router)
app.include_router(search.router)
app.include_router(share.router)
app.include_router(settings_router.router)
app.include_router(skus.router)
app.include_router(sources.router)
app.include_router(structures.router)
app.include_router(visits.router)
app.include_router(week.router)

# ── /api sub-app for production (Nginx reverse-proxy friendly) ──────────────
api_app = FastAPI(title="RxTrail API", version="1.0.0")

@api_app.get("/health")
async def api_health():
    return {"status": "ok", "app": "RxTrail", "version": "1.0.0"}

api_app.include_router(auth_router)
api_app.include_router(admin.router)
api_app.include_router(appointments.router)
api_app.include_router(cases.router)
api_app.include_router(dashboard.router)
api_app.include_router(doctors.router)
api_app.include_router(documents.router)
api_app.include_router(fulfilments.router)
api_app.include_router(imports.router)
api_app.include_router(knowledge.router)
api_app.include_router(llm.router)
api_app.include_router(notifications.router)
api_app.include_router(onboarding.router)
api_app.include_router(patients.router)
api_app.include_router(prescriptions.router)
api_app.include_router(queue.router)
api_app.include_router(search.router)
api_app.include_router(share.router)
api_app.include_router(settings_router.router)
api_app.include_router(skus.router)
api_app.include_router(sources.router)
api_app.include_router(structures.router)
api_app.include_router(visits.router)
api_app.include_router(week.router)
app.mount("/api", api_app)


# ── Health check ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "app": "RxTrail", "version": "1.0.0"}


# ── Static files (frontend) — MUST be last to not shadow API routes ─────────
# Disable caching for development
from fastapi.responses import FileResponse as _FileResponse

def _nocache_fileresponse(path, **kwargs):
    """FileResponse with no-cache headers for development."""
    resp = _FileResponse(path, **kwargs)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

if FRONTEND_DIR.exists():
    # Landing page (public)
    @app.get("/")
    async def serve_landing():
        landing = FRONTEND_DIR / "landing.html"
        if landing.exists():
            return _nocache_fileresponse(landing)
        return _nocache_fileresponse(FRONTEND_DIR / "index.html")

    # App SPA (authenticated users)
    @app.get("/app")
    async def serve_app():
        return _nocache_fileresponse(FRONTEND_DIR / "index.html")

    # Catch-all SPA fallback for app routes
    @app.get("/app/{full_path:path}")
    async def serve_app_routes(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return _nocache_fileresponse(file_path)
        return _nocache_fileresponse(FRONTEND_DIR / "index.html")

    # Legacy catch-all (for backward compatibility — serves index.html)
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # Skip API-looking paths
        if full_path.startswith("auth/") or full_path.startswith("api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not found"}, status_code=404)
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return _nocache_fileresponse(file_path)
        return _nocache_fileresponse(FRONTEND_DIR / "index.html")
