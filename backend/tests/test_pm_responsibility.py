"""Issued PM routing correction in disposable, offline schema-9 databases."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import sqlite3
import unittest
from unittest.mock import patch

from pak_maintenance.common import ApiError, User, loads
from pak_maintenance.pm_responsibility import effective_owner
from pak_maintenance import pm_form_info as info
import test_pm_service as fixtures
import test_pm_governance as governance
from test_pm_service import ROOT, INSTANT


class PmResponsibilityTests(unittest.TestCase):
    tearDown = fixtures.PmTests.tearDown
    custom = fixtures.PmTests.custom
    activate = fixtures.PmTests.activate
    order_id = fixtures.PmTests.order_id

    def setUp(self):
        fixtures.PmTests.setUp(self)
        self.users["manager"] = User("manager", role="manager")
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) "
                      "VALUES('manager','manager','مدیر','manager','',1,'',0)")
        self.plan = self.activate()
        self.pm.generate(INSTANT)
        self.id = self.order_id()

    def transfer(self, actor="head", **changes):
        payload = dict(version=1, supervisorId="sm2", reason="اصلاح ارجاع مسئول برگه")
        payload.update(changes)
        return self.pm.transfer_responsibility(self.users[actor], self.id, payload)

    def assert_error(self, status, operation):
        with self.assertRaises(ApiError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status)

    def test_transfer_moves_inbox_and_permissions_preserves_identity_clears_assignee(self):
        assigned = self.pm.assign(self.users["sm"], self.id, dict(version=1, assigneeId="m1"))
        with self.db.connect() as c:
            original = c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", dict(id=self.id))
            original_body = loads(c.scalar("SELECT body FROM orders WHERE id=:id", dict(id=self.id)))
        preview = self.pm.responsibility(self.users["head"], self.id)
        self.assertTrue(preview["eligible"])
        result = self.transfer(version=assigned["version"])
        self.assertIsNone(result["assigneeId"])
        self.assertEqual((result["pmSupervisorId"], result["pmOriginalSupervisorId"], result["pmEffectiveSupervisorId"]), ("sm", "sm", "sm2"))
        self.assertEqual(result["pmWindow"], original_body["pmWindow"])
        self.assertEqual(self.pm.tasks(self.users["sm"])["total"], 0)
        self.assertEqual(self.pm.tasks(self.users["sm2"])["total"], 1)
        self.assertEqual(self.work.list_page(self.users["sm"])["total"], 0)
        self.assertEqual(self.work.list_page(self.users["sm2"])["total"], 1)
        self.assert_error(403, lambda: self.work.get(self.users["sm"], self.id))
        self.assert_error(403, lambda: self.work.get(self.users["m1"], self.id))
        self.assert_error(403, lambda: self.pm.assign(self.users["sm"], self.id, dict(version=result["version"], assigneeId="m2")))
        reassigned = self.pm.assign(self.users["sm2"], self.id, dict(version=result["version"], assigneeId="m2"))
        self.assertEqual(reassigned["assigneeId"], "m2")
        with self.db.connect() as c:
            self.assertEqual(c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", dict(id=self.id)), original)
            self.assertEqual(c.scalar("SELECT supervisor_id FROM pm_weekly_plans WHERE id=:id", dict(id=self.plan["id"])), "sm")
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_responsibility_transfers"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='transfer-weekly-pm-responsibility'"), 1)

    def test_roles_live_actor_target_specialty_active_and_reason_validation(self):
        for actor in ("admin", "sm", "m1"):
            self.assert_error(403, lambda actor=actor: self.transfer(actor))
            self.assert_error(403, lambda actor=actor: self.pm.responsibility(self.users[actor], self.id))
        for target in ("se", "m1", "missing"):
            self.assert_error(400, lambda target=target: self.transfer(supervisorId=target))
        self.assert_error(400, lambda: self.transfer(reason=" "))
        self.assert_error(400, lambda: self.transfer(version=True))
        with self.db.transaction() as c:
            c.execute("UPDATE users SET active=0 WHERE id='sm2'")
        self.assert_error(400, lambda: self.transfer())
        with self.db.transaction() as c:
            c.execute("UPDATE users SET active=1 WHERE id='sm2'")
            c.execute("UPDATE users SET session_epoch=1 WHERE id='head'")
        self.assert_error(401, lambda: self.transfer())
        result = self.transfer("manager")
        self.assertEqual(result["pmEffectiveSupervisorId"], "sm2")

    def test_stale_duplicate_chained_transfer_and_atomic_rollback(self):
        self.assert_error(409, lambda: self.transfer(version=99))
        self.assert_error(409, lambda: self.transfer(supervisorId="sm"))
        with patch("pak_maintenance.pm_responsibility.audit", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.transfer()
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_responsibility_transfers"), 0)
            self.assertEqual(c.scalar("SELECT version FROM orders WHERE id=:id", dict(id=self.id)), 1)
            self.assertEqual(effective_owner(c, self.id), "sm")
        moved = self.transfer()
        self.assert_error(409, lambda: self.transfer())
        returned = self.transfer(version=moved["version"], supervisorId="sm")
        self.assertEqual(returned["pmEffectiveSupervisorId"], "sm")
        history = self.pm.responsibility(self.users["head"], self.id)["history"]
        self.assertEqual([(h["fromSupervisorId"], h["toSupervisorId"]) for h in history], [("sm2", "sm"), ("sm", "sm2")])

    def test_concurrent_transfers_commit_once(self):
        def move(_):
            try:
                return self.transfer()["version"]
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(move, range(2))), [2, 409])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_responsibility_transfers"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='transfer-weekly-pm-responsibility'"), 1)

    def execution(self, transferred=False):
        version = self.transfer()["version"] if transferred else 1
        owner = "sm2" if transferred else "sm"
        assigned = self.pm.assign(self.users[owner], self.id, dict(version=version, assigneeId="m1"))
        body = deepcopy(assigned)
        body.update(description="بازدید انجام شد", workDate="2026-09-26", startTime="08:00", endTime="09:00",
                    participants=[dict(personId="m1", minutes=60)], parts=[], checklist=[],
                    pmResults={cell["id"]: "سالم" for cell in info.cells(assigned["pmSheet"]) if cell.get("editable")})
        return self.work.save(self.users["m1"], body, self.id)

    def test_started_submitted_returned_completed_rejected_and_archive_freezes_both_owners(self):
        saved = self.execution(True)
        self.assertFalse(self.pm.responsibility(self.users["head"], self.id)["eligible"])
        self.assert_error(409, lambda: self.transfer(version=saved["version"], supervisorId="sm"))
        submitted = self.work.submit(self.users["m1"], self.id, saved["version"])
        self.assertFalse(self.work.get(self.users["head"], self.id)["pmCanTransferResponsibility"])
        self.assert_error(403, lambda: self.work.decision(self.users["sm"], self.id, dict(version=submitted["version"], decision="return", reason="بررسی مجدد")))
        self.assertEqual(self.work.inbox_page(self.users["sm2"])["total"], 1)
        self.assertEqual(self.work.inbox_page(self.users["sm"])["total"], 0)
        self.assert_error(400, lambda: self.work.decision(self.users["sm2"], self.id, dict(version=submitted["version"], decision="return", reason="اصلاح گزارش")))
        from historical_workflow_fixtures import seed_historical_return
        returned = seed_historical_return(self.db, self.work, self.users["sm2"], submitted, 'اصلاح گزارش در نسخهٔ قدیمی')
        self.assert_error(409, lambda: self.transfer(version=returned["version"], supervisorId="sm"))
        again = self.work.submit(self.users["m1"], self.id, returned["version"])
        head = self.work.decision(self.users["sm2"], self.id, dict(version=again["version"], decision="approve", grades=[dict(personId="m1", coefficient=Decimal("2"))]))
        done = self.work.decision(self.users["head"], self.id, dict(version=head["version"], decision="approve", participantScores=[dict(personId="m1", coefficient=Decimal("3"))]))
        self.assert_error(409, lambda: self.transfer(version=done["version"], supervisorId="sm"))
        with self.db.connect() as c:
            entries = c.query("SELECT supervisor_id,body FROM pm_submission_archive WHERE order_id=:id ORDER BY workflow_revision", dict(id=self.id))
            self.assertEqual(len(entries), 2)
            for entry in entries:
                frozen = loads(entry["body"])["responsibility"]
                self.assertEqual((entry["supervisor_id"], frozen["originalSupervisorId"], frozen["effectiveSupervisorId"]), ("sm", "sm", "sm2"))
                self.assertEqual(len(frozen["transferIds"]), 1)

    def test_nonempty_results_without_participant_are_rejected_and_sql_guards_are_immutable(self):
        with self.db.transaction() as c:
            body = loads(c.scalar("SELECT body FROM orders WHERE id=:id", dict(id=self.id)))
            body["pmResults"] = {"synthetic": "ثبت نتیجه"}
            from pak_maintenance.common import dumps
            c.execute("UPDATE orders SET body=:body WHERE id=:id", dict(id=self.id, body=dumps(body)))
        self.assert_error(409, lambda: self.transfer())
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as c:
                c.execute("INSERT INTO pm_responsibility_transfers VALUES('x',:id,1,'sm','sm2','head','علت آزمایشی','2026-11-02')", dict(id=self.id))
        with self.db.transaction() as c:
            body["pmResults"] = {}
            c.execute("UPDATE orders SET body=:body WHERE id=:id", dict(id=self.id, body=dumps(body)))
        self.transfer()
        for sql in ("UPDATE pm_responsibility_transfers SET reason='tamper'", "DELETE FROM pm_responsibility_transfers", "UPDATE pm_weekly_occurrences SET supervisor_id='sm2'"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with self.db.transaction() as c:
                    c.execute(sql)

    def test_database_rejects_unauthorized_actor_target_owner_and_version(self):
        sql = ("INSERT INTO pm_responsibility_transfers(id,order_id,order_version,from_supervisor_id,"
               "to_supervisor_id,actor_id,reason,created_at) VALUES('forged',:id,:version,:previous,:target,:actor,'علت آزمایشی','2026-11-02')")
        for changes in (dict(actor='admin'), dict(actor='sm'), dict(target='se'), dict(target='m1'),
                        dict(previous='sm2'), dict(version=2)):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                with self.db.transaction() as c:
                    c.execute(sql, {'id': self.id, 'version': 1, 'previous': 'sm', 'target': 'sm2', 'actor': 'head', **changes})
        with self.db.transaction() as c:
            c.execute("UPDATE users SET active=0 WHERE id='sm2'")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as c:
                c.execute(sql, dict(id=self.id, version=1, previous='sm', target='sm2', actor='head'))
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM pm_responsibility_transfers'), 0)
            self.assertEqual(c.scalar('SELECT version FROM orders WHERE id=:id', dict(id=self.id)), 1)


class PmResponsibilityPostgresqlTests(unittest.TestCase):
    """Same acceptance through the real guarded PostgreSQL API boundary."""
    tearDown = governance.PmGovernanceTests.tearDown
    design = staticmethod(governance.PmGovernanceTests.design)

    def request(self, *args, **kwargs):
        # Match the framework adapter: domain denials become HTTP results.
        from pak_maintenance.api import safe_error
        try:
            return governance.PmGovernanceTests.request(self, *args, **kwargs)
        except ApiError as error:
            return safe_error(error)

    def setUp(self):
        governance.PmGovernanceTests.setUp(self)
        self.users["sm2"] = User("sm2", "sm2", "سرپرست جدید آزمایشی", "supervisor", "مکانیک")
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) "
                      "VALUES('sm2','sm2','سرپرست جدید آزمایشی','supervisor','مکانیک',1,'',0)")
        template = self.pm.create_template(self.users["head"], self.design())
        self.pm.edit_plan(self.users["head"], template["planId"], dict(version=1, reason="تأیید آزمایشی", assetId="a",
            supervisorId="sm", startDate="2026-09-23", slots=[dict(month=7, week=1)], active=True, mappingConfirmed=True), INSTANT)
        self.pm.generate(INSTANT)
        with self.db.connect() as c:
            self.id = c.scalar("SELECT order_id FROM pm_weekly_occurrences")

    def test_full_api_transfer_execution_approval_and_frozen_archive(self):
        path = "/api/pm/tasks/" + self.id + "/responsibility"
        self.assertEqual(self.request("GET", path, "head").payload["supervisorId"], "sm")
        moved = self.request("POST", path, "manager", dict(version=1, supervisorId="sm2", reason="اصلاح کارتابل آزمایشی")).payload
        self.assertEqual(moved["pmEffectiveSupervisorId"], "sm2")
        self.assertEqual(self.request("GET", "/api/orders/" + self.id, "sm").status, 403)
        denied = self.request("POST", "/api/pm/tasks/" + self.id + "/assign", "sm", dict(version=moved["version"], assigneeId="tech"))
        self.assertEqual(denied.status, 403)
        assigned = self.pm.assign(self.users["sm2"], self.id, dict(version=moved["version"], assigneeId="tech"))
        body = deepcopy(assigned)
        body.update(description="بازدید کامل انجام شد", workDate="2026-09-26", startTime="08:00", endTime="09:00",
            participants=[dict(personId="tech", minutes=60)], parts=[], checklist=[],
            pmResults={cell["id"]: "سالم" for cell in info.cells(assigned["pmSheet"]) if cell.get("editable")})
        work = self.services.work
        saved = work.save(self.users["tech"], body, self.id)
        submitted = work.submit(self.users["tech"], self.id, saved["version"])
        self.assertEqual(work.inbox_page(self.users["sm"])["total"], 0)
        self.assertEqual(work.inbox_page(self.users["sm2"])["total"], 1)
        head = work.decision(self.users["sm2"], self.id, dict(version=submitted["version"], decision="approve", grades=[dict(personId="tech", coefficient=Decimal("2"))]))
        done = work.decision(self.users["head"], self.id, dict(version=head["version"], decision="approve", participantScores=[dict(personId="tech", coefficient=Decimal("3"))]))
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.request("POST", path, "head", dict(version=done["version"], supervisorId="sm", reason="تغییر نامجاز سابقه")).status, 409)
        archive = self.services.archive
        archived_id = archive.list_page(self.users["planner"])["items"][0]["id"]
        before = archive.detail(self.users["planner"], archived_id)
        with self.db.transaction() as c:
            c.execute("UPDATE users SET name='نام فعلی تغییرکرده' WHERE id IN ('sm','sm2')")
        frozen = archive.detail(self.users["planner"], archived_id)
        self.assertEqual(frozen, before)
        responsibility = frozen["responsibility"]
        self.assertEqual((responsibility["originalSupervisorId"], responsibility["effectiveSupervisorId"]), ("sm", "sm2"))
        self.assertEqual(responsibility["effectiveSupervisorName"], "سرپرست جدید آزمایشی")
        self.assertEqual(len(responsibility["transferIds"]), 1)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT supervisor_id FROM pm_weekly_occurrences WHERE order_id=:id", dict(id=self.id)), "sm")
            self.assertEqual(c.scalar("SELECT supervisor_id FROM pm_submission_archive WHERE id=:id", dict(id=archived_id)), "sm")

    def test_concurrency_one_commit_and_audit_failure_rolls_back_on_postgresql(self):
        def move(_):
            return self.request("POST", "/api/pm/tasks/" + self.id + "/responsibility", "head",
                                dict(version=1, supervisorId="sm2", reason="انتقال هم‌زمان آزمایشی")).status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(move, range(2))), [200, 409])
        with patch("pak_maintenance.pm_responsibility.audit", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.pm.transfer_responsibility(self.users["head"], self.id, dict(version=2, supervisorId="sm", reason="آزمون بازگشت اتمی"))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_responsibility_transfers"), 1)
            self.assertEqual(c.scalar("SELECT version FROM orders WHERE id=:id", dict(id=self.id)), 2)
            self.assertEqual(effective_owner(c, self.id), "sm2")
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='transfer-weekly-pm-responsibility'"), 1)

    def test_native_routing_guards_reject_forged_transfer_and_history_rewrites(self):
        sql = ("INSERT INTO pm_responsibility_transfers(id,order_id,order_version,from_supervisor_id,"
               "to_supervisor_id,actor_id,reason,created_at) VALUES('forged',:id,:version,:previous,:target,:actor,'علت آزمایشی','2026-11-02')")
        for changes in (dict(actor='admin'), dict(actor='sm'), dict(target='se'), dict(target='tech'),
                        dict(previous='sm2'), dict(version=2)):
            with self.subTest(changes=changes), self.assertRaises(Exception) as rejected:
                with self.db.transaction() as c:
                    c.execute(sql, {'id': self.id, 'version': 1, 'previous': 'sm', 'target': 'sm2', 'actor': 'head', **changes})
            self.assertEqual(getattr(rejected.exception, 'sqlstate', None), '23514')
        self.pm.transfer_responsibility(self.users['head'], self.id,
            dict(version=1, supervisorId='sm2', reason='اصلاح ارجاع آزمایشی'))
        for sql in ("UPDATE pm_responsibility_transfers SET reason='tamper'", "DELETE FROM pm_responsibility_transfers",
                    "UPDATE pm_weekly_occurrences SET supervisor_id='sm2'"):
            with self.subTest(sql=sql), self.assertRaises(Exception) as rejected:
                with self.db.transaction() as c:
                    c.execute(sql)
            self.assertEqual(getattr(rejected.exception, 'sqlstate', None), '23514')
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM pm_responsibility_transfers'), 1)
            self.assertEqual(effective_owner(c, self.id), 'sm2')


if __name__ == "__main__":
    unittest.main()
