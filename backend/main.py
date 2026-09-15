"""
Entry point for the Pipecat Voice Accelerator.

Run from the project root (pipecat-voice-accelerator/):
    python -m backend.main
or:
    uvicorn backend.src.api.app:app --host 0.0.0.0 --port 8080 --reload
"""
import uvicorn
from backend.src.config.settings import settings


if __name__ == "__main__":
    uvicorn.run(
        "backend.src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        log_level="info",
    )
