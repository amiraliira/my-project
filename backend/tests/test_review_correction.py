"""Supervisor corrections use fresh audited revisions and real final ledgers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pak_maintenance.api import Api, Request
from pak_maintenance.common import ApiError, User, audit, dumps, loads
from pak_maintenance.database import Database
from pak_maintenance.performance_service import PerformanceService
from pak_maintenance.pm_archive import PmArchiveService
from pak_maintenance.work_service import WorkService
import test_workflow as fixtures
import test_supervisor_work as supervisor
import test_pm_service as pm_fixture

ROOT = Path(__file__).resolve().parents[1]


class ReviewCorrectionTests(unittest.TestCase):
    schema = (ROOT / 'schema/sqlite-v10.sql').read_text(encoding='utf-8') + '\n' + supervisor.GUARDS
    tearDown = fixtures.WorkflowCase.tearDown
    body = fixtures.WorkflowCase.body
    grade = fixtures.WorkflowCase.grade
    submitted_by_supervisor = supervisor.SupervisorWorkTests.submitted_by_supervisor
    finalize = supervisor.SupervisorWorkTests.finalize

    def setUp(self):
        fixtures.WorkflowCase.setUp(self)
        self.add_part()

    def add_part(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO parts(id,code,name,unit,specification) VALUES('part-new','P002','روغن','لیتر','')")

    def row(self):
        return self.submitted_by_supervisor(('sm', 'm1', 'm2', 'e1'))

    def corrected(self, row, **changes):
        payload = dict(version=row['version'], reason='اصلاح ساعت واقعی و قطعات مصرفی',
                       participants=[dict(personId='m1', minutes=45)],
                       grades=[dict(personId=p['personId'], coefficient=3 if p['personId']=='m1' else 2)
                           for p in row['participants'] if p['specialty']=='مکانیک' and p.get('gradingPolicy')!='head-only-v1'])
        payload.update(changes)
        return self.work.review_correct(self.users['sm'], row['id'], payload)

    def denied(self, status, callback):
        with self.assertRaises(ApiError) as error:
            callback()
        self.assertEqual(error.exception.status, status)

    def test_atomic_correction_approves_own_and_carries_other_unit_without_re_review(self):
        row = self.row()
        self.assertTrue(self.work.get(self.users['sm'], row['id'])['canReviewCorrect'])
        row = self.grade(row, 'se', [('e1', 1)])
        previous = row['workflowRevision']
        changed = self.corrected(row, endTime='09:30', participants=[dict(personId='m1', minutes=75), dict(personId='sm', minutes=30)],
                                 parts=[dict(partId='part-new', quantity='2.500001')])
        self.assertEqual(changed['workflowRevision'], previous + 1)
        self.assertEqual(changed['status'], 'head')
        self.assertEqual(changed['durationMinutes'], 90)
        self.assertEqual({p['personId']: p['minutes'] for p in changed['participants']}, {'sm': 30, 'm1': 75, 'm2': 60, 'e1': 60})
        self.assertEqual(changed['pendingSupervisorSpecialties'], [])
        self.assertFalse(changed['canReviewCorrect'])
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM approvals WHERE revision=:rev', {'rev': previous}), 1)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_scores WHERE revision=:rev', {'rev': previous}), 1)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM participant_scores WHERE revision=:rev', {'rev': previous + 1}), 3)
            event = loads(c.scalar("SELECT details FROM audit WHERE action='review-correction'"))
            self.assertEqual(event['before']['durationMinutes'], 60)
            self.assertEqual(event['after']['durationMinutes'], 90)
            self.assertEqual(event['fromRevision'], previous)
            carried = loads(c.scalar("SELECT details FROM audit WHERE action='carry-supervisor-approval'"))
            self.assertEqual(carried['sourceRevision'], previous)
            self.assertEqual(carried['sourceActorId'], 'se')
            self.assertEqual(c.scalar("SELECT actor_id FROM audit WHERE action='carry-supervisor-approval'"), 'sm')
        copy = next(a for a in changed['approvals'] if a['specialty']=='برق')
        self.assertEqual(copy['carriedFromRevision'], previous)
        self.assertEqual(copy['actorName'], self.users['se'].name)
        self.assertEqual(copy['carriedByName'], self.users['sm'].name)
        done = self.finalize(changed, {'sm': 9, 'm1': 2, 'm2': 3, 'e1': 1})
        self.assertEqual(done['score'], Decimal('19.00'))
        own = PerformanceService(self.db).report(self.users['sm'], period='all')
        self.assertEqual(own['totals']['score'], Decimal('4.50'))
        with self.db.connect() as c:
            consumed = c.query('SELECT part_id,quantity_units FROM part_consumptions')
            self.assertEqual(consumed, [dict(part_id='part-new', quantity_units=2500001)])
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM completed_scores WHERE revision=:rev', {'rev': previous}), 0)
        self.denied(409, lambda: self.corrected(done))

    def test_parts_can_be_added_increased_removed_and_cleared_without_stock_movement(self):
        increased = self.corrected(self.row(), participants=[], parts=[dict(partId='part', quantity='3.000001'), dict(partId='part-new', quantity='0.25')])
        self.assertEqual({p['partId']: p['quantity'] for p in increased['parts']}, {'part': Decimal('3.000001'), 'part-new': Decimal('0.25')})
        removed = self.corrected(self.row(), participants=[], parts=[dict(partId='part-new', quantity='0.25')])
        self.assertEqual([p['partId'] for p in removed['parts']], ['part-new'])
        cleared = self.corrected(self.row(), participants=[], parts=[])
        self.assertEqual(cleared['parts'], [])
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM stock_movements'), 0)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM part_consumptions'), 0)

    def test_successive_specialty_corrections_preserve_original_carried_actor_after_rename(self):
        self.users['t1'] = User('t1', 'R12-T1', 'مجری انرژی', 'technician', 'انرژی')
        self.users['st'] = User('st', 'R12-ST', 'سرپرست انرژی', 'supervisor', 'انرژی')
        with self.db.transaction() as c:
            for person in (self.users['t1'], self.users['st']):
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:spec,1,'',0)",
                    dict(id=person.id, code=person.code, name=person.name, role=person.role, spec=person.specialty))
        row = self.grade(self.submitted_by_supervisor(('m1', 'e1', 't1')), 'se', [('e1', '1.25')])
        original_approval = next(a for a in row['approvals'] if a['specialty'] == 'برق')
        with self.db.transaction() as c:
            audit(c, self.users['se'], 'approve', 'order', row['id'],
                  dict(revision=row['workflowRevision'], reason='سابقهٔ قدیمی بدون پیوند شناسهٔ تأیید'))
            c.execute("UPDATE users SET name='نام تغییرکرده پیش از نخستین اصلاح' WHERE id='se'")
        first = self.corrected(row)
        self.assertEqual(first['status'], 'supervisor')
        self.assertEqual(first['pendingSupervisorSpecialties'], ['انرژی'])
        first_copy = next(a for a in first['approvals'] if a['specialty'] == 'برق')
        self.assertEqual(first_copy['actorName'], original_approval['actorName'])
        with self.db.transaction() as c:
            c.execute("UPDATE users SET name='نام تازهٔ سرپرست برق' WHERE id='se'")
        second = self.work.review_correct(self.users['st'], row['id'],
            dict(version=first['version'], reason='اصلاح دقیقهٔ واقعی واحد انرژی',
                 participants=[dict(personId='t1', minutes=30)], grades=[dict(personId='t1', coefficient=2)]))
        final_copy = next(a for a in second['approvals'] if a['specialty'] == 'برق')
        self.assertEqual(second['status'], 'head')
        self.assertEqual(second['pendingSupervisorSpecialties'], [])
        self.assertEqual(final_copy['actorName'], original_approval['actorName'])
        self.assertEqual(final_copy['createdAt'], original_approval['createdAt'])
        self.assertEqual(final_copy['carriedFromApprovalId'], first_copy['id'])
        self.assertEqual(final_copy['carriedByName'], self.users['st'].name)
        with self.db.connect() as c:
            score = c.one("SELECT actor_id,created_at,coefficient FROM participant_scores WHERE order_id=:id AND revision=:rev AND person_id='e1'",
                dict(id=row['id'], rev=second['workflowRevision']))
            self.assertEqual(score, dict(actor_id='se', created_at=original_approval['createdAt'], coefficient=Decimal('1.25')))
            events = [loads(e['details']) for e in c.query("SELECT details FROM audit WHERE action='carry-supervisor-approval' AND entity_id=:id", dict(id=row['id']))]
            provenance = next(e for e in events if e['approvalId'] == final_copy['id'])
            self.assertEqual(provenance['sourceActorId'], 'se')
            self.assertEqual(provenance['sourceActorName'], original_approval['actorName'])
            self.assertEqual(provenance['sourceCreatedAt'], original_approval['createdAt'])

    def test_joint_report_assigned_to_other_specialty_keeps_assignment_during_own_correction(self):
        body = self.body(('m1', 'e1'))
        body['assigneeId'] = 'e1'
        saved = self.work.save(self.users['head'], body)
        row = self.work.submit(self.users['head'], saved['id'], saved['version'])
        changed = self.corrected(row)
        self.assertEqual(changed['assigneeId'], 'e1')
        self.assertEqual({p['personId']: p['minutes'] for p in changed['participants']}, {'m1': 45, 'e1': 60})

    def test_foreign_people_unknown_fields_invalid_quantities_and_noops_are_atomic(self):
        row = self.row()
        for payload, code in (({'participants': [dict(personId='e1', minutes=30)]}, 403),
            ({'participants': [dict(personId='missing', minutes=30)]}, 403),
            ({'participants': [dict(personId='m1', minutes=61)]}, 400),
            ({'participants': [dict(personId='m1', minutes=0)]}, 400),
            ({'participants': [dict(personId='m1', minutes=True)]}, 400),
            ({'participants': [dict(personId='m1', minutes=30)] * 2}, 400),
            ({'parts': [dict(partId='part', quantity='1.0000001')]}, 400),
            ({'parts': [dict(partId='part', quantity='0')]}, 400),
            ({'parts': [dict(partId='missing', quantity='1')]}, 400),
            ({'parts': None}, 400),
            ({'title': 'تغییر عنوان نامجاز'}, 400), ({'reason': ' '}, 400)):
            with self.subTest(payload=payload):
                self.denied(code, lambda: self.corrected(row, **payload))
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT version FROM orders WHERE id=:id', {'id': row['id']}), row['version'])
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='review-correction'"), 0)

    def test_unchanged_form_approves_without_new_revision_or_correction_snapshot(self):
        row = self.grade(self.row(), 'se', [('e1', 1)])
        changed = self.corrected(row, participants=[], reason='')
        self.assertEqual(changed['status'], 'head')
        self.assertEqual(changed['workflowRevision'], row['workflowRevision'])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='review-correction'"), 0)

    def test_invalid_own_grades_roll_back_correction_carried_approvals_and_every_line(self):
        row = self.grade(self.row(), 'se', [('e1', 1)])
        with self.db.connect() as c:
            before = {table:c.query('SELECT * FROM '+table) for table in ('orders','participants','order_parts','participant_grading_policies','approvals','participant_scores','audit')}
        for grades,code in ((None,400),([],400),([dict(personId='m1',coefficient=4),dict(personId='m2',coefficient=2)],400),
            ([dict(personId='e1',coefficient=2)],403),([dict(personId='sm',coefficient=2)],403),
            ([dict(personId='m1',coefficient=1),dict(personId='m1',coefficient=1)],400)):
            self.denied(code,lambda grades=grades:self.corrected(row,grades=grades,parts=[]))
            with self.db.connect() as c:
                self.assertEqual(before,{table:c.query('SELECT * FROM '+table) for table in before})

    def test_first_review_for_other_specialty_stays_pending_without_return_loop(self):
        row = self.corrected(self.row())
        self.assertEqual(row['status'],'supervisor')
        self.assertEqual(row['pendingSupervisorSpecialties'],['برق'])
        self.assertFalse(row['canDecide'])
        row = self.grade(row,'se',[('e1',1)])
        self.assertEqual(row['status'],'head')
        self.denied(400,lambda:self.work.decision(self.users['head'],row['id'],dict(version=row['version'],decision='return',reason='بازگشت حذف شده')))

    def test_role_session_stage_and_already_approved_specialty_are_enforced(self):
        row = self.row()
        payload = dict(version=row['version'], reason='اصلاح معتبر', participants=[dict(personId='m1', minutes=30)])
        for actor in ('head', 'admin', 'planner', 'm1'):
            self.denied(403, lambda actor=actor: self.work.review_correct(self.users[actor], row['id'], payload))
        self.denied(403, lambda: self.work.review_correct(self.users['se'], row['id'], payload))
        with self.db.transaction() as c:
            c.execute("UPDATE users SET session_epoch=1 WHERE id='sm'")
        self.denied(401, lambda: self.work.review_correct(self.users['sm'], row['id'], payload))
        self.users['sm'] = replace(self.users['sm'], session_epoch=1)
        approved = self.grade(row, 'sm', [('m1', 2), ('m2', 2)])
        self.assertFalse(self.work.get(self.users['sm'], row['id'])['canReviewCorrect'])
        self.denied(403, lambda: self.corrected(approved))

    def test_role_or_specialty_drift_cannot_reinterpret_frozen_policies(self):
        row = self.row()
        for field, changed, original in (('role', 'supervisor', 'technician'), ('specialty', 'برق', 'مکانیک'), ('active', 0, 1)):
            with self.db.transaction() as c:
                c.execute('UPDATE users SET ' + field + '=:value WHERE id=\'m1\'', {'value': changed})
            self.denied(409, lambda: self.corrected(row))
            with self.db.transaction() as c:
                c.execute('UPDATE users SET ' + field + '=:value WHERE id=\'m1\'', {'value': original})
        self.assertEqual(self.corrected(row)['workflowRevision'], row['workflowRevision'] + 1)

    def test_audit_failure_rolls_back_every_line_policy_and_revision(self):
        row = self.row()
        with self.db.connect() as c:
            before = {table: c.query('SELECT * FROM ' + table) for table in ('orders', 'participants', 'order_parts', 'participant_grading_policies', 'audit')}
        with patch('pak_maintenance.work_review_correction.audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.corrected(row, parts=[])
        with self.db.connect() as c:
            after = {table: c.query('SELECT * FROM ' + table) for table in before}
        self.assertEqual(before, after)

    def test_concurrent_corrections_have_one_winner_and_no_duplicate_revision(self):
        row = self.row()
        def correct(_):
            try:
                self.corrected(row)
                return 200
            except ApiError as e:
                return e.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(correct, range(2))), [200, 409])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE action='review-correction'"), 1)

    def test_api_route_only_admits_supervisors_and_uses_real_domain_validation(self):
        row = self.row()
        services = SimpleNamespace(**{name: Mock() for name in ('master', 'pm', 'registry', 'setup', 'reporting', 'performance', 'consumption', 'backup', 'db')})
        services.work = self.work
        config = SimpleNamespace(database=SimpleNamespace(provider='postgresql'), seed_directory='unused', company_name='Synthetic', local_test_mode=False)
        for actor in ('head', 'admin', 'm1'):
            services.auth = SimpleNamespace(authenticate=lambda token, actor=actor: self.users[actor])
            self.denied(403, lambda: Api(services, config).dispatch(Request('POST', '/api/orders/' + row['id'] + '/review-correction', headers={'content-type': 'application/json'}, body=b'{}')))
        services.auth = SimpleNamespace(authenticate=lambda token: self.users['sm'])
        def reject_return_without_mutation(actor, current):
            services.auth = SimpleNamespace(authenticate=lambda token: self.users[actor])
            with self.db.connect() as c:
                before = {table:c.query('SELECT * FROM '+table) for table in ('orders','approvals','participant_scores','audit')}
            self.denied(400,lambda:Api(services,config).dispatch(Request('POST','/api/orders/'+current['id']+'/decision',
                headers={'content-type':'application/json'},body=dumps(dict(version=current['version'],decision='return',reason='مسیر برگشت حذف شده')).encode())))
            with self.db.connect() as c:
                self.assertEqual(before,{table:c.query('SELECT * FROM '+table) for table in before})
        reject_return_without_mutation('sm',row)
        services.auth = SimpleNamespace(authenticate=lambda token: self.users['sm'])
        data = dict(version=row['version'], reason='اصلاح از مسیر واقعی API', participants=[dict(personId='m1', minutes=45)], grades=[dict(personId='m1',coefficient=0),dict(personId='m2',coefficient=2)])
        updated = Api(services, config).dispatch(Request('POST', '/api/orders/' + row['id'] + '/review-correction', headers={'content-type': 'application/json'}, body=dumps(data).encode())).payload
        self.assertEqual(updated['workflowRevision'], row['workflowRevision'] + 1)
        self.assertEqual(next(p['supervisorCoefficient'] for p in updated['participants'] if p['personId']=='m1'),0)
        head = self.grade(updated,'se',[('e1',1)])
        reject_return_without_mutation('head',head)


class ReviewCorrectionPostgresqlTests(ReviewCorrectionTests):
    """The same acceptance over fresh explicitly disposable PostgreSQL databases."""
    def setUp(self):
        from pg_test_support import fresh_database
        config, self.owner = fresh_database(self, prefix='review')
        self.db = Database(config)
        self.users = {u.id: u for u in [User('m1', '1', 'مکانیک اول', 'technician', 'مکانیک'),
            User('m2', '2', 'مکانیک دوم', 'technician', 'مکانیک'), User('e1', '3', 'برق اول', 'technician', 'برق'),
            User('sm', '4', 'سرپرست مکانیک', 'supervisor', 'مکانیک'), User('se', '5', 'سرپرست برق', 'supervisor', 'برق'),
            User('head', '6', 'رئیس', 'head'), User('admin', '7', 'مدیر سیستم', 'admin'), User('planner', '8', 'برنامه ریز', 'planner')]}
        with self.db.transaction() as c:
            for u in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:code,:name,:role,:spec,1,'',0)", {'id': u.id, 'code': u.code, 'name': u.name, 'role': u.role, 'spec': u.specialty})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','A01','پمپ','فرایند','خط یک','{}')")
            c.execute("INSERT INTO parts(id,code,name,unit,specification) VALUES('part','P001','بلبرینگ','عدد','')")
        self.work = WorkService(self.db)
        self.add_part()

    def tearDown(self):
        pass


class PmReviewCorrectionTests(unittest.TestCase):
    tearDown = pm_fixture.PmTests.tearDown
    custom = pm_fixture.PmTests.custom
    activate = pm_fixture.PmTests.activate
    order_id = pm_fixture.PmTests.order_id

    def setUp(self):
        pm_fixture.PmTests.setUp(self)
        self.seed_review()

    def seed_review(self):
        self.users['planner'] = User('planner', role='planner')
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES('planner','planner','کارشناس نت','planner','',1,'',0)")
            c.execute("INSERT INTO parts(id,code,name,unit,specification) VALUES('part','P001','قطعه اول','عدد',''),('part-new','P002','قطعه دوم','عدد','')")
        self.activate()
        self.pm.generate(pm_fixture.INSTANT)
        self.id = self.order_id()
        assigned = self.pm.assign(self.users['sm'], self.id, dict(version=1, assigneeId='m1'))
        body = deepcopy(assigned)
        body.update(description='بازدید کامل انجام شد', workDate='2026-09-26', startTime='08:00', endTime='09:00',
                    participants=[dict(personId='m1', minutes=60)], parts=[dict(partId='part', quantity='1')], checklist=[],
                    pmResults={cell['id']: 'سالم' for cell in pm_fixture.info.cells(assigned['pmSheet']) if cell.get('editable')})
        saved = self.work.save(self.users['m1'], body, self.id)
        self.row = self.work.submit(self.users['m1'], self.id, saved['version'])

    def correction(self, **changes):
        payload = dict(version=self.row['version'], reason='اصلاح بازدید و مقدار واقعی مصرف', endTime='09:30',
                       participants=[dict(personId='m1', minutes=90)], parts=[dict(partId='part-new', quantity='2.25')],
                       grades=[dict(personId='m1',coefficient=2)])
        payload.update(changes)
        return self.work.review_correct(self.users['sm'], self.id, payload)

    def test_pm_snapshot_preserves_original_and_exposes_real_supervisor_actor_then_finalizes(self):
        with self.db.connect() as c:
            original = c.one('SELECT * FROM pm_submission_archive WHERE order_id=:id', {'id': self.id})
        with self.db.transaction() as c:
            c.execute("UPDATE users SET name='نام تازهٔ مجری' WHERE id='m1'")
        changed = self.correction()
        self.assertEqual(changed['status'], 'head')
        with self.db.connect() as c:
            self.assertEqual(c.one('SELECT * FROM pm_submission_archive WHERE id=:id', {'id': original['id']}), original)
            corrected = c.one('SELECT * FROM pm_submission_archive WHERE order_id=:id AND workflow_revision=:rev', {'id': self.id, 'rev': changed['workflowRevision']})
        self.assertEqual(corrected['submitted_by'], 'sm')
        self.assertEqual(corrected['performer_id'], 'm1')
        self.assertEqual(corrected['evidence_source'], 'supervisor-correction-snapshot')
        before, after = loads(original['body']), loads(corrected['body'])
        self.assertEqual(after['results'], before['results'])
        self.assertEqual(after['equipmentMembers'], before['equipmentMembers'])
        self.assertEqual(after['window'], before['window'])
        self.assertEqual(after['correction'], dict(baseArchiveId=original['id'], baseRevision=self.row['workflowRevision'], reason='اصلاح بازدید و مقدار واقعی مصرف'))
        self.assertEqual(after['execution']['durationMinutes'], 90)
        self.assertEqual(after['execution']['participants'][0]['personName'], before['execution']['participants'][0]['personName'])
        self.assertEqual(after['execution']['participants'][0]['personName'], corrected['performer_name'])
        archive = PmArchiveService(self.db)
        detail = archive.detail(self.users['planner'], corrected['id'])
        self.assertEqual(detail['correction']['baseArchiveId'], original['id'])
        self.assertEqual(detail['entry']['submittedByName'], 'sm')
        done = self.work.decision(self.users['head'], self.id, dict(version=changed['version'], decision='approve', participantScores=[dict(personId='m1', coefficient=3)]))
        self.assertEqual(done['score'], Decimal('9.00'))
        self.assertEqual(archive.detail(self.users['planner'], original['id'])['entry']['status'], 'superseded')
        with self.db.connect() as c:
            self.assertEqual(c.scalar('SELECT quantity_units FROM part_consumptions'), 2250000)

    def test_pm_clock_singleworker_rule_and_archive_failure_roll_back_everything(self):
        with self.assertRaises(ApiError) as error:
            self.correction(endTime='09:00')
        self.assertEqual(error.exception.status, 400)
        with self.db.connect() as c:
            before = {table: c.query('SELECT * FROM ' + table) for table in ('orders', 'participants', 'order_parts', 'participant_grading_policies', 'pm_weekly_occurrences', 'pm_submission_archive', 'audit')}
        with patch('pak_maintenance.pm_archive._insert', side_effect=RuntimeError('synthetic archive failure')):
            with self.assertRaises(RuntimeError):
                self.correction()
        with self.db.connect() as c:
            self.assertEqual(before, {table: c.query('SELECT * FROM ' + table) for table in before})


class PmReviewCorrectionPostgresqlTests(PmReviewCorrectionTests):
    def setUp(self):
        from pg_test_support import fresh_database
        from pak_maintenance.pm_service import PmService
        from pak_maintenance.pm_registry import PmRegistry
        from pak_maintenance.pm_setup import PmSetupService
        config, self.owner = fresh_database(self, prefix='pmreview')
        self.db = Database(config)
        self.users = {u.id: u for u in (User('head', role='head'), User('admin', role='admin'),
            User('sm', role='supervisor', specialty='مکانیک'), User('sm2', role='supervisor', specialty='مکانیک'),
            User('se', role='supervisor', specialty='برق'), User('m1', role='technician', specialty='مکانیک'),
            User('m2', role='technician', specialty='مکانیک'), User('e1', role='technician', specialty='برق'))}
        with self.db.transaction() as c:
            for u in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,:spec,1,'',0)", dict(id=u.id, role=u.role, spec=u.specialty))
            for identifier, code, hall in (('a1', '39-1', 'سالن فرایند'), ('a2', '39-2', 'سالن فرایند'), ('milk', '39-3', 'سالن بسته بندی شیر')):
                c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES(:id,:code,'پمپ',:hall,'خط یک','{}')", dict(id=identifier, code=code, hall=hall))
        self.work = WorkService(self.db)
        self.pm = PmService(self.db, self.work)
        self.work.pm_service = self.pm
        self.registry, self.setup = PmRegistry(self.db), PmSetupService(self.db, self.pm)
        self.seed_review()

    def tearDown(self):
        pass


if __name__ == '__main__':
    unittest.main()
