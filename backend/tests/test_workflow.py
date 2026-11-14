"""Real SQL transactions over disposable synthetic fixtures; no company data."""
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from historical_workflow_fixtures import seed_historical_return

from pak_maintenance.common import ApiError, User
from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.work_service import WorkService
from pak_maintenance.consumption_service import ConsumptionService, capture
from pak_maintenance.performance_service import PerformanceService
from pak_maintenance.reporting_service import ReportingService
from pak_maintenance.quantity import units, value
from pak_maintenance.score_facts import participant_score, round_score, decimal_add, stored_decimal


SCHEMA = """
CREATE TABLE users(id TEXT PRIMARY KEY,code TEXT NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL,specialty TEXT NOT NULL,active INTEGER NOT NULL,password_hash TEXT DEFAULT '',session_epoch INTEGER DEFAULT 0);
CREATE TABLE assets(id TEXT PRIMARY KEY,code TEXT,name TEXT,hall TEXT,line TEXT,body TEXT);
CREATE TABLE parts(id TEXT PRIMARY KEY,code TEXT,name TEXT,unit TEXT,unit_cost NUMERIC,version INTEGER DEFAULT 1,specification TEXT DEFAULT '');
CREATE TABLE orders(id TEXT PRIMARY KEY,number TEXT UNIQUE,version INTEGER NOT NULL,workflow_revision INTEGER NOT NULL,type TEXT,status TEXT,asset_id TEXT REFERENCES assets(id),created_by TEXT REFERENCES users(id),assignee_id TEXT REFERENCES users(id),work_date TEXT,due_date TEXT,body TEXT,title TEXT,description TEXT,duration_minutes INTEGER,created_at TEXT,updated_at TEXT);
CREATE TABLE participants(order_id TEXT REFERENCES orders(id),person_id TEXT REFERENCES users(id),minutes INTEGER CHECK(minutes>0),specialty TEXT,PRIMARY KEY(order_id,person_id));
CREATE TABLE participant_grading_policies(order_id TEXT REFERENCES orders(id),revision INTEGER,person_id TEXT REFERENCES users(id),specialty TEXT,role_at_submission TEXT,policy TEXT,created_at TEXT,PRIMARY KEY(order_id,revision,person_id));
CREATE TABLE order_parts(order_id TEXT REFERENCES orders(id),part_id TEXT REFERENCES parts(id),quantity NUMERIC,quantity_units BIGINT CHECK(quantity_units>0),PRIMARY KEY(order_id,part_id));
CREATE TABLE request_claims(actor_id TEXT REFERENCES users(id),request_key TEXT,order_id TEXT REFERENCES orders(id),payload_hash TEXT,PRIMARY KEY(actor_id,request_key));
CREATE TABLE approvals(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id),revision INTEGER,stage TEXT,specialty TEXT,decision TEXT,coefficient NUMERIC,reason TEXT,actor_id TEXT REFERENCES users(id),created_at TEXT);
CREATE TABLE participant_scores(order_id TEXT REFERENCES orders(id),revision INTEGER,person_id TEXT REFERENCES users(id),specialty TEXT,coefficient NUMERIC CHECK(coefficient>=0 AND coefficient<=3),actor_id TEXT REFERENCES users(id),approval_id TEXT REFERENCES approvals(id),created_at TEXT,PRIMARY KEY(order_id,revision,person_id));
CREATE TABLE participant_head_scores(order_id TEXT REFERENCES orders(id),revision INTEGER,person_id TEXT REFERENCES users(id),specialty TEXT,coefficient TEXT,actor_id TEXT REFERENCES users(id),approval_id TEXT REFERENCES approvals(id),created_at TEXT,PRIMARY KEY(order_id,revision,person_id));
CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT REFERENCES users(id),actor_name TEXT,action TEXT,entity TEXT,entity_id TEXT,details TEXT,created_at TEXT);
CREATE TABLE completed_scores(order_id TEXT REFERENCES orders(id),revision INTEGER,person_id TEXT REFERENCES users(id),supervisor_coefficient NUMERIC,head_coefficient NUMERIC,score_raw TEXT,score_cents BIGINT,source TEXT,head_coefficient_exact TEXT,PRIMARY KEY(order_id,revision,person_id));
CREATE TABLE part_consumptions(id TEXT PRIMARY KEY,order_id TEXT REFERENCES orders(id),part_id TEXT REFERENCES parts(id),approval_id TEXT REFERENCES approvals(id),revision INTEGER CHECK(revision>0),quantity_units BIGINT CHECK(quantity_units>0),part_code TEXT,part_name TEXT,unit TEXT,order_number TEXT,executed_at TEXT,approved_at TEXT,actor_id TEXT REFERENCES users(id),actor_name TEXT,UNIQUE(order_id,part_id));
CREATE TABLE part_consumption_totals(part_id TEXT PRIMARY KEY REFERENCES parts(id),quantity_units BIGINT CHECK(quantity_units>0),report_count BIGINT CHECK(report_count>0));
CREATE TABLE pm_weekly_occurrences(order_id TEXT PRIMARY KEY REFERENCES orders(id),supervisor_id TEXT REFERENCES users(id),end_utc TEXT,executed_at TEXT,submitted_at TEXT);
CREATE TABLE pm_responsibility_transfers(id TEXT PRIMARY KEY,order_id TEXT REFERENCES pm_weekly_occurrences(order_id),order_version INTEGER,from_supervisor_id TEXT REFERENCES users(id),to_supervisor_id TEXT REFERENCES users(id),actor_id TEXT REFERENCES users(id),reason TEXT,created_at TEXT,UNIQUE(order_id,order_version));
CREATE TABLE pm_weekly_assets(order_id TEXT REFERENCES orders(id),asset_id TEXT REFERENCES assets(id));
CREATE TABLE plans(id TEXT PRIMARY KEY,asset_id TEXT REFERENCES assets(id),interval_days INTEGER,next_due TEXT,version INTEGER,active INTEGER,body TEXT);
CREATE TABLE pm_occurrences(plan_id TEXT REFERENCES plans(id),due_date TEXT,order_id TEXT REFERENCES orders(id),PRIMARY KEY(plan_id,due_date));
CREATE INDEX ix_work_date ON orders(status,work_date);
CREATE INDEX ix_participant_person ON participants(person_id,order_id);
"""


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).resolve().parent / ".workflow-fixtures" / uuid4().hex
        self.directory.mkdir(parents=True, mode=0o755)
        self.path = self.directory / "synthetic.db"
        raw = sqlite3.connect(self.path)
        raw.executescript(getattr(self, "schema", SCHEMA))
        raw.close()
        self.db = Database(DatabaseConfig(provider="sqlite", database=str(self.path), development=True))
        self.users = {u.id: u for u in [User("m1", "1", "مکانیک اول", "technician", "مکانیک"), User("m2", "2", "مکانیک دوم", "technician", "مکانیک"), User("e1", "3", "برق اول", "technician", "برق"), User("sm", "4", "سرپرست مکانیک", "supervisor", "مکانیک"), User("sm2", "5", "سرپرست مکانیک دوم", "supervisor", "مکانیک"), User("se", "6", "سرپرست برق", "supervisor", "برق"), User("head", "7", "رئیس", "head"), User("admin", "8", "مدیر سیستم", "admin"), User("planner", "9", "برنامه ریز", "planner")]}
        with self.db.transaction() as c:
            for u in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:spec,1,'',0)", {"id": u.id, "code": u.code, "name": u.name, "role": u.role, "spec": u.specialty})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','A01','پمپ','فرایند','خط یک','{}')")
            c.execute("INSERT INTO parts(id,code,name,unit,specification) VALUES('part','P001','بلبرینگ','عدد','')")
        self.work = WorkService(self.db)

    def tearDown(self):
        shutil.rmtree(self.directory)
        parent = self.directory.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def body(self, people=("m1", "m2", "e1"), quantity="1.000001"):
        return {"type": "EM", "assetId": "asset", "title": "تعمیر پمپ", "description": "تعویض بلبرینگ", "workDate": "2026-10-10", "startTime": "08:00", "endTime": "09:00", "participants": [{"personId": p, "minutes": 60} for p in people], "parts": [{"partId": "part", "quantity": Decimal(quantity)}], "checklist": [], "downtimeMinutes": 15}

    def submitted(self, people=("m1", "m2", "e1")):
        saved = self.work.save(self.users["m1"], self.body(people))
        return self.work.submit(self.users["m1"], saved["id"], saved["version"])

    def grade(self, row, supervisor, grades):
        return self.work.decision(self.users[supervisor], row["id"], {"version": row["version"], "decision": "approve", "grades": [{"personId": person, "coefficient": Decimal(str(score))} for person, score in grades]})

    def ready_head(self):
        row = self.submitted()
        row = self.grade(row, "sm", [("m1", 0), ("m2", "1.25")])
        return self.grade(row, "se", [("e1", "2.5")])

    @staticmethod
    def head_body(row, coefficient):
        return {"version": row["version"], "decision": "approve", "participantScores": [{"personId": person["personId"], "coefficient": coefficient} for person in row["participants"]]}

    def test_joint_specialties_distinct_scores_zero_and_head_finalization(self):
        row = self.submitted()
        self.assertEqual(row["pendingSupervisorSpecialties"], ["برق", "مکانیک"])
        row = self.grade(row, "sm", [("m1", 0), ("m2", "1.25")])
        self.assertEqual(row["status"], "supervisor")
        self.assertEqual(row["pendingSupervisorSpecialties"], ["برق"])
        self.assertEqual(self.work.inbox_page(self.users["sm"])["total"], 0)
        self.assertEqual(self.work.inbox_page(self.users["head"])["total"], 0)
        row = self.grade(row, "se", [("e1", "2.5")])
        self.assertEqual(row["status"], "head")
        self.assertEqual(self.work.inbox_page(self.users["head"])["total"], 1)
        row = self.work.decision(self.users["head"], row["id"], self.head_body(row, Decimal("2")))
        self.assertEqual(row["status"], "completed")
        self.assertEqual({p["personId"]: p["finalScore"] for p in row["participants"]}, {"m1": Decimal("0"), "m2": Decimal("2.50"), "e1": Decimal("5.00")})
        self.assertEqual(row["score"], Decimal("7.50"))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 3)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), 1_000_001)

    def test_head_scores_each_person_independently_and_facts_preserve_precision(self):
        row = self.ready_head()
        exact = Decimal("0.3333333333333333333333333333")
        row = self.work.decision(self.users["head"], row["id"], {
            "version": row["version"], "decision": "approve", "participantScores": [
                {"personId": "m1", "coefficient": Decimal("2.75")},
                {"personId": "m2", "coefficient": exact},
                {"personId": "e1", "coefficient": 0},
            ],
        })
        people = {p["personId"]: p for p in row["participants"]}
        self.assertIsNone(row["headCoefficient"])
        self.assertEqual(people["m2"]["headCoefficient"], exact)
        self.assertEqual({p: person["finalScore"] for p, person in people.items()}, {"m1": Decimal("0"), "m2": Decimal("0.42"), "e1": Decimal("0")})
        self.assertEqual({p["personId"] for p in row["participantHeadScores"]}, {"m1", "m2", "e1"})
        self.assertTrue(all(p["headScoreSource"] == "individual" for p in people.values()))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_head_scores"), 3)
            self.assertIsNone(c.scalar("SELECT coefficient FROM approvals WHERE stage='head'"))
            fact = c.one("SELECT head_coefficient_exact,score_raw FROM completed_scores WHERE person_id='m2'")
            self.assertEqual(Decimal(fact["head_coefficient_exact"]), exact)
            self.assertEqual(Decimal(fact["score_raw"]), participant_score(60, Decimal("1.25"), exact))
        report = PerformanceService(self.db).report(self.users["head"], period="all")
        self.assertEqual(next(p["headCoefficient"] for p in report["details"] if p["personId"] == "m2"), exact)
        self.assertEqual(report["totals"]["score"], row["score"])
        self.assertEqual(ReportingService(self.db, self.work).dashboard(self.users["head"])["score"], row["score"])

    def test_head_requires_exact_participant_set_and_invalid_grades_rollback(self):
        row = self.ready_head()
        valid = self.head_body(row, Decimal("1.25"))
        cases = [
            ({"coefficient": 2}, 400),
            ({"participantScores": []}, 400),
            ({"participantScores": valid["participantScores"][:-1]}, 400),
            ({"participantScores": valid["participantScores"] + [valid["participantScores"][0]]}, 400),
            ({"participantScores": [{"personId": "sm", "coefficient": 1}]}, 403),
        ]
        cases.extend(({"participantScores": [{"personId": "m1", "coefficient": score}]}, 400) for score in (-1, Decimal("3.01"), True, None, "NaN", "Infinity", "1e-29"))
        cases.extend(({"participantScores": invalid}, 400) for invalid in (False, {}, "", [None], list(range(21))))
        for change, status in cases:
            with self.subTest(change=change), self.assertRaises(ApiError) as error:
                self.work.decision(self.users["head"], row["id"], dict(version=row["version"], decision="approve", **change))
            self.assertEqual(error.exception.status, status)
            with self.db.connect() as c:
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_head_scores"), 0)
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 0)
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)
            self.assertEqual(self.work.get(self.users["head"], row["id"])["version"], row["version"])

    def test_head_evidence_and_other_personnel_performance_are_chief_only(self):
        row = self.ready_head()
        row = self.work.decision(self.users["head"], row["id"], self.head_body(row, Decimal("2.25")))
        from pak_maintenance.common import dumps, loads
        with self.db.transaction() as c:
            original = loads(c.scalar("SELECT body FROM orders WHERE id=:id", {"id": row["id"]}))
            original["legacyDetails"] = {"headCoefficient": 2, "total_score": Decimal("9.99"), "nested": [{"grades": [{"personId": "m1", "coefficient": 3}]}]}
            c.execute("UPDATE orders SET body=:body WHERE id=:id", {"id": row["id"], "body": dumps(original)})
        for role in ("m1", "sm", "planner", "admin"):
            with self.subTest(role=role):
                view = self.work.get(self.users[role], row["id"])
                self.assertIsNone(view["headCoefficient"])
                self.assertIsNone(view["score"])
                self.assertEqual(view["participantHeadScores"], [])
                self.assertTrue(all(p["headCoefficient"] is None and p["finalScore"] is None for p in view["participants"]))
                self.assertIsNone(view["legacyDetails"]["headCoefficient"])
                self.assertIsNone(view["legacyDetails"]["total_score"])
                self.assertEqual(view["legacyDetails"]["nested"][0]["grades"], [])
                self.assertTrue(all(entry["details"].get("grades", []) == [] for entry in view["history"] if entry["details"].get("stage") == "head"))
                self.assertIsNone(ReportingService(self.db, self.work).dashboard(self.users[role])["score"])
                own = PerformanceService(self.db).report(self.users[role], period="all")
                self.assertEqual(own["scope"], "own")
                self.assertEqual({p["personId"] for p in own["people"]}, {role})
                self.assertTrue(all(p["personId"] == role for p in own["details"]))
                with self.assertRaises(ApiError) as denied:
                    PerformanceService(self.db).report(self.users[role], period="all", scope="all")
                self.assertEqual(denied.exception.status, 403)
        supervisor = self.work.get(self.users["sm"], row["id"])
        self.assertEqual({p["personId"] for p in supervisor["participantScores"]}, {"m1", "m2"})
        self.assertTrue(all(p["supervisorCoefficient"] is None for p in self.work.get(self.users["planner"], row["id"])["participants"]))

    def test_concurrent_final_head_scores_capture_exactly_once(self):
        row = self.ready_head()
        barrier, outcomes = threading.Barrier(2), []
        def compete(coefficient):
            barrier.wait()
            try:
                result = self.work.decision(self.users["head"], row["id"], self.head_body(row, coefficient))
                outcomes.append(result["status"])
            except ApiError as error:
                outcomes.append(error.status)
        threads = [threading.Thread(target=compete, args=(value,)) for value in (Decimal("1.25"), Decimal("2.75"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["completed", 409])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_head_scores"), 3)
            self.assertEqual(c.scalar("SELECT COUNT(DISTINCT coefficient) FROM participant_head_scores"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 3)
            self.assertEqual(c.scalar("SELECT report_count FROM part_consumption_totals"), 1)

    def test_planner_can_cancel_operational_report_but_cannot_grade(self):
        row = self.ready_head()
        planner = self.users["planner"]
        visible = self.work.get(planner, row["id"])
        self.assertTrue(visible["canCancel"])
        self.assertFalse(visible["canDecide"])
        with self.assertRaises(ApiError) as denied:
            self.work.decision(planner, row["id"], self.head_body(row, 3))
        self.assertEqual(denied.exception.status, 403)
        cancelled = self.work.cancel(planner, row["id"], {"version": row["version"], "reason": "گزارش آزمایشی تکراری است"})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(cancelled["score"])
        self.assertEqual(cancelled["participantScores"], [])
        self.assertEqual(cancelled["participantHeadScores"], [])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_head_scores"), 0)

    def test_role_isolation_on_detail_grade_history_and_reporting(self):
        row = self.ready_head()
        technician = self.work.get(self.users["m1"], row["id"])
        self.assertEqual(technician["participantScores"], [])
        self.assertTrue(all(h["details"].get("grades", []) == [] for h in technician["history"]))
        supervisor = self.work.get(self.users["sm"], row["id"])
        self.assertEqual({g["personId"] for g in supervisor["participantScores"]}, {"m1", "m2"})
        for role in (User("m1", role="unrecognized"), User("m1", role="technician", active=False)):
            with self.assertRaises(ApiError):
                self.work.get(role, row["id"])
            with self.assertRaises(ApiError):
                PerformanceService(self.db).report(role, period="all")
        with self.assertRaises(ApiError) as denied:
            PerformanceService(self.db).report(self.users["m1"], period="all", person_id="e1")
        self.assertEqual(denied.exception.status, 403)
        self.assertEqual(self.work.inbox_page(self.users["admin"])["total"], 0)
        self.assertEqual(self.work.inbox_page(self.users["planner"])["total"], 0)

    def test_missing_duplicate_and_foreign_grades_rollback(self):
        row = self.submitted()
        for grades, status in [([("m1", 1)], 400), ([("m1", 1), ("m1", 2)], 400), ([("e1", 1)], 403)]:
            with self.subTest(grades=grades):
                with self.assertRaises(ApiError) as error:
                    self.grade(row, "sm", grades)
                self.assertEqual(error.exception.status, status)
                with self.db.connect() as c:
                    self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals"), 0)
                    self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_scores"), 0)
                self.assertEqual(self.work.get(self.users["head"], row["id"])["version"], row["version"])

    def test_stale_version_and_duplicate_supervisor_cannot_change_facts(self):
        row = self.submitted()
        current = self.grade(row, "sm", [("m1", 1), ("m2", 2)])
        for version in (row["version"], current["version"]):
            with self.assertRaises(ApiError) as error:
                self.work.decision(self.users["sm"], row["id"], {"version": version, "decision": "approve", "grades": []})
            self.assertEqual(error.exception.status, 409)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_scores"), 2)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals"), 1)

    def test_idempotent_create_replay_and_conflicting_payload(self):
        body = self.body()
        first = self.work.save(self.users["m1"], body, request_key="once")
        second = self.work.save(self.users["m1"], body, request_key="once")
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(ApiError) as error:
            self.work.save(self.users["m1"], dict(body, title="عنوان متفاوت"), request_key="once")
        self.assertEqual(error.exception.status, 409)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='create'"), 1)

    def test_final_approval_is_atomic_if_score_capture_fails(self):
        row = self.ready_head()
        with self.db.transaction() as c:
            c.execute("CREATE TRIGGER fail_score BEFORE INSERT ON completed_scores BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.work.decision(self.users["head"], row["id"], self.head_body(row, 3))
        current = self.work.get(self.users["head"], row["id"])
        self.assertEqual((current["version"], current["status"]), (row["version"], "head"))
        with self.db.connect() as c:
            for table in ("part_consumptions", "part_consumption_totals", "completed_scores"):
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM " + table), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)

    def test_final_approval_replay_never_doubles_consumption(self):
        row = self.ready_head()
        body = self.head_body(row, Decimal("3"))
        completed = self.work.decision(self.users["head"], row["id"], body)
        with self.assertRaises(ApiError) as error:
            self.work.decision(self.users["head"], row["id"], body)
        self.assertEqual(error.exception.status, 409)
        with self.db.transaction() as c:
            self.assertEqual(capture(c, row["id"]), 0)
            self.assertEqual(c.scalar("SELECT report_count FROM part_consumption_totals"), 1)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), 1_000_001)
        with self.assertRaises(ApiError):
            self.work.cancel(self.users["head"], row["id"], {"version": completed["version"], "reason": "لغو نهایی"})

    def test_consumption_aggregate_overflow_aborts_whole_final_approval(self):
        row = self.ready_head()
        with self.db.transaction() as c:
            c.execute("INSERT INTO part_consumption_totals(part_id,quantity_units,report_count) VALUES('part',:maximum,1)", {"maximum": (1 << 63) - 1})
        with self.assertRaises(ApiError) as error:
            self.work.decision(self.users["head"], row["id"], self.head_body(row, 3))
        self.assertEqual(error.exception.status, 409)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM part_consumptions"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), (1 << 63) - 1)
            self.assertEqual(c.scalar("SELECT status FROM orders"), "head")

    def test_missing_historical_grade_stays_null_and_unscored(self):
        row = self.work.save(self.users["m1"], self.body(people=("m1",)))
        with self.db.transaction() as c:
            c.execute("UPDATE orders SET status='completed' WHERE id=:id", {"id": row["id"]})
        detail = self.work.get(self.users["m1"], row["id"])
        self.assertIsNone(detail["participants"][0]["supervisorCoefficient"])
        self.assertIsNone(detail["participants"][0]["finalScore"])
        report = PerformanceService(self.db).report(self.users["head"], period="all")
        self.assertIsNone(report["details"][0]["score"])
        self.assertEqual(report["totals"]["unscoredCount"], 1)

    def test_legacy_pm_generation_keeps_missed_occurrences_and_skips_inactive(self):
        from pak_maintenance.common import dumps
        with self.db.transaction() as c:
            c.execute("INSERT INTO plans(id,asset_id,interval_days,next_due,version,active,body) VALUES('plan','asset',7,'2026-09-01',1,1,:body)", {"body": dumps({"title": "بازدید ماهانه", "description": "بازدید پمپ", "assigneeId": "m1", "checklist": [{"text": "بررسی روغن", "done": False}]})})
            c.execute("INSERT INTO plans(id,asset_id,interval_days,next_due,version,active,body) VALUES('inactive','asset',7,'2026-01-01',1,1,:body)", {"body": dumps({"title": "غیرفعال", "assigneeId": "m2"})})
            c.execute("UPDATE users SET active=0 WHERE id='m2'")
        with patch("pak_maintenance.work_service.today", return_value="2026-09-15"):
            self.assertEqual(self.work.generate_pm(), 1)
            self.assertEqual(self.work.generate_pm(), 1)
            self.assertEqual(self.work.generate_pm(), 1)
            self.assertEqual(self.work.generate_pm(), 0)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_occurrences"), 3)
            self.assertEqual(c.scalar("SELECT next_due FROM plans WHERE id='plan'"), "2026-09-22")
            self.assertEqual(c.scalar("SELECT next_due FROM plans WHERE id='inactive'"), "2026-01-01")
        with self.assertRaises(ApiError):
            self.work.generate_pm(self.users["m1"])

    def test_live_return_is_removed_but_historical_return_resubmits_with_a_new_revision(self):
        row = self.ready_head()
        with self.assertRaises(ApiError) as denied:
            self.work.decision(self.users["head"], row["id"], {"version": row["version"], "decision": "return", "reason": "شرح نیازمند اصلاح"})
        self.assertEqual(denied.exception.status, 400)
        self.assertEqual(self.work.get(self.users['head'], row['id'])['version'], row['version'])
        row = seed_historical_return(self.db, self.work, self.users['head'], row, 'شرح نیازمند اصلاح در نسخهٔ قدیمی')
        old_revision = row["workflowRevision"]
        row = self.work.submit(self.users["m1"], row["id"], row["version"])
        self.assertEqual(row["workflowRevision"], old_revision + 1)
        self.assertEqual(row["participantScores"], [])
        self.assertEqual(row["pendingSupervisorSpecialties"], ["برق", "مکانیک"])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_scores"), 3)

    def test_negative_excess_precision_nan_and_invalid_shapes_rejected(self):
        for quantity in ("-1", "0", "0.0000001", "1000000001", "NaN", "Infinity", "1e999999999"):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ApiError):
                    self.work.save(self.users["m1"], self.body(quantity=quantity))
        for collection in (False, 0, "", {}, [{"personId": "m1", "minutes": True}]):
            with self.subTest(collection=collection):
                with self.assertRaises(ApiError):
                    self.work.save(self.users["m1"], dict(self.body(), participants=collection))

    def test_midnight_crossing_and_participant_duration(self):
        body = dict(self.body(people=("m1",)), startTime="23:30", endTime="00:30")
        row = self.work.save(self.users["m1"], body)
        self.assertEqual(row["durationMinutes"], 60)
        for modification in ({"endTime": "23:30"}, {"participants": [{"personId": "m1", "minutes": 61}]}):
            with self.assertRaises(ApiError):
                self.work.save(self.users["m1"], dict(body, **modification))

    def test_consumption_and_performance_scope_monthly_and_pagination(self):
        row = self.ready_head()
        self.work.decision(self.users["head"], row["id"], self.head_body(row, 2))
        consumption = ConsumptionService(self.db)
        result = consumption.history(self.users["head"], from_date="2026-10-10", to_date="2026-10-10", page_size=1)
        self.assertEqual(result["items"][0]["quantity"], Decimal("1.000001"))
        self.assertEqual(result["total"], 1)
        self.assertEqual(list(consumption.export(self.users["head"])), result["items"])
        with self.assertRaises(ApiError):
            consumption.history(self.users["m1"])
        for role in ("m1", "sm", "planner", "admin"):
            with self.subTest(role=role):
                own = PerformanceService(self.db).report(self.users[role], period="monthly", year=1405, month=7)
                self.assertEqual(own["scope"], "own")
                self.assertEqual({p["personId"] for p in own["people"]}, {role})
                self.assertTrue(all(p["personId"] == role for p in own["details"]))
            with self.assertRaises(ApiError) as denied:
                PerformanceService(self.db).report(self.users[role], period="monthly", year=1405, month=7, scope="all")
            self.assertEqual(denied.exception.status, 403)
        all_report = PerformanceService(self.db).report(self.users["head"], period="all", detail_page_size=1)
        self.assertEqual(all_report["detailTotal"], 3)
        self.assertEqual(len(all_report["details"]), 1)
        self.assertEqual(all_report["totals"]["score"], Decimal("7.50"))
        dashboard = ReportingService(self.db, self.work).dashboard(self.users["head"])
        self.assertEqual(dashboard["score"], all_report["totals"]["score"])

    def test_weekly_pm_owner_scope_and_fail_closed_without_pm_adapter(self):
        row = self.work.save(self.users["m1"], self.body(people=("m1",)))
        with self.db.transaction() as c:
            c.execute("UPDATE orders SET assignee_id='m1' WHERE id=:id", {"id": row["id"]})
            c.execute("INSERT INTO pm_weekly_occurrences(order_id,supervisor_id,end_utc) VALUES(:id,'sm','2030-01-01T00:00:00+00:00')", {"id": row["id"]})
        with self.assertRaises(ApiError) as error:
            self.work.get(self.users["sm2"], row["id"])
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.work.list_page(self.users["sm2"])["total"], 0)
        with self.assertRaises(ApiError) as error:
            self.work.submit(self.users["m1"], row["id"], row["version"])
        self.assertEqual(error.exception.status, 503)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT status FROM orders WHERE id=:id", {"id": row["id"]}), "draft")

    def test_concurrent_same_version_accepts_exactly_one_decision(self):
        row = self.submitted(people=("m1",))
        barrier, results = threading.Barrier(2), []
        def compete():
            barrier.wait()
            try:
                result = self.grade(row, "sm", [("m1", 2)])
                results.append(result["status"])
            except ApiError as error:
                results.append(error.status)
        threads = [threading.Thread(target=compete) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["head", 409])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals"), 1)

    def test_concurrent_same_idempotency_key_returns_one_document(self):
        barrier, results = threading.Barrier(2), []
        def compete():
            barrier.wait()
            results.append(self.work.save(self.users["m1"], self.body(), request_key="concurrent")["id"])
        threads = [threading.Thread(target=compete) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 1)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), 1)

    def test_two_hundred_parts_capture_exactly_once_in_bounded_batches(self):
        body = self.body(people=("m1",))
        with self.db.transaction() as c:
            for index in range(199):
                c.execute("INSERT INTO parts(id,code,name,unit) VALUES(:id,:code,'قطعه','عدد')", {"id": f"part{index}", "code": f"P{index:04d}"})
        body["parts"] = [{"partId": "part", "quantity": Decimal("0.000001")}] + [{"partId": f"part{index}", "quantity": Decimal("0.000001")} for index in range(199)]
        row = self.work.save(self.users["m1"], body)
        row = self.work.submit(self.users["m1"], row["id"], row["version"])
        row = self.grade(row, "sm", [("m1", 1)])
        row = self.work.decision(self.users["head"], row["id"], self.head_body(row, 1))
        with self.db.transaction() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM part_consumptions"), 200)
            self.assertEqual(c.scalar("SELECT SUM(quantity_units) FROM part_consumption_totals"), 200)
            self.assertEqual(capture(c, row["id"]), 0)

    def test_literal_search_and_page_bounds(self):
        self.work.save(self.users["m1"], dict(self.body(), title="تعمیر %_ ویژه"))
        self.work.save(self.users["m1"], self.body())
        self.assertEqual(self.work.list_page(self.users["m1"], q="%_")["total"], 1)
        self.assertEqual(self.work.list_page(self.users["m1"], q="' OR 1=1--")["total"], 0)
        for page, size in ((0, 25), (1, 101), (True, 25), (1, False)):
            with self.assertRaises(ApiError):
                self.work.list_page(self.users["m1"], page=page, page_size=size)


