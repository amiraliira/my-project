"""Manual ASGI entry point; the existing domain package remains under src/."""

from pathlib import Path
import importlib.util
import os
import sys

# Support uvicorn app.main:app from backend/ without an editable install.
# Add only this delivered source directory, never a configurable search path.
_backend = Path(__file__).absolute().parent.parent
_domain_source = str(_backend / "src")
_package_init = _backend / "src" / "pak_maintenance" / "__init__.py"
_repair = (
    "Restore the complete matching backend folder, including "
    "src/pak_maintenance/__init__.py. Do not create an empty initialization file. "
    "See backend/README.md#troubleshooting."
)
if not _package_init.is_file():
    raise ImportError(f"Incomplete PAK backend: missing {_package_init}. {_repair}")
if _domain_source in sys.path:
    sys.path.remove(_domain_source)
sys.path.insert(0, _domain_source)
try:
    _spec = importlib.util.find_spec("pak_maintenance")
except (ImportError, ValueError):
    _spec = None
if (_spec is None or not _spec.origin or
        os.path.normcase(os.path.abspath(_spec.origin)) !=
        os.path.normcase(os.path.abspath(_package_init))):
    raise ImportError(f"PAK package was loaded from another location. Start a fresh Python process from {_backend}. {_repair}")

import pak_maintenance as _package

if not isinstance(getattr(_package, "__version__", None), str) or not _package.__version__:
    raise ImportError(f"Incomplete PAK package version in {_package_init}. {_repair}")
