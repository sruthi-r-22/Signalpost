"""
Signalpost Quick Start Script.
Starts the FastAPI server with Uvicorn.
"""
import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    print("=" * 60)
    print("📡 Signalpost - Corporate Intelligence Agent")
    print(f"Server: http://{settings.HOST}:{settings.PORT}")
    print(f"API Docs: http://{settings.HOST}:{settings.PORT}/docs")
    print(f"LLM Provider: {settings.LLM_PROVIDER}")
    print(f"Search Provider: {settings.SEARCH_PROVIDER}")
    print("=" * 60)
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