class FullSchemaWorkflowTests(unittest.TestCase):
    schema_directory = Path(__file__).resolve().parent.parent / "schema"
    schema = (schema_directory / "sqlite-v10.sql").read_text(encoding="utf-8") + "\n" + "\n".join(p.read_text(encoding="utf-8") for p in sorted(schema_directory.glob("sqlite-*-guards.sql")))
    setUp = WorkflowCase.setUp
    tearDown = WorkflowCase.tearDown
    body = WorkflowCase.body
    submitted = WorkflowCase.submitted
    grade = WorkflowCase.grade
    ready_head = WorkflowCase.ready_head
    head_body = staticmethod(WorkflowCase.head_body)

    def test_full_schema_workflow_report_and_foreign_keys(self):
        self.db.verify_schema()
        row = self.ready_head()
        row = self.work.decision(self.users["head"], row["id"], self.head_body(row, Decimal("2.25")))
        self.assertEqual(row["score"], Decimal("8.43"))
        report = PerformanceService(self.db).report(self.users["head"], period="monthly", year=1405, month=7)
        self.assertEqual(report["totals"]["score"], row["score"])
        self.assertEqual(ReportingService(self.db, self.work).dashboard(self.users["head"])["score"], row["score"])
        with self.db.connect() as c:
            self.assertEqual(c.query("PRAGMA foreign_key_check"), [])
            self.assertEqual(c.scalar("PRAGMA integrity_check"), "ok")
            self.assertNotIn("stock", {column["name"] for column in c.query("PRAGMA table_info(parts)")})

    def test_approved_consumption_and_scoring_evidence_cannot_be_changed(self):
        row = self.ready_head()
        self.work.decision(self.users["head"], row["id"], self.head_body(row, Decimal("2.25")))
        for statement in ("UPDATE part_consumptions SET quantity_units=2", "DELETE FROM part_consumptions", "DELETE FROM part_consumption_totals", "UPDATE completed_scores SET score_cents=999", "DELETE FROM completed_scores"):
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.db.transaction() as c:
                        c.execute(statement)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), 1_000_001)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM completed_scores"), 3)


