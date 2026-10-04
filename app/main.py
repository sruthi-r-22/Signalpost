"""
Main FastAPI Application Entrypoint for Signalpost.
Configures CORS, routers, health check, and static frontend hosting.
"""
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import get_settings
from app.database.db import get_db

settings = get_settings()

app = FastAPI(
    title="Signalpost - Corporate Intelligence Agent",
    description="Agentic corporate research pipeline for Norwegian companies with evidence tracking and verification.",
    version="1.0.0"
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint reporting service, database, and provider statuses."""
    db = get_db()
    db_healthy = db.check_health()
    return {
        "status": "healthy" if db_healthy else "degraded",
        "service": "signalpost",
        "version": "1.0.0",
        "database": "connected" if db_healthy else "error",
        "llm_provider": settings.LLM_PROVIDER,
        "search_provider": settings.SEARCH_PROVIDER,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# Frontend static files mounting
frontend_path = Path(__file__).resolve().parent.parent / "frontend"

if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_file = frontend_path / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Signalpost API running. Frontend index.html not found."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
