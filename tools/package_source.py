"""Create a source ZIP without installed dependencies, credentials or user data."""
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_PARTS = {"node_modules", ".venv", "__pycache__", ".build", ".git", "wheelhouse", "test-data", "test-payload", "backups", "seed", "pm-originals", "runtime", "postgres"}
EXCLUDE_SUFFIXES = {".pyc", ".tsbuildinfo", ".dump", ".key", ".pfx", ".pgpass", ".pem", ".crt", ".cer"}


def excluded_name(name):
    name = name.lower()
    return (name in {"server.toml", "credentials.json", "local-state.json", "bootstrap.pgpass"}
            or name.startswith("owner") and name.endswith(".toml"))


def source_files():
    for directory, folders, files in os.walk(ROOT, topdown=True, followlinks=False):
        folders[:] = sorted(name for name in folders
            if name not in EXCLUDE_PARTS and not name.startswith(".")
            and not (Path(directory) / name).is_symlink()
            and not (Path(directory) / name).is_junction())
        for name in sorted(files):
            path = Path(directory) / name
            if name.startswith(".") and name not in {".gitignore", ".editorconfig"}:
                continue
            if path.is_symlink() or not path.is_file() or path.suffix.lower() in EXCLUDE_SUFFIXES or excluded_name(name):
                continue
            if path.relative_to(ROOT).as_posix() == "source-manifest.json":
                continue
            yield path


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/package_source.py /absolute/destination.zip")
    destination = Path(sys.argv[1]).resolve()
    if not destination.is_absolute() or destination.is_relative_to(ROOT):
        raise SystemExit("Choose an archive destination outside the source tree")
    if destination.exists():
        raise SystemExit("Refusing to overwrite an existing source archive")
    files = list(source_files())
    manifest = {"applicationVersion": "2.0.0-rc.10", "handoffRevision": 1,
                "containsCompanyData": False, "containsInstalledDependencies": False,
                "linuxTargetAccepted": False,
                "files": {p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}}
    manifest_path = ROOT / "source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(manifest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = zipfile.ZipInfo("PAK-Source-rc10/" + path.relative_to(ROOT).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = ((0o100755 if path.suffix == ".sh" else 0o100644) << 16)
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Source ZIP CRC verification failed")
    print(json.dumps({"archive": str(destination), "bytes": destination.stat().st_size,
        "files": len(files), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
