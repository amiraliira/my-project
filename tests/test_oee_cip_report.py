"""Company CIP policy through saved-sheet/report reads, without database writes."""
from contextlib import contextmanager
from decimal import Decimal
import unittest
from unittest.mock import Mock

from pak_maintenance.common import User, dumps
from pak_maintenance.oee_metrics import facts, normalize_shift
from pak_maintenance.oee_service import OeeService


class ReadOnlyReportDb:
    def __init__(self, documents, cached_facts):
        self.documents=documents
        self.cached_facts=cached_facts
        self.is_sqlite=False
        self.reads=[]

    @contextmanager
    def transaction(self, *, write):
        if write:
            raise AssertionError('Report must not write or migrate the database')
        yield self

    def scalar(self, sql, params=None):
        self.reads.append(sql)
        if "to_regclass" in sql:
            return True
        if 'SELECT version FROM oee_module' in sql:
            return 1
        if 'COUNT(*) FROM oee_sheets' in sql:
            return len(self.documents)
        raise AssertionError(sql)

    def query(self, sql, params):
        self.reads.append(sql)
        if 'SELECT body FROM oee_sheets' in sql:
            return [dict(body=x) for x in self.documents]
        if 'SELECT facts,CASE' in sql:
            return [dict(facts=x,zero_products=None) for x in self.cached_facts]
        raise AssertionError(sql)


class CipReportCase(unittest.TestCase):
    def test_report_recalculates_old_facts_and_details_without_writing(self):
        shift=normalize_shift(dict(start='08:00',end='16:00',products=[dict(profileId='p',total=3000,rejected=30)],stops=[
            dict(start='08:00',end='09:00',kind='planned',reason='CIP'),
            dict(start='10:00',end='11:00',kind='technical',reason='failure'),
            dict(start='12:00',end='12:30',kind='nontechnical',reason='waiting')]),
            {'p':dict(name='product',idealRate=10)}, {})
        shift['metrics']=dict(formulaVersion='pak-oee-count-2',runMinutes=330)
        oldfacts=facts(shift);oldfacts.update(productionMinutes=420,runMinutes=330)
        encoded=dumps(shift);encoded_facts=dumps(oldfacts)
        db=ReadOnlyReportDb([encoded],[encoded_facts])
        report=OeeService(db).report(User('head',role='head'),'2026-10-01','2026-10-01')
        self.assertEqual(report['metrics'],report['items'][0]['metrics'])
        self.assertEqual(report['metrics']['runMinutes'],420)
        self.assertEqual(report['metrics']['technicalAvailability'],Decimal('87.5'))
        self.assertEqual(report['metrics']['formulaVersion'],'pak-oee-count-3')
        self.assertEqual(db.documents,[encoded])
        self.assertEqual(db.cached_facts,[encoded_facts])

    def test_revision_history_returns_original_stored_evidence(self):
        document=dict(metrics=dict(formulaVersion='pak-oee-count-2',runMinutes=330))
        connection=Mock()
        connection.is_sqlite=False
        connection.scalar.return_value=1
        connection.query.return_value=[dict(version=1,body=dumps(document),created_at='2026-10-01T00:00:00Z')]
        class Db:
            @contextmanager
            def transaction(self, *, write):
                if write:
                    raise AssertionError('History is read-only')
                yield connection
        history=OeeService(Db()).history(User('head',role='head'),'sheet')
        self.assertEqual(history['items'][0]['sheet'],document)
        self.assertEqual(history['items'][0]['sheet']['metrics']['formulaVersion'],'pak-oee-count-2')
