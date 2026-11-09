"""Explicit synthetic PostgreSQL/TLS load review; never targets company data.

Requires PAK_POSTGRES_TEST_CONFIG from prepare_postgresql_qa.py. Seed only a
new disposable database; subsequent runs require its private descriptor. No
downloads, company data, OS services, firewall or production configuration.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from http.client import HTTPSConnection
from http.cookies import SimpleCookie
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket
import ssl
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('seed', 'measure', 'verify-writes'))
parser.add_argument('--variant', choices=('baseline', 'current'), default='current')
parser.add_argument('--reports', type=int, default=100000)
parser.add_argument('--pm-sheets', type=int, default=20000)
parser.add_argument('--clients', type=int, nargs='+', default=[1, 10, 25])
parser.add_argument('--requests-per-client', type=int, default=8)
args = parser.parse_args()
if not (100 <= args.reports <= 200000 and 10 <= args.pm_sheets <= 100000
        and all(1 <= value <= 50 for value in args.clients) and 1 <= args.requests_per_client <= 40):
    parser.error('Synthetic load bounds exceeded')
APP = ROOT.parents[2] / 'outputs/PakMaintenance'
sys.path.insert(0, str((APP if args.variant == 'baseline' else ROOT) / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
from pg_test_support import qa_settings
from pak_maintenance.database import Database
from pak_maintenance.common import dumps, now
from pak_maintenance.application import compose, create_app
from pak_maintenance.configuration import AppConfig
from pak_maintenance.security import Authentication, COOKIE, hash_password

EVIDENCE = ROOT / '.build/rc6-load-review'
EVIDENCE.mkdir(exist_ok=True)
DESCRIPTOR = EVIDENCE / 'fixture.json'
PASSWORD = 'Synthetic-Load-Only-2026!'


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed():
    from test_pm_archive import PmArchiveTests
    fixture = PmArchiveTests()
    fixture.setUp()
    _, submitted = fixture.submitted()
    stamp = now()
    em = dumps(dict(type='EM', assetId='a', title='گزارش ساختگی آزمون ظرفیت', description='فاقد اطلاعات شرکت',
        workDate='2026-10-10', startTime='08:00', endTime='09:00', durationMinutes=60, assigneeId='tech',
        participants=[dict(personId='tech',minutes=60)], parts=[], checklist=[], downtimeMinutes=0,
        productionStopped=False))
    started = time.perf_counter()
    with fixture.owner.transaction() as c:
        c.execute("SET LOCAL statement_timeout='180s'")
        # Fixture construction is an explicit owner-only bulk import, not a
        # measurement of business writes. Avoid a cached empty-table trigger
        # plan during generation; all FK checks stay enabled. Every user guard
        # is restored and verified before any benchmark request is accepted.
        guarded_tables = ('orders','participants','approvals','participant_scores','participant_head_scores',
            'completed_scores','pm_weekly_occurrences','pm_submission_archive')
        for table in guarded_tables:
            c.execute('ALTER TABLE ' + table + ' DISABLE TRIGGER USER')
        c.execute("UPDATE users SET password_hash=:hash", dict(hash=hash_password(PASSWORD)))
        c.execute("INSERT INTO parts(id,code,name,specification,unit) SELECT 'part-'||g::text,'SYN-'||g::text,'قطعه ساختگی','','عدد' FROM generate_series(1,10000) g")
        c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
SELECT 'em-'||g::text,'SYN-EM-'||g::text,4,2,'EM','head','a','tech','tech','2026-10-10',NULL,:body,'گزارش ساختگی آزمون ظرفیت','فاقد اطلاعات شرکت',60,:stamp,:stamp FROM generate_series(1,:count) g""",dict(body=em,stamp=stamp,count=args.reports))
        c.execute('ANALYZE orders')
        c.execute("INSERT INTO participants(order_id,person_id,minutes,specialty) SELECT 'em-'||g::text,'tech',60,'مکانیک' FROM generate_series(1,:count) g",dict(count=args.reports))
        c.execute('ANALYZE participants')
        c.execute("""INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,coefficient,reason,actor_id,created_at)
SELECT 'sup-'||g::text,'em-'||g::text,2,'supervisor','مکانیک','approve',NULL,'synthetic only','sm',:stamp FROM generate_series(1,:count) g""",dict(count=args.reports,stamp=stamp))
        c.execute('ANALYZE approvals')
        c.execute("""INSERT INTO participant_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at)
SELECT 'em-'||g::text,2,'tech','مکانیک',1,'sm','sup-'||g::text,:stamp FROM generate_series(1,:count) g""",dict(count=args.reports,stamp=stamp))
        c.execute('ANALYZE participant_scores')
        c.execute("""INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,coefficient,reason,actor_id,created_at)
SELECT 'chief-'||g::text,'em-'||g::text,2,'head','','approve',NULL,'synthetic only','head',:stamp FROM generate_series(1,:count) g""",dict(count=args.reports,stamp=stamp))
        c.execute('ANALYZE approvals')
        c.execute("""INSERT INTO participant_head_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at)
SELECT 'em-'||g::text,2,'tech','مکانیک','1','head','chief-'||g::text,:stamp FROM generate_series(1,:count) g""",dict(count=args.reports,stamp=stamp))
        c.execute('ANALYZE participant_head_scores')
        c.execute("UPDATE orders SET status='completed' WHERE id LIKE 'em-%'")
        c.execute("""INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,head_coefficient_exact,score_raw,score_cents,source)
SELECT 'em-'||g::text,2,'tech',1,1,'1','1',100,'individual' FROM generate_series(1,:count) g""",dict(count=args.reports))
        c.execute("""INSERT INTO orders SELECT 'pm-load-'||g::text,'SYN-PM-'||g::text,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,created_at,updated_at,duration_minutes,title,description FROM orders CROSS JOIN generate_series(1,:count) g WHERE id=:id""",dict(count=args.pm_sheets,id=submitted['id']))
        c.execute("""INSERT INTO pm_weekly_occurrences SELECT plan_id,1406+((g-1)/48),1+(((g-1)%48)/4),1+((g-1)%4),'pm-load-'||g::text,plan_version,template_id,template_version,start_utc,end_utc,specialty,supervisor_id,submitted_at,executed_at FROM pm_weekly_occurrences CROSS JOIN generate_series(1,:count) g WHERE order_id=:id""",dict(count=args.pm_sheets,id=submitted['id']))
        c.execute('ANALYZE pm_weekly_occurrences')
        c.execute("INSERT INTO participants(order_id,person_id,minutes,specialty) SELECT 'pm-load-'||g::text,'tech',60,'مکانیک' FROM generate_series(1,:count) g",dict(count=args.pm_sheets))
        from pak_maintenance.pm_archive import ARCHIVE_COLUMNS
        values = ["'pm-archive-'||g::text" if key=='id' else "'pm-load-'||g::text" if key=='order_id' else "'SYN-PM-'||g::text" if key=='number' else "'legacy-reconstructed'" if key=='evidence_source' else 'a.'+key for key in ARCHIVE_COLUMNS]
        c.execute('INSERT INTO pm_submission_archive('+','.join(ARCHIVE_COLUMNS)+') SELECT '+','.join(values)+' FROM pm_submission_archive a CROSS JOIN generate_series(1,:count) g WHERE a.order_id=:id',dict(count=args.pm_sheets,id=submitted['id']))
        for table in guarded_tables:
            c.execute('ALTER TABLE ' + table + ' ENABLE TRIGGER USER')
        c.execute('ANALYZE')
    fixture.db.verify_schema()
    descriptor = dict(database=fixture.db.config.database, directory=str(fixture.directory),
        qaConfiguration=str(qa_settings()[0]), reports=args.reports, pmSheets=args.pm_sheets+1,
        parts=10000, seedSeconds=round(time.perf_counter()-started,3), syntheticOnly=True,
        seedMethod='owner-only synthetic bulk fixture; user guards restored, FK checks retained',
        allGuardsEnabledDuringLoad=True)
    save(DESCRIPTOR, descriptor)
    print(json.dumps({key:descriptor[key] for key in ('reports','pmSheets','parts','seedSeconds','syntheticOnly')}),flush=True)
    # Intentionally retained for the paired baseline/current runs. Root review
    # removes this owned cluster after verification; no production DB involved.


