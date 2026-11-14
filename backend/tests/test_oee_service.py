"""Real PostgreSQL role/API/transaction tests on exclusively synthetic targets."""
from copy import deepcopy
from pathlib import Path
import unittest

from pak_maintenance.common import ApiError, loads
from pak_maintenance.oee_migration import enable_oee
import test_api_integration as api_fixture


class OeeServiceCase(unittest.TestCase):
    password=api_fixture.ApiIntegrationTests.password
    setUpClass=classmethod(api_fixture.ApiIntegrationTests.setUpClass.__func__)
    tearDown=api_fixture.ApiIntegrationTests.tearDown
    request=api_fixture.ApiIntegrationTests.request
    login=api_fixture.ApiIntegrationTests.login

    def setUp(self):
        api_fixture.ApiIntegrationTests.setUp(self)
        enable_oee(self.owner,runtime_user=self.services.db.config.user)
        self.login('planner'); self.login('head')
        self.line=self.request('POST','/api/oee/lines',actor='planner',body=dict(code='L1',name='خط آزمایشی',version=0,
            profiles=[dict(id='product',name='محصول',idealRate=10)],components=[dict(id='filler',name='پرکن')])).payload

    def body(self,**changes):
        result=dict(lineId=self.line['id'],lineVersion=self.line['version'],workDate='2026-10-03',shift='morning',
            version=0,state='final',start='08:00',end='16:00',products=[dict(profileId='product',total=3000,rejected=30)],
            stops=[dict(start='10:00',end='10:30',kind='technical',reason='خرابی',componentIds=['filler'],rootCause='لقی',action='تنظیم')])
        result.update(changes);return result

    def test_replay_versions_final_correction_and_immutable_evidence(self):
        body=self.body();one=self.request('POST','/api/oee/sheets',actor='planner',body=body).payload
        replay=self.request('POST','/api/oee/sheets',actor='planner',body=body).payload
        self.assertEqual((one['id'],one['version']),(replay['id'],replay['version']))
        changed=deepcopy(body);changed.update(note='اصلاح')
        with self.assertRaises(ApiError) as stale:self.request('POST','/api/oee/sheets',actor='planner',body=changed)
        self.assertEqual(stale.exception.status,409)
        changed['version']=1
        with self.assertRaises(ApiError):self.request('POST','/api/oee/sheets',actor='planner',body=changed)
        changed['revisionReason']='اصلاح از روی برگه'
        two=self.request('POST','/api/oee/sheets',actor='planner',body=changed).payload
        self.assertEqual(two['version'],2)
        history=self.request('GET','/api/oee/sheets/'+one['id']+'/history',actor='planner').payload
        self.assertEqual(len(history['items']),2)
        with self.assertRaises(Exception):
            with self.owner.transaction() as c:c.execute('DELETE FROM oee_sheet_revisions WHERE sheet_id=:id',dict(id=one['id']))

    def test_role_enforcement_on_read_preview_and_write(self):
        self.request('GET','/api/oee/lines',actor='head')
        for actor in ('admin','sm','m1'):
            self.login(actor)
            for method,path,body in [('GET','/api/oee/lines',None),('POST','/api/oee/preview',self.body()),('POST','/api/oee/sheets',self.body())]:
                with self.subTest(actor=actor,path=path),self.assertRaises(ApiError) as denied:
                    self.request(method,path,actor=actor,body=body)
                self.assertEqual(denied.exception.status,403)
        with self.assertRaises(ApiError) as denied:self.request('POST','/api/oee/sheets',actor='head',body=self.body())
        self.assertEqual(denied.exception.status,403)

    def test_foreign_components_invalid_counts_and_client_rate_are_not_trusted(self):
        body=self.body();body['products'][0]['idealRate']=1000
        sheet=self.request('POST','/api/oee/sheets',actor='planner',body=body).payload
        self.assertEqual(sheet['products'][0]['idealRate'],10)
        for patch in [dict(stops=[dict(start='09:00',end='10:00',kind='technical',reason='خرابی',componentIds=['foreign'])]),
                      dict(products=[dict(profileId='product',total=5,rejected=6)])]:
            with self.assertRaises(ApiError):self.request('POST','/api/oee/sheets',actor='planner',body=self.body(shift='night',start='16:00',end='23:00',**patch))

    def test_speed_change_preserves_historical_baseline_and_new_day_uses_new_rate(self):
        first=self.request('POST','/api/oee/sheets',actor='planner',body=self.body()).payload
        line=deepcopy(self.line);line['profiles'][0]['idealRate']=20
        new=self.request('PUT','/api/oee/lines/'+self.line['id'],actor='planner',body=line).payload
        old=self.request('GET','/api/oee/day',actor='planner',query=dict(lineId=self.line['id'],date='2026-10-03')).payload
        self.assertEqual(old['sheets'][0]['metrics']['oee'],first['metrics']['oee'])
        edited=self.body(version=1,note='بررسی',revisionReason='اصلاح',lineVersion=new['version'])
        self.assertEqual(self.request('POST','/api/oee/sheets',actor='planner',body=edited).payload['products'][0]['idealRate'],10)
        second=self.body(workDate='2026-10-04',lineVersion=new['version'])
        self.assertEqual(self.request('POST','/api/oee/sheets',actor='planner',body=second).payload['products'][0]['idealRate'],20)

    def test_adjacent_day_and_shift_overlap_rolls_back(self):
        self.request('POST','/api/oee/sheets',actor='planner',body=self.body(shift='night',start='20:00',end='08:00',stops=[]))
        bad=self.body(workDate='2026-10-04',start='07:00',end='16:00',stops=[])
        with self.assertRaises(ApiError) as overlap:self.request('POST','/api/oee/sheets',actor='planner',body=bad)
        self.assertEqual(overlap.exception.status,409)
        day=self.request('GET','/api/oee/day',actor='planner',query=dict(lineId=self.line['id'],date='2026-10-04')).payload
        self.assertEqual(day['sheets'],[])

    def test_report_excludes_drafts_from_aggregate_and_preserves_components_rca(self):
        one=self.request('POST','/api/oee/sheets',actor='planner',body=self.body()).payload
        self.request('POST','/api/oee/sheets',actor='planner',body=self.body(workDate='2026-10-04',state='draft'))
        result=self.request('GET','/api/oee/report',actor='head',query={'from':'2026-10-01','to':'2026-10-10','pageSize':'1'}).payload
        self.assertEqual((result['total'],len(result['items']),result['finalCount']),(2,1,1))
        self.assertEqual(result['metrics']['oee'],one['metrics']['oee'])
        with self.services.db.connect() as c:
            document=loads(c.scalar('SELECT body FROM oee_sheets WHERE id=:id',dict(id=one['id'])))
        self.assertEqual(document['stops'][0]['rootCause'],'لقی')
        self.assertEqual(document['stops'][0]['componentIds'],['filler'])

    def test_revoked_account_and_disabled_line_do_not_accept_new_records(self):
        with self.services.db.connect() as c:
            from pak_maintenance.common import User
            stale=User.from_row(c.one("SELECT * FROM users WHERE id='planner'"))
        with self.owner.transaction() as c:c.execute("UPDATE users SET active=0 WHERE id='planner'")
        with self.assertRaises(ApiError):self.services.oee.save_sheet(stale,self.body())

    def test_module_enable_is_idempotent_and_does_not_reset_data(self):
        sheet=self.request('POST','/api/oee/sheets',actor='planner',body=self.body()).payload
        result=enable_oee(self.owner,runtime_user=self.services.db.config.user)
        self.assertTrue(result['alreadyApplied'])
        with self.owner.connect() as c:
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM oee_sheets'),1)
            self.assertEqual(c.scalar('SELECT version FROM schema_version'),10)
        with self.assertRaises(Exception):
            with self.services.db.transaction() as c:c.execute('DELETE FROM oee_sheets WHERE id=:id',dict(id=sheet['id']))

    def test_final_requires_explicit_counts_and_rejected_zero_does_not_mean_missing(self):
        for products in ([],[dict(profileId='product',total=100,rejected=None)]):
            with self.assertRaises(ApiError) as error:
                self.request('POST','/api/oee/sheets',actor='planner',body=self.body(products=products))
            self.assertEqual(error.exception.status,400)
        result=self.request('POST','/api/oee/sheets',actor='planner',body=self.body(products=[dict(profileId='product',total=0,rejected=0)])).payload
        self.assertEqual(result['metrics']['oee'],0)

    def test_saved_day_report_and_revision_preserve_exact_counts_and_overnight_minutes(self):
        body=self.body(shift='night',start='20:00',end='08:00',stops=[dict(kind='technical',reason='خرابی',start='23:50',end='00:20',componentIds=['filler'])],products=[dict(profileId='product',total=6000,rejected=60)])
        saved=self.request('POST','/api/oee/sheets',actor='planner',body=body).payload
        reloaded=self.request('GET','/api/oee/day',actor='planner',query=dict(lineId=self.line['id'],date=body['workDate'])).payload['sheets'][0]
        self.assertEqual(saved,reloaded)
        self.assertEqual((reloaded['shiftMinutes'],reloaded['metrics']['runMinutes'],reloaded['metrics']['total'],reloaded['metrics']['good'],reloaded['metrics']['rejected']),(720,690,6000,5940,60))
        self.assertEqual(self.request('POST','/api/oee/sheets',actor='planner',body=body).payload['id'],saved['id'])
        edited=deepcopy(body);edited.update(version=1,revisionReason='اصلاح تعداد از روی برگه');edited['products'][0].update(total=6100,rejected=100)
        self.request('POST','/api/oee/sheets',actor='planner',body=edited)
        report=self.request('GET','/api/oee/report',actor='head',query={'from':body['workDate'],'to':body['workDate']}).payload
        self.assertEqual((report['total'],report['metrics']['total'],report['metrics']['good'],report['metrics']['rejected'],report['metrics']['runMinutes']),(1,6100,6000,100,690))

    def test_legacy_zero_counts_are_corrected_on_read_without_overwriting_evidence(self):
        from pak_maintenance.common import dumps
        saved=self.request('POST','/api/oee/sheets',actor='planner',body=self.body(products=[dict(profileId='product',total=0,rejected=0)])).payload
        legacy=deepcopy(saved);legacy['metrics'].update(oee=None,technicalOee=None,good=None,idealMinutes=None,idealGoodMinutes=None,formulaVersion='pak-oee-count-1')
        from pak_maintenance.oee_metrics import facts
        oldfacts=facts(saved);oldfacts.update(good=None,idealMinutes=None,idealGoodMinutes=None)
        encoded=dumps(legacy)
        with self.owner.transaction() as c:c.execute('UPDATE oee_sheets SET body=:body,facts=:facts WHERE id=:id',dict(body=encoded,facts=dumps(oldfacts),id=saved['id']))
        day=self.request('GET','/api/oee/day',actor='planner',query=dict(lineId=self.line['id'],date=saved['workDate'])).payload
        report=self.request('GET','/api/oee/report',actor='head',query={'from':saved['workDate'],'to':saved['workDate']}).payload
        self.assertEqual(day['sheets'][0]['metrics']['oee'],0)
        self.assertEqual(report['metrics']['oee'],0)
        with self.owner.connect() as c:self.assertEqual(c.scalar('SELECT body FROM oee_sheets WHERE id=:id',dict(id=saved['id'])),encoded)

    def test_cip_legacy_day_and_period_report_use_company_timebase_without_rewriting(self):
        from pak_maintenance.common import dumps
        body=self.body(stops=[
            dict(start='08:00',end='09:00',kind='planned',reason='CIP'),
            dict(start='10:00',end='11:00',kind='technical',reason='خرابی'),
            dict(start='12:00',end='12:30',kind='nontechnical',reason='انتظار محصول')])
        saved=self.request('POST','/api/oee/sheets',actor='planner',body=body).payload
        self.assertEqual((saved['metrics']['runMinutes'],saved['metrics']['technicalAvailability']),(420,87.5))
        legacy=deepcopy(saved)
        legacy['metrics'].update(formulaVersion='pak-oee-count-2',runMinutes=330,productionMinutes=420)
        from pak_maintenance.oee_metrics import facts
        oldfacts=facts(saved);oldfacts.update(runMinutes=330,productionMinutes=420)
        encoded=dumps(legacy);encoded_facts=dumps(oldfacts)
        with self.owner.transaction() as c:
            c.execute('UPDATE oee_sheets SET body=:body,facts=:facts WHERE id=:id',
                dict(body=encoded,facts=encoded_facts,id=saved['id']))
        day=self.request('GET','/api/oee/day',actor='head',query=dict(lineId=self.line['id'],date=saved['workDate'])).payload
        report=self.request('GET','/api/oee/report',actor='head',query={'from':saved['workDate'],'to':saved['workDate']}).payload
        self.assertEqual(day['sheets'][0]['metrics']['runMinutes'],420)
        self.assertEqual(day['sheets'][0]['metrics']['formulaVersion'],'pak-oee-count-3')
        self.assertEqual(report['metrics'],day['sheets'][0]['metrics'])
        with self.owner.connect() as c:
            current=c.one('SELECT body,facts FROM oee_sheets WHERE id=:id',dict(id=saved['id']))
        self.assertEqual((current['body'],current['facts']),(encoded,encoded_facts))

    def test_native_backup_restores_oee_data_and_immutable_revisions(self):
        import hashlib,os,subprocess
        from pg_test_support import fresh_database,native_tool_paths
        from test_migration import fixture_directory
        from pak_maintenance.backup import BackupService
        from pak_maintenance.postgres_backup import _environment,_passfile,_conninfo
        self.request('POST','/api/oee/sheets',actor='planner',body=self.body())
        def hashes(db):
            result={}
            with db.transaction(write=False) as c:
                from pak_maintenance.common import dumps
                for table,order in [('oee_module','version'),('oee_lines','id'),('oee_sheets','id'),('oee_sheet_revisions','sheet_id,version')]:
                    result[table]=hashlib.sha256(dumps(c.query('SELECT * FROM '+table+' ORDER BY '+order)).encode()).hexdigest()
            return result
        before=hashes(self.owner)
        _,target=fresh_database(self,prefix='oeerestore',initialize=False)
        tools=native_tool_paths()
        with fixture_directory() as directory:
            snapshot=BackupService(self.services.db,directory,dump_executable=tools['postgresql_dump_executable'],restore_executable=tools['postgresql_restore_executable']).create_snapshot()
            executable=Path(tools['postgresql_restore_executable']);environment=_environment(executable)
            with _passfile(directory,target.config) as credentials:
                environment['PGPASSFILE']=str(credentials)
                restored=subprocess.run([str(executable),'--no-password','--exit-on-error','--single-transaction','--no-owner','--no-privileges','--dbname='+_conninfo(target.config),str(snapshot.path)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=60,env=environment,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.assertEqual(restored.returncode,0,restored.stderr[-600:])
            self.assertEqual(hashes(target),before)
            self.assertEqual(hashes(self.owner),before)
            with self.assertRaises(Exception):
                with target.transaction() as c:c.execute('DELETE FROM oee_sheet_revisions')
