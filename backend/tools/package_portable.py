"""Build PostgreSQL-only Windows packages from verified offline inputs.

The private presentation package contains the official PostgreSQL 17 runtime
and redacted custom dump. The IT server package contains neither company data
nor a database daemon; IT supplies its PostgreSQL 17+ endpoint and native tools.
No download, installation, service registration or host change is performed.
"""
import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import json
import re
from pathlib import Path
import shutil
from uuid import uuid4
import zipfile
import tomllib
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
PRODUCTION = frozenset({"annotated-doc", "annotated-types", "anyio", "click", "fastapi", "h11", "idna",
    "opentelemetry-api", "psycopg", "psycopg-binary", "pydantic", "pydantic-core", "starlette",
    "typing-extensions", "typing-inspection", "tzdata", "uvicorn"})
VERSION = "2.0.0-rc.10"
REVIEW_TAG = 'rc' + VERSION.split('-rc.', 1)[1] if '-rc.' in VERSION else VERSION
POSTGRESQL_EXECUTABLES = frozenset({"postgres.exe", "initdb.exe", "pg_ctl.exe", "pg_dump.exe", "pg_restore.exe", "psql.exe", "pg_isready.exe"})


def frontend_root():
    local = PROJECT / "frontend"
    return local if local.is_dir() else PROJECT.parent / "frontend"


