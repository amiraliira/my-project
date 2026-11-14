"""PM contract acceptance over disposable SQL databases and original read-only files."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
from pak_maintenance.common import ApiError,User
from pak_maintenance.database import Database,DatabaseConfig
from pak_maintenance.work_service import WorkService
from pak_maintenance.pm_service import PmService,dump,parse
from pak_maintenance.pm_registry import PmRegistry
from pak_maintenance.pm_setup import PmSetupService
from pak_maintenance import pm_calendar as cal,pm_form_info as info

ROOT=Path(__file__).resolve().parents[1]
INSTANT=datetime(2026,10,10,8,0,tzinfo=timezone.utc)


class PmTests(unittest.TestCase):
    def setUp(self):
        self.directory=ROOT/"tests"/".pm-fixtures"/uuid4().hex;self.directory.mkdir(parents=True)
        self.path=self.directory/"synthetic.db"
        raw=sqlite3.connect(self.path)
        for script in (ROOT/"schema"/"sqlite-v10.sql",*sorted((ROOT/"schema").glob("sqlite-*-guards.sql"))):
            raw.executescript(script.read_text(encoding="utf-8-sig"))
        raw.close()
        self.db=Database(DatabaseConfig(provider="sqlite",database=str(self.path),development=True))
        self.users={u.id:u for u in (User("head",role="head"),User("admin",role="admin"),User("sm",role="supervisor",specialty="مکانیک"),User("sm2",role="supervisor",specialty="مکانیک"),User("se",role="supervisor",specialty="برق"),User("m1",role="technician",specialty="مکانیک"),User("m2",role="technician",specialty="مکانیک"),User("e1",role="technician",specialty="برق"))}
        with self.db.transaction() as c:
            for u in self.users.values():c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,:spec,1,'',0)",dict(id=u.id,role=u.role,spec=u.specialty))
            for id,code,hall in (("a1","39-1","سالن فرایند"),("a2","39-2","سالن فرایند"),("milk","39-3","سالن بسته بندی شیر")):
                c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES(:id,:code,'پمپ',:hall,'خط یک','{}')",dict(id=id,code=code,hall=hall))
        self.work=WorkService(self.db);self.pm=PmService(self.db,self.work);self.work.pm_service=self.pm;self.registry=PmRegistry(self.db);self.setup=PmSetupService(self.db,self.pm)

    def tearDown(self):
        shutil.rmtree(self.directory)
        if not any(self.directory.parent.iterdir()):self.directory.parent.rmdir()

    def custom(self,**changes):
        payload=dict(requestKey=uuid4().hex,title="بازدید پمپ",hall="فرآیند",specialty="مکانیک",period="ماهانه",activities=["کنترل نشتی","کنترل لرزش"]);payload.update(changes)
        return self.pm.create_template(self.users["head"],payload)

    def activate(self,template=None,start="2026-09-23",slots=None):
        template=template or self.custom()
        return self.pm.edit_plan(self.users["head"],template["planId"],dict(version=self.pm.plan(self.users["head"],template["planId"])["version"],reason="تأیید برنامه و اصل برگه",assetId="a1",supervisorId="sm",startDate=start,slots=slots if slots is not None else [dict(month=m,week=1) for m in range(1,13)],active=True,mappingConfirmed=True),INSTANT)

    def order_id(self):
        with self.db.connect() as c:return c.scalar("SELECT order_id FROM pm_weekly_occurrences ORDER BY start_utc LIMIT 1")

    def test_custom_create_idempotency_collision_and_roles(self):
        p=dict(requestKey=uuid4().hex,title="پمپ",hall="فرآیند",specialty="مکانیک",period="ماهانه",activities=["نشتی"])
        a=self.pm.create_template(self.users["head"],p);b=self.pm.create_template(self.users["head"],p)
        self.assertEqual(a["id"],b["id"]);self.assertTrue(b["replayed"])
        p["title"]="دیگر"
        with self.assertRaises(ApiError) as e:self.pm.create_template(self.users["head"],p)
        self.assertEqual(e.exception.status,409)
        for user in (self.users["admin"],self.users["sm"],self.users["m1"],User("head",role="head",active=False)):
            with self.assertRaises(ApiError):self.pm.create_template(user,p)
        with self.db.connect() as c:self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_templates"),1)

    def test_approval_is_not_activation_and_first_activation_cannot_drop_history(self):
        t=self.custom();plan=self.pm.plan(self.users["head"],t["planId"])
        self.assertFalse(plan["active"]);self.assertEqual(self.pm.generate(INSTANT),0)
        p=dict(version=1,reason="تطبیق بدون فعال سازی",assetId="a1",supervisorId="sm",startDate="2026-09-23",slots=[dict(month=7,week=1)],active=False,mappingConfirmed=True)
        saved=self.pm.edit_plan(self.users["head"],t["planId"],p,INSTANT)
        self.assertIsNone(saved["effectiveFromUtc"])
        p.update(version=2,active=True,issueFromDate="2026-10-03")
        with self.assertRaises(ApiError):self.pm.edit_plan(self.users["head"],t["planId"],p,INSTANT)
        p["issueFromDate"]="2026-09-23";self.pm.edit_plan(self.users["head"],t["planId"],p,INSTANT)
        self.assertEqual(self.pm.generate(INSTANT),1)

    def test_backlog_all_weeks_bounded_batches_exactly_once_and_concurrent_issuers(self):
        slots=[dict(month=m,week=w) for m in range(1,13) for w in range(1,5)]
        self.activate(start="2023-03-21",slots=slots)
        counts=[]
        for _ in range(20):
            counts.append(self.pm.generate(INSTANT))
            if not self.pm.has_due(INSTANT):break
        expected=sum(1 for y in range(1402,1406) for w in cal.get_year(y) if w.start_date.isoformat()>="2023-03-21" and w.utc_window()[0]<=INSTANT)
        self.assertEqual(sum(counts),expected);self.assertGreater(expected,100);self.assertTrue(all(n<=100 for n in counts))
        self.assertEqual(self.pm.generate(INSTANT),0)
        errors=[]
        def issue():
            try:self.pm.generate(INSTANT)
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=issue) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[])
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences"),expected)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"),expected)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_assets"),expected)

    def test_undrained_revision_and_retirement_block_backlog_loss(self):
        p=self.activate(slots=[dict(month=7,week=1),dict(month=7,week=2)])
        edit=dict(p,version=p["version"],reason="تغییر مسئول برنامه")
        with self.assertRaises(ApiError) as e:self.pm.edit_plan(self.users["head"],p["id"],edit,INSTANT)
        self.assertEqual(e.exception.status,409)
        template=self.pm.template(self.users["head"],p["templateId"])
        with self.assertRaises(ApiError):self.pm.retire_template(self.users["head"],template["id"],dict(version=1,reason="حذف پس از بررسی"))
        self.assertEqual(self.pm.generate(INSTANT),2)
        revised=self.pm.edit_plan(self.users["head"],p["id"],edit,INSTANT)
        self.assertEqual(revised["version"],3)

    def test_template_snapshot_history_and_future_edits_do_not_rewrite_issued(self):
        t=self.custom();p=self.activate(t);self.pm.generate(INSTANT)
        with self.db.connect() as c:old=self.pm.issued_template(c,self.order_id())
        activity=next(c for c in info.cells(t) if c.get("activity"))
        revised=self.pm.edit_template(self.users["head"],t["id"],dict(version=1,reason="اصلاح فعالیت جدید",changes=[dict(cellId=activity["id"],text="فعالیت جدید")]))
        self.assertEqual(revised["version"],2)
        with self.db.connect() as c:self.assertEqual(self.pm.issued_template(c,self.order_id()),old)
        self.assertEqual(len(self.pm.history(self.users["head"],t["id"])["items"]),2)
        with self.assertRaises(ApiError):self.pm.edit_template(self.users["head"],t["id"],dict(version=1,reason="نسخه کهنه",changes=[dict(cellId=activity["id"],text="غلط")]))

    def test_assignment_owner_specialty_one_person_and_queue_scopes(self):
        self.activate();self.pm.generate(INSTANT);id=self.order_id()
        for user,assignee in (("sm2","m1"),("se","e1"),("head","m1"),("m1","m1"),("sm","e1")):
            with self.assertRaises(ApiError):self.pm.assign(self.users[user],id,dict(version=1,assigneeId=assignee))
        assigned=self.pm.assign(self.users["sm"],id,dict(version=1,assigneeId="m1"))
        self.assertEqual(assigned["assigneeId"],"m1")
        self.assertEqual(self.pm.tasks(self.users["m1"],instant=INSTANT)["total"],1)
        self.assertEqual(self.pm.tasks(self.users["m2"],instant=INSTANT)["total"],0)
        self.assertEqual(self.pm.tasks(self.users["sm2"],instant=INSTANT)["total"],0)
        self.assertEqual(self.pm.tasks(self.users["head"],instant=INSTANT)["total"],1)
        with self.assertRaises(ApiError):self.pm.assign(self.users["sm"],id,dict(version=1,assigneeId="m2"))

    def test_execution_original_results_only_single_time_row_and_complete_workflow(self):
        self.activate();self.pm.generate(INSTANT);id=self.order_id();assigned=self.pm.assign(self.users["sm"],id,dict(version=1,assigneeId="m1"))
        body=deepcopy(assigned);body.update(description="بازدید طبق برگه انجام شد",workDate="2026-09-26",startTime="08:00",endTime="09:00",participants=[dict(personId="m1",minutes=60)],parts=[],checklist=[])
        with self.db.connect() as c:sheet=self.pm.issued_template(c,id)
        answers={cell["id"]:"سالم" for cell in info.cells(sheet) if cell.get("editable")};body["pmResults"]=answers
        for bad in (dict(pmResults={"foreign":"سالم"}),dict(participants=[dict(personId="m1",minutes=30)]),dict(participants=[dict(personId="m1",minutes=60),dict(personId="m2",minutes=60)]),dict(title="تغییر غیرمجاز"),dict(assetId="a2"),dict(dueDate="2026-10-10")):
            test=deepcopy(body);test.update(bad)
            with self.assertRaises(ApiError):self.work.save(self.users["m1"],test,id)
        saved=self.work.save(self.users["m1"],body,id)
        self.assertEqual(saved["pmResults"],answers)
        with self.assertRaises(ApiError):self.pm.assign(self.users["sm"],id,dict(version=saved["version"],assigneeId="m2"))
        submitted=self.work.submit(self.users["m1"],id,saved["version"])
        with self.assertRaises(ApiError):self.work.decision(self.users["sm2"],id,dict(version=submitted["version"],decision="approve",grades=[dict(personId="m1",coefficient=Decimal("1.25"))]))
        head=self.work.decision(self.users["sm"],id,dict(version=submitted["version"],decision="approve",grades=[dict(personId="m1",coefficient=Decimal("1.25"))]))
        done=self.work.decision(self.users["head"],id,dict(version=head["version"],decision="approve",participantScores=[dict(personId="m1",coefficient=Decimal("2"))]))
        self.assertEqual(done["status"],"completed");self.assertEqual(done["score"],Decimal("2.50"))

    def test_overdue_exact_exclusive_boundary_and_late_execution(self):
        self.activate();self.pm.generate(INSTANT);id=self.order_id();deadline=cal.get_week(1405,7,1).utc_window()[1]
        self.assertEqual(self.pm.tasks(self.users["head"],overdue=True,instant=deadline-timedelta(microseconds=1))["total"],0)
        stats=self.pm.tasks(self.users["head"],overdue=True,instant=deadline)
        self.assertEqual(stats["total"],1);self.assertEqual(stats["counts"]["unassignedOverdue"],1)
        self.pm.assign(self.users["sm"],id,dict(version=1,assigneeId="m1"))
        stats=self.pm.tasks(self.users["head"],instant=deadline);self.assertEqual(stats["counts"]["assignedOverdue"],1)

    @patch("pak_maintenance.pm_template_lifecycle.datetime", wraps=datetime)
    def test_lifecycle_preserves_issued_work_and_restore_stays_inactive(self, clock):
        clock.now.return_value=INSTANT
        t=self.custom();self.activate(t);self.pm.generate(INSTANT)
        retired=self.pm.retire_template(self.users["head"],t["id"],dict(version=1,reason="توقف برگه قدیمی"))
        self.assertTrue(retired["retired"]);self.assertEqual(self.pm.templates(self.users["head"])["total"],0)
        self.assertEqual(self.pm.templates(self.users["head"],retired=True)["total"],1)
        restored=self.pm.restore_template(self.users["head"],t["id"],dict(version=2,reason="بازگردانی برگه"))
        self.assertFalse(restored["retired"]);self.assertFalse(self.pm.plan(self.users["head"],t["planId"])["active"])
        with self.db.connect() as c:self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"),1)

    def test_designer_activity_identity_and_structural_edit_requires_inactive(self):
        t=self.custom();activity=next(cell for cell in info.cells(t) if cell.get("activity"))
        payload=dict(version=1,reason="طراحی اصلاح شده",title=t["title"],hall=t["hall"],specialty=t["specialty"],period=t["period"],activities=[dict(id=activity["id"],text="اصلاح شرح"),"جدید"])
        saved=self.pm.design_template(self.users["head"],t["id"],payload)
        self.assertIn(activity["id"],{cell["id"] for cell in info.cells(saved)})
        self.activate(saved)
        payload["version"]=2
        with self.assertRaises(ApiError):self.pm.design_template(self.users["head"],t["id"],payload)

    def test_setup_durable_replay_changed_selection_and_all_past_required(self):
        t=self.custom();p=self.pm.edit_plan(self.users["head"],t["planId"],dict(version=1,reason="تطبیق قطعی",assetId="a1",supervisorId="sm",startDate="2026-09-23",slots=[dict(month=7,week=1)],active=False,mappingConfirmed=True),INSTANT)
        payload=dict(requestKey=str(uuid4()),reason="راه اندازی مصوب",startDate="2026-09-23",plans=[dict(id=p["id"],version=p["version"])])
        result=self.setup.activate(self.users["head"],payload,INSTANT)
        self.assertEqual((result["activated"],result["generated"]),(1,1))
        replay=self.setup.activate(self.users["head"],payload,INSTANT+timedelta(days=8))
        self.assertEqual((replay["alreadyApplied"],replay["generated"]),(1,0))
        payload["reason"]="تغییر انتخاب"
        with self.assertRaises(ApiError):self.setup.activate(self.users["head"],payload,INSTANT)
        payload["requestKey"]=str(uuid4());payload["includePast"]=False
        with self.assertRaises(ApiError):self.setup.activate(self.users["head"],payload,INSTANT)

    def test_original_hash_integrity_and_path_escaping(self):
        directory=self.directory/"seed";originals=directory/"pm-originals";originals.mkdir(parents=True);data=b"synthetic-original";hash=hashlib.sha256(data).hexdigest();path=originals/(hash+".xlsx");path.write_bytes(data)
        form=dict(source=dict(sha256=hash,path="../../old/اصل.xlsx"));resolved,name=self.pm._original(directory,form)
        self.assertEqual(resolved,path.resolve());self.assertEqual(name,"اصل.xlsx")
        path.write_bytes(b"changed")
        with self.assertRaises(ApiError) as e:self.pm._original(directory,form)
        self.assertEqual(e.exception.status,409)
        with self.assertRaises(ApiError):self.pm._original(directory,dict(source=dict(sha256="../"+hash,path="evil.exe")))

    def test_group_pump_roster_and_single_sheet_registry(self):
        t=self.custom();id=t["id"]
        source=deepcopy(t);source.update(version=2,formKind="grouped-inspection",origin="original",title="برگه پمپ های فرایند",assetMemberCodes=[],tables=[dict(sourceSheet="پمپ",rows=[dict(sourceRow=i,cells=[dict(id=f"c{i}-{col}",sourceRef=dict(column=col),text="نشتی" if i==3 and col==4 else "",activity=i==3 and col==4,editable=False) for col in range(1,10)]) for i in range(3,8)])])
        with self.db.transaction() as c:
            c.execute("UPDATE pm_templates SET version=2,body=:body WHERE id=:id",dict(body=dump(source),id=id))
            c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,2,:body,'Synthetic original-form fixture','head','2026-10-10T08:00:00.0000000+00:00')",dict(body=dump(source),id=id))
        self.assertTrue(info.empty_pump_roster(source));self.assertTrue(info.has_activities(source))
        p=dict(version=1,reason="تأیید پمپ ها",assetId="a1",supervisorId="sm",startDate="2026-09-23",slots=[dict(month=7,week=1)],active=True,mappingConfirmed=True,groupMemberCodes=["39-1","39-2"])
        self.pm.edit_plan(self.users["head"],t["planId"],p,INSTANT)
        forms=self.registry.forms(self.users["head"],family="pumps")
        self.assertEqual(forms["total"],1);self.assertEqual(forms["items"][0]["memberCount"],2)
        self.pm.generate(INSTANT)
        with self.db.connect() as c:self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_assets"),2)
        p.update(version=2,groupMemberCodes=["39-3"])
        with self.assertRaises(ApiError):self.pm.edit_plan(self.users["head"],t["planId"],p,INSTANT)

    def test_full_original_bundle_269_readonly_hashes_and_no_auto_activation(self):
        seed=ROOT/".build"/"acceptance-inputs-current"/"seed"
        if not (seed/"pm-import.json").is_file():self.skipTest("Original company input bundle is not part of this portable test checkout")
        result=self.pm.stage_import(self.users["head"],seed)
        self.assertEqual((result["inserted"],result["total"],result["activePlans"]),(269,269,0))
        self.assertEqual(self.pm.stage_import(self.users["head"],seed)["inserted"],0)
        forms=self.registry.forms(self.users["head"],page_size=100)
        self.assertEqual(forms["total"],269)
        self.assertEqual(self.pm.generate(INSTANT),0)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_weekly_plans WHERE active<>0 OR mapping_confirmed<>0"),0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_template_revisions"),269)
        all_forms=[]
        for page in (1,2,3):all_forms+=self.registry.forms(self.users["head"],page=page,page_size=100)["items"]
        self.assertEqual(len({f["templateId"] for f in all_forms}),269)
        pumps=[f for f in all_forms if f["family"]=="pumps"]
        self.assertTrue(pumps)
        self.assertTrue(all(f["periodClass"]=="monthly" for f in pumps))

    def test_registry_equipment_sql_pagination_specialty_and_missing_coverage(self):
        self.activate();self.pm.generate(INSTANT)
        first=self.registry.get(self.users["head"],page_size=1)
        second=self.registry.get(self.users["head"],page=2,page_size=1)
        self.assertEqual(first["total"],3);self.assertNotEqual(first["items"][0]["assetId"],second["items"][0]["assetId"])
        present=self.registry.get(self.users["head"],status="present")
        self.assertEqual(present["total"],1);self.assertEqual(present["summary"]["withoutForms"],2)
        self.assertEqual(self.registry.get(self.users["head"],status="missing")["total"],2)
        self.assertEqual(self.registry.get(self.users["se"])["total"],0)
        self.assertEqual(self.registry.get(self.users["head"],q="%_'")["total"],0)
        for user in (self.users["m1"],User("head",role="head",active=False)):
            with self.assertRaises(ApiError):self.registry.get(user)

    def test_pm_sql_guards_preserve_published_revisions_and_occurrence_identity(self):
        self.activate();self.pm.generate(INSTANT);id=self.order_id()
        raw=sqlite3.connect(self.path);raw.executescript((ROOT/"schema"/"sqlite-pm-guards.sql").read_text());raw.close()
        attacks=("UPDATE pm_template_revisions SET reason='tamper'", "DELETE FROM pm_template_revisions", "UPDATE pm_plan_revisions SET reason='tamper'", "DELETE FROM pm_plan_revisions", "UPDATE pm_weekly_occurrences SET end_utc='2099-01-01'", "UPDATE pm_weekly_occurrences SET supervisor_id='sm2'", "DELETE FROM pm_weekly_occurrences", "DELETE FROM pm_weekly_assets", "UPDATE pm_weekly_assets SET asset_id='a2'")
        for sql in attacks:
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.db.transaction() as c:c.execute(sql)
        with self.db.transaction() as c:
            c.execute("UPDATE pm_weekly_occurrences SET submitted_at=:time,executed_at=:time WHERE order_id=:id",dict(time=cal.utc_text(INSTANT),id=id))
        assigned=self.pm.assign(self.users["sm"],id,dict(version=1,assigneeId="m1"))
        self.assertEqual(assigned["assigneeId"],"m1")


if __name__=="__main__":unittest.main()
