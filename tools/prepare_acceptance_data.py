"""Prepare a PRIVATE, disposable acceptance copy from a verified legacy backup.

Never reads the live database and never ships production password hashes. This
operator tool is excluded from runtime startup; its inputs stay under .build.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import stat
from uuid import uuid4

from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.migration import TABLES, V7_TABLES, LEGACY_TABLES, migrate_snapshot, snapshot_sha256
from pak_maintenance.network_policy import validate_file_path

PROJECT = Path(__file__).resolve().parents[1]
PASSWORD_MARKER = "!LOCAL_ACCEPTANCE_PASSWORD_REQUIRED!"
_ORIGINAL = re.compile(r"([a-fA-F0-9]{64})(\.docx|\.xlsx)\Z", re.ASCII)
_MAX_JSON = 32 * 1024 * 1024


class AcceptanceDataError(ValueError):
    """No unverifiable or incomplete acceptance copy is published."""


def _local(path: Path) -> Path:
    path = Path(validate_file_path(path)).absolute()
    if str(path).startswith("\\\\"):
        raise AcceptanceDataError("Acceptance inputs must be local files.")
    for component in (path, *path.parents):
        if component.exists():
            metadata = component.lstat()
            if component.is_symlink() or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise AcceptanceDataError("Acceptance paths cannot traverse links or reparse points.")
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, maximum: int = _MAX_JSON):
    path = _local(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise AcceptanceDataError("A required JSON input is missing or too large.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        raise AcceptanceDataError("An input is not valid JSON.") from None


@contextmanager
def _read_only(path: Path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection
    finally:
        connection.close()


def verify_backup(directory: Path) -> tuple[Path, str, dict]:
    """Select the newest named frozen backup; a bad latest file never falls back."""
    directory = _local(directory)
    if directory.name != "backups" or not directory.is_dir():
        raise AcceptanceDataError("Use the legacy backups directory, never the live data directory.")
    def chronological_key(item):
        # The legacy directory mixes pak-auto-* and pak-before-1.x-*; sorting
        # their full names would pick an older release backup over a new auto.
        match = re.search(r"(\d{8}T\d{6})(\d{0,6})Z", item.name, flags=re.ASCII)
        if match is None:
            raise AcceptanceDataError("Backup filenames must contain an explicit sortable UTC timestamp.")
        return match[1], match[2].ljust(6, "0"), item.name
    candidates = sorted(directory.glob("*.db"), key=chronological_key, reverse=True)
    if not candidates:
        raise AcceptanceDataError("No frozen SQLite backup is available.")
    source = _local(candidates[0])
    manifest = _json(Path(str(source) + ".json"), 16 * 1024)
    if not isinstance(manifest, dict) or manifest.get("application") != "PakMaintenance" or manifest.get("kind") != "sqlite-online-snapshot" or manifest.get("file") != source.name or manifest.get("integrityChecked") is not True:
        raise AcceptanceDataError("The newest backup has no valid matching online-snapshot manifest.")
    digest = snapshot_sha256(source)
    if manifest.get("bytes") != source.stat().st_size or manifest.get("sha256") != digest:
        raise AcceptanceDataError("The newest backup does not match its independent size/SHA-256 manifest.")
    with _read_only(source) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise AcceptanceDataError("The snapshot failed independent integrity/foreign-key checks.")
    return source, digest, manifest


def _original_name(source: dict) -> str:
    if not isinstance(source, dict):
        raise AcceptanceDataError("An original declaration is missing.")
    digest, name = source.get("sha256"), source.get("path")
    if not isinstance(digest, str) or not isinstance(name, str):
        raise AcceptanceDataError("An original declaration has no hash/path.")
    suffix = Path(PureWindowsPath(name).name).suffix
    combined = digest + suffix
    if not _ORIGINAL.fullmatch(combined):
        raise AcceptanceDataError("An original has an unsupported extension or invalid SHA-256.")
    return combined


def verify_seed(seed_directory: Path, snapshot: Path) -> tuple[dict[str, str], int]:
    seed_directory = _local(seed_directory)
    import_path = seed_directory / "pm-import.json"
    document = _json(import_path)
    templates = document.get("templates") if isinstance(document, dict) else None
    if not isinstance(templates, list) or not templates or len(templates) > 1000:
        raise AcceptanceDataError("The PM import must contain a bounded nonempty template list.")
    ids = [item.get("id") for item in templates if isinstance(item, dict)]
    if len(ids) != len(templates) or not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != len(ids):
        raise AcceptanceDataError("The PM import contains invalid or duplicate template IDs.")
    declarations = {_original_name(item.get("source")) for item in templates}
    with _read_only(snapshot) as connection:
        for table in ("pm_templates", "pm_template_revisions"):
            for (body,) in connection.execute(f"SELECT body FROM {table}"):
                try:
                    form = json.loads(body)
                except (ValueError, TypeError):
                    raise AcceptanceDataError("A stored PM template is not valid JSON.") from None
                if isinstance(form, dict) and form.get("source") is not None:
                    declarations.add(_original_name(form["source"]))
        for (body,) in connection.execute("SELECT body FROM orders"):
            try:
                order = json.loads(body)
            except (ValueError, TypeError):
                raise AcceptanceDataError("A stored order is not valid JSON.") from None
            form = order.get("pmSheet") if isinstance(order, dict) else None
            if isinstance(form, dict) and form.get("source") is not None:
                declarations.add(_original_name(form["source"]))
    originals = _local(seed_directory / "pm-originals")
    if not originals.is_dir():
        raise AcceptanceDataError("The original PM document directory is missing.")
    hashes = {"pm-import.json": _hash(import_path)}
    present = set()
    for candidate in sorted(originals.iterdir()):
        candidate = _local(candidate)
        match = _ORIGINAL.fullmatch(candidate.name)
        if not candidate.is_file() or match is None:
            raise AcceptanceDataError("The original directory contains an unexpected file or directory.")
        digest = _hash(candidate)
        if digest != match[1].lower():
            raise AcceptanceDataError("An original PM document no longer matches its declared bytes.")
        present.add(candidate.name.lower())
        hashes["pm-originals/" + candidate.name] = digest
    if not {name.lower() for name in declarations}.issubset(present):
        raise AcceptanceDataError("At least one declared original PM document is missing.")
    return hashes, len(templates)


def _new_guarded_target(target: Path, schema_directory: Path):
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript((schema_directory / "sqlite-v8.sql").read_text(encoding="utf-8"))
        for filename in ("sqlite-ledger-guards.sql", "sqlite-pm-guards.sql", "sqlite-evidence-guards.sql", "sqlite-archive-guards.sql"):
            connection.executescript((schema_directory / filename).read_text(encoding="utf-8"))


def _logical_hash(connection, spec, *, expected: bool) -> tuple[int, str]:
    keys = spec.keys or spec.columns
    ordering = ",".join(key if key in spec.integer or key in spec.bigint else key + " COLLATE BINARY" for key in keys)
    cursor = connection.execute(f"SELECT {','.join(spec.columns)} FROM {spec.name} ORDER BY {ordering}")
    digest = hashlib.sha256()
    count = 0
    for row in cursor:
        if expected and spec.name == "sessions":
            continue
        canonical = []
        for column, value in zip(spec.columns, row):
            if expected and spec.name == "schema_version":
                value = 8
            if expected and spec.name == "users":
                if column == "password_hash":
                    value = PASSWORD_MARKER
                elif column == "session_epoch":
                    value += 1
            if value is not None and column in spec.numeric:
                number = Decimal(format(value, ".15g") if isinstance(value, float) else str(value))
                value = format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
                canonical.append(["decimal", value if number else "0"])
            else:
                canonical.append([type(value).__name__, value])
        encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return count, digest.hexdigest()


def _redact_passwords(target: Path):
    with closing(sqlite3.connect(target)) as connection, connection:
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("UPDATE users SET password_hash=?", (PASSWORD_MARKER,))
        connection.execute("DELETE FROM sessions")
    # Remove old password bytes from free pages as well as logical records.
    with closing(sqlite3.connect(target, isolation_level=None)) as connection:
        connection.execute("VACUUM")


def _verify_no_source_password_bytes(source: Path, target: Path):
    with _read_only(source) as connection:
        secrets = [value.encode("utf-8") for (value,) in connection.execute("SELECT password_hash FROM users") if value and value != PASSWORD_MARKER]
    if not secrets:
        return
    maximum = max(map(len, secrets))
    tail = b""
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            combined = tail + chunk
            if any(secret in combined for secret in secrets):
                raise AcceptanceDataError("Source authentication material remains in the prepared database.")
            tail = combined[-maximum:]


def prepare(backup_directory: Path, seed_directory: Path, destination: Path, *, schema_directory: Path | None = None, evidence_path: Path | None = None) -> dict:
    source, before, source_manifest = verify_backup(backup_directory)
    source_manifest_path = Path(str(source) + ".json")
    source_manifest_hash = _hash(source_manifest_path)
    seed_directory, destination = _local(seed_directory), _local(destination)
    if destination.exists() or destination == source or destination.is_relative_to(_local(backup_directory)) or destination.is_relative_to(seed_directory):
        raise AcceptanceDataError("The acceptance destination must be new and separate from every source.")
    hashes, imported_templates = verify_seed(seed_directory, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep the private staging name short enough for ordinary Windows paths;
    # original hash filenames already consume 69 characters.
    staging = destination.parent / (".ap-" + uuid4().hex[:16])
    staging.mkdir(mode=0o755)
    try:
        target = staging / "data" / "acceptance.db"
        target.parent.mkdir(mode=0o755)
        _new_guarded_target(target, schema_directory or PROJECT / "schema")
        database = Database(DatabaseConfig(provider="sqlite", database=str(target), development=True))
        migration = migrate_snapshot(source, database, expected_sha256=before)
        _redact_passwords(target)
        _verify_no_source_password_bytes(source, target)
        tables = []
        with _read_only(source) as original, _read_only(target) as copied:
            source_specs = {spec.name: spec for spec in (TABLES if migration.source_schema_version == 8 else V7_TABLES if migration.source_schema_version == 7 else LEGACY_TABLES)}
            for spec in TABLES:
                source_spec = source_specs.get(spec.name)
                source_count = original.execute(f"SELECT COUNT(*) FROM {spec.name}").fetchone()[0] if source_spec else 0
                expected_count, expected_hash = _logical_hash(original, source_spec, expected=True) if source_spec else (0, hashlib.sha256().hexdigest())
                target_count, target_hash = _logical_hash(copied, source_spec or spec, expected=False)
                if (expected_count, expected_hash) != (target_count, target_hash):
                    raise AcceptanceDataError(f"Independent final reconciliation failed for {spec.name}.")
                tables.append(dict(table=spec.name, sourceRows=source_count, targetRows=target_count, expectedRows=expected_count, logicalSha256=target_hash, equalAfterDocumentedAuthenticationRedaction=True))
            if copied.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or copied.execute("PRAGMA foreign_key_check").fetchall():
                raise AcceptanceDataError("The final acceptance copy failed integrity/foreign-key checks.")
            inactive = copied.execute("SELECT COUNT(*) FROM pm_weekly_plans WHERE active=0").fetchone()[0]
            unconfirmed = copied.execute("SELECT COUNT(*) FROM pm_weekly_plans WHERE mapping_confirmed=0").fetchone()[0]
            unset_passwords = copied.execute("SELECT COUNT(*) FROM users WHERE password_hash=?", (PASSWORD_MARKER,)).fetchone()[0]
        for relative, digest in hashes.items():
            original_path, copied_path = seed_directory / relative, staging / "seed" / relative
            copied_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original_path, copied_path)
            if _hash(copied_path) != digest or _hash(original_path) != digest:
                raise AcceptanceDataError("A PM input changed while its copy was being verified.")
        after = snapshot_sha256(source)
        if before != after or _hash(source_manifest_path) != source_manifest_hash:
            raise AcceptanceDataError("The frozen source or its manifest changed during preparation.")
        # Recheck the complete seed at the end, including absence of extra files.
        if verify_seed(seed_directory, source) != (hashes, imported_templates):
            raise AcceptanceDataError("The source PM input collection changed during preparation.")
        target_sha = _hash(target)
        manifest = dict(formatVersion=1, purpose="private-local-acceptance-only", schemaVersion=8, sourceSchemaVersion=migration.source_schema_version, sourceSnapshotSha256=before,
            sourceSnapshotUnchanged=True, databaseFile="data/acceptance.db", databaseSha256=target_sha,
            tableCounts={row["table"]: row["targetRows"] for row in tables},
            authentication=dict(passwordMarker=PASSWORD_MARKER, passwordsRemoved=unset_passwords, sessions=0, sessionEpochIncrement=1),
            seedFiles=[dict(path=relative, sha256=digest, bytes=(staging / "seed" / relative).stat().st_size) for relative, digest in sorted(hashes.items())])
        (staging / "acceptance-data.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence = dict(formatVersion=1, preparedAtUtc=datetime.now(timezone.utc).isoformat(), purpose=manifest["purpose"],
            sourceBackup=source.name, sourceCreatedAt=source_manifest.get("createdAt"), sourceSha256Before=before, sourceSha256After=after,
            sourceUnchanged=True, sourceIntegrity="ok", sourceForeignKeyViolations=0, targetIntegrity="ok", targetForeignKeyViolations=0,
            targetSha256=target_sha, schemaVersion=8, sourceSchemaVersion=migration.source_schema_version, reconciledTables=len(tables), tables=tables,
            copiedOriginalDocuments=len(hashes)-1, importedTemplates=imported_templates, copiedOriginalBytes=sum(item["bytes"] for item in manifest["seedFiles"] if item["path"].startswith("pm-originals/")),
            allOriginalBytesUnchanged=True, allDeclaredOriginalsVerified=True, inactivePlansPreserved=inactive, unconfirmedPlansPreserved=unconfirmed,
            authentication=dict(passwordHashesRemoved=unset_passwords, sourcePasswordBytesAbsent=True, discardedSessions=migration.discarded_sessions, sessionEpochIncrement=1),
            deliberatelyExcluded=["live-database", "first-login.txt", "legacy-backups", "legacy-master-data-json", "legacy-data-json"],
            automaticPlanActivation=False, syntheticRecordsAdded=False, requiresLocalBrowserPasswordSetup=True,
            limitations=["Local acceptance copy; not a production database cutover.", "Existing unconfirmed/inactive PM plans stay unchanged and require the same explicit chief review."])
        # Publish only after every database, original, source and credential check.
        staging.rename(destination)
        if evidence_path is not None:
            evidence_path = _local(evidence_path)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return evidence
    finally:
        if staging.exists():
            if staging.parent.resolve() != destination.parent.resolve() or staging.is_symlink():
                raise RuntimeError("Refusing cleanup outside the owned acceptance staging directory.")
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backups", type=Path, default=PROJECT.parent / "PakMaintenance-Personal/app/backups")
    parser.add_argument("--seed", type=Path, default=PROJECT.parent / "PakMaintenance-Personal/app/seed")
    parser.add_argument("--destination", type=Path, default=PROJECT / ".build/acceptance-inputs")
    parser.add_argument("--evidence", type=Path, default=PROJECT / "docs/acceptance-data-verification.json")
    args = parser.parse_args()
    evidence = prepare(args.backups, args.seed, args.destination, evidence_path=args.evidence)
    print(json.dumps(dict(reconciledTables=evidence["reconciledTables"], originals=evidence["copiedOriginalDocuments"],
        sourceUnchanged=evidence["sourceUnchanged"], passwordsRemoved=evidence["authentication"]["passwordHashesRemoved"])))


if __name__ == "__main__":
    main()