def measure():
    import uvicorn
    descriptor = json.loads(DESCRIPTOR.read_text(encoding='utf-8'))
    qa, owner_config, runtime, _, _ = qa_settings()
    if str(qa) != descriptor['qaConfiguration'] or not descriptor['database'].startswith('pak_qa_governance_'):
        raise RuntimeError('Only the explicitly seeded disposable QA database is permitted')
    runtime = replace(runtime,database=descriptor['database'])
    listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',0));listener.listen(128)
    port = listener.getsockname()[1]
    tls = qa.parent/'tls'
    directory = Path(descriptor['directory'])
    config = AppConfig(runtime, f'https://127.0.0.1:{port}', str(directory/'frontend'),
        str(directory/'seed'), str(directory/'backups'), development=True,scheduling_enabled=False)
    services = compose(config)
    app = create_app(config,services=services)
    options = dict(app=app,host='127.0.0.1',port=port,proxy_headers=False,forwarded_allow_ips='',
        loop='asyncio',http='h11',ws='none',lifespan='on',limit_concurrency=128,backlog=128,
        timeout_keep_alive=10,timeout_graceful_shutdown=10,access_log=False,server_header=False,
        log_config=None,log_level='error',ssl_certfile=str(tls/'server.pem'),ssl_keyfile=str(tls/'server.key'))
    server = uvicorn.Server(uvicorn.Config(**options))
    thread = threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
    thread.start()
    until=time.monotonic()+15
    while not server.started and thread.is_alive() and time.monotonic()<until:time.sleep(.01)
    if not server.started:raise RuntimeError('Owned loopback TLS listener failed')
    context = ssl.create_default_context(cafile=str(tls/'root.pem'))
    cookies={}
    def connection():return HTTPSConnection('127.0.0.1',port,context=context,timeout=35)
    try:
        for actor in ('head','planner','tech','sm'):
            client=connection()
            try:
                client.request('POST','/api/auth/login',body=dumps(dict(code=actor,password=PASSWORD)).encode(),
                    headers={'content-type':'application/json','x-app-request':'1','origin':config.listen_url})
                response=client.getresponse();body=response.read()
                if response.status!=200:raise RuntimeError('Synthetic login failed: '+str(response.status))
                cookie=SimpleCookie();cookie.load(response.getheader('set-cookie'))
                cookies[actor]=COOKIE+'='+cookie[COOKIE].value
            finally:client.close()
        if args.phase == 'verify-writes':
            from decimal import Decimal
            from uuid import uuid4
            workflow_count=10
            with services.db.connect() as c:
                starting_orders=c.scalar('SELECT COUNT(*) FROM orders')
            def workflow(index):
                client=connection(); retries=0
                def post(actor,path,body):
                    nonlocal retries
                    for attempt in range(6):
                        headers={
                            'cookie':cookies[actor],'content-type':'application/json',
                            'x-app-request':'1','origin':config.listen_url}
                        if path=='/api/orders':headers['x-idempotency-key']=body['requestKey']
                        client.request('POST',path,body=dumps(body).encode(),headers=headers)
                        response=client.getresponse();data=json.loads(response.read())
                        if response.status in (200,201):return data
                        if response.status!=409 or attempt==5:
                            raise AssertionError('Synthetic business write failed: '+str(response.status))
                        retries+=1;time.sleep(.05*(attempt+1))
                try:
                    body=dict(requestKey=uuid4().hex,type='EM',assetId='a',title='آزمون هم‌زمان '+str(index),
                        description='اطلاعات ساختگی، بدون داده شرکت',workDate=datetime.now(timezone.utc).date().isoformat(),
                        startTime='08:00',endTime='09:00',participants=[dict(personId='tech',minutes=60)],
                        parts=[dict(partId='part-1',quantity=Decimal('0.100001'))],checklist=[],downtimeMinutes=0)
                    saved=post('tech','/api/orders',body)
                    duplicate=post('tech','/api/orders',body)
                    if saved['id']!=duplicate['id']:raise AssertionError('Duplicate request created a second report')
                    row=post('tech','/api/orders/'+saved['id']+'/submit',dict(version=saved['version']))
                    row=post('sm','/api/orders/'+row['id']+'/decision',dict(version=row['version'],decision='approve',grades=[dict(personId='tech',coefficient=Decimal('1.25'))]))
                    row=post('head','/api/orders/'+row['id']+'/decision',dict(version=row['version'],decision='approve',participantScores=[dict(personId='tech',coefficient=Decimal('1.1'))]))
                    if row['status']!='completed' or Decimal(str(row['score']))!=Decimal('1.38'):
                        raise AssertionError('Incorrect final status or exact score')
                    return dict(id=row['id'],score='1.38',conflictRetries=retries)
                finally:client.close()
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=5) as pool:completed=list(pool.map(workflow,range(workflow_count)))
            identifiers={row['id'] for row in completed}
            if len(identifiers)!=workflow_count:raise AssertionError('Lost or duplicated completed report')
            with services.db.transaction(write=False) as c:
                keys={f'k{i}':identifier for i,identifier in enumerate(sorted(identifiers))}
                membership='('+','.join(':'+key for key in keys)+')'
                claims=c.scalar('SELECT COUNT(*) FROM request_claims WHERE order_id IN '+membership,keys)
                consumption=c.one('SELECT COUNT(*) AS records,SUM(quantity_units) AS units FROM part_consumptions WHERE order_id IN '+membership,keys)
                cents=c.scalar('SELECT SUM(score_cents) FROM completed_scores WHERE order_id IN '+membership,keys)
                if claims!=workflow_count or consumption['records']!=workflow_count or consumption['units']!=workflow_count*100001 or cents!=workflow_count*138:
                    raise AssertionError('Inconsistent claims, immutable consumption, or final scoring facts')
                invalid=c.scalar("SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND NOT convalidated")
                if invalid:raise AssertionError('Invalid relational constraints')
                if c.scalar('SELECT COUNT(*) FROM orders')-starting_orders!=workflow_count:
                    raise AssertionError('Incorrect total created report count')
            report=dict(recordedUtc=datetime.now(timezone.utc).isoformat(),syntheticOnly=True,liveCompanyDataWritten=False,
                preexistingSyntheticReports=descriptor['reports'],preexistingPmSheets=descriptor['pmSheets'],startingOrderCount=starting_orders,
                concurrentWorkflows=5,completedWorkflows=workflow_count,replayedCreateRequests=workflow_count,
                conflictRetries=sum(row['conflictRetries'] for row in completed),unresolvedFailures=0,
                quantityUnits=int(consumption['units']),scoreCents=int(cents),invalidConstraints=invalid,
                elapsedSeconds=round(time.perf_counter()-started,3),allGuardsEnabled=True,
                limits='Actual HTTPS and guarded PostgreSQL writes; same synthetic actors deliberately stress shared approvals. No company server or data involved.')
            save(ROOT/'docs/rc6-concurrent-writes.json',report)
            print(json.dumps(report),flush=True)
            return
        routes=(('dashboard','head','/api/dashboard'),('performance','head','/api/performance?period=all&detailPageSize=25'),
            ('orders','tech','/api/orders?page=1&pageSize=25'),('parts','planner','/api/catalog/parts?page=1&pageSize=25'),
            ('pmTasks','sm','/api/pm/tasks?page=1&pageSize=25&view=submitted'),('archive','planner','/api/pm/archive?page=1&pageSize=25'),
            ('inbox','sm','/api/inbox?page=1&pageSize=25'),('search','head','/api/orders?page=1&pageSize=25&q=SYN-EM-999'))
        warmup=connection()
        try:
            for name,actor,path in routes:
                start=time.perf_counter()
                warmup.request('GET',path,headers={'cookie':cookies[actor]});resp=warmup.getresponse();resp.read()
                print(json.dumps(dict(warmup=name,status=resp.status,milliseconds=round((time.perf_counter()-start)*1000,3))),flush=True)
        finally:warmup.close()
        rounds=[]
        for clients in args.clients:
            barrier=threading.Barrier(clients)
            def worker(index):
                client=connection();samples=[]
                try:
                    barrier.wait(timeout=15)
                    for number in range(args.requests_per_client):
                        name,actor,path=routes[(index+number)%len(routes)]
                        start=time.perf_counter();status=0;size=0;error=None
                        try:
                            client.request('GET',path,headers={'cookie':cookies[actor]})
                            response=client.getresponse();data=response.read();status=response.status;size=len(data)
                            if status==200:
                                value=json.loads(data)
                                if name=='orders' and len(value['orders'])>25:raise AssertionError('Unbounded work page')
                                if name=='archive' and (value['total']!=descriptor['pmSheets'] or len(value['items'])>25):raise AssertionError('Incorrect archive page')
                                if name=='dashboard' and value['completed']!=descriptor['reports']:raise AssertionError('Incorrect completed total')
                                if name=='performance' and value['totals']['score']!=descriptor['reports']:raise AssertionError('Incorrect exact score total')
                            else:error='HTTP '+str(status)
                        except Exception as exc:
                            error=type(exc).__name__;client.close();client=connection()
                        samples.append(dict(route=name,milliseconds=round((time.perf_counter()-start)*1000,3),status=status,bytes=size,error=error))
                finally:client.close()
                return samples
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=clients) as pool:samples=[sample for group in pool.map(worker,range(clients)) for sample in group]
            elapsed=time.perf_counter()-started
            def summary(values):
                times=sorted(item['milliseconds'] for item in values)
                return dict(requests=len(values),p50Milliseconds=round(statistics.median(times),3),
                    p95Milliseconds=times[min(len(times)-1,max(0,int(len(times)*.95)-1))],maxMilliseconds=max(times),
                    failures=sum(item['status']!=200 or item['error'] is not None for item in values),
                    maxResponseBytes=max(item['bytes'] for item in values))
            result=dict(clients=clients,elapsedSeconds=round(elapsed,3),requestsPerSecond=round(len(samples)/elapsed,3),
                overall=summary(samples),routes={name:summary([item for item in samples if item['route']==name]) for name,_,_ in routes},
                errors=[item for item in samples if item['status']!=200 or item['error'] is not None])
            rounds.append(result)
            print(json.dumps(dict(variant=args.variant,clients=clients,**result['overall'])),flush=True)
        timings=[]
        for _ in range(10):
            start=time.perf_counter()
            with services.db.connect() as c:c.scalar('SELECT 1')
            timings.append((time.perf_counter()-start)*1000)
        with services.db.connect() as c:
            query_workers=c.scalar('SHOW max_parallel_workers_per_gather')
            database_bytes=c.scalar('SELECT pg_database_size(current_database())')
        report=dict(recordedUtc=datetime.now(timezone.utc).isoformat(),variant=args.variant,syntheticOnly=True,
            liveCompanyDataUsed=False,provider='PostgreSQL 17.11',schemaVersion=8,transport='real loopback verified TLS HTTP',
            reports=descriptor['reports'],pmSheets=descriptor['pmSheets'],parts=descriptor['parts'],
            seedMethod=descriptor['seedMethod'],allGuardsEnabledDuringLoad=True,
            parallelQueryWorkers=int(query_workers),databaseBytes=database_bytes,
            hostRestriction='Sandbox native background/parallel process signaling is restricted; isolated QA roles use serial query plans. This is not a production database setting.',
            requestsPerClient=args.requests_per_client,rounds=rounds,connectionAndOneQueryMedianMilliseconds=round(statistics.median(timings),3),
            companyNetworkCapacityTested=False,limits='Local synthetic mixed reads over actual HTTPS and PostgreSQL; fixed role mix, not the company server/LAN or a production capacity guarantee.')
        save(ROOT/'docs'/('rc6-load-'+args.variant+'.json'),report)
    finally:
        server.should_exit=True;thread.join(15);listener.close()
        if thread.is_alive():raise RuntimeError('Owned load listener did not finish')


if __name__=='__main__':
    {'seed':seed,'measure':measure,'verify-writes':measure}[args.phase]()
