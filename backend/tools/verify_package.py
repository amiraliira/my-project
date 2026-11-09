"""Verify the fixed delivered files without network access or database access."""
import argparse
import hashlib
import json
import re
import stat
from pathlib import Path
import sys


def verify(root):
    root = Path(root).resolve(strict=True)
    manifest_path = root / "package-manifest.json"
    with manifest_path.open("rb") as stream:
        encoded = stream.read(4 * 1024 * 1024 + 1)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("Manifest exceeds its size limit")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate manifest key")
            result[key] = value
        return result
    manifest = json.loads(encoded.decode("utf-8"), object_pairs_hook=unique_keys)
    if (manifest.get("databaseProvider") != "postgresql" or manifest.get("minimumPostgreSQLMajor") != 17
            or manifest.get("applicationAllowsSQLite") is not False):
        raise ValueError("The application manifest must require PostgreSQL 17 or newer")
    files = manifest["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= 10000:
        raise ValueError("Invalid manifest")
    seen = set()
    for name, item in files.items():
        if not isinstance(name, str) or not name or any(p in ("", ".", "..") for p in name.split("/")):
            raise ValueError("Unsafe manifest path")
        relative = Path(name)
        if relative.is_absolute() or any(p in (".", "..") for p in relative.parts) or ":" in name or "\\" in name:
            raise ValueError("Unsafe manifest path")
        if name.casefold() in seen or relative.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx", ".pyc"}:
            raise ValueError("A duplicate, legacy data or sensitive file is listed in the package")
        seen.add(name.casefold())
        if (not isinstance(item, dict) or type(item.get("bytes")) is not int or item["bytes"] < 0
                or not isinstance(item.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
            raise ValueError("Invalid delivered file metadata")
        path = root / relative
        component = root
        for part in relative.parts:
            component = component / part
            attributes = component.lstat()
            if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & 0x400:
                raise ValueError("Delivered paths must not contain symlinks or reparse points")
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Manifest file escapes application directory")
        if path.stat().st_size != item["bytes"]:
            raise ValueError("Delivered file size differs")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError("Delivered file hash differs")
    required = {"runtime/python.exe", "runtime/python314.dll", "frontend/index.html", "src/pak_maintenance/application.py",
                "src/pak_maintenance/configuration.py", "src/pak_maintenance/database.py", "tools/verify_package.py"}
    if not required <= files.keys():
        raise ValueError("The package lacks required application components")
    if manifest.get("localAcceptanceOnly") is True:
        required = {"Start-Test.cmd", "test-payload/data/acceptance.dump", "test-payload/acceptance-data.json", "provenance/postgresql-provenance.json"}
        required |= {"postgres/bin/" + name + ".exe" for name in ("postgres", "initdb", "pg_ctl", "pg_dump", "pg_restore")}
        if manifest.get("containsDatabaseServer") is not True or not required <= files.keys():
            raise ValueError("The local presentation package lacks its PostgreSQL runtime or payload")
    elif manifest.get("containsDatabaseServer") is not False or manifest.get("containsCompanyData") is not False or any(name.startswith(("postgres/", "test-payload/")) for name in files):
        raise ValueError("The IT server package must exclude the database daemon and private payload")
    elif not {"Start-Server.cmd", "server.example.toml"} <= files.keys():
        raise ValueError("The IT server launcher or configuration template is absent")
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify local delivered PAK application files")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        print(f"PASS: {verify(args.root)} delivered files match. No network/database accessed.")
    except (OSError, ValueError, KeyError, TypeError):
        print("FAIL: Delivered files differ or cannot be verified. Extract a fresh package.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
