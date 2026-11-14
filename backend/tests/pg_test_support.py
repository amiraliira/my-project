"""Real PostgreSQL fixtures in fresh, explicitly disposable loopback databases.

No implicit local server, fallback provider, company database or downloaded
runtime. The prepared QA cluster configuration authorizes only pak_qa_* names.
"""
from dataclasses import replace
import ipaddress
import json
import os
from pathlib import Path
import re
import unittest
from uuid import uuid4

from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.migration import verify_schema8_snapshot_target


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("postgresql-v10.sql", "postgresql-score-functions.sql", "postgresql-ledger-guards.sql",
           "postgresql-pm-guards.sql", "postgresql-evidence-guards.sql", "postgresql-archive-guards.sql")


def initialize_schema8(owner, runtime_user, *, optional_oee=False):
    """Prepare the frozen historical contract only for explicit upgrade tests."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", runtime_user):
        raise ValueError("Invalid synthetic runtime role")
    directory = ROOT / "tests" / "fixtures" / "schema8"
    with owner.transaction() as c:
        for script in ("postgresql-v8.sql", *SCRIPTS[1:]):
            c.raw.execute((directory / script).read_text(encoding="utf-8-sig"))
        if optional_oee:
            c.raw.execute((directory / "postgresql-oee-v1.sql").read_text(encoding="utf-8-sig"))
        c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        c.execute(f'GRANT USAGE ON SCHEMA public TO "{runtime_user}"')
        c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{runtime_user}"')
        c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{runtime_user}"')
    if not optional_oee:
        verify_schema8_snapshot_target(owner)


def initialize_schema9(owner, runtime_user, *, optional_oee=False):
    """Frozen rc.11 source, never used as the current application schema."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", runtime_user):
        raise ValueError("Invalid synthetic runtime role")
    directory = ROOT / "tests" / "fixtures" / "schema9"
    with owner.transaction() as c:
        for script in ("postgresql-v9.sql", *SCRIPTS[1:]):
            c.raw.execute((directory / script).read_text(encoding="utf-8-sig"))
        if optional_oee:
            c.raw.execute((directory / "postgresql-oee-v1.sql").read_text(encoding="utf-8-sig"))
        c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        c.execute(f'GRANT USAGE ON SCHEMA public TO "{runtime_user}"')
        c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{runtime_user}"')
        c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{runtime_user}"')
    with owner.transaction(write=False) as c:
        owner._verify_schema_connection(c, runtime_role=False, expected_version=9)


def qa_settings():
    value = os.environ.get("PAK_POSTGRES_TEST_CONFIG")
    if not value:
        raise unittest.SkipTest("Real PostgreSQL fixture requires explicit isolated PAK_POSTGRES_TEST_CONFIG")
    path = Path(value).absolute()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("QA configuration must be a local regular file")
    if str(path).startswith("\\\\"):
        raise RuntimeError("QA configuration must be on local disk")
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    if settings.get("allowDestructiveTesting") is not True:
        raise RuntimeError("Disposable QA authorization is required")
    owner, runtime = DatabaseConfig(**settings["owner"]), DatabaseConfig(**settings["runtime"])
    if (owner.provider != "postgresql" or owner.sslmode != "verify-full" or not owner.development
            or not ipaddress.ip_address(owner.host).is_loopback or not owner.database.startswith("pak_qa_")
            or (owner.host, owner.port, owner.database) != (runtime.host, runtime.port, runtime.database)
            or owner.user == runtime.user):
        raise RuntimeError("Only separate accounts on the same isolated TLS PostgreSQL target are permitted")
    provenance = json.loads((path.parent / "provenance.json").read_text(encoding="utf-8"))
    line = (path.parent / "bootstrap.pgpass").read_text(encoding="utf-8").strip().split(":")
    if (len(line) != 5 or line[0] != owner.host or line[1] != str(owner.port) or line[2] != "*"
            or line[3] != provenance["bootstrapUser"] or not line[3].startswith("pak_qa_boot_")):
        raise RuntimeError("Prepared QA bootstrap identity does not match the explicit target")
    bootstrap = replace(owner, database="postgres", user=line[3], password=line[4])
    return path, owner, runtime, bootstrap, provenance


def fresh_database(testcase, *, prefix="app", initialize=True):
    """Initialize a brand new database and register cleanup for just that name."""
    if re.fullmatch(r"[a-z]{1,12}", prefix) is None:
        raise ValueError("Invalid QA fixture prefix")
    path, owner, runtime, bootstrap_config, provenance = qa_settings()
    bootstrap = Database(bootstrap_config)
    name = "pak_qa_" + prefix + "_" + uuid4().hex[:16]
    with bootstrap.connect() as c:
        c.execute(f'CREATE DATABASE "{name}" OWNER "{owner.user}" TEMPLATE template0 ENCODING \'UTF8\'')
        c.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
        c.execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{runtime.user}"')
    def remove_owned_database():
        # The name is a closed, generated identifier created by this call.
        if re.fullmatch(r"pak_qa_[a-z]{1,12}_[0-9a-f]{16}", name) is None:
            raise RuntimeError("Unsafe synthetic database cleanup target")
        # Keep the disposable database shell within the owned cluster. This
        # Windows QA runtime has an observed DROP DATABASE ProcSignalBarrier
        # hang; no fixture cleanup may wait unboundedly for that native issue.
        # Remove only objects in the exclusively new database instead.
        with Database(replace(owner, database=name)).transaction() as c:
            c.execute("DROP SCHEMA public CASCADE")
            c.execute("CREATE SCHEMA public")
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    testcase.addCleanup(remove_owned_database)
    owner_db = Database(replace(owner, database=name))
    config = replace(runtime, database=name)
    if initialize:
        with owner_db.transaction() as c:
            for script in SCRIPTS:
                c.raw.execute((ROOT / "schema" / script).read_text(encoding="utf-8-sig"))
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            c.execute(f'GRANT USAGE ON SCHEMA public TO "{runtime.user}"')
            c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{runtime.user}"')
            c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{runtime.user}"')
        owner_db.verify_schema(runtime_role=False)
        Database(config).verify_schema()
    return config, owner_db


def assert_database_constraints(testcase, database):
    with database.transaction(write=False) as c:
        testcase.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated"), 0)
        testcase.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT i.indisvalid"), 0)


def native_tool_paths():
    _, _, _, _, provenance = qa_settings()
    binary = Path(provenance["runtime"]) / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return dict(postgresql_dump_executable=str(binary / ("pg_dump" + suffix)),
                postgresql_restore_executable=str(binary / ("pg_restore" + suffix)))
