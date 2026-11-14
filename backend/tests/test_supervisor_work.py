"""Supervisor-performed work on disposable fixtures with production evidence guards."""
from decimal import Decimal
from pathlib import Path
import sqlite3
import unittest

from pak_maintenance.common import ApiError, User
from pak_maintenance.performance_service import PerformanceService
from pak_maintenance.reporting_service import ReportingService
import test_workflow as workflow

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / 'schema/sqlite-v10.sql').read_text(encoding='utf-8')
GUARDS = '\n'.join(p.read_text(encoding='utf-8') for p in sorted((ROOT / 'schema').glob('sqlite-*-guards.sql')))


class SupervisorWorkTests(unittest.TestCase):
    schema = BASE + '\n' + GUARDS
    setUp = workflow.WorkflowCase.setUp
    tearDown = workflow.WorkflowCase.tearDown
    body = workflow.WorkflowCase.body
    grade = workflow.WorkflowCase.grade
    head_body = staticmethod(workflow.WorkflowCase.head_body)

    def submitted_by_supervisor(self, people=('sm',)):
        body = self.body(people)
        body['participants'][0]['gradingPolicy'] = 'two-stage-v1'  # Client cannot choose the policy.
        saved = self.work.save(self.users['sm'], body)
        return self.work.submit(self.users['sm'], saved['id'], saved['version'])

    def finalize(self, row, grades):
        return self.work.decision(self.users['head'], row['id'], {'version': row['version'], 'decision': 'approve',
            'participantScores': [{'personId': key, 'coefficient': str(value)} for key, value in grades.items()]})

    def test_supervisor_goes_directly_to_head_and_keeps_real_evidence(self):
        row = self.submitted_by_supervisor()
        self.assertEqual(row['status'], 'head')
        self.assertEqual(row['pendingSupervisorSpecialties'], [])
        self.assertEqual(self.work.inbox_page(self.users['sm'])['total'], 0)
        self.assertEqual(self.work.inbox_page(self.users['head'])['total'], 1)
        self.assertTrue(self.work.get(self.users['head'], row['id'])['canDecide'])
        row = self.finalize(row, {'sm': Decimal('8.5')})
        self.assertEqual(row['score'], Decimal('8.50'))
        self.assertEqual(row['participants'][0]['gradingPolicy'], 'head-only-v1')
        self.assertEqual(row['participants'][0]['headGradeMaximum'], 9)
        self.assertIsNone(row['participants'][0]['supervisorCoefficient'])
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_scores'), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='supervisor'"), 0)
            evidence = c.one('SELECT * FROM completed_scores')
            self.assertEqual(evidence['source'], 'head-only')
            self.assertEqual(evidence['score_cents'], 850)
            self.assertIsNone(evidence['supervisor_coefficient'])
        report = PerformanceService(self.db).report(self.users['head'], period='all')
        self.assertEqual(report['totals']['unscoredCount'], 0)
        self.assertEqual(report['totals']['score'], row['score'])
        self.assertEqual(ReportingService(self.db, self.work).dashboard(self.users['head'])['score'], row['score'])
        private = self.work.get(self.users['sm'], row['id'])
        self.assertIsNone(private['score'])
        self.assertEqual(private['participantHeadScores'], [])

    def test_zero_nine_and_rejected_out_of_range_are_atomic(self):
        for value in ('0', '9'):
            with self.subTest(value=value):
                row = self.submitted_by_supervisor()
                for invalid in ('9.01', '-1', 'NaN', 'Infinity', '1e-29'):
                    with self.assertRaises(ApiError):
                        self.finalize(row, {'sm': invalid})
                    with self.db.connect() as c:
                        self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_head_scores WHERE order_id=:id', {'id': row['id']}), 0)
                        self.assertEqual(c.scalar("SELECT status FROM orders WHERE id=:id", {'id': row['id']}), 'head')
                completed = self.finalize(row, {'sm': value})
                self.assertEqual(completed['score'], Decimal(value).quantize(Decimal('0.01')))
                with self.assertRaises(ApiError) as stale:
                    self.finalize(row, {'sm': value})
                self.assertEqual(stale.exception.status, 409)

    def test_mixed_report_grades_only_technicians_in_supervisor_stage(self):
        row = self.submitted_by_supervisor(('sm', 'm1', 'e1'))
        self.assertEqual(row['status'], 'supervisor')
        with self.assertRaises(ApiError) as invalid:
            self.grade(row, 'sm', [('m1', 2), ('sm', 3)])
        self.assertEqual(invalid.exception.status, 403)
        row = self.grade(row, 'sm', [('m1', 2)])
        self.assertEqual(row['status'], 'supervisor')
        self.assertEqual(row['pendingSupervisorSpecialties'], ['برق'])
        row = self.grade(row, 'se', [('e1', 1)])
        self.assertEqual(row['status'], 'head')
        with self.assertRaises(ApiError):
            self.finalize(row, {'sm': 9, 'm1': '3.01', 'e1': 3})
        row = self.finalize(row, {'sm': 9, 'm1': 3, 'e1': 3})
        self.assertEqual(row['score'], Decimal('18.00'))
        details = PerformanceService(self.db).report(self.users['head'], period='all')
        self.assertEqual(details['totals']['score'], Decimal('18.00'))
        self.assertEqual(details['totals']['unscoredCount'], 0)
        self.assertEqual({p['personId']: p['score'] for p in details['details']}, {'sm': Decimal('9.00'), 'm1': Decimal('6.00'), 'e1': Decimal('3.00')})

    def test_account_changes_do_not_reinterpret_submitted_policy(self):
        row = self.submitted_by_supervisor()
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='technician' WHERE id='sm'")
        completed = self.finalize(row, {'sm': 9})
        self.assertEqual(completed['score'], Decimal('9.00'))
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='supervisor' WHERE id='sm'")
        self.assertEqual(self.work.get(self.users['head'], row['id'])['score'], Decimal('9.00'))

    def test_promoted_participant_cannot_grade_own_work_as_head(self):
        row = self.submitted_by_supervisor()
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='head',session_epoch=1 WHERE id='sm'")
        promoted = User('sm', '4', 'سرپرست مکانیک', 'head', 'مکانیک', session_epoch=1)
        with self.assertRaises(ApiError) as rejected:
            self.work.decision(promoted, row['id'], {'version': row['version'], 'decision': 'approve',
                'participantScores': [{'personId': 'sm', 'coefficient': '9'}]})
        self.assertEqual(rejected.exception.status, 403)
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_head_scores'), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)
        with self.assertRaises(Exception) as db_rejected:
            with self.db.transaction() as c:
                c.execute("INSERT INTO approvals VALUES('self-head',:id,:rev,'head','','approve',NULL,'','sm','2026-11-02')",
                          {'id': row['id'], 'rev': row['workflowRevision']})
                c.execute("INSERT INTO participant_head_scores VALUES(:id,:rev,'sm','مکانیک','9','sm','self-head','2026-11-02')",
                          {'id': row['id'], 'rev': row['workflowRevision']})
        if self.db.config.provider == 'sqlite':
            self.assertIsInstance(db_rejected.exception, sqlite3.IntegrityError)
        else:
            self.assertEqual(getattr(db_rejected.exception, 'sqlstate', None), '23514')
        self.assertEqual(self.finalize(row, {'sm': 9})['score'], Decimal('9.00'))

    def test_historical_return_and_resubmit_preserve_old_policy_and_use_new_performer_role(self):
        row = self.submitted_by_supervisor()
        from historical_workflow_fixtures import seed_historical_return
        with self.assertRaises(ApiError) as denied:
            self.work.decision(self.users['head'], row['id'], {'version': row['version'], 'decision': 'return', 'reason': 'اصلاح شرح کار'})
        self.assertEqual(denied.exception.status, 400)
        row = seed_historical_return(self.db, self.work, self.users['head'], row, 'اصلاح شرح کار در نسخهٔ قدیمی')
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='technician',session_epoch=1 WHERE id='sm'")
        actor = User('sm', '4', 'سرپرست مکانیک', 'technician', 'مکانیک', session_epoch=1)
        row = self.work.submit(actor, row['id'], row['version'])
        self.assertEqual(row['status'], 'supervisor')
        with self.db.connect() as c:
            policies = c.query("SELECT policy FROM participant_grading_policies WHERE order_id=:id ORDER BY revision", {'id': row['id']})
        self.assertEqual([p['policy'] for p in policies], ['head-only-v1', 'two-stage-v1'])

    def test_policy_history_and_self_grading_are_guarded_at_database_boundary(self):
        row = self.submitted_by_supervisor(('sm', 'm1'))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as c:
                c.execute("UPDATE participant_grading_policies SET policy='two-stage-v1' WHERE person_id='sm'")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as c:
                c.execute("INSERT INTO approvals VALUES('self',:id,:rev,'supervisor','مکانیک','approve',NULL,'','sm','2026-11-02')", {'id': row['id'], 'rev': row['workflowRevision']})
                c.execute("INSERT INTO participant_scores VALUES(:id,:rev,'sm','مکانیک',3,'sm','self','2026-11-02')", {'id': row['id'], 'rev': row['workflowRevision']})
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE id='self'"), 0)

    def test_grade_policy_controls_storage_limits_not_current_account_role(self):
        row = self.submitted_by_supervisor(('m1',))
        row = self.grade(row, 'sm', [('m1', 2)])
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='supervisor' WHERE id='m1'")
        with self.assertRaises(ApiError):
            self.finalize(row, {'m1': 9})
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as c:
                c.execute("INSERT INTO approvals VALUES('manual',:id,:rev,'head','','approve',NULL,'','head','2026-11-02')", {'id': row['id'], 'rev': row['workflowRevision']})
                c.execute("INSERT INTO participant_head_scores VALUES(:id,:rev,'m1','مکانیک','9','head','manual','2026-11-02')", {'id': row['id'], 'rev': row['workflowRevision']})
        completed = self.finalize(row, {'m1': 3})
        self.assertEqual(completed['score'], Decimal('6.00'))
        self.assertEqual(completed['participants'][0]['gradingPolicy'], 'two-stage-v1')


