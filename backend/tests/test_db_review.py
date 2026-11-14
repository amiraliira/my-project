"""Independent evidence/guard review: real synthetic SQLite, no company data.

PostgreSQL tests here inject the driver boundary only. They do not execute SQL
on a PostgreSQL server or establish native TLS/SCRAM connections.
"""
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import patch
from contextlib import contextmanager
import hashlib
import os
import shutil
import sqlite3
import unittest
from uuid import uuid4

from pak_maintenance.common import User
from pak_maintenance.database import Database, DatabaseConfig, DatabaseDependencyError, DatabaseConfigurationError, SchemaMismatchError
from pak_maintenance.database import _REQUIRED_COLUMNS, _POSTGRESQL_GUARDS
from pak_maintenance.work_service import WorkService
from test_database import ScriptedDriver, postgres_config

PROJECT = Path(__file__).resolve().parents[1]


class EvidenceGuardsTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT / "tests" / ".db-review-fixtures"
        self.path = self.root / uuid4().hex / "synthetic.db"
        self.path.parent.mkdir(parents=True, mode=0o755)
        self.schema = (PROJECT / "schema" / "sqlite-v10.sql").read_text(encoding="utf-8")
        self.guards = "\n".join(p.read_text(encoding="utf-8") for p in sorted((PROJECT / "schema").glob("sqlite-*-guards.sql")))
        raw = sqlite3.connect(self.path)
        raw.executescript(self.schema + "\n" + self.guards)
        raw.close()
        self.db = Database(DatabaseConfig("sqlite", str(self.path), development=True))
        self.users = {u.id: u for u in (
            User("tech", role="technician", specialty="مکانیک"),
            User("tech2", role="technician", specialty="مکانیک"),
            User("sup", role="supervisor", specialty="مکانیک"),
            User("head", role="head"), User("other", role="head"),
        )}
        with self.db.transaction() as c:
            for u in self.users.values():
                c.execute("INSERT INTO users VALUES(:id,:id,:id,:role,:spec,1,'',0)", {"id":u.id,"role":u.role,"spec":u.specialty})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','A','پمپ','فرایند','یک','{}')")
            c.execute("INSERT INTO parts(id,code,name,specification,unit) VALUES('part','P','قطعه','','عدد')")
        self.work = WorkService(self.db)

    def tearDown(self):
        if self.path.parent.resolve().parent != self.root.resolve() or self.path.parent.is_symlink():
            raise RuntimeError("Unsafe synthetic fixture cleanup")
        shutil.rmtree(self.path.parent)
        if self.root.exists() and not any(self.root.iterdir()):
            self.root.rmdir()

    def report(self):
        return self.work.save(self.users["tech"], {
            "type":"EM", "assetId":"asset", "title":"بازدید پمپ", "description":"ثبت نتیجه",
            "workDate":"2026-10-10", "startTime":"08:00", "endTime":"09:00", "downtimeMinutes":0,
            "participants":[{"personId":"tech","minutes":60},{"personId":"tech2","minutes":30}],
            "parts":[{"partId":"part","quantity":Decimal("0.000001")}], "checklist":[],
        }, request_key="review-once")

    def completed(self):
        row=self.report()
        row=self.work.submit(self.users["tech"], row["id"], row["version"])
        row=self.work.decision(self.users["sup"],row["id"],{"version":row["version"],"decision":"approve","grades":[{"personId":"tech","coefficient":0},{"personId":"tech2","coefficient":Decimal("1.25")}]})
        return self.work.decision(self.users["head"],row["id"],{"version":row["version"],"decision":"approve","participantScores":[{"personId": p["personId"], "coefficient": Decimal("2.5")} for p in row["participants"]]})

    def test_required_guards_gate_and_explicit_legacy_read_gate(self):
        self.db.verify_schema()
        with self.db.transaction() as c:
            c.execute("DROP TRIGGER guard_consumption_history_delete")
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaises(SchemaMismatchError):
            self.db.verify_schema()
        self.db.verify_schema(require_guards=False)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        with self.assertRaises(TypeError):
            self.db.verify_schema(require_guards="false")

    def test_complete_workflow_with_all_guards_and_distinct_zero_grades(self):
        row=self.completed()
        self.assertEqual(row["status"],"completed")
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"),2)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"),1)
            scores=c.query("SELECT person_id,score_cents FROM completed_scores ORDER BY person_id")
            self.assertEqual(scores,[{"person_id":"tech","score_cents":0},{"person_id":"tech2","score_cents":156}])
            self.assertEqual(c.scalar("PRAGMA foreign_key_check"),None)

    def test_published_workflow_evidence_cannot_be_changed_or_deleted(self):
        self.completed()
        for table in ("approvals","participant_scores","participant_head_scores","completed_scores","part_consumptions","request_claims","audit"):
            with self.subTest(table=table),self.assertRaises(sqlite3.IntegrityError):
                self.db.run_transaction(lambda c:c.execute("DELETE FROM "+table))
            column="revision" if table in {"approvals","participant_scores","participant_head_scores","completed_scores","part_consumptions"} else "payload_hash" if table=="request_claims" else "details"
            with self.subTest(table=table,operation="update"),self.assertRaises(sqlite3.IntegrityError):
                self.db.run_transaction(lambda c:c.execute("UPDATE "+table+" SET "+column+"="+column))

    def test_forged_consumption_and_score_approval_linkages_are_rejected(self):
        row=self.completed()
        with self.db.connect() as c:
            old=c.one("SELECT * FROM part_consumptions")
            score=c.one("SELECT * FROM participant_scores WHERE person_id='tech'")
        # A forged INSERT is rejected before a duplicate-key conflict can hide it.
        columns=tuple(old)
        sql="INSERT INTO part_consumptions("+",".join(columns)+") VALUES("+",".join(":"+k for k in columns)+")"
        for changes in ({"id":"bad","approval_id":score["approval_id"]},{"id":"bad","actor_id":"other"},{"id":"bad","revision":2},{"id":"bad","quantity_units":2}):
            with self.subTest(changes=changes),self.assertRaises(sqlite3.IntegrityError):
                self.db.run_transaction(lambda c:c.execute(sql,dict(old,**changes)))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.run_transaction(lambda c:c.execute("INSERT INTO participant_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at) VALUES(:order_id,1,'tech2','مکانیک',2,'other',:approval_id,:created_at)",score))
        with self.db.connect() as c:
            head_score = c.one("SELECT * FROM participant_head_scores WHERE person_id='tech'")
        columns = tuple(head_score)
        sql = "INSERT INTO participant_head_scores(" + ",".join(columns) + ") VALUES(" + ",".join(":" + key for key in columns) + ")"
        for changes in ({"actor_id":"other"}, {"approval_id":score["approval_id"]}, {"specialty":"برق"}, {"person_id":"head"}, {"coefficient":"not-a-number"}):
            with self.subTest(head_changes=changes), self.assertRaises(sqlite3.IntegrityError):
                self.db.run_transaction(lambda c:c.execute(sql,dict(head_score,**changes)))

    def test_final_evidence_needs_a_completed_order_and_current_participant(self):
        row=self.report()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.run_transaction(lambda c:c.execute("INSERT INTO completed_scores(order_id,revision,person_id) VALUES(:id,1,'tech')",{"id":row["id"]}))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.run_transaction(lambda c:c.execute("INSERT INTO approvals VALUES('bad',:id,1,'head','','approve',4,'','head','2026-10-10')",{"id":row["id"]}))

    def test_completed_score_coefficients_must_have_matching_approval_proof(self):
        self.completed()
        with self.db.connect() as c:
            fact=c.one("SELECT * FROM completed_scores WHERE person_id='tech2'")
        columns=tuple(fact)
        sql="INSERT INTO completed_scores("+",".join(columns)+") VALUES("+",".join(":"+k for k in columns)+")"
        for changes in ({"head_coefficient":Decimal("1.5")},{"head_coefficient_exact":"1.5"},{"head_coefficient_exact":None},{"supervisor_coefficient":3},{"source":"legacy-specialty"}):
            with self.subTest(changes=changes),self.assertRaisesRegex(sqlite3.IntegrityError,"invalid completed score evidence"):
                self.db.run_transaction(lambda c:c.execute(sql,dict(fact,**changes)))

    def test_missing_historical_grades_remain_null(self):
        row=self.report()
        with self.db.transaction() as c:
            c.execute("UPDATE orders SET status='completed' WHERE id=:id",{"id":row["id"]})
            c.execute("INSERT INTO completed_scores(order_id,revision,person_id) VALUES(:id,1,'tech')",{"id":row["id"]})
            self.assertEqual(c.one("SELECT supervisor_coefficient,head_coefficient,score_raw,score_cents,source FROM completed_scores"),dict(supervisor_coefficient=None,head_coefficient=None,score_raw=None,score_cents=None,source=None))

    def test_positive_micro_units_and_monotonic_totals(self):
        row=self.completed()
        for number in (0,-1,1.5,Decimal("9223372036854775808")):
            with self.subTest(number=str(number)),self.assertRaises((sqlite3.IntegrityError,OverflowError)):
                self.db.run_transaction(lambda c:c.execute("UPDATE order_parts SET quantity_units=:units WHERE order_id=:id",{"units":number,"id":row["id"]}))
        for sql in ("UPDATE part_consumption_totals SET quantity_units=0", "UPDATE part_consumption_totals SET report_count=0", "DELETE FROM part_consumption_totals"):
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):
                self.db.run_transaction(lambda c:c.execute(sql))

    def test_historical_scores_survive_changed_current_participants(self):
        row=self.report()
        with self.db.transaction() as c:
            c.execute("UPDATE orders SET workflow_revision=2 WHERE id=:id",{"id":row["id"]})
            c.execute("DELETE FROM participants WHERE order_id=:id AND person_id='tech2'",{"id":row["id"]})
            c.execute("INSERT INTO approvals VALUES('historic',:id,1,'supervisor','مکانیک','approve',NULL,'','sup','2026-10-10')",{"id":row["id"]})
            c.execute("INSERT INTO participant_scores VALUES(:id,1,'tech2','مکانیک',2,'sup','historic','2026-10-10')",{"id":row["id"]})
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_scores WHERE person_id='tech2'"),1)