class ExactArithmeticTests(unittest.TestCase):
    def test_micro_units_are_exact_without_silent_rounding(self):
        self.assertEqual(units(Decimal("0.000001")), 1)
        self.assertEqual(value(1_234_567), Decimal("1.234567"))
        with self.assertRaises(ApiError):
            units(Decimal("0.0000005"))
        with self.assertRaises(ApiError):
            units(Decimal("1.000000000000000000000000000000000000000000000000000000001"))

    def test_half_even_and_null_are_distinct_from_zero(self):
        self.assertEqual(round_score(Decimal("2.345")), Decimal("2.34"))
        self.assertEqual(round_score(Decimal("2.355")), Decimal("2.36"))
        self.assertIsNone(participant_score(60, None, 3))
        self.assertEqual(participant_score(60, 0, 3), Decimal("0"))
        self.assertEqual(round_score(participant_score(1, Decimal("0.3"), 1)), Decimal("0.00"))

    def test_dotnet_decimal_large_coefficient_and_storage_compatibility(self):
        self.assertEqual(decimal_add(Decimal("79228162514264337593543950334"), Decimal(1)), Decimal("79228162514264337593543950335"))
        self.assertEqual(stored_decimal(0.3333333333333333), Decimal("0.333333333333333"))
        self.assertEqual(stored_decimal(Decimal("0.3333333333333333333333333333")), Decimal("0.3333333333333333333333333333"))


if __name__ == "__main__":
    unittest.main()
