"""Verify the complete source handoff using only the Python standard library."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tomllib


ROOT = Path(__file__).absolute().parents[1]


def canonical_local_path(path):
    """Strict resolution with a no-reparse Windows final-path broker fallback."""
    path = Path(path)
    try:
        return path.resolve(strict=True)
    except PermissionError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 5:
            raise
        absolute = Path(os.path.abspath(path))
        for component in reversed((absolute, *absolute.parents)):
            metadata = component.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_file_attributes & 0x400:
                raise ValueError("Source paths must not contain symbolic links or reparse points") from error
        return absolute


def verify(root=ROOT):
    root = canonical_local_path(root)
    manifest = json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
    entries = manifest["files"]
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Source file manifest is empty")
    for name, expected in entries.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")) or "\\" in name or ":" in name:
            raise ValueError("Unsafe source manifest path")
        path = root.joinpath(*relative.parts)
        current = root
        for component in relative.parts:
            current = current / component
            if current.is_symlink():
                raise ValueError("Source manifest entries must not use symbolic links")
        if not canonical_local_path(path).is_relative_to(root) or not path.is_file():
            raise ValueError("Missing source file: " + name)
        if path.stat().st_size != expected["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
            raise ValueError("Source checksum mismatch: " + name)
    required = {
        "backend/pyproject.toml", "backend/requirements.txt", "requirements.txt",
        "backend/src/pak_maintenance/application.py", "backend/src/pak_maintenance/api.py",
        "frontend/src/App.tsx", "frontend/package.json", "frontend/pnpm-lock.yaml",
        "frontend/vite.config.mjs", "frontend/dist/index.html",
        "deploy/linux/server.example.toml", "tools/run_backend.sh",
    }
    if not required.issubset(entries):
        raise ValueError("Backend or frontend source delivery is incomplete")
    backend = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    if backend["project"]["version"] != "2.0.0rc12" or frontend["version"] != "2.0.0-rc.12":
        raise ValueError("Backend/frontend application versions do not match this handoff")
    pins = (root / "backend/requirements.txt").read_text(encoding="utf-8").splitlines()
    if "fastapi==0.143.0" not in pins or "uvicorn==0.54.0" not in pins:
        raise ValueError("FastAPI runtime dependency pins are missing")
    return {"verifiedFiles": len(entries), "applicationVersion": manifest["applicationVersion"],
            "handoffRevision": manifest["handoffRevision"], "successful": True}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True))
    except (ValueError, KeyError, OSError, TypeError):
        print("Source verification failed; use the complete original source ZIP.", file=sys.stderr)
        raise SystemExit(1)