class NativeClientBoundaryReviewTests(unittest.TestCase):
    def db(self,driver):
        db=Database(postgres_config());db._driver=driver;return db

    def test_unknown_or_old_native_client_is_rejected_before_connect(self):
        for version in (None,160009,True,"180006"):
            driver=ScriptedDriver()
            driver.pq=SimpleNamespace(version=lambda:version)
            with self.subTest(version=version),self.assertRaises(DatabaseDependencyError):
                with self.db(driver).connect():pass
            self.assertEqual(driver.connections,[])

    def test_tls_environment_inheritance_is_rejected_before_connect(self):
        for name in ("OPENSSL_CONF","OPENSSL_CONF_INCLUDE","OPENSSL_MODULES","SSL_CERT_FILE","SSL_CERT_DIR"):
            driver=ScriptedDriver()
            with self.subTest(name=name),patch.dict(os.environ,{name:r"\\external.example\profile"}),self.assertRaises(DatabaseConfigurationError):
                with self.db(driver).connect():pass
            self.assertEqual(driver.connections,[])

    def test_explicit_scram_and_client_certificate_policy_on_driver_boundary(self):
        driver=ScriptedDriver()
        with self.db(driver).connect():pass
        self.assertEqual(driver.parameters[0]["require_auth"],"scram-sha-256")
        self.assertEqual(driver.parameters[0]["sslcertmode"],"disable")
        self.assertEqual(driver.parameters[0]["channel_binding"],"disable")
        self.assertEqual(driver.parameters[0]["ssl_min_protocol_version"],"TLSv1.2")

    def test_tls_crl_location_is_explicit_local_and_channel_binding_required(self):
        driver=ScriptedDriver()
        ca=PROJECT/"tests"/"synthetic-root.pem"
        db=Database(postgres_config(sslmode="verify-full",sslrootcert=str(ca)))
        db._driver=driver
        with db.connect():pass
        options=driver.parameters[0]
        self.assertEqual(Path(options["sslrootcert"]),ca)
        self.assertEqual(Path(options["sslcrl"]),Path(str(ca)+".crl"))
        self.assertEqual(options["channel_binding"],"require")
        self.assertFalse(ca.exists())


