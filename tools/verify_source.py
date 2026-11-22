"""Verify the complete source handoff using only the Python standard library."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tomllib

HERE = Path(__file__).absolute().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from source_inventory import source_files, canonical_local_path, required_contract_files
from package_source import release_metadata


ROOT = Path(__file__).absolute().parents[1]


def verify(root=ROOT):
    root = canonical_local_path(root)
    manifest = json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
    release = release_metadata(root)
    if (manifest.get("applicationVersion") != release["applicationVersion"] or
            manifest.get("handoffRevision") != release["packageRevision"] or
            manifest.get("coreSchemaVersion") != release["coreSchemaVersion"] or
            manifest.get("apiVersion") != release["apiVersion"]):
        raise ValueError("Source manifest disagrees with RELEASE.json")
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
        if not path.is_file():
            raise ValueError("Missing source file: " + name)
        if not canonical_local_path(path).is_relative_to(root):
            raise ValueError("Source file escapes the package: " + name)
        if path.stat().st_size != expected["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
            raise ValueError("Source checksum mismatch: " + name)
    required = {
        "backend/pyproject.toml", "backend/requirements.txt", "requirements.txt",
        "backend/app/__init__.py", "backend/app/main.py", "backend/app/settings.py",
        "backend/src/pak_maintenance/__init__.py", "backend/tools/check_installation.py",
        "backend/src/pak_maintenance/settings.py", "README.md", "RELEASE.json",
        "backend/README.md", "backend/schema/README.md", "backend/oee/README.md",
        "deploy/README.md", "docs/README.md", "docs/features.md",
        "docs/development/README.md", "docs/runtime-data/README.md",
        "tools/source_inventory.py", "tools/package_source.py", "backend/.env.example",
        "backend/src/pak_maintenance/application.py", "backend/src/pak_maintenance/api.py",
        "frontend/src/App.tsx", "frontend/package.json", "frontend/pnpm-lock.yaml",
        "frontend/vite.config.mjs", "frontend/dist/index.html",
    }
    if not required.issubset(entries):
        raise ValueError("Backend or frontend source delivery is incomplete")
    if not required_contract_files(release).issubset(entries):
        raise ValueError("Schema/client upgrade source delivery is incomplete")
    backend = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    if (backend["project"]["version"] != release["applicationVersion"].replace("-rc.", "rc") or
            frontend["version"] != release["applicationVersion"]):
        raise ValueError("Backend/frontend application versions do not match this handoff")
    pins = (root / "backend/requirements.txt").read_text(encoding="utf-8").splitlines()
    if "fastapi==0.143.0" not in pins or "uvicorn==0.54.0" not in pins:
        raise ValueError("FastAPI runtime dependency pins are missing")
    public_names = {name for name, _ in source_files(root)}
    if public_names != set(entries):
        extra = sorted(public_names - set(entries))
        raise ValueError("Unlisted public source files: " + ", ".join(extra[:10]))
    return {"verifiedFiles": len(entries), "applicationVersion": manifest["applicationVersion"],
            "handoffRevision": manifest["handoffRevision"], "successful": True}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True))
    except ValueError as error:
        print("Source verification failed: " + str(error) + ". Use the complete matching source ZIP.", file=sys.stderr)
        raise SystemExit(1)
    except (KeyError, OSError, TypeError):
        print("Source verification failed; use the complete original source ZIP.", file=sys.stderr)
        raise SystemExit(1)
