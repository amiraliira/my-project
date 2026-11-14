"""Opt-in real PostgreSQL acceptance against an explicitly disposable LOCAL DB.

Set PAK_POSTGRES_TEST_CONFIG to a local JSON file containing
allowDestructiveTesting=true and owner/runtime DatabaseConfig dictionaries.
Both must identify the same pak_qa_* database on numeric loopback with TLS
verify-full. No server or firewall is started, configured or contacted elsewhere.
The public schema must initially contain no relations or functions. Only the
objects created by this test class are initialized, truncated and removed.
Without that explicit configuration, the entire class is skipped.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import importlib
import ipaddress
import hashlib
import os
from pathlib import Path
import ssl
import subprocess
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.common import ApiError, User, loads
from pak_maintenance.consumption_service import ConsumptionService, capture
from pak_maintenance.database import Database, DatabaseCleanupError, DatabaseConfig, SchemaMismatchError
from pak_maintenance.migration import MigrationError, TABLES, migrate_snapshot, snapshot_sha256
from pak_maintenance.network_policy import validate_file_path
from pak_maintenance.performance_service import PerformanceService
from pak_maintenance.pm_service import PmService
from pak_maintenance.pm_registry import PmRegistry
from pak_maintenance.pm_setup import PmSetupService
from pak_maintenance.reporting_service import ReportingService
from pak_maintenance.score_facts import participant_score, round_score
from pak_maintenance.work_service import WorkService


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ENV = "PAK_POSTGRES_TEST_CONFIG"
INSTANT = datetime(2026, 10, 11, 8, tzinfo=timezone.utc)
INITIALIZATION = (
    "postgresql-v10.sql", "postgresql-score-functions.sql",
    "postgresql-ledger-guards.sql", "postgresql-pm-guards.sql",
    "postgresql-evidence-guards.sql", "postgresql-archive-guards.sql",
)
# Public, unrelated synthetic CA fixture; its private key was discarded.
# Keeping this test-only trust root avoids adding a certificate-generation
# dependency to the portable application's integration test runtime.
UNTRUSTED_CA = b"""-----BEGIN CERTIFICATE-----
MIIC6DCCAdCgAwIBAgIBATANBgkqhkiG9w0BAQsFADAsMSowKAYDVQQDDCFTeW50
aGV0aWMgdW5yZWxhdGVkIFFBIHRydXN0IHJvb3QwIBcNMjAwMTAxMDAwMDAwWhgP
MjEyMDAxMDEwMDAwMDBaMCwxKjAoBgNVBAMMIVN5bnRoZXRpYyB1bnJlbGF0ZWQg
UUEgdHJ1c3Qgcm9vdDCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAJDi
rTL++eH5zIkRTPNmvbgrYX5YM/gnOyUmMvKgRrgoi5krsdfqre1ATVthsbFOS9LE
QFnLZleTd7uVeiu5WhaEQeX2um5mf+0aLco85BxhhqerpKlKLgMvVaWAlSLx9oh1
TCMhciz1jq+Gf3bRcdQLq6vQJgGCspjmFu+HII6R8zDPRK4Kl61GeF/lN4Rcmg//
AkTuOGIn3w50oqVWaRzqj7eHWbIr06LYAU40yrT2ViLQxV5Whqf1rXVE6bly8OKH
m3XDEtj1WE2Ve0GTb2M8gQkY7FxgnNl7KJifhBYYn4dMFQqluO4GGj+uFrABrQ9a
1IeKIprDUKjZHdWFE8MCAwEAAaMTMBEwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG
9w0BAQsFAAOCAQEAC5CXHvjXnVbBSRKO5FFsF7e+YpPytNKog5cfU0r5yw5h+zLo
qINiBMozavRH21oaq4v9OOhdjcTZKq+uipEAnLgTrFYhL4eJnxNudxvmVJViInlr
a8dKQNOqS5Vd1H8Mh5UuvSqQ5JGgi6l5JQfL9uiOzqBkTczFRMeIx8d6G6QT0Epq
gB9LeFf0YfUBlHFWRhiUgQOMtSjIGiHLe1bxvnVuKBv07T9Qtoc5c/YbcQRO4Drz
A+CtmQ8eJcSkSvCE/akEhzAKePHysZUZN13w2/Cg+ivcfkykSWfgCMu5mIqTbRQr
3GCpuzpxe91HBt2Szw9K5oM1RoQUe+nVP/Duhw==
-----END CERTIFICATE-----
"""


class PostgreSQLAcceptance(unittest.TestCase):
    """Real driver, TLS, SQL, transactions, permissions and concurrent writes."""

    @classmethod
    def setUpClass(cls):
        name = os.environ.get(CONFIG_ENV)
        if not name:
            raise unittest.SkipTest("Explicit isolated PostgreSQL QA target is not configured.")
        path = validate_file_path(name)
        if path.startswith("\\\\"):
            raise RuntimeError("PostgreSQL QA configuration must be a local file.")
        settings = loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict) or settings.get("allowDestructiveTesting") is not True:
            raise RuntimeError("An explicit disposable-test authorization flag is required.")
        configurations = []
        for key in ("owner", "runtime"):
            if not isinstance(settings.get(key), dict):
                raise RuntimeError("Separate owner and runtime QA configurations are required.")
            config = DatabaseConfig(**settings[key])
            if (config.provider != "postgresql" or not config.development
                    or not config.database.startswith("pak_qa_")
                    or len(config.database) < 9
                    or not ipaddress.ip_address(config.host).is_loopback
                    or config.sslmode != "verify-full"):
                raise RuntimeError("Only an explicitly disposable loopback PostgreSQL QA database with verified TLS is permitted.")
            configurations.append(config)
        owner_config, runtime_config = configurations
        if ((owner_config.host, owner_config.port, owner_config.database)
                != (runtime_config.host, runtime_config.port, runtime_config.database)
                or owner_config.user == runtime_config.user):
            raise RuntimeError("QA owner/runtime must use separate accounts on exactly the same disposable database.")
        cls.driver = importlib.import_module("psycopg")
        cls.owner = Database(owner_config)
        cls.db = Database(runtime_config)
        cls._initialized = False
        cls.addClassCleanup(cls._remove_created_schema)
        with cls.owner.transaction() as c:
            if c.scalar("SELECT current_database()") != owner_config.database:
                raise RuntimeError("The actual QA database does not match the explicitly configured target.")
            relations = c.scalar("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'")
            functions = c.scalar("SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'")
            if relations or functions:
                raise RuntimeError("Refusing PostgreSQL acceptance: public schema must be EMPTY before the first test.")
            for script in INITIALIZATION:
                # Initialization is an explicit test-owner action. The runtime
                # adapter and application never execute these DDL scripts.
                c.raw.execute((ROOT / "schema" / script).read_text(encoding="utf-8-sig"))
            runtime = cls.db.config.user  # DatabaseConfig validates simple identifiers.
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            c.execute(f'GRANT USAGE ON SCHEMA public TO "{runtime}"')
            c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{runtime}"')
            c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{runtime}"')
        cls._initialized = True
        cls.owner.verify_schema(runtime_role=False)
        cls.db.verify_schema()

    @classmethod
    def _remove_created_schema(cls):
        if not cls._initialized:
            return
        # This class established exclusive ownership of an empty pak_qa_*
        # schema before creating anything. Never run against a preexisting app.
        with cls.owner.transaction() as c:
            c.execute("DROP SCHEMA public CASCADE")
            c.execute("CREATE SCHEMA public")
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cls._initialized = False

    def clear_fixture(self):
        if not self._initialized:
            raise RuntimeError("QA fixture was not initialized by this test class.")
        with self.owner.transaction() as c:
            c.execute("TRUNCATE TABLE " + ",".join(spec.name for spec in TABLES) + ",participant_grading_policies,pm_responsibility_transfers")
            c.execute("INSERT INTO schema_version(version) VALUES(10)")

    def setUp(self):
        self.clear_fixture()
        self.users = {u.id: u for u in (
            User("m1", "001", "مکانیک اول", "technician", "مکانیک"),
            User("m2", "002", "مکانیک دوم", "technician", "مکانیک"),
            User("e1", "003", "برق اول", "technician", "برق"),
            User("sm", "004", "سرپرست مکانیک", "supervisor", "مکانیک"),
            User("sm2", "005", "سرپرست دوم", "supervisor", "مکانیک"),
            User("se", "006", "سرپرست برق", "supervisor", "برق"),
            User("head", "007", "رئیس", "head"),
            User("manager", "008", "مدیر", "manager"),
            User("admin", "009", "مدیر سامانه", "admin"),
            User("planner", "010", "کارشناس نت", "planner"),
        )}
        with self.db.transaction() as c:
            for user in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:spec,1,'',0)",
                          dict(id=user.id, code=user.code, name=user.name, role=user.role, spec=user.specialty))
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','A01','پمپ','سالن فرایند','خط یک','{}')")
            c.execute("INSERT INTO parts(id,code,name,specification,unit) VALUES('part','P001','بلبرینگ','','عدد')")
        self.work = WorkService(self.db)

    def body(self, people=("m1", "m2", "e1"), quantity="1.000001"):
        return {"type": "EM", "assetId": "asset", "title": "تعمیر پمپ", "description": "تعویض بلبرینگ",
                "workDate": "2026-10-11", "startTime": "08:00", "endTime": "09:00",
                "participants": [{"personId": person, "minutes": 60} for person in people],
                "parts": [{"partId": "part", "quantity": Decimal(quantity)}], "checklist": [], "downtimeMinutes": 15}

    def submitted(self, people=("m1", "m2", "e1")):
        row = self.work.save(self.users["m1"], self.body(people))
        return self.work.submit(self.users["m1"], row["id"], row["version"])

    def grade(self, row, supervisor, values):
        return self.work.decision(self.users[supervisor], row["id"], {
            "version": row["version"], "decision": "approve",
            "grades": [{"personId": person, "coefficient": Decimal(str(score))} for person, score in values]})

    def ready_head(self):
        row = self.submitted()
        row = self.grade(row, "sm", [("m1", 0), ("m2", "1.25")])
        return self.grade(row, "se", [("e1", "2.5")])

    @staticmethod
    def head_body(row, coefficient):
        return {"version": row["version"], "decision": "approve", "participantScores": [
            {"personId": person["personId"], "coefficient": coefficient} for person in row["participants"]]}

    def test_verified_tls_and_restricted_runtime_account(self):
        self.db.verify_schema()
        self.owner.verify_schema(runtime_role=False)
        with self.assertRaises(SchemaMismatchError):
            self.owner.verify_schema()
        with self.db.transaction(write=False) as c:
            ssl = c.one("SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
        self.assertTrue(ssl["ssl"])
        self.assertIn(ssl["version"], ("TLSv1.2", "TLSv1.3"))
        for statement in ("CREATE TABLE forbidden_runtime_ddl(id integer)",
                          "TRUNCATE TABLE orders", "ALTER TABLE orders DISABLE TRIGGER ALL"):
            with self.subTest(statement=statement):
                with self.assertRaises(self.driver.Error) as failure:
                    with self.db.transaction() as c:
                        c.execute(statement)
                self.assertEqual(failure.exception.sqlstate, "42501")

    def test_untrusted_certificate_authority_is_rejected(self):
        from test_migration import fixture_directory
        with fixture_directory() as directory:
            path = directory / "untrusted-ca.pem"
            path.write_bytes(UNTRUSTED_CA)
            ssl.create_default_context(cafile=str(path))  # Reject malformed fixture PEM locally.
            wrong = Database(replace(self.db.config, sslrootcert=str(path)))
            with self.assertRaises(self.driver.OperationalError):
                with wrong.connect():
                    self.fail("An unrelated CA must never authenticate the QA database certificate.")

    def test_runtime_privilege_gate_requires_each_crud_and_rejects_trigger_or_truncate_without_repair(self):
        runtime = self.db.config.user
        for privilege, permitted in (("SELECT", True), ("INSERT", True), ("UPDATE", True), ("DELETE", True),
                                      ("TRUNCATE", False), ("TRIGGER", False)):
            with self.subTest(privilege=privilege):
                change, restore = ("REVOKE", "GRANT") if permitted else ("GRANT", "REVOKE")
                change_target, restore_target = ("FROM", "TO") if permitted else ("TO", "FROM")
                with self.owner.transaction() as c:
                    c.execute(f'{change} {privilege} ON public.assets {change_target} "{runtime}"')
                try:
                    with self.assertRaises(SchemaMismatchError):
                        self.db.verify_schema()
                    # Verification must leave the deliberately modified ACL
                    # untouched. Only this explicit synthetic owner cleanup
                    # restores the original policy below.
                    with self.owner.transaction(write=False) as c:
                        actual = c.scalar("SELECT has_table_privilege(:role,'public.assets',:privilege)",
                                          {"role": runtime, "privilege": privilege})
                        self.assertIs(actual, not permitted)
                        self.assertEqual(c.scalar("SELECT COUNT(*) FROM assets"), 1)
                finally:
                    with self.owner.transaction() as c:
                        c.execute(f'{restore} {privilege} ON public.assets {restore_target} "{runtime}"')
                self.db.verify_schema()

    def test_sql_decimal_score_functions_match_python_exact_values(self):
        vectors = ((1, "1.005", "2.5"), (7, "1.2345678901234567890123456789", "2.9999999999999999999999999999"),
                   (59, "0", "3"), (61, "0.125", "2.875"), (1440, "3", "3"))
        with self.db.transaction(write=False) as c:
            for minutes, supervisor, head in vectors:
                args = dict(minutes=minutes, supervisor=Decimal(supervisor), head=Decimal(head))
                actual = c.scalar("SELECT pak_participant_score(:minutes,:supervisor,:head)", args)
                self.assertEqual(actual, participant_score(minutes, Decimal(supervisor), Decimal(head)))
            for value in ("0.005", "0.015", "1.225", "1.235", "-1.225", "-1.235"):
                self.assertEqual(c.scalar("SELECT pak_round_even(:value,2)", {"value": Decimal(value)}), round_score(Decimal(value)))

    def test_joint_work_grades_consumption_reporting_and_scope(self):
        row = self.ready_head()
        final = self.work.decision(self.users["head"], row["id"], self.head_body(row, Decimal(2)))
        self.assertEqual(final["status"], "completed")
        self.assertEqual({p["personId"]: p["finalScore"] for p in final["participants"]},
                         {"m1": Decimal(0), "m2": Decimal("2.50"), "e1": Decimal("5.00")})
        self.assertEqual(ReportingService(self.db).dashboard(self.users["head"])["score"], Decimal("7.50"))
        for actor in ("sm", "m1", "manager", "admin"):
            own = PerformanceService(self.db).report(self.users[actor], period="all")
            self.assertEqual(own["scope"], "own")
            self.assertEqual({p["personId"] for p in own["people"]}, {actor})
            with self.subTest(actor=actor), self.assertRaises(ApiError) as denied:
                PerformanceService(self.db).report(self.users[actor], period="all", scope="all")
            self.assertEqual(denied.exception.status, 403)
        history = ConsumptionService(self.db).history(self.users["head"])
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["items"][0]["quantity"], Decimal("1.000001"))
        with self.db.transaction() as c:
            self.assertEqual(capture(c, row["id"]), 0)
            self.assertEqual(c.scalar("SELECT report_count FROM part_consumption_totals"), 1)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), 1_000_001)

    def test_individual_chief_coefficients_exact_history_and_personnel_privacy(self):
        row = self.submitted(("m1", "m2"))
        row = self.grade(row, "sm", [("m1", "1.25"), ("m2", "2.5")])
        final = self.work.decision(self.users["head"], row["id"], {
            "version": row["version"], "decision": "approve", "participantScores": [
                dict(personId="m1", coefficient="0"), dict(personId="m2", coefficient="2.125")],
        })
        participants = {person["personId"]: person for person in final["participants"]}
        self.assertEqual(participants["m1"]["finalScore"], Decimal(0))
        self.assertEqual(participants["m2"]["finalScore"], Decimal("5.31"))
        with self.db.transaction(write=False) as c:
            exact = c.query("SELECT person_id,coefficient FROM participant_head_scores ORDER BY person_id")
            self.assertEqual(exact, [dict(person_id="m1", coefficient="0"), dict(person_id="m2", coefficient="2.125")])
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores WHERE head_coefficient_exact IS NOT NULL"), 2)
        for actor in ("planner", "manager", "admin", "m1", "sm"):
            value = self.work.get(self.users[actor], row["id"])
            for person in value["participants"]:
                self.assertIsNone(person["headCoefficient"])
                self.assertIsNone(person["finalScore"])
            self.assertEqual(value["participantHeadScores"], [])
            own = PerformanceService(self.db).report(self.users[actor], period="all")
            self.assertEqual(own["scope"], "own")
            self.assertTrue(all(p["personId"] == actor for p in own["details"]))
            with self.assertRaises(ApiError) as denied:
                PerformanceService(self.db).report(self.users[actor], period="all", scope="all")
            self.assertEqual(denied.exception.status, 403)
        for statement in ("UPDATE participant_head_scores SET coefficient='3'", "DELETE FROM participant_head_scores"):
            with self.assertRaises(self.driver.Error) as denied:
                with self.db.transaction() as c:
                    c.execute(statement)
            self.assertEqual(denied.exception.sqlstate, "23514")

    def test_pm_proposal_and_concurrent_chief_decision_publish_exactly_once(self):
        pm = PmService(self.db, self.work)
        body = dict(requestKey=uuid4().hex, title="برگه پیشنهادی پمپ", hall="سالن فرایند", specialty="مکانیک",
                    period="ماهانه", activities=["کنترل نشتی"], reason="طراحی برگه آزمایشی")
        proposal = pm.request_change(self.users["planner"], "create-template", None, body)["changeRequest"]
        replay = pm.request_change(self.users["planner"], "create-template", None, body)
        self.assertEqual(proposal["id"], replay["changeRequest"]["id"])
        self.assertTrue(replay["replayed"])
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_templates"), 0)
        with self.assertRaises(ApiError) as denied:
            pm.decide_change(self.users["planner"], proposal["id"], dict(version=1, decision="approve", reason="تلاش غیرمجاز"))
        self.assertEqual(denied.exception.status, 403)
        gate = threading.Barrier(2)
        def approve():
            gate.wait(timeout=10)
            return PmService(self.db).decide_change(self.users["head"], proposal["id"],
                dict(version=1, decision="approve", reason="تأیید برگه توسط رئیس"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = [result.result(timeout=30) for result in (pool.submit(approve), pool.submit(approve))]
        self.assertTrue(all(value["status"] == "approved" for value in values))
        self.assertEqual(sum(bool(value.get("replayed")) for value in values), 1)
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_templates"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_template_revisions"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='approve-pm-change'"), 1)

    def test_native_application_backup_restores_all_rows_hashes_constraints_and_guards(self):
        from pg_test_support import fresh_database, native_tool_paths, assert_database_constraints
        from test_migration import fixture_directory
        from pak_maintenance.backup import BackupService
        from pak_maintenance.common import dumps
        from pak_maintenance.postgres_backup import _environment, _passfile, _conninfo
        row = self.ready_head()
        self.work.decision(self.users["head"], row["id"], self.head_body(row, "2.125"))
        pm = PmService(self.db, self.work)
        proposal = pm.request_change(self.users["planner"], "create-template", None,
            dict(requestKey=uuid4().hex, title="آزمون پشتیبان PM", hall="سالن فرایند", specialty="مکانیک",
                 period="ماهانه", activities=["کنترل نشتی"], reason="آزمون بازگردانی برگه"))["changeRequest"]
        pm.decide_change(self.users["head"], proposal["id"], dict(version=1, decision="approve", reason="تأیید آزمایشی"))
        def digest(database):
            hashes = {}
            with database.transaction(write=False) as c:
                for spec in TABLES:
                    rows = c.query("SELECT " + ",".join(spec.columns) + " FROM " + spec.name + " ORDER BY " + ",".join(spec.keys or spec.columns))
                    hashes[spec.name] = (len(rows), hashlib.sha256(dumps(rows).encode("utf-8")).hexdigest())
                for table in ("participant_grading_policies", "pm_responsibility_transfers"):
                    rows = c.query("SELECT to_jsonb(t)::text AS row FROM " + table + " t ORDER BY to_jsonb(t)::text")
                    hashes[table] = (len(rows), hashlib.sha256(dumps(rows).encode("utf-8")).hexdigest())
            return hashes
        before = digest(self.db)
        target_config, target = fresh_database(self, prefix="restore", initialize=False)
        tools = native_tool_paths()
        with fixture_directory() as directory:
            result = BackupService(self.db, directory, dump_executable=tools["postgresql_dump_executable"],
                                   restore_executable=tools["postgresql_restore_executable"]).create_snapshot()
            self.assertTrue(result.path.read_bytes().startswith(b"PGDMP"))
            self.assertEqual(result.sha256, hashlib.sha256(result.path.read_bytes()).hexdigest())
            executable = Path(tools["postgresql_restore_executable"])
            environment = _environment(executable)
            with _passfile(directory, target.config) as credentials:
                environment["PGPASSFILE"] = str(credentials)
                restored = subprocess.run([str(executable), "--no-password", "--exit-on-error", "--single-transaction",
                    "--no-owner", "--no-privileges", "--dbname=" + _conninfo(target.config), str(result.path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    timeout=60, env=environment)
            self.assertEqual(restored.returncode, 0, restored.stderr[-1500:])
            self.assertEqual(before, digest(target))
            self.assertEqual(before, digest(self.db))
            target.verify_schema(runtime_role=False)
            assert_database_constraints(self, target)
            with self.assertRaises(self.driver.Error) as denied:
                with target.transaction() as c:
                    c.execute("UPDATE participant_head_scores SET coefficient='3'")
            self.assertEqual(denied.exception.sqlstate, "23514")
            self.assertFalse(list(directory.glob(".pak-pgpass-*")))

    def test_actual_pg_evidence_failure_rolls_back_entire_final_approval(self):
        row = self.ready_head()
        with self.owner.transaction() as c:
            c.raw.execute("CREATE FUNCTION pak_qa_fail_score() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic score failure' USING ERRCODE='23514'; END; $$")
            c.execute("CREATE TRIGGER pak_qa_fail_score BEFORE INSERT ON completed_scores FOR EACH ROW EXECUTE FUNCTION pak_qa_fail_score()")
        try:
            with self.assertRaises(self.driver.Error):
                self.work.decision(self.users["head"], row["id"], self.head_body(row, 3))
            current = self.work.get(self.users["head"], row["id"])
            self.assertEqual((current["version"], current["status"]), (row["version"], "head"))
            with self.db.transaction(write=False) as c:
                for table in ("part_consumptions", "part_consumption_totals", "completed_scores"):
                    self.assertEqual(c.scalar("SELECT COUNT(*) FROM " + table), 0)
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)
        finally:
            with self.owner.transaction() as c:
                c.execute("DROP TRIGGER pak_qa_fail_score ON completed_scores")
                c.execute("DROP FUNCTION pak_qa_fail_score()")

    def test_aggregate_overflow_is_atomic_and_immutable_history_is_guarded(self):
        row = self.ready_head()
        with self.db.transaction() as c:
            c.execute("INSERT INTO part_consumption_totals VALUES('part',:maximum,1)", {"maximum": (1 << 63) - 1})
        with self.assertRaises(ApiError) as error:
            self.work.decision(self.users["head"], row["id"], self.head_body(row, 3))
        self.assertEqual(error.exception.status, 409)
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), (1 << 63) - 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM part_consumptions"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 0)
            self.assertEqual(c.scalar("SELECT status FROM orders"), "head")
        for statement in ("UPDATE approvals SET reason='rewritten'", "DELETE FROM participant_scores",
                          "UPDATE audit SET details='{}'", "UPDATE part_consumption_totals SET quantity_units=1"):
            with self.subTest(statement=statement), self.assertRaises(self.driver.Error) as rejected:
                with self.db.transaction() as c:
                    c.execute(statement)
            self.assertEqual(rejected.exception.sqlstate, "23514")

    def test_concurrent_duplicate_create_replays_exactly_once(self):
        gate = threading.Barrier(2)
        body = self.body()
        def create():
            gate.wait(timeout=10)
            return WorkService(self.db).save(self.users["m1"], body, request_key="real-pg-duplicate")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(results[0]["id"], results[1]["id"])
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM request_claims"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='create'"), 1)

    def test_ten_simultaneous_independent_technician_creates_preserve_every_report(self):
        actors = [User("writer" + str(i), "W" + str(i), "تکنسین آزمایشی " + str(i), "technician", "مکانیک") for i in range(10)]
        with self.db.transaction() as c:
            for actor in actors:
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,'technician','مکانیک',1,'',0)",
                          dict(id=actor.id, code=actor.code, name=actor.name))
        gate = threading.Barrier(len(actors))
        def create(actor):
            body = self.body((actor.id,))
            body["description"] = "گزارش مستقل " + actor.id
            gate.wait(timeout=15)
            return WorkService(self.db).save(actor, body, request_key="parallel-" + actor.id)
        with ThreadPoolExecutor(max_workers=len(actors)) as pool:
            results = [future.result(timeout=40) for future in [pool.submit(create, actor) for actor in actors]]
        self.assertEqual(len({row["id"] for row in results}), len(actors))
        self.assertEqual(len({row["number"] for row in results}), len(actors))
        with self.db.transaction(write=False) as c:
            for table in ("orders", "request_claims", "participants", "order_parts"):
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM " + table), len(actors))
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='create'"), len(actors))
        # Retrying all ten successful requests must preserve their original IDs.
        for actor, previous in zip(actors, results):
            body = self.body((actor.id,))
            body["description"] = "گزارش مستقل " + actor.id
            replayed = self.work.save(actor, body, request_key="parallel-" + actor.id)
            self.assertEqual(replayed["id"], previous["id"])
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), len(actors))
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='create'"), len(actors))

    def test_concurrent_final_approval_has_one_winner_and_no_duplicate_facts(self):
        row = self.ready_head()
        gate = threading.Barrier(2)
        def approve(actor):
            gate.wait(timeout=10)
            try:
                return WorkService(self.db).decision(self.users[actor], row["id"], self.head_body(row, 2))
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=30) for future in (pool.submit(approve, "head"), pool.submit(approve, "head"))]
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn(409, results)
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 3)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM part_consumptions"), 1)
            self.assertEqual(c.scalar("SELECT report_count FROM part_consumption_totals"), 1)

    def test_real_serialization_abort_is_retried_after_transaction_disposal(self):
        gate = threading.Barrier(2)
        attempts = [0, 0]
        def update(index):
            def operation(c):
                attempts[index] += 1
                version = c.scalar("SELECT version FROM assets WHERE id='asset'")
                if attempts[index] == 1:
                    gate.wait(timeout=10)
                c.execute("UPDATE assets SET version=:version WHERE id='asset'", {"version": version + 1})
                return version + 1
            return self.db.run_transaction(operation)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=30) for future in (pool.submit(update, 0), pool.submit(update, 1))]
        self.assertEqual(sorted(results), [2, 3])
        self.assertEqual(sorted(attempts), [1, 2])
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT version FROM assets WHERE id='asset'"), 3)

    def test_connection_loss_before_commit_never_retries_or_leaves_rows(self):
        attempts = 0
        def operation(c):
            nonlocal attempts
            attempts += 1
            c.execute("INSERT INTO legacy_records VALUES('lost-connection','{}','2026-10-11')")
            # Drop the real client connection before COMMIT, without needing
            # server signaling privileges or altering another connection.
            c.raw.close()
            c.scalar("SELECT 1")
        with self.assertRaises(DatabaseCleanupError):
            self.owner.run_transaction(operation)
        self.assertEqual(attempts, 1)
        with self.owner.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM legacy_records"), 0)

    def test_real_snapshot_migration_preserves_hash_decimal_units_and_evidence(self):
        from test_migration import BODY, create_database, fixture_directory
        from pg_test_support import fresh_database, initialize_schema8
        config, target = fresh_database(self, prefix="snapshot", initialize=False)
        initialize_schema8(target, config.user)
        with fixture_directory() as directory:
            source = directory / "frozen-synthetic.db"
            create_database(source, populated=True)
            expected = snapshot_sha256(source)
            report = migrate_snapshot(source, target, expected_sha256=expected)
            self.assertEqual(report.target_provider, "postgresql")
            self.assertEqual(report.discarded_sessions, 1)
            # Every table in the current migration contract must be reconciled,
            # including additions such as the immutable PM submission archive.
            # Session invalidation and the version marker are deliberately
            # managed separately; every business table must be reconciled.
            expected_tables = {spec.name for spec in TABLES} - {'sessions', 'schema_version'}
            self.assertEqual({table.table for table in report.tables}, expected_tables)
            self.assertEqual(len(report.tables), len(expected_tables))
            self.assertIn("pm_submission_archive", {table.table for table in report.tables})
            self.assertEqual(snapshot_sha256(source), expected)
            with Database(config).transaction(write=False) as c:
                self.assertEqual(c.scalar("SELECT body FROM orders"), BODY)
                self.assertEqual(c.scalar("SELECT quantity_units FROM order_parts"), 123456)
                self.assertEqual(c.scalar("SELECT unit_cost FROM parts"), Decimal("12345.67"))
                self.assertEqual(c.scalar("SELECT coefficient FROM participant_scores"), Decimal("1.25"))
                self.assertEqual(c.scalar("SELECT session_epoch FROM users WHERE id='person'"), 5)
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM sessions"), 0)

    def test_real_migration_source_final_hash_failure_rolls_back_every_table(self):
        from test_migration import create_database, fixture_directory
        from pg_test_support import fresh_database, initialize_schema8
        config, target = fresh_database(self, prefix="snapshot", initialize=False)
        initialize_schema8(target, config.user)
        with fixture_directory() as directory:
            source = directory / "frozen-synthetic.db"
            create_database(source, populated=True)
            expected = snapshot_sha256(source)
            with patch("pak_maintenance.migration.snapshot_sha256", side_effect=[expected, "0" * 64]):
                with self.assertRaisesRegex(MigrationError, "changed"):
                    migrate_snapshot(source, target, expected_sha256=expected)
            with Database(config).transaction(write=False) as c:
                for spec in TABLES:
                    self.assertEqual(c.scalar(f"SELECT COUNT(*) FROM {spec.name}"), 1 if spec.name == "schema_version" else 0)

    def test_pm_concurrent_issuance_and_assignment_are_exactly_once(self):
        pm = PmService(self.db, self.work)
        self.work.pm_service = pm
        template = pm.create_template(self.users["head"], dict(requestKey=uuid4().hex, title="بازدید پمپ", hall="سالن فرایند", specialty="مکانیک", period="ماهانه", activities=["کنترل نشتی", "کنترل لرزش"]))
        pm.edit_plan(self.users["head"], template["planId"], dict(version=1, reason="تأیید برنامه آزمایشی", assetId="asset", supervisorId="sm", startDate="2026-09-23", slots=[dict(month=7, week=week) for week in range(1, 5)], active=True, mappingConfirmed=True), INSTANT)
        gate = threading.Barrier(2)
        def issue():
            gate.wait(timeout=10)
            return PmService(self.db).generate(INSTANT)
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = [future.result(timeout=30) for future in (pool.submit(issue), pool.submit(issue))]
        self.assertEqual(sum(counts), 3)
        self.assertEqual(pm.generate(INSTANT), 0)
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences"), 3)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), 3)
            order_id = c.query("SELECT order_id FROM pm_weekly_occurrences ORDER BY start_utc LIMIT 1")[0]["order_id"]
        assigned = pm.assign(self.users["sm"], order_id, dict(version=1, assigneeId="m1"))
        self.assertEqual(assigned["assigneeId"], "m1")
        self.assertEqual(pm.tasks(self.users["m1"], instant=INSTANT)["total"], 1)
        self.assertEqual(pm.tasks(self.users["m2"], instant=INSTANT)["total"], 0)
        with self.assertRaises(ApiError):
            pm.assign(self.users["sm"], order_id, dict(version=1, assigneeId="m2"))

    def test_pm_library_optional_filters_registry_setup_and_queue_views(self):
        from pak_maintenance import pm_form_info as info
        pm = PmService(self.db, self.work)
        self.work.pm_service = pm
        templates = []
        for specialty in ("مکانیک", "برق"):
            templates.append(pm.create_template(self.users["head"], dict(requestKey=uuid4().hex, title="بازدید پمپ " + specialty, hall="سالن فرایند", specialty=specialty, period="ماهانه", activities=["کنترل نشتی"])))
        for actor, total in (("head", 2), ("sm", 1), ("se", 1)):
            self.assertEqual(pm.templates(self.users[actor])["total"], total)
            self.assertEqual(pm.plans(self.users[actor])["total"], total)
        self.assertEqual(pm.templates(self.users["head"], q="بدون نتیجه")["total"], 0)
        registry = PmRegistry(self.db)
        self.assertEqual(registry.forms(self.users["head"], year=1405)["total"], 2)
        self.assertEqual(registry.forms(self.users["sm"], year=1405)["total"], 1)
        self.assertEqual(registry.get(self.users["head"], year=1405)["total"], 1)
        self.assertEqual(registry.unlinked(self.users["head"], year=1405)["total"], 2)
        setup = PmSetupService(self.db, pm)
        self.assertEqual(setup.get(self.users["head"])["summary"]["active"], 0)
        template = templates[0]
        pm.edit_plan(self.users["head"], template["planId"], dict(version=1, reason="تأیید برنامه آزمایشی", assetId="asset", supervisorId="sm", startDate="2026-09-23", slots=[dict(month=7, week=1)], active=True, mappingConfirmed=True), INSTANT)
        self.assertEqual(pm.generate(INSTANT), 1)
        for actor, total in (("head", 1), ("sm", 1), ("se", 0), ("m1", 0)):
            self.assertEqual(pm.tasks(self.users[actor], view="pending", instant=INSTANT)["total"], total)
            self.assertEqual(pm.tasks(self.users[actor], overdue=True, instant=INSTANT)["total"], total)
        cell = next(cell for cell in info.cells(template) if cell.get("activity"))
        pm.edit_template(self.users["head"], template["id"], dict(version=1, reason="اصلاح فعالیت آینده", changes=[dict(cellId=cell["id"], text="بررسی کامل نشتی")]))
        self.assertEqual(len(pm.history(self.users["head"], template["id"])["items"]), 2)
        with self.assertRaises(self.driver.Error) as rejected:
            with self.db.transaction() as c:
                c.execute("UPDATE pm_template_revisions SET reason='rewritten'")
        self.assertEqual(rejected.exception.sqlstate, "23514")

    def test_pm_setup_replay_and_full_technician_approval_execution(self):
        from pak_maintenance import pm_form_info as info
        pm = PmService(self.db, self.work)
        self.work.pm_service = pm
        template = pm.create_template(self.users["head"], dict(requestKey=uuid4().hex, title="بازدید پمپ", hall="سالن فرایند", specialty="مکانیک", period="ماهانه", activities=["کنترل نشتی"]))
        plan = pm.edit_plan(self.users["head"], template["planId"], dict(version=1, reason="تطبیق پیش از راه‌اندازی", assetId="asset", supervisorId="sm", startDate="2026-09-23", slots=[dict(month=7, week=1)], active=False, mappingConfirmed=True), INSTANT)
        setup = PmSetupService(self.db, pm)
        selection = dict(requestKey=str(uuid4()), reason="راه‌اندازی آزمایشی تأییدشده", startDate="2026-09-23", includePast=True, plans=[dict(id=plan["id"], version=plan["version"])])
        activated = setup.activate(self.users["head"], selection, INSTANT)
        self.assertEqual((activated["activated"], activated["generated"]), (1, 1))
        replay = setup.activate(self.users["head"], selection, INSTANT)
        self.assertEqual((replay["alreadyApplied"], replay["generated"]), (1, 0))
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_setup_requests"), 1)
            order_id = c.scalar("SELECT order_id FROM pm_weekly_occurrences")
            sheet = pm.issued_template(c, order_id)
        assigned = pm.assign(self.users["sm"], order_id, dict(version=1, assigneeId="m1"))
        body = deepcopy(assigned)
        body.update(description="بازدید طبق برگه انجام شد", workDate="2026-09-26", startTime="08:00", endTime="09:00", participants=[dict(personId="m1", minutes=60)], parts=[], checklist=[])
        answers = {cell["id"]: "سالم" for cell in info.cells(sheet) if cell.get("editable")}
        body["pmResults"] = answers
        saved = self.work.save(self.users["m1"], body, order_id)
        self.assertEqual(saved["pmResults"], answers)
        submitted = self.work.submit(self.users["m1"], order_id, saved["version"])
        graded = self.grade(submitted, "sm", [("m1", "1.25")])
        completed = self.work.decision(self.users["head"], order_id, self.head_body(graded, Decimal(2)))
        self.assertEqual((completed["status"], completed["score"]), ("completed", Decimal("2.50")))
        self.assertEqual(pm.tasks(self.users["m1"], view="completed", instant=INSTANT)["total"], 1)
        with self.db.transaction(write=False) as c:
            self.assertEqual(pm.issued_template(c, order_id), sheet)
            self.assertIsNotNone(c.scalar("SELECT executed_at FROM pm_weekly_occurrences"))
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 1)


if __name__ == "__main__":
    unittest.main()