class PostgreSQLRolePolicyTests(unittest.TestCase):
    """Metadata fault injection, not execution of SQL on PostgreSQL."""
    class Metadata:
        def __init__(self, *, administrative=False, schema_create=False, owner=False):
            self.role=dict(rolsuper=administrative,rolcreatedb=False,rolcreaterole=False,rolreplication=False,rolbypassrls=False)
            self.schema_create=schema_create;self.owner=owner;self.guards=set(_POSTGRESQL_GUARDS)
            self.functions={"pak_round_even","pak_decimal","pak_participant_score","pak_decimal_sum"}
            self.constraints={"ck_order_parts_units","ck_movements_units"};self.unit_type="bigint";self.calls=[]
            self.correction_contract=True
            self.privileges=[dict(table_name=name,can_select=True,can_insert=True,can_update=True,can_delete=True,can_truncate=False,can_trigger=False) for name in _REQUIRED_COLUMNS]
        def query(self,sql,params=None):
            self.calls.append(sql)
            if sql=="SELECT version FROM schema_version":return [{"version":10}]
            if "information_schema.columns" in sql:
                if "table_name=:table" in sql:return [{"column_name":name} for name in _REQUIRED_COLUMNS[params["table"]]]
                return [{"table_name":name,"data_type":self.unit_type} for name in ("order_parts","stock_movements","part_consumptions","part_consumption_totals")]
            if "FROM pg_trigger" in sql:return [{"name":name} for name in self.guards]
            if "FROM pg_constraint" in sql:return [{"name":name} for name in self.constraints]
            if "FROM pg_proc" in sql:return [{"name":name} for name in self.functions]
            if "has_table_privilege" in sql:return self.privileges
            raise AssertionError("Unexpected metadata query: "+sql)
        def one(self,sql,params=None):
            self.calls.append(sql)
            if "FROM pg_roles" in sql:return self.role
            raise AssertionError("Unexpected metadata single query: "+sql)
        def scalar(self,sql,params=None):
            self.calls.append(sql)
            if "FROM pg_constraint" in sql and "supervisor-correction-snapshot" in sql:return int(self.correction_contract)
            if "has_schema_privilege" in sql:return self.schema_create
            if "pg_has_role" in sql:return int(self.owner)
            raise AssertionError("Unexpected metadata scalar query: "+sql)

    def verify(self,metadata,**options):
        db=Database(postgres_config())
        @contextmanager
        def transaction(*,write):
            self.assertIs(write,False)
            yield metadata
        with patch.object(db,"transaction",transaction):db.verify_schema(**options)

    def test_default_runtime_admission_refuses_admin_create_and_owner_roles(self):
        for change in ({"administrative":True},{"schema_create":True},{"owner":True}):
            with self.subTest(change=change),self.assertRaises(SchemaMismatchError):
                self.verify(self.Metadata(**change))
        self.verify(self.Metadata())

    def test_explicit_offline_migration_role_skips_only_role_policy(self):
        metadata=self.Metadata(administrative=True,schema_create=True,owner=True)
        self.verify(metadata,runtime_role=False)
        self.assertTrue(any("FROM pg_trigger" in sql for sql in metadata.calls))
        self.assertTrue(any("FROM pg_constraint" in sql for sql in metadata.calls))
        self.assertFalse(any("FROM pg_roles" in sql or "has_schema_privilege" in sql or "pg_has_role" in sql for sql in metadata.calls))
        for attribute in ("guards","constraints","functions"):
            malformed=self.Metadata(administrative=True)
            setattr(malformed,attribute,set())
            with self.subTest(missing=attribute),self.assertRaises(SchemaMismatchError):
                self.verify(malformed,runtime_role=False)
        malformed=self.Metadata(administrative=True);malformed.unit_type="numeric"
        with self.assertRaises(SchemaMismatchError):self.verify(malformed,runtime_role=False)

    def test_runtime_role_flag_must_be_boolean(self):
        for invalid in (None,0,1,"false"):
            with self.subTest(invalid=invalid),self.assertRaises(TypeError):
                self.verify(self.Metadata(),runtime_role=invalid)

    def test_current_schema_requires_validated_correction_archive_contract(self):
        metadata=self.Metadata();metadata.correction_contract=False
        with self.assertRaisesRegex(SchemaMismatchError,"correction archive"):
            self.verify(metadata)

    def test_runtime_requires_each_dml_privilege_and_rejects_history_bypasses(self):
        for permission in ("can_select","can_insert","can_update","can_delete","can_truncate","can_trigger"):
            metadata=self.Metadata()
            metadata.privileges[0][permission]=permission in ("can_truncate","can_trigger")
            with self.subTest(permission=permission),self.assertRaises(SchemaMismatchError):
                self.verify(metadata)
        metadata=self.Metadata();metadata.privileges.pop()
        with self.assertRaises(SchemaMismatchError):self.verify(metadata)
        metadata=self.Metadata();metadata.privileges=[]
        self.verify(metadata,runtime_role=False)
        self.assertFalse(any("has_table_privilege" in sql for sql in metadata.calls))


if __name__=="__main__":unittest.main()
