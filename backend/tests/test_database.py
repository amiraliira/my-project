"""Real SQLite tests plus clearly isolated PostgreSQL driver fault injection.

No company database is opened and no database server is contacted. Fault
injection checks transaction lifecycle / binding boundaries; it does not replace
the PostgreSQL integration, SCRAM, certificate, and concurrency acceptance gates.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import sqlite3
import unittest
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from pak_maintenance.database import (
    Connection,
    Database,
    DatabaseCleanupError,
    DatabaseConfig,
    DatabaseConfigurationError,
    DatabaseDependencyError,
    SchemaMismatchError,
    compile_sql,
)
from pak_maintenance.network_policy import NetworkPolicyError


@contextmanager
def fixture_directory():
    root = Path(__file__).resolve().parent / ".database-test-data"
    root.mkdir(mode=0o755, exist_ok=True)
    child = root / uuid.uuid4().hex
    child.mkdir(mode=0o755)
    try:
        yield child
    finally:
        if child.resolve().parent != root.resolve() or child.is_symlink():
            raise RuntimeError("Refusing cleanup outside the database test fixture root.")
        shutil.rmtree(child)
        try:
            root.rmdir()
        except OSError:
            pass


def create_fixture(path: Path, schema: str = "") -> None:
    raw = sqlite3.connect(path)
    try:
        raw.executescript(schema or "CREATE TABLE entries(id INTEGER PRIMARY KEY,value TEXT NOT NULL);")
        raw.commit()
    finally:
        raw.close()


def postgres_config(**changes) -> DatabaseConfig:
    values = dict(provider="postgresql", database="pak_test", host="127.0.0.1", port=55442,
                  user="pak_test_app", password="fixture-only-not-a-real-password", sslmode="disable", development=True)
    values.update(changes)
    return DatabaseConfig(**values)


class ScriptedPgError(Exception):
    def __init__(self, sqlstate=None):
        super().__init__("Injected driver boundary failure, not a server connection.")
        self.sqlstate = sqlstate


class ScriptedCursor:
    description = (("value",),)
    rowcount = 1

    def __init__(self, raw):
        self.raw = raw
        self.closed = False

    def execute(self, statement, values):
        self.raw.events.append((statement, values))
        if statement == "COMMIT" and self.raw.commit_error is not None:
            raise self.raw.commit_error
        if statement == "ROLLBACK" and self.raw.rollback_error is not None:
            raise self.raw.rollback_error

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        self.closed = True


class ScriptedRawConnection:
    def __init__(self, commit_error=None, rollback_error=None, close_error=None, server_version=180006):
        self.info = SimpleNamespace(server_version=server_version)
        self.events = []
        self.cursors = []
        self.closed = False
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error

    def cursor(self):
        cursor = ScriptedCursor(self)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class ScriptedDriver:
    Error = ScriptedPgError
    pq = SimpleNamespace(version=lambda: 180006)

    def __init__(self, scripts=()):
        self.scripts = list(scripts)
        self.connections = []
        self.parameters = []

    def connect(self, **parameters):
        self.parameters.append(parameters)
        script = self.scripts.pop(0) if self.scripts else {}
        raw = ScriptedRawConnection(**script)
        self.connections.append(raw)
        return raw


def scripted_database(driver) -> Database:
    database = Database(postgres_config())
    database._driver = driver
    return database


class DatabaseConfigurationTests(unittest.TestCase):
    def test_password_is_hidden_and_configuration_is_frozen(self):
        config = postgres_config()
        self.assertNotIn(config.password, repr(config))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.host = "8.8.8.8"

    def test_unknown_provider_and_conninfo_smuggling_are_refused(self):
        with fixture_directory() as directory:
            for provider in ("", "postgres", "sqlite ", None):
                with self.subTest(provider=provider), self.assertRaises(DatabaseConfigurationError):
                    DatabaseConfig(provider, str(directory / "local.db"))
        for changes in (
            {"database": "postgresql://evil.example/db"}, {"database": "dbname=pak host=8.8.8.8"},
            {"user": "pak user=other"}, {"password": ""}, {"password": "unsafe\x00tail"},
            {"connect_timeout": True}, {"connect_timeout": 0}, {"connect_timeout": 31},
            {"busy_timeout_ms": False}, {"busy_timeout_ms": 60001}, {"development": "true"},
        ):
            with self.subTest(changes=tuple(changes)), self.assertRaises(DatabaseConfigurationError):
                postgres_config(**changes)

    def test_tls_disable_requires_explicit_loopback_development(self):
        self.assertEqual(postgres_config().sslmode, "disable")
        for changes in ({"development": False}, {"host": "192.168.1.20"}, {"host": "fd00::1"}, {"sslmode": "prefer"}, {"sslmode": "require"}):
            with self.subTest(changes=changes), self.assertRaises(DatabaseConfigurationError):
                postgres_config(**changes)
        with self.assertRaises(NetworkPolicyError):
            postgres_config(host="8.8.8.8")

    def test_verified_tls_requires_explicit_local_root_certificate(self):
        with fixture_directory() as directory:
            certificate = directory / "internal-ca.pem"
            certificate.write_text("fixture path validation only; not a real certificate", encoding="utf-8")
            config = postgres_config(host="192.168.1.20", sslmode="verify-full", development=False, sslrootcert=str(certificate))
            self.assertEqual(config.sslrootcert, str(certificate))
            with self.assertRaises(DatabaseConfigurationError):
                postgres_config(sslmode="verify-full")
            with self.assertRaises(DatabaseConfigurationError):
                postgres_config(host="192.168.1.20", sslmode="verify-full", sslrootcert=r"\\192.168.1.20\ca\root.pem")
            with self.assertRaises(NetworkPolicyError):
                postgres_config(sslmode="verify-full", sslrootcert="https://external.example/ca.pem")

    def test_sqlite_rejects_network_shares_relative_names_and_memory_fallback(self):
        for path in (r"\\192.168.1.20\data\company.db", "company.db", ":memory:", "file:company.db?mode=memory"):
            with self.subTest(path=path), self.assertRaises((DatabaseConfigurationError, NetworkPolicyError)):
                DatabaseConfig("sqlite", path)


class PlaceholderCompilerTests(unittest.TestCase):
    def test_real_bindings_ignore_sql_quoting_comments_and_casts(self):
        sql = """SELECT :value::numeric, ':fake '' :also_fake', ":identifier", `:backtick`, [:bracket],
