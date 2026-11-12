"""Read-only local preflight; never connects, migrates, seeds or starts a server."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import sys
import tomllib

BACKEND = Path(__file__).absolute().parents[1]


def check(*, config=False):
    errors = []
    if sys.version_info < (3, 12):
        return ["Python 3.12 or newer is required."]
    required = (
        "app/__init__.py", "app/main.py", "app/settings.py", "pyproject.toml",
        "requirements.txt", "src/pak_maintenance/__init__.py",
        "src/pak_maintenance/application.py", "src/pak_maintenance/api.py",
        "schema/release-policy.json",
    )
    for name in required:
        if not (BACKEND / name).is_file():
            errors.append("Missing backend file: " + name)
    if errors:
        return errors
    sys.path.insert(0, str(BACKEND))
    try:
        import app  # Only selects and checks delivered source; no settings/DB.
        import pak_maintenance
        project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
        expected = project["project"]["version"]
        if pak_maintenance.__version__ != expected:
            errors.append("Package version does not match pyproject.toml; restore a complete matching backend.")
        else:
            print("[OK] Backend version: " + expected)
            print("[OK] Package source: " + str(pak_maintenance.__file__))
        policy = json.loads((BACKEND / "schema/release-policy.json").read_text(encoding="utf-8"))
        print(f"[INFO] Required core schema: {policy['coreSchema']}; optional OEE module: {policy['optionalOeeSchema']}")
    except (ImportError, OSError, ValueError, KeyError) as error:
        return [str(error)]
    for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, version = line.strip().partition("==")
        if not separator:
            errors.append("Unsupported dependency entry in requirements.txt; restore the original file.")
            continue
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            errors.append("Missing dependency: " + name)
            continue
        if installed != version:
            errors.append(f"Dependency mismatch: {name} (expected {version}, installed {installed})")
    if errors:
        errors.append("In this virtual environment run: python -m pip install -r requirements.txt")
        return errors
    print("[OK] Pinned runtime dependencies are installed in this interpreter.")
    if config:
        try:
            from app.settings import load_config
            settings = load_config()
        except (ImportError, ValueError, OSError):
            # Do not echo arbitrary filesystem/OS exception text from private paths.
            return ["Configuration validation failed. Check the private .env against .env.example and README.md; values were not printed."]
        if not (Path(settings.seed_directory) / "pm-import.json").is_file():
            errors.append("PAK_PM_SEED_DIR must contain the existing pm-import.json file.")
        if not (Path(settings.seed_directory) / "pm-originals").is_dir():
            errors.append("PAK_PM_SEED_DIR must contain the existing pm-originals/ directory.")
        if not errors:
            print("[OK] Local configuration and PM seed paths validated; no database connection attempted.")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", action="store_true", help="Also validate the private .env and existing PM seed paths; never prints values")
    arguments = parser.parse_args()
    print("Interpreter: " + sys.executable)
    errors = check(config=arguments.config)
    for error in errors:
        print("[ERROR] " + error, file=sys.stderr)
    if errors:
        return 1
    print("[OK] Local preflight passed. Database contents/schema and live HTTP service were NOT checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
