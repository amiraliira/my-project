"""Real PostgreSQL projections across Nowruz and sparse multi-year history."""
from unittest.mock import patch
import unittest

from pg_test_support import fresh_database, qa_settings
from pak_maintenance.database import Database
from pak_maintenance.performance_service import available_years
from pak_maintenance.pm_calendar import from_persian


class PerformanceYearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()

    def setUp(self):
        config,self.owner=fresh_database(self,prefix='years',initialize=False)
        self.db=Database(config)
        with self.owner.transaction() as c:
            c.execute('CREATE TABLE users(id text PRIMARY KEY)')
            c.execute('CREATE TABLE orders(id text PRIMARY KEY,status text,work_date text)')
            c.execute('CREATE TABLE participants(order_id text,person_id text)')
            c.execute("INSERT INTO users VALUES('one'),('two')")
            c.execute(f'GRANT USAGE ON SCHEMA public TO "{config.user}"')
            c.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{config.user}"')

    def add(self,identifier,day,person='one',status='completed'):
        with self.owner.transaction() as c:
            c.execute('INSERT INTO orders VALUES(:id,:status,:day)',dict(id=identifier,status=status,day=day.isoformat()))
            c.execute('INSERT INTO participants VALUES(:id,:person)',dict(id=identifier,person=person))

    def years(self,scope='1=1',args=None):
        with self.db.connect() as c,patch('pak_maintenance.performance_service.today',return_value='2026-10-21'):
            return available_years(c,scope,args or {})

    def test_nowruz_boundaries_and_draft_exclusion(self):
        self.add('last',from_persian(1402,12,29))
        self.add('first',from_persian(1403,1,1))
        self.add('draft',from_persian(1401,1,1),status='draft')
        self.assertEqual(self.years(),[1405,1403,1402])

    def test_sparse_history_uses_two_queries_regardless_of_year_count(self):
        for year in (1390,1398,1400,1402,1403):
            self.add(str(year),from_persian(year,7,1))
        with self.db.connect() as c,patch('pak_maintenance.performance_service.today',return_value='2026-10-21'):
            with patch.object(type(c),'query',autospec=True,side_effect=type(c).query) as calls:
                self.assertEqual(available_years(c,'1=1',{}),[1405,1403,1402,1400,1398,1390])
                self.assertEqual(calls.call_count,2)

    def test_authorized_scope_is_retained(self):
        self.add('one',from_persian(1400,7,1))
        self.add('two',from_persian(1402,7,1),person='two')
        self.assertEqual(self.years('p.person_id=:viewer',dict(viewer='one')),[1405,1400])

    def test_empty_history_still_offers_current_year(self):
        self.assertEqual(self.years(),[1405])
