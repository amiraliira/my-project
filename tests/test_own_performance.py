"""Personal inbox performance uses final facts without widening personnel access."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pak_maintenance.api import Api, Request
from pak_maintenance.common import ApiError, User
from pak_maintenance.performance_service import PerformanceService
from test_supervisor_work import BASE, GUARDS
import test_supervisor_work as supervisor_work
import test_workflow as workflow


class OwnPerformanceTests(unittest.TestCase):
    schema = BASE + '\n' + GUARDS
    setUp = workflow.WorkflowCase.setUp
    tearDown = workflow.WorkflowCase.tearDown
    body = workflow.WorkflowCase.body
    grade = workflow.WorkflowCase.grade
    submitted_by_supervisor = supervisor_work.SupervisorWorkTests.submitted_by_supervisor
    finalize = supervisor_work.SupervisorWorkTests.finalize

    def completed_shared(self):
        row = self.submitted_by_supervisor(('sm', 'm1', 'm2', 'e1'))
        row = self.grade(row, 'sm', [('m1', 0), ('m2', 2)])
        row = self.grade(row, 'se', [('e1', 1)])
        return self.finalize(row, {'sm': 9, 'm1': 3, 'm2': 2, 'e1': 1})

    def api(self, actor):
        services = SimpleNamespace(auth=SimpleNamespace(authenticate=lambda token: actor),
            **{name: Mock() for name in ('master', 'work', 'pm', 'registry', 'setup',
                'reporting', 'consumption', 'backup', 'db')})
        services.performance = PerformanceService(self.db)
        config = SimpleNamespace(database=SimpleNamespace(provider='postgresql'), seed_directory='unused',
            company_name='Synthetic', local_test_mode=False)
        return Api(services, config)

    def test_joint_work_shows_only_own_final_score_including_zero_and_head_only_nine(self):
        completed = self.completed_shared()
        self.assertEqual(completed['score'], Decimal('14.00'))
        for actor, expected in (('sm', '9.00'), ('m1', '0.00'), ('m2', '4.00'), ('e1', '1.00')):
            with self.subTest(actor=actor):
                result = PerformanceService(self.db).report(self.users[actor], period='all')
                self.assertEqual(result['scope'], 'own')
                self.assertEqual(result['totals']['peopleCount'], 1)
                self.assertEqual(result['totals']['score'], Decimal(expected))
                self.assertEqual(result['totals']['unscoredCount'], 0)
                self.assertEqual(result['totals']['minutes'], 60)
                self.assertEqual(result['totals']['activityCount'], 1)
                self.assertEqual({p['personId'] for p in result['people']}, {actor})
                self.assertEqual({p['personId'] for p in result['details']}, {actor})
                self.assertEqual(result['details'][0]['score'], Decimal(expected))
        own = PerformanceService(self.db).report(self.users['sm'], period='all')
        self.assertIsNone(own['details'][0]['supervisorCoefficient'])
        self.assertEqual(own['details'][0]['headCoefficient'], Decimal(9))
        self.assertEqual(own['details'][0]['source'], 'head-only')

    def test_cross_person_and_all_scope_are_rejected_for_every_non_head_role(self):
        self.completed_shared()
        actors = [self.users[key] for key in ('m1', 'sm', 'planner', 'admin')]
        manager = User('manager', '10', 'مدیر آزمایشی', 'manager')
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES('manager','10','مدیر آزمایشی','manager','',1,'',0)")
        actors.append(manager)
        for actor in actors:
            with self.subTest(role=actor.role):
                for requested in ('m2', 'e1', 'head', 'missing'):
                    with self.assertRaises(ApiError) as denied:
                        PerformanceService(self.db).report(actor, period='all', person_id=requested, scope='own')
                    self.assertEqual(denied.exception.status, 403)
                with self.assertRaises(ApiError) as denied:
                    PerformanceService(self.db).report(actor, period='all', scope='all')
                self.assertEqual(denied.exception.status, 403)
                own = PerformanceService(self.db).report(actor, period='all', person_id=actor.id)
                self.assertEqual({p['personId'] for p in own['people']}, {actor.id})

    def test_head_all_report_is_preserved_and_can_explicitly_select_personal_scope(self):
        self.completed_shared()
        service = PerformanceService(self.db)
        combined = service.report(self.users['head'], period='all', detail_page_size=1)
        self.assertEqual(combined['scope'], 'all')
        self.assertEqual(combined['totals']['score'], Decimal('14.00'))
        self.assertEqual(combined['detailTotal'], 4)
        self.assertEqual(len(combined['details']), 1)
        selected = service.report(self.users['head'], period='all', person_id='sm')
        self.assertEqual({p['personId'] for p in selected['details']}, {'sm'})
        personal = service.report(self.users['head'], period='all', scope='own')
        self.assertEqual(personal['scope'], 'own')
        self.assertEqual({p['personId'] for p in personal['people']}, {'head'})
        self.assertEqual(personal['totals']['score'], 0)
        self.assertEqual(personal['details'], [])
        with self.assertRaises(ApiError) as denied:
            service.report(self.users['head'], period='all', scope='own', person_id='m1')
        self.assertEqual(denied.exception.status, 403)

    def test_year_choices_and_totals_never_include_other_people_or_pending_work(self):
        self.completed_shared()
        body = self.body(('m2',))
        body['workDate'] = '2020-10-10'
        old = self.work.save(self.users['m2'], body)
        old = self.work.submit(self.users['m2'], old['id'], old['version'])
        old = self.grade(old, 'sm', [('m2', 3)])
        self.finalize(old, {'m2': 3})
        # A submitted but unapproved personal report is not final performance.
        self.submitted_by_supervisor(('sm', 'm1'))
        with patch('pak_maintenance.performance_service.today', return_value='2026-11-03'):
            first = PerformanceService(self.db).report(self.users['m1'], period='all')
            second = PerformanceService(self.db).report(self.users['m2'], period='all', detail_page_size=1)
        self.assertNotIn(1399, first['years'])
        self.assertIn(1399, second['years'])
        self.assertEqual(first['detailTotal'], 1)
        self.assertEqual(first['totals']['minutes'], 60)
        self.assertEqual(second['detailTotal'], 2)
        self.assertEqual(len(second['details']), 1)
        self.assertEqual(second['totals']['score'], Decimal('13.00'))
        next_page = PerformanceService(self.db).report(self.users['m2'], period='all', detail_page=2, detail_page_size=1)
        self.assertEqual(next_page['details'][0]['workDate'], '2020-10-10')

    def test_monthly_results_empty_state_and_historical_role_change_remain_personal(self):
        self.completed_shared()
        service = PerformanceService(self.db)
        result = service.report(self.users['sm'], period='monthly', year=1405, month=7)
        self.assertEqual(result['totals']['score'], Decimal('9.00'))
        empty = service.report(self.users['sm'], period='monthly', year=1405, month=8)
        self.assertEqual(empty['details'], [])
        self.assertEqual(empty['totals']['activityCount'], 0)
        self.assertEqual(empty['totals']['score'], 0)
        with self.db.transaction() as c:
            c.execute("UPDATE users SET role='planner',specialty='' WHERE id='sm'")
        changed = service.report(replace(self.users['sm'], role='planner', specialty=''), period='all')
        self.assertEqual(changed['totals']['score'], Decimal('9.00'))
        self.assertEqual(changed['details'][0]['source'], 'head-only')
        for actor in ('planner', 'admin', 'se'):
            no_work = service.report(self.users[actor], period='all')
            self.assertEqual(no_work['scope'], 'own')
            self.assertEqual(no_work['totals']['score'], 0)
            self.assertEqual({p['personId'] for p in no_work['people']}, {actor})
            self.assertEqual(no_work['details'], [])

    def test_api_dispatch_admits_personal_reports_but_rejects_forged_scope_and_auth(self):
        self.completed_shared()
        for actor in ('m1', 'sm', 'planner', 'admin', 'head'):
            api = self.api(self.users[actor])
            own = api.dispatch(Request('GET', '/api/performance', {'period': 'all', 'scope': 'own'})).payload
            self.assertEqual(own['scope'], 'own')
            self.assertEqual({p['personId'] for p in own['people']}, {actor})
            with self.assertRaises(ApiError) as denied:
                api.dispatch(Request('GET', '/api/performance', {'scope': 'own', 'personId': 'm2'}))
            self.assertEqual(denied.exception.status, 403)
        for actor in (None, replace(self.users['m1'], active=False), replace(self.users['m1'], role='unknown')):
            with self.assertRaises(ApiError) as denied:
                self.api(actor).dispatch(Request('GET', '/api/performance', {'scope': 'own'}))
            self.assertEqual(denied.exception.status, 401)
        for scope in ('specialty', '', 'everything'):
            with self.assertRaises(ApiError) as denied:
                self.api(self.users['head']).dispatch(Request('GET', '/api/performance', {'scope': scope}))
            self.assertEqual(denied.exception.status, 400)


if __name__ == '__main__':
    unittest.main()
