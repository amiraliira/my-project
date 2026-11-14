"""PM publication and personnel privacy over private guarded SQL fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.api import Api, Request
from pak_maintenance.application import compose
from pak_maintenance.common import ApiError, User, dumps, loads
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pak_maintenance.exports import report
from pak_maintenance.permissions import can_view_grades, can_view_personnel_reports
from pg_test_support import fresh_database


ROOT = Path(__file__).resolve().parents[1]


class PmGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture_root = ROOT / "tests" / ".pm-governance-fixtures"
        self.directory = self.fixture_root / uuid4().hex
        self.directory.mkdir(parents=True)
        for folder in ("frontend", "seed", "backups"):
            (self.directory / folder).mkdir()
        database_config, self.owner = fresh_database(self, prefix="governance")
        config = AppConfig(database=database_config,
                           listen_url="http://127.0.0.1:5021", development=True, scheduling_enabled=False,
                           frontend_directory=str(self.directory / "frontend"), seed_directory=str(self.directory / "seed"),
                           backup_directory=str(self.directory / "backups"))
        self.services = compose(config)
        self.db, self.pm = self.services.db, self.services.pm
        self.users = {identifier: User(identifier, identifier, "آزمایشی " + identifier, role, specialty)
                      for identifier, role, specialty in (("head", "head", ""), ("planner", "planner", ""),
                          ("planner2", "planner", ""), ("manager", "manager", ""), ("admin", "admin", ""),
                          ("sm", "supervisor", "مکانیک"), ("se", "supervisor", "برق"),
                          ("tech", "technician", "مکانیک"))}
        with self.db.transaction() as c:
            for user in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:name,:role,:specialty,1,'',0)",
                          dict(id=user.id, name=user.name, role=user.role, specialty=user.specialty))
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('a','A-1','پمپ','فرایند','','{}')")
        self.services.auth = SimpleNamespace(authenticate=lambda token: self.users.get(token))
        self.api = Api(self.services, config)

    def tearDown(self):
        if self.directory.resolve().parent != self.fixture_root.resolve() or self.directory.is_symlink():
            raise RuntimeError("Refusing cleanup outside the owned governance fixture")
        shutil.rmtree(self.directory)
        try:
            self.fixture_root.rmdir()
        except OSError:
            pass

    def request(self, method, path, user, body=None, query=None):
        return self.api.dispatch(Request(method, path, query or {},
                    {"cookie": "pak_session=" + user, "content-type": "application/json"},
                    dumps(body).encode() if body is not None else b"", "127.0.0.1"))

    @staticmethod
    def design(**overrides):
        value = dict(requestKey=uuid4().hex, title="بازدید پمپ", hall="فرایند", specialty="مکانیک",
                     period="ماهانه", activities=["کنترل نشتی", "کنترل لرزش"], reason="پیشنهاد برگه جدید")
        value.update(overrides)
        return value

    def published(self):
        return self.pm.create_template(self.users["head"], self.design())

    def assert_status(self, status, call):
        with self.assertRaises(ApiError) as raised:
            call()
        self.assertEqual(raised.exception.status, status)

    def decision(self, identifier, **overrides):
        body = dict(version=1, decision="approve", reason="برگه بررسی و تأیید شد")
        body.update(overrides)
        return self.pm.decide_change(self.users["head"], identifier, body)

    def test_proposals_leave_publication_unchanged_and_request_retries_are_exact(self):
        body = self.design()
        response = self.request("POST", "/api/pm/templates", "planner", body)
        self.assertEqual(response.status, 202)
        proposal = response.payload["changeRequest"]
        self.assertTrue(response.payload["pendingApproval"])
        self.assertIsNone(proposal["before"])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_templates"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_plans"), 0)
        again = self.request("POST", "/api/pm/templates", "planner", body).payload
        self.assertEqual(again["changeRequest"]["id"], proposal["id"])
        self.assertTrue(again["replayed"])
        self.assert_status(409, lambda: self.request("POST", "/api/pm/templates", "planner", dict(body, title="دیگر")))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='propose-pm-change'"), 1)

    def test_head_approval_publishes_once_and_manager_cannot_approve(self):
        proposal = self.request("POST", "/api/pm/templates", "manager", self.design()).payload["changeRequest"]
        for actor in ("manager", "planner", "admin", "sm", "tech"):
            self.assert_status(403, lambda actor=actor: self.request("POST", "/api/pm/changes/" + proposal["id"] + "/decision", actor,
                dict(version=1, decision="approve", reason="نباید منتشر شود")))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.decision(proposal["id"]), range(2)))
        self.assertTrue(all(value["status"] == "approved" for value in results))
        self.assertEqual(sum(bool(value.get("replayed")) for value in results), 1)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_templates"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_template_revisions"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_plans WHERE active=1"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='approve-pm-change'"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated"), 0)

    def test_before_after_snapshot_and_stale_publication_roll_back_decision(self):
        published = self.published()
        from pak_maintenance.pm_form_info import cells
        activity = next(cell for cell in cells(published) if cell.get("activity"))
        body = dict(version=1, requestKey=uuid4().hex, reason="اصلاح دستورالعمل بازدید",
                    changes=[dict(cellId=activity["id"], text="بررسی اصلاح‌شده")])
        proposal = self.request("PUT", "/api/pm/templates/" + published["id"], "planner", body).payload["changeRequest"]
        self.assertEqual(proposal["before"]["version"], 1)
        self.assertEqual(self.pm.template(self.users["planner"], published["id"])["version"], 1)
        self.pm.edit_template(self.users["head"], published["id"], dict(body, changes=[dict(cellId=activity["id"], text="اصلاح نهایی دیگر")]))
        self.assert_status(409, lambda: self.decision(proposal["id"]))
        row = self.pm.change_request(self.users["head"], proposal["id"])
        self.assertEqual((row["status"], row["version"]), ("pending", 1))
        self.assertEqual(self.pm.template(self.users["head"], published["id"])["version"], 2)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='approve-pm-change'"), 0)

    def test_rejection_never_publishes_and_pending_requests_are_private(self):
        proposal = self.request("POST", "/api/pm/templates", "planner", self.design()).payload["changeRequest"]
        self.assertEqual(self.pm.change_requests(self.users["planner2"])["total"], 0)
        self.assert_status(404, lambda: self.pm.change_request(self.users["planner2"], proposal["id"]))
        self.assertEqual(self.pm.change_requests(self.users["head"])["total"], 1)
        result = self.decision(proposal["id"], decision="reject", reason="به بازنگری فعالیت نیاز دارد")
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["result"])
        self.assert_status(409, lambda: self.decision(proposal["id"]))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_templates"), 0)

    def test_supervisor_scope_and_manager_library_operational_access(self):
        published = self.published()
        self.assertEqual(self.pm.templates(self.users["planner"])["total"], 1)
        self.assertEqual(self.pm.templates(self.users["manager"])["total"], 1)
        self.assert_status(403, lambda: self.pm.request_change(self.users["se"], "retire-template", published["id"],
                                    dict(version=1, requestKey=uuid4().hex, reason="حذف برگه خارج از تخصص")))
        self.assert_status(403, lambda: self.pm.request_change(self.users["sm"], "create-template", None, self.design(specialty="برق")))
        for actor in ("manager", "planner", "sm", "admin"):
            self.assert_status(403, lambda actor=actor: self.pm.create_template(self.users[actor], self.design()))
        self.assert_status(403, lambda: self.pm.stage_import(self.users["admin"], self.directory / "seed"))

    def test_approved_schedule_request_changes_only_after_head_review(self):
        published = self.published()
        plan_id = published["planId"]
        body = dict(version=1, requestKey=uuid4().hex, reason="تعیین برنامه بازدید ماهانه", assetId="a", supervisorId="sm",
                    startDate="2026-09-23", slots=[dict(month=7, week=1)], active=False, mappingConfirmed=True)
        proposal = self.request("PUT", "/api/pm/plans/" + plan_id, "planner", body).payload["changeRequest"]
        self.assertFalse(self.pm.plan(self.users["head"], plan_id)["mappingConfirmed"])
        decided = self.decision(proposal["id"])
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(decided["result"]["version"], 2)
        self.assertTrue(self.pm.plan(self.users["head"], plan_id)["mappingConfirmed"])
        self.assertFalse(self.pm.plan(self.users["head"], plan_id)["active"])

    def test_confidential_personnel_boundaries_cover_routes_catalogs_and_exports(self):
        for actor in ("planner", "manager", "sm", "tech"):
            own = self.request("GET", "/api/performance", actor, query={"scope": "own"}).payload
            self.assertEqual(own["scope"], "own")
            self.assertEqual({p["personId"] for p in own["people"]}, {actor})
            for path in ("/api/performance", "/api/reports/people.csv", "/api/users"):
                self.assert_status(403, lambda actor=actor, path=path: self.request("GET", path, actor, query={"scope": "all"}))
            self.assert_status(403, lambda actor=actor: report(self.services, Request("HEAD", "/api/reports/people.csv"), self.users[actor], "people"))
        for actor in ("planner", "manager"):
            for person in self.services.master.catalog(self.users[actor], "people", {"active": "false"})["items"]:
                self.assertEqual(set(person), {"id", "code", "name", "specialty"})
                self.assertIn(person["id"], ("sm", "se", "tech"))
            self.assert_status(403, lambda actor=actor: self.services.master.legacy(self.users[actor]))
            self.assert_status(403, lambda actor=actor: self.services.master.audit_log(self.users[actor]))
        self.assertEqual(self.request("GET", "/api/users", "head").status, 200)
        self.assertTrue(can_view_personnel_reports(self.users["head"]))
        self.assertFalse(can_view_personnel_reports(self.users["sm"]))
        self.assertTrue(can_view_grades(self.users["sm"], "مکانیک"))
        self.assertFalse(can_view_grades(self.users["sm"], "برق"))
        self.assertFalse(can_view_grades(self.users["admin"], "مکانیک"))

    def test_legacy_mutation_cannot_bypass_review_and_administration_audit_is_redacted(self):
        for actor in ("manager", "planner", "sm", "admin"):
            self.assert_status(403, lambda actor=actor: self.services.master.save_plan(self.users[actor], {}))
            self.assert_status(403, lambda actor=actor: self.request("POST", "/api/plans", actor, {}))
        self.published()
        self.assertTrue(all(row["details"] is None for row in self.services.master.audit_log(self.users["admin"])))
        self.assertTrue(any(row["details"] is not None for row in self.services.master.audit_log(self.users["head"])))

    def test_head_can_manage_personnel_and_reset_password_but_other_operational_roles_cannot(self):
        from pak_maintenance.security import Authentication, verify_password
        auth = Authentication(self.db)
        password = "Synthetic-New-Password-123!"
        auth.reset_password(self.users["head"], "tech", password)
        with self.db.connect() as c:
            self.assertTrue(verify_password(password, c.scalar("SELECT password_hash FROM users WHERE id='tech'")))
            self.assertEqual(c.scalar("SELECT session_epoch FROM users WHERE id='tech'"), 1)
        for actor in ("planner", "manager", "sm"):
            self.assert_status(403, lambda actor=actor: auth.reset_password(self.users[actor], "tech", password))
            self.assert_status(403, lambda actor=actor: self.services.master.save_user(self.users[actor], {}))
        self.assert_status(409, lambda: self.services.master.save_user(self.users["admin"], dict(
            code="head", name="رئیس", role="planner", specialty="", active=True, version=0), "head"))

    def test_summary_and_request_lists_omit_heavy_form_tables(self):
        proposal = self.request("POST", "/api/pm/templates", "planner", self.design()).payload["changeRequest"]
        row = self.pm.change_requests(self.users["head"])["items"][0]
        self.assertEqual(row["id"], proposal["id"])
        self.assertNotIn("before", row)
        self.assertEqual(row["payload"], {"title": "بازدید پمپ"})
        self.assertEqual(self.pm.change_request(self.users["head"], row["id"])["payload"]["activities"], ["کنترل نشتی", "کنترل لرزش"])
        summary = self.request("GET", "/api/pm/setup", "planner", query={"summary": "true"}).payload
        full = self.request("GET", "/api/pm/setup", "planner").payload
        self.assertNotIn("items", summary)
        self.assertEqual(summary["summary"], full["summary"])

    def test_activation_date_suggestion_tracks_tehran_clock_without_changing_stored_schedule(self):
        self.published()
        with self.db.connect() as c:
            before = c.query("SELECT id,start_date,version,body FROM pm_weekly_plans ORDER BY id")
        cases = ((datetime(2030, 12, 31, 22, 0, tzinfo=timezone.utc), "2031-01-01"),
                 (datetime(2031, 1, 1, 19, 0, tzinfo=timezone.utc), "2031-01-01"),
                 (datetime(2031, 1, 1, 22, 0, tzinfo=timezone.utc), "2031-01-02"))
        for instant, expected in cases:
            with self.subTest(instant=instant), patch("pak_maintenance.pm_setup.datetime") as clock:
                clock.now.return_value = instant
                result = self.services.setup.get(self.users["head"])
                self.assertEqual(result["today"], expected)
                self.assertEqual(result["defaultStartDate"], expected)
                self.assertEqual(result["items"][0]["startDate"], before[0]["start_date"])
                self.assertFalse(result["items"][0]["active"])
        with self.db.connect() as c:
            self.assertEqual(c.query("SELECT id,start_date,version,body FROM pm_weekly_plans ORDER BY id"), before)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences"), 0)


if __name__ == "__main__":
    unittest.main()