E'escaped\\\' :still_fake', $$:body$$, $tag$:other_body$tag$, :value, :second
/* :ignored /* :nested */ :ignored_too */ -- :comment
"""
        compiled, names = compile_sql(sql, postgresql=True)
        self.assertEqual(names, ("value", "second"))
        self.assertIn("%(value)s::numeric", compiled)
        self.assertIn("':fake '' :also_fake'", compiled)
        self.assertIn("$$:body$$", compiled)
        self.assertIn("/* :ignored /* :nested */ :ignored_too */", compiled)
        self.assertEqual(compile_sql(sql, postgresql=False), (sql, names))

    def test_percent_literals_are_escaped_without_changing_bindings(self):
        sql = "SELECT :value, '100% :literal', 7 % 3 /* 25% */"
        compiled, names = compile_sql(sql, postgresql=True)
        self.assertEqual(compiled, "SELECT %(value)s, '100%% :literal', 7 %% 3 /* 25%% */")
        self.assertEqual(names, ("value",))

    def test_unterminated_sql_lexical_contexts_fail_closed(self):
        for sql in ("SELECT 'missing", 'SELECT "missing', "SELECT [missing", "SELECT `missing", "/* missing", "SELECT $tag$missing", "", "SELECT \x00"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                compile_sql(sql, postgresql=True)

    def test_postgresql_driver_receives_values_separately_and_ignores_unused_keys(self):
        driver = ScriptedDriver()
        database = scripted_database(driver)
        with database.connect() as connection:
            connection.execute("SELECT :value, '100% literal', :value", {"value": "'; DROP TABLE users; --", "unused": "ignored"})
        raw = driver.connections[0]
        self.assertEqual(raw.events[0], ("SELECT %(value)s, '100%% literal', %(value)s", {"value": "'; DROP TABLE users; --"}))
        self.assertTrue(raw.closed)
        self.assertTrue(all(cursor.closed for cursor in raw.cursors))


class SQLiteLifecycleTests(unittest.TestCase):
    def test_unpatched_sqlite_engine_fails_before_opening_a_data_file(self):
        with fixture_directory() as directory:
            missing = directory / "must-not-be-created.db"
            database = Database(DatabaseConfig("sqlite", str(missing)))
            for version in ((3, 50, 6), (3, 51, 2), (3, 44, 5)):
                with self.subTest(version=version), patch("pak_maintenance.database.sqlite3.sqlite_version_info", version):
                    with self.assertRaises(DatabaseDependencyError):
                        with database.connect():
                            self.fail("An unsupported SQLite engine opened a database.")
            self.assertFalse(missing.exists())

    def test_constructor_and_missing_connection_never_create_or_seed_data(self):
        with fixture_directory() as directory:
            path = directory / "missing.db"
            database = Database({"provider": "sqlite", "database": str(path)})
            self.assertFalse(path.exists())
            with self.assertRaises(sqlite3.OperationalError):
                with database.connect():
                    self.fail("A missing database must never connect.")
            self.assertFalse(path.exists())

    def test_connect_closes_raw_connection_after_success_and_failure(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path)
            database = Database(DatabaseConfig("sqlite", str(path)))
            for fail in (False, True):
                try:
                    with database.connect() as connection:
                        raw = connection.raw
                        if fail:
                            raise LookupError("fixture error")
                        self.assertEqual(connection.scalar("SELECT COUNT(*) FROM entries"), 0)
                except LookupError:
                    pass
                with self.assertRaises(sqlite3.ProgrammingError):
                    raw.execute("SELECT 1")

    def test_commit_rollback_and_no_mutation_in_read_transaction(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path)
            database = Database(DatabaseConfig("sqlite", str(path)))
            result = database.run_transaction(lambda c: c.execute("INSERT INTO entries(id,value) VALUES(:id,:value)", {"id": 1, "value": "committed"}))
            self.assertEqual(result, 1)
            with self.assertRaises(LookupError):
                with database.transaction() as connection:
                    connection.execute("INSERT INTO entries VALUES(2,'rollback')")
                    raise LookupError("must roll back")
            with database.transaction(write=False) as connection:
                self.assertEqual(connection.query("SELECT id,value FROM entries"), [{"id": 1, "value": "committed"}])
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("DELETE FROM entries")
            with self.assertRaises(TypeError):
                with database.transaction(write="true"):
                    self.fail("Nonboolean write flag accepted.")

    def test_read_snapshot_is_repeatable_during_concurrent_committed_write(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path)
            database = Database(DatabaseConfig("sqlite", str(path)))
            with database.connect() as connection:
                self.assertEqual(connection.scalar("PRAGMA journal_mode=WAL"), "wal")
            with database.transaction(write=False) as reader:
                self.assertEqual(reader.scalar("SELECT COUNT(*) FROM entries"), 0)
                database.run_transaction(lambda writer: writer.execute("INSERT INTO entries VALUES(1,'concurrent')"))
                self.assertEqual(reader.scalar("SELECT COUNT(*) FROM entries"), 0)
            with database.transaction(write=False) as reader:
                self.assertEqual(reader.scalar("SELECT COUNT(*) FROM entries"), 1)

    def test_write_transaction_reserves_lock_before_first_write_and_does_not_retry_sqlite(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path)
            database = Database(DatabaseConfig("sqlite", str(path), busy_timeout_ms=20))
            with database.transaction(write=True):
                with patch("pak_maintenance.database.time.sleep", side_effect=AssertionError("SQLite locks must not replay")):
                    with self.assertRaises(sqlite3.OperationalError):
                        database.run_transaction(lambda connection: self.fail("BEGIN must fail before operation"))

    def test_foreign_keys_are_enforced(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path, "CREATE TABLE parents(id INTEGER PRIMARY KEY); CREATE TABLE children(id INTEGER PRIMARY KEY,parent INTEGER REFERENCES parents(id));")
            database = Database(DatabaseConfig("sqlite", str(path)))
            with self.assertRaises(sqlite3.IntegrityError):
                database.run_transaction(lambda c: c.execute("INSERT INTO children VALUES(1,999)"))
            with database.connect() as connection:
                self.assertEqual(connection.scalar("SELECT COUNT(*) FROM children"), 0)

    def test_bound_injection_and_exact_decimal_text_storage(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path)
            database = Database(DatabaseConfig("sqlite", str(path)))
            injection = "'; DROP TABLE entries; --"
            exact = Decimal("12345678901234567890.123456")
            with database.transaction() as connection:
                connection.execute("INSERT INTO entries VALUES(:id,:value)", {"id": 1, "value": injection, "unused": "ignored"})
                connection.execute("INSERT INTO entries VALUES(:id,:value)", {"id": 2, "value": exact})
                self.assertEqual(connection.scalar("SELECT value FROM entries WHERE id=:id", {"id": 1}), injection)
                self.assertEqual(connection.scalar("SELECT value FROM entries WHERE id=:id", {"id": 2}), str(exact))
                with self.assertRaises(ValueError):
                    connection.execute("INSERT INTO entries VALUES(:id,:missing)", {"id": 3})
            with database.connect() as connection:
                self.assertEqual(connection.scalar("SELECT COUNT(*) FROM entries"), 2)

    def test_executemany_generator_is_atomic_with_transaction(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path)
            database = Database(DatabaseConfig("sqlite", str(path)))
            def broken_rows():
                yield {"id": 1, "value": "must roll back"}
                yield {"id": 2}
            with self.assertRaises(ValueError):
                database.run_transaction(lambda c: c.executemany("INSERT INTO entries VALUES(:id,:value)", broken_rows()))
            with database.connect() as connection:
                self.assertEqual(connection.scalar("SELECT COUNT(*) FROM entries"), 0)
            rows = ({"id": number, "value": Decimal("0.123456")} for number in range(1000))
            self.assertEqual(database.run_transaction(lambda c: c.executemany("INSERT INTO entries VALUES(:id,:value)", rows)), 1000)
            with database.connect() as connection:
                self.assertEqual(connection.scalar("SELECT COUNT(*) FROM entries"), 1000)
                self.assertEqual(connection.one("SELECT value FROM entries WHERE id=999"), {"value": "0.123456"})

    def test_one_and_scalar_refuse_ambiguous_results(self):
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path, "CREATE TABLE entries(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO entries VALUES(1,'one'),(2,'two');")
            database = Database(DatabaseConfig("sqlite", str(path)))
            with database.connect() as connection:
                self.assertIsNone(connection.one("SELECT * FROM entries WHERE id=999"))
                self.assertIsNone(connection.scalar("SELECT value FROM entries WHERE id=999"))
                with self.assertRaises(ValueError):
                    connection.one("SELECT * FROM entries")
                with self.assertRaises(ValueError):
                    connection.scalar("SELECT id,value FROM entries WHERE id=1")


class PostgreSQLBoundaryTests(unittest.TestCase):
    def test_server_older_than_17_or_unverifiable_is_rejected_and_closed(self):
        for version in (160011, 150015, 0, None, "180006", True):
            driver = ScriptedDriver([{"server_version": version}])
            with self.subTest(version=version), self.assertRaises(DatabaseDependencyError) as raised:
                with scripted_database(driver).connect():
                    self.fail("An unsupported PostgreSQL server was accepted")
            self.assertIn("17 or newer", str(raised.exception))
            self.assertTrue(driver.connections[0].closed)
            self.assertEqual(driver.connections[0].events, [])

    def test_supported_17_and_newer_servers_are_accepted_without_schema_mutation(self):
        for version in (170000, 170006, 180006, 190000):
            driver = ScriptedDriver([{"server_version": version}])
            with self.subTest(version=version), scripted_database(driver).connect():
                pass
            self.assertTrue(driver.connections[0].closed)
            self.assertEqual(driver.connections[0].events, [])

    def test_missing_driver_is_explicit_and_never_falls_back(self):
        database = Database(postgres_config())
        with patch("pak_maintenance.database.importlib.import_module", side_effect=ModuleNotFoundError("fixture missing psycopg")):
            with self.assertRaises(DatabaseDependencyError) as raised:
                with database.connect():
                    self.fail("A provider fallback occurred.")
        self.assertIn("offline", str(raised.exception))
        self.assertIn("no SQLite fallback", str(raised.exception))

    def test_inherited_libpq_destinations_are_refused_before_connect(self):
        for key in ("PGHOSTADDR", "PGSERVICE", "PGPASSFILE", "PGSSLROOTCERT", "PGOPTIONS", "PGFUTURESETTING"):
            driver = ScriptedDriver()
            database = scripted_database(driver)
            with self.subTest(key=key), patch.dict(os.environ, {key: "untrusted"}):
                with self.assertRaises(DatabaseConfigurationError):
                    with database.connect():
                        self.fail("Inherited libpq settings reached the connection.")
            self.assertEqual(driver.connections, [])

    def test_numeric_hostaddr_and_fixed_options_cannot_drift(self):
        driver = ScriptedDriver()
        database = scripted_database(driver)
        with database.transaction(write=False):
            pass
        parameters = driver.parameters[0]
        self.assertEqual(parameters["hostaddr"], "127.0.0.1")
        self.assertEqual(parameters["host"], parameters["hostaddr"])
        self.assertEqual(parameters["client_encoding"], "UTF8")
        self.assertEqual(parameters["gssencmode"], "disable")
        self.assertIn("standard_conforming_strings=on", parameters["options"])
        self.assertEqual(driver.connections[0].events[0][0], "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertEqual(driver.connections[0].events[-1][0], "COMMIT")
        self.assertTrue(driver.connections[0].closed)

    def test_confirmed_abort_retries_only_after_rollback_and_close(self):
        for state in ("40001", "40P01"):
            driver = ScriptedDriver()
            database = scripted_database(driver)
            attempts = []
            def operation(connection):
                attempts.append(connection)
                if len(attempts) == 1:
                    raise ScriptedPgError(state)
                return {"result": "built before commit"}
            def after_disposal(delay):
                self.assertTrue(driver.connections[-1].closed)
                self.assertEqual(driver.connections[-1].events[-1][0], "ROLLBACK")
            with self.subTest(state=state), patch("pak_maintenance.database.time.sleep", side_effect=after_disposal):
                self.assertEqual(database.run_transaction(operation), {"result": "built before commit"})
            self.assertEqual(len(attempts), 2)
            self.assertTrue(all(raw.closed for raw in driver.connections))
            self.assertTrue(all(cursor.closed for raw in driver.connections for cursor in raw.cursors))

    def test_confirmed_commit_abort_may_retry_but_successful_commit_has_no_more_queries(self):
        driver = ScriptedDriver([{"commit_error": ScriptedPgError("40001")}, {}])
        database = scripted_database(driver)
        results = []
        def operation(connection):
            result = {"attempt": len(results) + 1}
            results.append(result)
            return result
        with patch("pak_maintenance.database.time.sleep"):
            self.assertEqual(database.run_transaction(operation), {"attempt": 2})
        self.assertEqual([event[0] for event in driver.connections[0].events][-2:], ["COMMIT", "ROLLBACK"])
        self.assertEqual(driver.connections[1].events[-1][0], "COMMIT")
        self.assertTrue(all(raw.closed for raw in driver.connections))

    def test_unknown_commit_and_non_driver_errors_never_retry(self):
        for error in (ScriptedPgError("08006"), ScriptedPgError("57P01"), ScriptedPgError(), ValueError("application conflict 40001")):
            driver = ScriptedDriver([{"commit_error": error}])
            database = scripted_database(driver)
            with self.subTest(error=type(error).__name__, state=getattr(error, "sqlstate", None)), patch("pak_maintenance.database.time.sleep", side_effect=AssertionError("Unknown outcomes must not replay")):
                with self.assertRaises(type(error)):
                    database.run_transaction(lambda c: "result")
            self.assertEqual(len(driver.connections), 1)
            self.assertTrue(driver.connections[0].closed)

    def test_cleanup_failures_close_and_never_replay_confirmed_abort(self):
        for script in (
            {"commit_error": ScriptedPgError("40001"), "rollback_error": ScriptedPgError("08006")},
            {"commit_error": ScriptedPgError("40001"), "close_error": RuntimeError("close failed")},
        ):
            driver = ScriptedDriver([script])
            database = scripted_database(driver)
            with self.subTest(failure=tuple(script)), patch("pak_maintenance.database.time.sleep", side_effect=AssertionError("Cleanup failures must not replay")):
                with self.assertRaises(DatabaseCleanupError):
                    database.run_transaction(lambda c: "result")
            self.assertEqual(len(driver.connections), 1)
            self.assertTrue(driver.connections[0].closed)

    def test_retry_budget_is_bounded_and_all_failed_attempts_are_closed(self):
        driver = ScriptedDriver([{"commit_error": ScriptedPgError("40001")} for _ in range(3)])
        database = scripted_database(driver)
        with patch("pak_maintenance.database.time.sleep") as sleeper:
            with self.assertRaises(ScriptedPgError):
                database.run_transaction(lambda c: "result", max_attempts=3)
        self.assertEqual(len(driver.connections), 3)
        self.assertEqual(sleeper.call_count, 2)
        self.assertTrue(all(raw.closed for raw in driver.connections))
        for attempts in (0, 7, True, "3"):
            with self.subTest(attempts=attempts), self.assertRaises(ValueError):
                database.run_transaction(lambda c: "result", max_attempts=attempts)


class SchemaInspectionTests(unittest.TestCase):
    def test_exported_data_free_schema10_is_verified_without_mutation(self):
        schema = (Path(__file__).resolve().parents[1] / "schema" / "sqlite-v10.sql").read_text(encoding="utf-8")
        schema += "\n" + "\n".join(path.read_text(encoding="utf-8") for path in sorted((Path(__file__).resolve().parents[1] / "schema").glob("sqlite-*-guards.sql")))
        with fixture_directory() as directory:
            path = directory / "fixture.db"
            create_fixture(path, schema)
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            database = Database(DatabaseConfig("sqlite", str(path)))
            database.verify_schema()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), fingerprint)
            with database.connect() as connection:
                self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users"), 0)
                self.assertEqual(connection.scalar("SELECT COUNT(*) FROM orders"), 0)

    def test_missing_score_evidence_and_returned_stock_columns_fail_schema_gate(self):
        schema = (Path(__file__).resolve().parents[1] / "schema" / "sqlite-v10.sql").read_text(encoding="utf-8")
        schema += "\n" + "\n".join(path.read_text(encoding="utf-8") for path in sorted((Path(__file__).resolve().parents[1] / "schema").glob("sqlite-*-guards.sql")))
        for mutation in ("DROP TABLE completed_scores", "ALTER TABLE parts ADD COLUMN stock INTEGER"):
            with self.subTest(mutation=mutation), fixture_directory() as directory:
                path = directory / "fixture.db"
                create_fixture(path, schema)
                database = Database(DatabaseConfig("sqlite", str(path)))
                database.verify_schema()
                with database.transaction() as connection:
                    connection.execute(mutation)
                with self.assertRaises(SchemaMismatchError):
                    database.verify_schema()

    def test_missing_or_old_schema_is_refused_without_changes(self):
        for schema in (
            "CREATE TABLE unrelated(id TEXT);",
            "CREATE TABLE schema_version(version INTEGER NOT NULL); INSERT INTO schema_version VALUES(5);",
            "CREATE TABLE schema_version(version INTEGER NOT NULL); INSERT INTO schema_version VALUES(6),(6);",
            "CREATE TABLE schema_version(version INTEGER NOT NULL); INSERT INTO schema_version VALUES(6);",
        ):
            with self.subTest(schema=schema), fixture_directory() as directory:
                path = directory / "fixture.db"
                create_fixture(path, schema)
                fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
                database = Database(DatabaseConfig("sqlite", str(path)))
                with self.assertRaises(SchemaMismatchError):
                    database.verify_schema()
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), fingerprint)


if __name__ == "__main__":
    unittest.main()
