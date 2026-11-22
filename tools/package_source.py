"""Compatibility entry point for the complete backend/frontend source packager."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[2] / "tools/package_source.py"), run_name="__main__")
