"""PM archival, approvals and schema upgrade against isolated real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.api import Request
from pak_maintenance.common import ApiError, dumps, loads
from pak_maintenance.pm_archive import ARCHIVE_COLUMNS
from pak_maintenance.pm_archive_migration import upgrade_postgresql_v7
from pak_maintenance import pm_form_info as info
from test_pm_governance import PmGovernanceTests
from test_pm_service import INSTANT


class PmArchiveTests(unittest.TestCase):
    setUp = PmGovernanceTests.setUp
    tearDown = PmGovernanceTests.tearDown
    request = PmGovernanceTests.request
    design = staticmethod(PmGovernanceTests.design)
    assert_status = PmGovernanceTests.assert_status

    def prepared(self):
        template = self.pm.create_template(self.users['head'], self.design())
        self.pm.edit_plan(self.users['head'], template['planId'], dict(version=1, reason='تأیید آزمایشی', assetId='a', supervisorId='sm', startDate='2026-09-23', slots=[dict(month=7,week=1)], active=True, mappingConfirmed=True), INSTANT)
        self.assertEqual(self.pm.generate(INSTANT), 1)
        with self.db.transaction(write=False) as c:
            identifier = c.scalar('SELECT order_id FROM pm_weekly_occurrences')
        assigned = self.pm.assign(self.users['sm'], identifier, dict(version=1, assigneeId='tech'))
        body = deepcopy(assigned)
        body.update(description='بازدید کامل انجام شد', workDate='2026-09-26', startTime='08:00', endTime='09:00', participants=[dict(personId='tech',minutes=60)], parts=[], checklist=[], pmResults={cell['id']:'سالم' for cell in info.cells(assigned['pmSheet']) if cell.get('editable')})
        return template, self.services.work.save(self.users['tech'], body, identifier)

    def submitted(self):
        template, saved = self.prepared()
        return template, self.services.work.submit(self.users['tech'], saved['id'], saved['version'])

    def archive(self):
        return self.services.archive.list_page(self.users['planner'])

    def test_only_planner_reads_archive_and_search_filters_are_effective(self):
        template, saved = self.prepared()
        self.assertEqual(self.archive()['total'], 0)
        with self.db.transaction() as c:
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('group-member','PUMP-GROUP-SECOND','پمپ دوم','فرایند','','{}')")
            c.execute("INSERT INTO pm_weekly_assets(order_id,asset_id) VALUES(:order,'group-member')",dict(order=saved['id']))
        submitted = self.services.work.submit(self.users['tech'], saved['id'], saved['version'])
        entry = self.archive()['items'][0]
        with self.db.transaction() as c:c.execute("UPDATE assets SET code='RENAMED-PUMP',name='نام جدید گروه' WHERE id='group-member'")
        self.assertEqual((entry['status'],entry['performerId'],entry['workDate']), ('supervisor','tech','2026-09-26'))
        self.assertNotIn('body', entry)
        self.assertEqual(self.services.archive.list_page(self.users['planner'], q='ناموجود')['total'], 0)
        for filters in (dict(q=submitted['number']),dict(q='PUMP-GROUP-SECOND'),dict(asset_id='group-member'),dict(person_id='tech'),dict(template_id=template['id']),dict(asset_id='a'),dict(hall='فرایند'),dict(from_date='2026-09-26',to_date='2026-09-26')):
            self.assertEqual(self.services.archive.list_page(self.users['planner'], **filters)['total'], 1)
        self.assertEqual(self.services.archive.list_page(self.users['planner'], q='%')['total'],0)
        for actor in ('head','admin','manager','sm','se','tech'):
            for path in ('/api/pm/archive','/api/pm/archive/'+entry['id'],'/api/pm/archive/'+entry['id']+'/original'):
                self.assert_status(403, lambda actor=actor,path=path:self.request('GET',path,actor))
        self.assertEqual(self.request('GET','/api/pm/archive','planner',query={'q':'ناموجود'}).payload['total'],0)
        self.assert_status(400,lambda:self.services.archive.list_page(self.users['planner'],from_date='2026-10-01',to_date='2026-09-01'))
        with self.db.transaction() as c:c.execute("UPDATE users SET role='technician' WHERE id='planner'")
        self.assert_status(401,lambda:self.archive())

    def test_historical_return_resubmit_keeps_answers_names_form_and_revision_approvals(self):
        template, first = self.submitted()
        old_id = self.archive()['items'][0]['id']
        old = self.services.archive.detail(self.users['planner'], old_id)
        self.assert_status(400, lambda: self.services.work.decision(self.users['sm'], first['id'], dict(version=first['version'], decision='return',reason='نتیجه نیازمند اصلاح است')))
        from historical_workflow_fixtures import seed_historical_return
        returned = seed_historical_return(self.db, self.services.work, self.users['sm'], first, 'نتیجه در نسخهٔ قدیمی نیازمند اصلاح بود')
        self.assertEqual(self.services.archive.detail(self.users['planner'],old_id)['entry']['status'],'returned')
        revised = deepcopy(returned)
        revised.update(endTime='10:00',participants=[dict(personId='tech',minutes=120)],pmResults={key:'اصلاح شده' for key in old['results']})
        saved = self.services.work.save(self.users['tech'],revised,first['id'])
        new = self.services.work.submit(self.users['tech'],first['id'],saved['version'])
        activity = next(cell for cell in info.cells(template) if cell.get('activity'))
        self.pm.edit_template(self.users['head'],template['id'],dict(version=1,reason='اصلاح فعالیت آینده',changes=[dict(cellId=activity['id'],text='عنوان فعالیت بعدی')]))
        head = self.services.work.decision(self.users['sm'],new['id'],dict(version=new['version'],decision='approve',grades=[dict(personId='tech',coefficient=Decimal('1.125'))]))
        done = self.services.work.decision(self.users['head'],new['id'],dict(version=head['version'],decision='approve',participantScores=[dict(personId='tech',coefficient=Decimal('2.25'))]))
        with self.db.transaction() as c:
            c.execute("UPDATE users SET name='نام جدید' WHERE id IN ('tech','sm','head')")
            c.execute("UPDATE assets SET name='دستگاه جدید' WHERE id='a'")
        older = self.services.archive.detail(self.users['planner'],old_id)
        entries = self.archive()['items'];latest_id=next(item['id'] for item in entries if item['id']!=old_id)
        latest=self.services.archive.detail(self.users['planner'],latest_id)
        self.assertEqual((older['entry']['status'],latest['entry']['status']),('superseded','completed'))
        self.assertEqual(older['results'],old['results']);self.assertEqual(older['sheet'],old['sheet'])
        self.assertEqual(older['execution']['durationMinutes'],60);self.assertEqual(latest['execution']['durationMinutes'],120)
        self.assertEqual(older['entry']['performerName'],'آزمایشی tech')
        self.assertEqual(older['entry']['assetName'],'پمپ')
        self.assertEqual([x['decision'] for x in older['approvals']],['return'])
        self.assertEqual([x['decision'] for x in latest['approvals']],['approve','approve'])
        self.assertEqual(latest['approvals'][0]['actorName'],'آزمایشی sm')
        self.assertEqual(latest['approvals'][1]['actorName'],'آزمایشی head')
        encoded=dumps(latest)
        self.assertNotIn('coefficient',encoded);self.assertNotIn('participantScores',encoded)
        self.assertEqual(done['status'],'completed');self.assertIsNotNone(latest['entry']['approvedAt'])

    def test_concurrent_submission_commits_one_immutable_snapshot(self):
        _,saved=self.prepared()
        def send():
            try:return self.services.work.submit(self.users['tech'],saved['id'],saved['version'])['status']
            except ApiError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:send(),range(2)))
        self.assertCountEqual(results,['supervisor',409])
        self.assertEqual(self.archive()['total'],1)
        import psycopg
        for sql in ("UPDATE pm_submission_archive SET performer_name='changed'",'DELETE FROM pm_submission_archive'):
            with self.assertRaises(psycopg.Error) as error:
                with self.db.transaction() as c:c.execute(sql)
            self.assertEqual(error.exception.sqlstate,'23514')
        with self.db.transaction(write=False) as c:row=c.one('SELECT * FROM pm_submission_archive')
        row.update(id=str(uuid4()),workflow_revision=999,evidence_source='legacy-reconstructed')
        with self.assertRaises(psycopg.Error):
            with self.db.transaction() as c:c.execute('INSERT INTO pm_submission_archive('+','.join(ARCHIVE_COLUMNS)+') VALUES('+','.join(':'+key for key in ARCHIVE_COLUMNS)+')',row)

    def test_explicit_v7_upgrade_is_additive_idempotent_and_backfills_honestly(self):
        from pak_maintenance.database import Database, SchemaMismatchError
        from pak_maintenance.migration import verify_schema8_snapshot_target
        from pak_maintenance.pm_archive import PmArchiveService
        from pak_maintenance.pm_service import execution_end
        from pg_test_support import ROOT, fresh_database, initialize_schema8
        import test_core_schema_upgrade as frozen

        # A schema-7 source must not contain current policy/transfer tables or
        # schema-10 correction guards. Start from reviewed frozen historical
        # storage, then remove exactly the additive schema-8 archive objects.
        config, owner = fresh_database(self, prefix='archiveold', initialize=False)
        initialize_schema8(owner, config.user)
        runtime = Database(config)
        legacy = SimpleNamespace(runtime=runtime, owner=owner)
        frozen.CoreSchemaUpgradePostgresqlTests.seed_history(legacy, include_oee=False)
        body = dict(type='PM', assetId='asset', title='Historical PM',
            description='Retained historical execution', workDate='2026-10-02',
            startTime='08:00', endTime='09:00', durationMinutes=60,
            downtimeMinutes=0, productionStopped=False,
            participants=[dict(personId='tech', specialty='mechanical', minutes=60)],
            parts=[], pmResults={'legacy-check':'سالم'},
            pmWindow=dict(startDate='2026-10-01', endDate='2026-10-07'))
        edit_stamp, submit_stamp = '2026-10-02T05:29:00+00:00', '2026-10-02T05:30:00+00:00'
        planner = self.users['planner']
        with owner.transaction() as c:
            c.execute('DROP TABLE pm_submission_archive')
            c.execute('DROP FUNCTION pak_guard_pm_archive_insert()')
            c.execute('UPDATE schema_version SET version=7')
            c.execute("INSERT INTO users VALUES(:id,:id,:name,'planner','',1,'synthetic-hash',0)",
                      dict(id=planner.id, name=planner.name))
            c.execute("UPDATE orders SET body=:body,description=:description,updated_at=:stamp WHERE id='pm-old'",
                      dict(body=dumps(body), description=body['description'], stamp=submit_stamp))
            c.execute("UPDATE pm_weekly_occurrences SET submitted_at=:stamp,executed_at=:end WHERE order_id='pm-old'",
                      dict(stamp=submit_stamp, end=execution_end(body)))
            c.execute("INSERT INTO audit VALUES('legacy-edit','tech','tech','edit','order','pm-old',:details,:stamp)",
                      dict(details=dumps(dict(revision=0, values=body)), stamp=edit_stamp))
            c.execute("INSERT INTO audit VALUES('legacy-submit','tech','tech','submit','order','pm-old',:details,:stamp)",
                      dict(details=dumps(dict(revision=1)), stamp=submit_stamp))
        before = frozen.CoreSchemaUpgradePostgresqlTests.snapshot(legacy)
        historical_sql = ROOT/'tests/fixtures/schema8'
        result=upgrade_postgresql_v7(owner,runtime_user=config.user,schema_directory=historical_sql)
        self.assertEqual((result['schemaVersion'],result['reconstructed'],result['unavailableSubmissions']),(8,1,0))
        verify_schema8_snapshot_target(runtime)
        with self.assertRaises(SchemaMismatchError):
            runtime.verify_schema()  # The current application still requires 10.
        archive = PmArchiveService(runtime)
        entry=archive.list_page(planner)['items'][0]
        self.assertEqual(entry['evidenceSource'],'legacy-reconstructed')
        detail=archive.detail(planner,entry['id'])
        self.assertIsNotNone(detail['identityCapturedAt'])
        self.assertEqual(detail['equipmentMembers'][0]['code'],'A01')
        self.assertEqual(detail['results'],body['pmResults'])
        self.assertEqual(detail['execution']['participants'][0]['minutes'],60)
        after = frozen.CoreSchemaUpgradePostgresqlTests.snapshot(legacy)
        self.assertEqual({table:after[table] for table in before},before)
        self.assertEqual(set(after)-set(before),{'pm_submission_archive'})
        self.assertTrue(upgrade_postgresql_v7(owner,runtime_user=config.user,schema_directory=historical_sql)['alreadyApplied'])
        self.assertEqual(frozen.CoreSchemaUpgradePostgresqlTests.snapshot(legacy),after)

    def test_large_archive_pages_are_bounded_and_lighter_than_work_order_lists(self):
        import json, statistics, time, tracemalloc
        from datetime import datetime, timezone
        _,submitted=self.submitted()
        with self.owner.transaction() as c:
            c.execute("""INSERT INTO orders SELECT 'load-'||g::text,'PM-LOAD-'||g::text,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,created_at,updated_at,duration_minutes,title,description FROM orders CROSS JOIN generate_series(1,10000) g WHERE id=:id""",dict(id=submitted['id']))
            c.execute("""INSERT INTO pm_weekly_occurrences SELECT plan_id,1406+((g-1)/48),1+(((g-1)%48)/4),1+((g-1)%4),'load-'||g::text,plan_version,template_id,template_version,start_utc,end_utc,specialty,supervisor_id,submitted_at,executed_at FROM pm_weekly_occurrences CROSS JOIN generate_series(1,10000) g WHERE order_id=:id""",dict(id=submitted['id']))
            columns=','.join(ARCHIVE_COLUMNS)
            values=[]
            for key in ARCHIVE_COLUMNS:
                values.append("'load-archive-'||g::text" if key=='id' else "'load-'||g::text" if key=='order_id' else "'PM-LOAD-'||g::text" if key=='number' else "'legacy-reconstructed'" if key=='evidence_source' else 'a.'+key)
            c.execute('INSERT INTO pm_submission_archive('+columns+') SELECT '+','.join(values)+' FROM pm_submission_archive a CROSS JOIN generate_series(1,10000) g WHERE a.order_id=:id',dict(id=submitted['id']))
            for table in ('orders','pm_weekly_occurrences','pm_submission_archive'):c.execute('ANALYZE '+table)
        user=self.users['planner']; samples={}; results={};peaks={}
        calls={'archive':lambda:self.services.archive.list_page(user,page=1,page_size=25), 'workOrderList':lambda:self.services.work.list_page(user,type='PM',page=1,page_size=25)}
        for name,call in calls.items():
            call();elapsed=[]
            for _ in range(3):
                start=time.perf_counter();result=call();elapsed.append((time.perf_counter()-start)*1000)
            tracemalloc.start();result=call();_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
            samples[name]=round(statistics.median(elapsed),3);results[name]=result;peaks[name]=peak
        archive=results['archive'];self.assertEqual(archive['total'],10001);self.assertEqual(len(archive['items']),25)
        self.assertTrue(all('body' not in row and 'sheet' not in row and 'results' not in row for row in archive['items']))
        archive_bytes=len(dumps(archive).encode());old_bytes=len(dumps(results['workOrderList']).encode())
        self.assertLess(archive_bytes,old_bytes);self.assertLess(peaks['archive'],8*1024*1024)
        with self.db.transaction(write=False) as c:
            plan=c.query('EXPLAIN (FORMAT JSON) '+self.services.archive._select()+ ' ORDER BY a.submitted_at DESC,a.id DESC LIMIT 25')
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname LIKE 'ix_pm_archive_%' AND i.indisvalid"),6)
        report=dict(timestampUtc=datetime.now(timezone.utc).isoformat(),provider='PostgreSQL '+self.db.server_version,databaseVersionNum=self.db.server_version_num,syntheticOnly=True,submissions=10001,pageSize=25,medianMilliseconds=samples,pythonPeakBytes=peaks,responseBytes={'archive':archive_bytes,'workOrderList':old_bytes},explain=plan,companyNetworkCapacityTested=False)
        target=__import__('pathlib').Path(__file__).resolve().parents[1]/'docs/rc5-archive-performance.json'
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    def test_native_backup_restore_preserves_filled_archive_and_immutable_guards(self):
        import os,time
        from pak_maintenance.backup import BackupService
        from pak_maintenance.postgres_backup import _environment,_passfile,_conninfo,_run
        from pg_test_support import fresh_database,native_tool_paths
        _,submitted=self.submitted()
        entry=self.archive()['items'][0]
        expected=self.services.archive.detail(self.users['planner'],entry['id'])
        tools=native_tool_paths()
        snapshot=BackupService(self.db,self.directory/'backups',dump_executable=tools['postgresql_dump_executable'],restore_executable=tools['postgresql_restore_executable']).create_snapshot()
        _,target=fresh_database(self,prefix='archrestore',initialize=False)
        tool=__import__('pathlib').Path(tools['postgresql_restore_executable']);env=_environment(tool)
        with _passfile(self.directory/'backups',target.config) as passfile:
            env['PGPASSFILE']=str(passfile)
            _run([str(tool),'--no-password','--no-owner','--no-privileges','--exit-on-error','--single-transaction','--dbname='+_conninfo(target.config),str(snapshot.path)],env,time.monotonic()+120,lambda:False)
        target.verify_schema(runtime_role=False)
        from pak_maintenance.pm_archive import PmArchiveService
        actual=PmArchiveService(target).detail(self.users['planner'],entry['id'])
        self.assertEqual(expected,actual)
        import psycopg
        with self.assertRaises(psycopg.Error) as error:
            with target.transaction() as c:c.execute('DELETE FROM pm_submission_archive')
        self.assertEqual(error.exception.sqlstate,'23514')
        self.assertEqual(hashlib.sha256(snapshot.path.read_bytes()).hexdigest(),snapshot.sha256)


del PmGovernanceTests
