"""Run manually from backend/: uvicorn app.main:app --host 127.0.0.1 --port 5187."""

from .settings import load_config
from pak_maintenance.application import create_app

# Validation happens at import; database verification runs in app lifespan.
app = create_app(load_config())
