"""Manual ASGI entry point; the existing domain package remains under src/."""

from pathlib import Path
import sys

# Support uvicorn app.main:app from backend/ without an editable install.
# Add only this delivered source directory, never a configurable search path.
_domain_source = str(Path(__file__).absolute().parent.parent / "src")
if _domain_source not in sys.path:
    sys.path.insert(0, _domain_source)