def validate_release():
    """Refuse a stale frontend or different app version before publishing."""
    pep_version = VERSION.replace('-rc.', 'rc')
    if tomllib.loads((PROJECT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version'] != pep_version:
        raise ValueError('Python project version differs from delivered release')
    if json.loads((frontend_root()/'package.json').read_text(encoding='utf-8'))['version'] != VERSION:
        raise ValueError('Frontend project version differs from delivered release')
    if f'__version__ = "{pep_version}"' not in (PROJECT/'src/pak_maintenance/__init__.py').read_text(encoding='utf-8'):
        raise ValueError('Python runtime version differs from delivered release')
    if f"clientVersion = '{VERSION}'" not in (frontend_root()/'src/compatibility.ts').read_text(encoding='utf-8'):
        raise ValueError('Frontend source version differs from delivered release')
    html=(frontend_root()/'dist/index.html').read_text(encoding='utf-8')
    entry=re.search(r'src="/assets/(index-[A-Za-z0-9_-]+\.js)"',html)
    if not entry or VERSION not in (frontend_root()/'dist/assets'/entry[1]).read_text(encoding='utf-8'):
        raise ValueError('Build the matching frontend before publishing this release')


def validate_final_review():
    path = PROJECT / 'docs' / (REVIEW_TAG + '-review.json')
    review = json.loads(path.read_text(encoding='utf-8'))
    if review.get('version') != VERSION or review.get('readyForItAcceptance') is not True:
        raise ValueError('A completed review for this exact release is required')
    for section in ('sourceFiles', 'frontendFiles'):
        entries = review.get(section)
        if not isinstance(entries, dict) or not entries:
            raise ValueError('Final review must bind source and compiled frontend hashes')
        for name, expected in entries.items():
            if sha256(safe_child(PROJECT, name)) != expected:
                raise ValueError('Files changed after final verification; repeat relevant checks')


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(archive, destination):
    for member in archive.infolist():
        path = Path(member.filename)
        if path.is_absolute() or any(p in (".", "..") for p in path.parts) or ":" in member.filename or "\\" in member.filename:
            raise ValueError("Unsafe archive member")
        if not (destination / path).resolve().is_relative_to(destination.resolve()):
            raise ValueError("Archive member escapes its target")
    archive.extractall(destination)


def wheels():
    selected = {}
    for path in sorted((PROJECT / ".build/wheels314").glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ValueError("Ambiguous wheel metadata")
            metadata = BytesParser().parsebytes(archive.read(names[0]))
        name = metadata["Name"].lower().replace("_", "-")
        if name not in PRODUCTION:
            continue
        if name in selected:
            raise ValueError("Multiple candidate versions for one dependency")
        selected[name] = dict(name=name, version=metadata["Version"], file=path.name, sha256=sha256(path))
    if set(selected) != PRODUCTION:
        raise ValueError("A locked production dependency is missing")
    return selected


def safe_child(root, relative):
    if not isinstance(relative, str) or not relative or any(p in ("", ".", "..") for p in relative.split("/")) or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe relative payload path")
    path = root.joinpath(*relative.split("/"))
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("Payload path escapes its root")
    return path


def copy_postgresql(build, stage):
    provenance_path = build / "vendor-postgresql/postgresql-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if provenance.get("version") != "17.11" or provenance.get("zipCrcVerified") is not True:
        raise ValueError("The reviewed official PostgreSQL 17.11 runtime is required")
    archive_path = build / "vendor-postgresql" / Path(urlsplit(provenance["url"]).path).name
    if archive_path.stat().st_size != provenance["bytes"] or sha256(archive_path) != provenance["sha256"]:
        raise ValueError("Official PostgreSQL archive hash differs from provenance")
    source = build / "vendor-postgresql/edb-17.11/pgsql"
    files = []
    for folder in ("bin", "lib", "share", "doc"):
        for path in sorted((source / folder).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if path.is_symlink() or path.suffix.lower() in {".pem", ".key", ".pfx", ".map", ".pyc", ".pdb", ".lib", ".a"}:
                continue
            if folder == "bin" and path.name not in POSTGRESQL_EXECUTABLES:
                if path.suffix.lower() != ".dll" or path.name.lower().startswith("wx") or path.name.lower() == "libcurl.dll":
                    continue
            files.append((path, relative.as_posix()))
    executable_hashes = {}
    with zipfile.ZipFile(archive_path) as archive:
        for path, relative in files:
            # Verify the extracted source itself against the recorded official
            # archive. Hashing only the download does not verify later edits.
            member = archive.getinfo("pgsql/" + relative)
            digest = hashlib.sha256()
            with archive.open(member) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if member.file_size != path.stat().st_size or digest.hexdigest() != sha256(path):
                raise ValueError("Extracted PostgreSQL runtime differs from its official archive")
            target = stage / "postgres" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            if relative.startswith("bin/") and path.suffix == ".exe":
                executable_hashes[path.stem] = digest.hexdigest()
        for name in ("server_license.txt", "commandlinetools_3rd_party_licenses.txt"):
            target = stage / "postgres/legal" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open("pgsql/" + name) as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    if {name + ".exe" for name in executable_hashes} != POSTGRESQL_EXECUTABLES:
        raise ValueError("A required PostgreSQL executable is missing")
    result = {key: value for key, value in provenance.items() if key != "extractionDirectory"}
    result["packagedExecutableSha256"] = executable_hashes
    result["packagedFiles"] = len(files) + 2
    result["excluded"] = ["StackBuilder/download installers", "GUI libraries", "build/link artifacts", "unused database administration executables"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build offline PAK application packages")
    parser.add_argument("--acceptance", action="store_true", help="Private local copy with password-redacted company test data and original PM forms")
    parser.add_argument("--acceptance-inputs", help="Explicit verified PostgreSQL custom-dump schema-8 input directory; never a live database")
    parser.add_argument("--staging-root", help="Explicit short local build directory for native Windows path limits")
    parser.add_argument('--final', action='store_true', help='Require completed exact-source/frontend release evidence before publishing')
    args = parser.parse_args(argv)
    validate_release()
    if args.final:
        validate_final_review()
    build = PROJECT / ".build"
    python_provenance = json.loads((build / "python-provenance.json").read_text())
    sqlite_provenance = json.loads((build / "sqlite-provenance.json").read_text())
    python_archive = build / "python-3.14.8-embed-amd64.zip"
    sqlite_dll = build / "portable-python/sqlite3.dll"
    if sha256(python_archive) != python_provenance["sha256"] or sha256(sqlite_dll) != sqlite_provenance["dllSha256"]:
        raise ValueError("Official runtime input hash mismatch")
    dependencies = wheels()
    lock = "# Windows x64 / CPython 3.14.8; generated only from local reviewed wheels.\n"
    lock += "\n".join(f"{name}=={entry['version']} --hash=sha256:{entry['sha256']}" for name, entry in sorted(dependencies.items())) + "\n"
    (PROJECT / "requirements.lock").write_text(lock, encoding="utf-8")
    staging = Path(args.staging_root).resolve() if args.staging_root else build
    if str(staging).startswith("\\\\"):
        raise ValueError("Package staging requires a local directory")
    stage = staging / ("p-" + uuid4().hex[:12]) / "PakMaintenance-Python"
    stage.mkdir(parents=True, mode=0o755)
    runtime = stage / "runtime"
    runtime.mkdir(mode=0o755)
    with zipfile.ZipFile(python_archive) as archive:
        extract(archive, runtime)
    shutil.copy2(sqlite_dll, runtime / "sqlite3.dll")
    (runtime / "python314._pth").write_text("python314.zip\n.\nLib/site-packages\n../src\n", encoding="ascii")
    site = runtime / "Lib/site-packages"
    site.mkdir(parents=True, mode=0o755)
    for entry in dependencies.values():
        with zipfile.ZipFile(build / "wheels314" / entry["file"]) as archive:
            extract(archive, site)
    shutil.copytree(PROJECT / "src", stage / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(PROJECT / "schema", stage / "schema")
    frontend = frontend_root() / "dist"
    if not (frontend / "index.html").is_file():
        raise ValueError("Build the local frontend before packaging")
    shutil.copytree(frontend, stage / "frontend")
    launcher_names = ("Start-Test.cmd", "Verify-Package.cmd", "Enable-OEE.cmd") if args.acceptance else ("Start-Server.cmd", "Verify-Package.cmd", "Install-Update.cmd", "Rollback.cmd", "server.example.toml")
    for name in launcher_names:
        if name.endswith(".cmd"):
            value = (PROJECT / "launchers" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
            (stage / name).write_bytes(value.replace("\n", "\r\n").encode("utf-8"))
        else:
            shutil.copy2(PROJECT / "launchers" / name, stage / name)
    if args.acceptance:
        shutil.copy2(stage / "Start-Test.cmd", stage / "شروع تست.cmd")
        inputs = Path(args.acceptance_inputs).resolve() if args.acceptance_inputs else build / "acceptance-inputs-current"
        input_manifest = json.loads((inputs / "acceptance-data.json").read_text(encoding="utf-8"))
        if (input_manifest.get("schemaVersion") != 8 or input_manifest.get("databaseProvider") != "postgresql"
                or input_manifest.get("databaseFormat") != "postgresql-custom"
                or input_manifest.get("databaseFile") != "data/acceptance.dump"):
            raise ValueError("Acceptance payload must be a PostgreSQL schema-8 custom dump")
        if input_manifest.get("purpose") != "private-local-acceptance-only" or input_manifest.get("authentication", {}).get("passwordMarker") != "!LOCAL_ACCEPTANCE_PASSWORD_REQUIRED!":
            raise ValueError("Acceptance payload must be a password-redacted private copy")
        database_input = safe_child(inputs, input_manifest["databaseFile"])
        if sha256(database_input) != input_manifest["databaseSha256"]:
            raise ValueError("Acceptance database has changed since verification")
        with database_input.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise ValueError("Acceptance payload is not a PostgreSQL custom archive")
        for entry in input_manifest["seedFiles"]:
            path = safe_child(inputs / "seed", entry["path"])
            if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
                raise ValueError("Original PM source hash mismatch")
        payload = stage / "test-payload"
        (payload / "data").mkdir(parents=True)
        shutil.copy2(database_input, payload / "data/acceptance.dump")
        shutil.copy2(inputs / "acceptance-data.json", payload / "acceptance-data.json")
        for entry in input_manifest["seedFiles"]:
            source = safe_child(inputs / "seed", entry["path"])
            destination = safe_child(payload / "seed", entry["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Copy only explicitly verified originals; unknown extra files in
            # the staging input must never silently enter the private package.
            with source.open("rb") as incoming, destination.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    shutil.copy2(PROJECT / "requirements.lock", stage / "requirements.lock")
    shutil.copy2(PROJECT / ("docs/local-test-start.md" if args.acceptance else "docs/it-start-here.md"), stage / "README.md")
    documents = stage if args.acceptance else stage / 'docs'
    documents.mkdir(exist_ok=True)
    shutil.copy2(PROJECT / "docs/oee-v1.md", documents / "oee-v1.md")
    if args.acceptance:
        shutil.copy2(PROJECT / "docs/local-oee-acceptance.md", documents / "local-oee-acceptance.md")
    if args.acceptance:
        # README links remain usable after extracting the private test package.
        for name in ("verification-results.json", "synthetic-load-results.json", "postgresql-acceptance.json", "postgresql-migration-reconciliation.json", "frontend-rc6-verification.json", "rc6-load-review.json", "rc6-concurrent-writes.json", "rc6-browser-verification.json", "rc6-review.md", "oee-v1.md", "rc7-oee-verification.json", "rc8-review-verification.json", "rc8-review-summary.json", "local-oee-acceptance.md", "rc5-archive-verification.json", "rc5-browser-verification.json", "rc5-payload-upgrade.json", "postgresql-it-handoff.md"):
            if (PROJECT / "docs" / name).is_file():
                shutil.copy2(PROJECT / "docs" / name, stage / name)
    shutil.copy2(PROJECT / "schema/release-policy.json", stage / "release-policy.json")
    for name in ("it-install-update.md", "postgresql-it-handoff.md", "maintenance-map.md", "it-functional-acceptance.md",
                 REVIEW_TAG + "-review.json", REVIEW_TAG + "-installer-e2e.json", REVIEW_TAG + "-load-comparison.json",
                 "verification-results.json", REVIEW_TAG + "-load-current.json", REVIEW_TAG + "-concurrent-writes.json",
                 REVIEW_TAG + "-load-host-limitation.json", REVIEW_TAG + "-final-query-verification.json",
                 REVIEW_TAG + "-query-plan-review.json", REVIEW_TAG + "-load-baseline.json"):
        if (PROJECT / "docs" / name).is_file():
            shutil.copy2(PROJECT / "docs" / name, documents / name)
    (stage / "tools").mkdir()
    for tool in ("verify_package.py", "upgrade_pm_archive.py", "enable_oee.py", "release_manager.py"):
        shutil.copy2(PROJECT / "tools" / tool, stage / "tools" / tool)
    (stage / "provenance").mkdir()
    for name in ("python-provenance.json", "sqlite-provenance.json"):
        shutil.copy2(build / name, stage / "provenance" / name)
    postgresql_provenance = copy_postgresql(build, stage) if args.acceptance else None
    if postgresql_provenance is not None:
        (stage / "provenance/postgresql-provenance.json").write_text(json.dumps(postgresql_provenance, indent=2) + "\n", encoding="utf-8")
    (stage / "provenance/dependencies.json").write_text(json.dumps(dependencies, indent=2) + "\n", encoding="utf-8")
    files = {}
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink() or path.suffix in {".pem", ".key", ".pfx", ".map", ".pyc", ".db", ".sqlite", ".sqlite3"}:
            raise ValueError("Unexpected sensitive/build artifact in portable package")
        files[path.relative_to(stage).as_posix()] = dict(bytes=path.stat().st_size, sha256=sha256(path))
    manifest = dict(version=VERSION, platform="Windows x64", python="3.14.8", databaseProvider="postgresql",
        minimumPostgreSQLMajor=17, bundledPostgreSQL="17.11" if args.acceptance else None,
        offlineLegacySQLiteImportRuntime="3.54.0", applicationAllowsSQLite=False,
        createdUtc=datetime.now(timezone.utc).isoformat(), productionAccepted=False,
        containsCompanyData=args.acceptance, containsDatabaseServer=args.acceptance,
        localAcceptanceOnly=args.acceptance, containsOriginalPasswords=False, files=files)
    (stage / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    directory = PROJECT / "dist"
    directory.mkdir(exist_ok=True)
    target = directory / f"{'PakMaintenance-Test' if args.acceptance else 'PakMaintenance-Python'}-{VERSION}-win-x64.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, arcname="PakMaintenance-Python/" + path.relative_to(stage).as_posix())
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError("Portable archive CRC verification failed")
    report = dict(archive=target.name, bytes=target.stat().st_size, expandedBytes=sum(x["bytes"] for x in files.values()),
        sha256=sha256(target), files=len(files), productionAccepted=False, containsCompanyData=args.acceptance,
        containsOriginalPasswords=False, localAcceptanceOnly=args.acceptance, databaseProvider="postgresql", minimumPostgreSQLMajor=17,
        containsDatabaseServer=args.acceptance, bundledPostgreSQL="17.11" if args.acceptance else None,
        stagingDirectory=str(stage), dependencies=dependencies)
    (directory / ("acceptance-package.json" if args.acceptance else "portable-package.json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"dependencies"}}))


if __name__ == "__main__":
    main()