if __name__ == '__main__':
    unittest.main()

class SupervisorWorkPostgresqlTests(unittest.TestCase):
    """Opt-in real PostgreSQL; fresh_database refuses any company target."""
    body = workflow.WorkflowCase.body
    grade = workflow.WorkflowCase.grade
    head_body = staticmethod(workflow.WorkflowCase.head_body)
    submitted_by_supervisor = SupervisorWorkTests.submitted_by_supervisor
    finalize = SupervisorWorkTests.finalize
    test_promoted_participant_cannot_grade_own_work_as_head = SupervisorWorkTests.test_promoted_participant_cannot_grade_own_work_as_head

    def setUp(self):
        from pak_maintenance.database import Database
        from pak_maintenance.work_service import WorkService
        from pg_test_support import fresh_database
        config, self.owner_db = fresh_database(self, prefix='supwork')
        self.db = Database(config)
        self.users = {u.id: u for u in [User('m1','1','تکنسین مکانیک','technician','مکانیک'),
            User('e1','2','تکنسین برق','technician','برق'), User('sm','3','سرپرست مکانیک','supervisor','مکانیک'),
            User('se','4','سرپرست برق','supervisor','برق'), User('head','5','رئیس','head')]}
        with self.db.transaction() as c:
            for u in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:spec,1,'',0)", {'id':u.id,'code':u.code,'name':u.name,'role':u.role,'spec':u.specialty})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','A01','پمپ','سالن','خط','{}')")
            c.execute("INSERT INTO parts(id,code,name,unit,specification) VALUES('part','P001','بلبرینگ','عدد','')")
        self.work = WorkService(self.db)

    def test_direct_head_real_guards_and_report_totals(self):
        row = self.submitted_by_supervisor()
        self.assertEqual(row['status'], 'head')
        self.assertEqual(self.work.inbox_page(self.users['head'])['total'], 1)
        row = self.finalize(row, {'sm': '8.75'})
        self.assertEqual(row['score'], Decimal('8.75'))
        with self.db.connect() as c:
            fact = c.one('SELECT * FROM completed_scores')
            self.assertEqual(fact['source'], 'head-only')
            self.assertIsNone(fact['supervisor_coefficient'])
            self.assertEqual(fact['score_cents'], 875)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_scores'), 0)
        self.assertEqual(PerformanceService(self.db).report(self.users['head'], period='all')['totals']['unscoredCount'], 0)
        self.assertEqual(ReportingService(self.db, self.work).dashboard(self.users['head'])['score'], Decimal('8.75'))

    def test_mixed_native_policy_limits_and_atomic_failure(self):
        row = self.submitted_by_supervisor(('sm', 'm1', 'e1'))
        row = self.grade(row, 'sm', [('m1', 2)])
        row = self.grade(row, 'se', [('e1', 1)])
        with self.assertRaises(ApiError):
            self.finalize(row, {'sm': 9, 'm1': '3.01', 'e1': 3})
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_head_scores'), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE stage='head'"), 0)
        row = self.finalize(row, {'sm': 9, 'm1': 3, 'e1': 3})
        self.assertEqual(row['score'], Decimal('18.00'))
        self.assertEqual(PerformanceService(self.db).report(self.users['head'], period='all')['totals']['score'], Decimal('18.00'))

    def test_native_policy_immutability_and_grade_rejection(self):
        row = self.submitted_by_supervisor(('m1',))
        row = self.grade(row, 'sm', [('m1', 2)])
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='supervisor' WHERE id='m1'")
        for operation in ('policy', 'grade'):
            with self.subTest(operation=operation):
                with self.assertRaises(Exception) as failed:
                    with self.db.transaction() as c:
                        if operation == 'policy':
                            c.execute("UPDATE participant_grading_policies SET policy='head-only-v1' WHERE person_id='m1'")
                        else:
                            c.execute("INSERT INTO approvals VALUES('manual',:id,:rev,'head','','approve',NULL,'','head','2026-11-02')", {'id':row['id'],'rev':row['workflowRevision']})
                            c.execute("INSERT INTO participant_head_scores VALUES(:id,:rev,'m1','مکانیک','9','head','manual','2026-11-02')", {'id':row['id'],'rev':row['workflowRevision']})
                self.assertEqual(getattr(failed.exception, 'sqlstate', None), '23514')
        self.assertEqual(self.finalize(row, {'m1': 3})['score'], Decimal('6.00'))
