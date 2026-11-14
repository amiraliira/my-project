"""Versioned PM templates, exact weekly issuance, assignment and execution."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime,date,time,timedelta,timezone
import hashlib
import json
from pathlib import Path,PureWindowsPath
import re
import uuid
from .common import ApiError,audit,now,dumps,loads
from . import pm_calendar as cal,pm_form_info as info,pm_schedule as schedule
from .pm_template_lifecycle import TemplateLifecycleMixin
from .pm_governance import PmGovernanceMixin
from .pm_responsibility import PmResponsibilityMixin, effective_owner, owner_sql
from .permissions import CHIEFS


def dump(value): return dumps(value)
def parse(value): return loads(value) if isinstance(value,str) else deepcopy(value)
def chief(user): return user.active and user.role in CHIEFS
def require_chief(user):
    if not chief(user): raise ApiError(403,"تأیید و انتشار تغییر فعالیت و زمان‌بندی فقط با رئیس است.")
def require_library(user):
    if not user.active or user.role not in ("head","manager","planner","admin","supervisor"): raise ApiError(403,"کتابخانه برنامه‌ریزی برای این حساب در دسترس نیست.")
def text(input,key,maximum=2000):
    value=input.get(key,"")
    if value is None: value=""
    if not isinstance(value,str): raise ApiError(400,"متن اطلاعات معتبر نیست.")
    value=value.strip()
    if len(value)>maximum: raise ApiError(400,"متن بیش از حد مجاز است.")
    return value
def reason(input):
    value=text(input,"reason")
    if len(value)<3: raise ApiError(400,"علت اصلاح یا تأیید تطبیق را وارد کنید.")
    return value
def version(input):
    if not isinstance(input,dict): raise ApiError(400,"اطلاعات درخواست معتبر نیست.")
    value=input.get("version",-1)
    if type(value) is not int: raise ApiError(400,"نسخه برگه باید عدد صحیح باشد.")
    return value
def bounds(page=None,size=None):
    page=1 if page is None else page;size=25 if size is None else size
    if type(page) is not int or type(size) is not int or not 1<=page<=1000000 or not 1<=size<=100: raise ApiError(400,"صفحه‌بندی نامعتبر است.")
    return page,size,(page-1)*size
def page_result(items,total,page,size,**other): return dict(items=items,total=total,page=page,pageSize=size,totalPages=(total+size-1)//size,**other)
def validate_date(value,label="تاریخ"):
    try:
        if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value): raise ValueError()
        return date.fromisoformat(value)
    except ValueError: raise ApiError(400,f"{label} معتبر نیست.") from None
def bool_value(input,key,default=False):
    value=input.get(key,default)
    if type(value) is not bool: raise ApiError(400,"گزینه باید بله یا خیر باشد.")
    return value
def require_version(row,input):
    if row["version"]!=version(input): raise ApiError(409,"برگه یا برنامه هم‌زمان تغییر کرده است؛ تازه‌سازی کنید.")
def template_view(row,detail=True):
    j=parse(row["body"]);retired=row["mapping_status"]=="retired"
    j.update(version=row["version"],mappingStatus=row["mapping_status"],planId="plan-"+row["id"],isCustom=j.get("origin")=="custom",hasOriginal=bool(re.fullmatch(r"[a-fA-F0-9]{64}",j.get("source",{}).get("sha256",""))),retired=retired,lifecycleState="retired" if retired else "active")
    if info.has_activities(j):j["issues"]=[i for i in j.get("issues",[]) if i!="no-actual-activities"]
    if detail:return j
    return {key:j.get(key) for key in ("id","planId","title","specialty","hall","period","version","mappingStatus","issues","isCustom","hasOriginal","retired","lifecycleState")}
def plan_view(row):
    j=parse(row["body"]);j.update(version=row["version"],active=row["active"]==1,mappingConfirmed=row["mapping_confirmed"]==1);return j
def issued_template(c,order_id):
    row=c.one("SELECT r.body FROM pm_weekly_occurrences p JOIN pm_template_revisions r ON r.template_id=p.template_id AND r.revision=p.template_version WHERE p.order_id=:id",{"id":order_id})
    return parse(row["body"]) if row else None
def execution_end(body):
    day=validate_date(body.get("workDate"))
    try:
        start=time.fromisoformat(body["startTime"]);end=time.fromisoformat(body["endTime"])
        if not re.fullmatch(r"\d{2}:\d{2}",body["startTime"]) or not re.fullmatch(r"\d{2}:\d{2}",body["endTime"]):raise ValueError()
        if end<start:day+=timedelta(days=1)
        return cal.utc_text(cal.local_execution_utc(datetime.combine(day,end)))
    except (ValueError,KeyError):raise ApiError(400,"زمان واقعی اجرای کار معتبر یا بدون ابهام نیست.") from None


def preserve_execution(c,old,input,normalized,user,submitted):
    occurrence=c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id",{"id":old["id"]})
    if occurrence is None:return
    original=parse(old["body"])
    if normalized.get("title")!=old["title"]:raise ApiError(403,"عنوان برگه هفتگی از نسخه اصلی آن تعیین می‌شود.")
    if normalized.get("type")!="PM" or (normalized.get("assetId") or "")!=(old.get("asset_id") or "") or (normalized.get("assigneeId") or "")!=(old.get("assignee_id") or "") or (normalized.get("dueDate") or "")!=(old.get("due_date") or ""):raise ApiError(403,"دستگاه، موعد و تخصیص برگه هفتگی از برنامه و سرپرست تعیین می‌شود.")
    for key in ("pmWeeklyPlanId","pmSheet","pmWindow","pmSpecialty","pmSupervisorId","pmAssetCodes"):normalized[key]=deepcopy(original.get(key))
    participants=normalized.get("participants",[])
    if len(participants)>1 or any(p.get("personId")!=old.get("assignee_id") or p.get("specialty")!=occurrence["specialty"] for p in participants):raise ApiError(400,"این برگه فقط یک مجری از همان تخصص دارد.")
    if participants and participants[0].get("minutes")!=normalized.get("durationMinutes"):raise ApiError(400,"زمان یک مجری باید برابر ردیف ساعت کار برگه باشد.")
    if normalized.get("checklist"):raise ApiError(400,"نتایج در قالب اصلی برگه ثبت می‌شوند.")
    template=issued_template(c,old["id"])
    if template is None:raise ApiError(409,"نسخهٔ معتبر برگه موجود نیست.")
    allowed={cell["id"] for cell in info.cells(template) if cell.get("editable") is True}
    results=input.get("pmResults",{})
    if not isinstance(results,dict) or len(results)>1000:raise ApiError(400,"نتایج برگه باید یک شیء معتبر و محدود باشند.")
    saved={}
    for key,value in results.items():
        if key not in allowed or not isinstance(value,str) or len(value)>2000:raise ApiError(400,"نتیجه خارج از خانه‌های اصلی یا نامعتبر است.")
        saved[key]=value.strip()
    normalized["pmResults"]=saved
    if submitted:
        if user.role!="technician" or user.id!=old.get("assignee_id"):raise ApiError(403,"ارسال اجرای برگه با همان تکنسین تخصیص‌یافته است.")
        if len(participants)!=1 or normalized.get("durationMinutes",0)<=0:raise ApiError(400,"مجری و یک ردیف زمان کار را کامل کنید.")
        if not allowed or any(not saved.get(key) for key in allowed):raise ApiError(400,"نتیجه همه خانه‌های بررسی برگه اصلی را ثبت کنید.")
        current=datetime.now(timezone.utc)
        if cal.parse_utc(execution_end(normalized))>current:raise ApiError(400,"زمان پایان کار در آینده است؛ ساعت واقعی را ثبت کنید.")
        day=validate_date(normalized.get("workDate"));start=validate_date(original["pmWindow"]["startDate"])
        if not start<=day<=cal.tehran_date(current):raise ApiError(400,"تاریخ اجرای کار باید از شروع بازه تا امروز باشد.")


class PmService(TemplateLifecycleMixin, PmGovernanceMixin, PmResponsibilityMixin):
    issued_template=staticmethod(issued_template)
    execution_end=staticmethod(execution_end)
    preserve_execution=staticmethod(preserve_execution)
    chief=staticmethod(chief)
    cells=staticmethod(info.cells)

    def __init__(self,db,work=None):self.db=db;self.work=work

    def templates(self,user,page=None,size=None,q=None,retired=False):
        require_library(user)
        # Retired templates remain operational references for planning proposals.
        page,size,offset=bounds(page,size)
        if q is not None and len(q)>300:raise ApiError(400,"جستجو بیش از حد طولانی است.")
        where=" WHERE mapping_status"+("='retired'" if retired else "<>'retired'")+" AND (CAST(:spec AS TEXT) IS NULL OR specialty=:spec) AND (title LIKE :q ESCAPE '~' OR hall LIKE :q ESCAPE '~' OR period LIKE :q ESCAPE '~')"
        params=dict(q="%"+(q or "").replace("~","~~").replace("%","~%").replace("_","~_")+"%",spec=user.specialty if user.role=="supervisor" else None,size=size,offset=offset)
        with self.db.transaction(write=False) as c:
            total=c.scalar("SELECT COUNT(*) FROM pm_templates"+where,params)
            items=[template_view(r,False) for r in c.query("SELECT * FROM pm_templates"+where+" ORDER BY title,id LIMIT :size OFFSET :offset",params)]
            return page_result(items,total,page,size)

    def template(self,user,id):
        require_library(user)
        with self.db.connect() as c:
            row=c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id))
            if row is None:raise ApiError(404,"قالب پیدا نشد.")
            if user.role=="supervisor" and row["specialty"]!=user.specialty:raise ApiError(403,"قالب خارج از تخصص شما است.")
            return template_view(row)

    def plans(self,user,page=None,size=None,q=None):
        require_library(user);page,size,offset=bounds(page,size)
        if q is not None and len(q)>300:raise ApiError(400,"جستجو بیش از حد طولانی است.")
        where=" WHERE f.mapping_status<>'retired' AND (CAST(:spec AS TEXT) IS NULL OR p.specialty=:spec) AND (f.title LIKE :q ESCAPE '~' OR p.hall LIKE :q ESCAPE '~')"
        params=dict(q="%"+(q or "").replace("~","~~").replace("%","~%").replace("_","~_")+"%",spec=user.specialty if user.role=="supervisor" else None,size=size,offset=offset)
        with self.db.transaction(write=False) as c:
            total=c.scalar("SELECT COUNT(*) FROM pm_weekly_plans p JOIN pm_templates f ON f.id=p.template_id"+where,params)
            rows=c.query("SELECT p.* FROM pm_weekly_plans p JOIN pm_templates f ON f.id=p.template_id"+where+" ORDER BY f.title,p.id LIMIT :size OFFSET :offset",params)
            return page_result([plan_view(r) for r in rows],total,page,size)

    def plan(self,user,id):
        require_library(user)
        with self.db.transaction(write=False) as c:
            row=c.one("SELECT * FROM pm_weekly_plans WHERE id=:id",dict(id=id))
            if row is None:raise ApiError(404,"برنامه پیدا نشد.")
            if user.role=="supervisor" and row["specialty"]!=user.specialty:raise ApiError(403,"برنامه خارج از تخصص شما است.")
            j=plan_view(row);template=parse(c.scalar("SELECT body FROM pm_templates WHERE id=:id",dict(id=row["template_id"])))
            j.update(info.metadata(template));codes=info.member_codes(template)
            members={a["code"]:a for a in info.lookup_assets(c,codes)}
            j.update(memberCodes=codes,memberCount=len(codes),assetCount=len(members),members=[dict(code=code,assetId=members.get(code,{}).get("id"),name=members.get(code,{}).get("name"),hall=members.get(code,{}).get("hall")) for code in codes])
            info.apply_hall_projection(j,[a["hall"] for a in members.values()]);j["equipmentLabel"]=info.equipment_label(j,{k:a["name"] for k,a in members.items()})
            j.update(identityStatus="working-roster" if info.empty_pump_roster(template) else "unlinked" if not codes else "missing-members" if len(members)<len(codes) else "present",groupedSheet=template.get("formKind")=="grouped-inspection" or len(codes)>1,templateVersion=template.get("version"),nonCalendarBlockers=schedule.non_calendar_blockers(template),recurrence=j.get("recurrence") or dict(mode="annual-slots"))
            p=info.period(template);j["sourcePeriodSuggestion"]=dict(unit="month",interval=p.interval_months) if p.interval_months is not None else dict(unit="week",interval=1) if p.period_class=="weekly" else None
            j["assetLabel"]=c.scalar("SELECT code||' · '||name FROM assets WHERE id=:id",dict(id=row.get("asset_id"))) if row.get("asset_id") else None
            j["supervisorLabel"]=c.scalar("SELECT name FROM users WHERE id=:id",dict(id=row.get("supervisor_id"))) if row.get("supervisor_id") else None
            return j

    def history(self,user,id,template=True):
        require_library(user)
        table,revisions,column=("pm_templates","pm_template_revisions","template_id") if template else ("pm_weekly_plans","pm_plan_revisions","plan_id")
        with self.db.connect() as c:
            row=c.one(f"SELECT specialty FROM {table} WHERE id=:id",dict(id=id))
            if row is None:raise ApiError(404,"برگه یا برنامه پیدا نشد.")
            if user.role=="supervisor" and row["specialty"]!=user.specialty:raise ApiError(403,"سابقه خارج از تخصص شما است.")
            rows=c.query(f"SELECT r.revision,r.reason,r.created_at,u.name AS actor_name FROM {revisions} r JOIN users u ON u.id=r.actor_id WHERE r.{column}=:id ORDER BY r.revision DESC LIMIT 20",dict(id=id))
            return dict(items=[dict(version=r["revision"],reason=r["reason"],createdAt=r["created_at"],actorName=r["actor_name"]) for r in rows],limit=20)

    def preview_activities(self,user,id,input):
        from .permissions import PM_PROPOSERS
        from .security import require
        from .pm_activity_structure import apply_activity_structure
        require(user,*CHIEFS,*PM_PROPOSERS)
        current=self.template(user,id)
        if current.get("retired"):raise ApiError(409,"این برگه حذف شده است؛ ابتدا آن را بازگردانید.")
        if version(input)!=current["version"]:raise ApiError(409,"نسخهٔ برگه تغییر کرده است؛ آن را تازه کنید.")
        proposed=apply_activity_structure(current,input.get("activities"))
        proposed["version"]=current["version"]+1
        return proposed

    def edit_template(self,user,id,input):
        require_chief(user);why=reason(input);changes=input.get("changes")
        structural="activities" in input
        if not structural and (not isinstance(changes,list) or not 1<=len(changes)<=1000):raise ApiError(400,"فهرست اصلاح فعالیت نامعتبر است.")
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            row=c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id))
            if row is None:raise ApiError(404,"قالب پیدا نشد.")
            if row["mapping_status"]=="retired":raise ApiError(409,"این برگه حذف شده است؛ ابتدا آن را بازگردانید.")
            require_version(row,input);j=parse(row["body"]);j["version"]=row["version"]
            if structural:
                from .pm_activity_structure import apply_activity_structure
                j=apply_activity_structure(j,input["activities"])
            else:
                cells={cell["id"]:cell for cell in info.cells(j)};seen=set()
                for change in changes:
                    if not isinstance(change,dict):raise ApiError(400,"فهرست اصلاح فعالیت نامعتبر است.")
                    key,value=text(change,"cellId",120),text(change,"text",10000)
                    if key in seen or key not in cells or cells[key].get("activity") is not True or not value:raise ApiError(400,"فقط شرح فعالیت موجود را با متن معتبر اصلاح کنید.")
                    cells[key]["text"]=value;seen.add(key)
            j["version"]=row["version"]+1
            if c.execute("UPDATE pm_templates SET body=:body,version=version+1 WHERE id=:id AND version=:version",dict(id=id,version=row["version"],body=dump(j)))!=1:raise ApiError(409,"قالب هم‌زمان تغییر کرده است.")
            c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,:revision,:body,:reason,:actor,:created)",dict(id=id,revision=j["version"],body=dump(j),reason=why,actor=user.id,created=now()))
            audit(c,user,"revise-pm-template","pm-template",id,dict(previous=row["version"],revision=j["version"],reason=why,changes=changes,activities=input.get("activities") if structural else None,appliesTo="future-occurrences"));return j
        return self.db.run_transaction(operation)

    def has_due(self,instant=None,actor=None):
        instant=instant or datetime.now(timezone.utc)
        with self.db.connect() as c:
            return bool(c.scalar("SELECT CASE WHEN EXISTS(SELECT 1 FROM pm_weekly_plans p JOIN pm_templates f ON f.id=p.template_id AND f.mapping_status<>'retired' JOIN users u ON u.id=p.supervisor_id AND u.active=1 AND u.role='supervisor' AND u.specialty=p.specialty WHERE p.active=1 AND p.mapping_confirmed=1 AND p.next_scan<=:today"+(" AND p.supervisor_id=:owner" if actor and actor.role=="supervisor" else "")+") THEN 1 ELSE 0 END",dict(today=cal.tehran_date(instant).isoformat(),owner=actor.id if actor else None)))

    def generate(self,instant=None,actor=None):
        if actor and (not actor.active or actor.role not in ("head","manager","admin","planner","supervisor")):raise ApiError(403,"صدور برنامه برای این حساب مجاز نیست.")
        instant=instant or datetime.now(timezone.utc);today=cal.tehran_date(instant);iso=today.isoformat()
        def operation(c):
            if actor:
                from .security import assert_live_actor
                assert_live_actor(c,actor)
            count=0
            plans=c.query("SELECT p.* FROM pm_weekly_plans p JOIN pm_templates f ON f.id=p.template_id AND f.mapping_status<>'retired' JOIN users u ON u.id=p.supervisor_id AND u.active=1 AND u.role='supervisor' AND u.specialty=p.specialty WHERE p.active=1 AND p.mapping_confirmed=1 AND p.next_scan<=:today"+(" AND p.supervisor_id=:owner" if actor and actor.role=="supervisor" else "")+" ORDER BY p.next_scan,p.id LIMIT 25"+("" if c.is_sqlite else " FOR UPDATE OF p"),dict(today=iso,owner=actor.id if actor else None))
            for row in plans:
                p=parse(row["body"]);cursor=row["cursor_month"];current=cal.month_key(today);next_scan=iso;activation=date.fromisoformat(row["start_date"]);effective=cal.parse_utc(p["effectiveFromUtc"])
                for _ in range(24):
                    if cursor>current or count>=100:break
                    future=[]
                    for slot in schedule.slots_for_month(p,cursor//100,cursor%100):
                        window=cal.get_week(cursor//100,slot.month,slot.ordinal);start_utc,end_utc=window.utc_window()
                        if window.start_date<activation or start_utc<effective:continue
                        if start_utc>instant:future.append(window.start_date);continue
                        identity=dict(id=row["id"],year=window.persian_year,month=slot.month,week=slot.ordinal)
                        if c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences WHERE plan_id=:id AND persian_year=:year AND month=:month AND week=:week",identity):continue
                        args=dict(template=row["template_id"],minimum=p.get("minimumTemplateVersion",1),start=cal.utc_text(start_utc))
                        revision=c.one("SELECT revision,body FROM pm_template_revisions WHERE template_id=:template AND revision>=:minimum AND created_at<=:start ORDER BY revision DESC LIMIT 1",args) or c.one("SELECT revision,body FROM pm_template_revisions WHERE template_id=:template AND revision>=:minimum ORDER BY revision LIMIT 1",args)
                        if revision is None:raise ApiError(409,"نسخهٔ منتشرشدهٔ برگه پیدا نشد؛ صدور متوقف شد.")
                        template=parse(revision["body"]);order=str(uuid.uuid4());created=cal.utc_text(instant)
                        body=dict(type="PM",assetId=row["asset_id"],title=template.get("title",""),description="",workDate=window.start_date.isoformat(),dueDate=window.end_date.isoformat(),startTime="",endTime="",durationMinutes=0,assigneeId=None,participants=[],parts=[],checklist=[],downtimeMinutes=0,productionStopped=False,pmWeeklyPlanId=row["id"],pmSheet=dict(id=row["template_id"],version=revision["revision"],source=deepcopy(template.get("source"))),pmResults={},pmAssetCodes=deepcopy(p.get("assetCodes",[])),pmWindow=dict(year=window.persian_year,month=slot.month,week=slot.ordinal,startDate=window.start_date.isoformat(),endDate=window.end_date.isoformat(),startUtc=cal.utc_text(start_utc),endUtc=cal.utc_text(end_utc)),pmSpecialty=row["specialty"],pmSupervisorId=row["supervisor_id"])
                        c.execute("INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at) VALUES(:id,:number,1,1,'PM','draft',:asset,:owner,NULL,:start,:end,:body,:title,'',0,:created,:created)",dict(id=order,number=f"PMW-{window.key}-{order.replace('-','')}",asset=row["asset_id"],owner=row["supervisor_id"],start=window.start_date.isoformat(),end=window.end_date.isoformat(),body=dump(body),title=body["title"],created=created))
                        for member in p.get("assetIds",[]):c.execute("INSERT INTO pm_weekly_assets(order_id,asset_id) VALUES(:order,:member)",dict(order=order,member=member))
                        args=dict(identity,order=order,version=row["version"],template=row["template_id"],templateVersion=revision["revision"],start=cal.utc_text(start_utc),end=cal.utc_text(end_utc),spec=row["specialty"],owner=row["supervisor_id"])
                        c.execute("INSERT INTO pm_weekly_occurrences(plan_id,persian_year,month,week,order_id,plan_version,template_id,template_version,start_utc,end_utc,specialty,supervisor_id) VALUES(:id,:year,:month,:week,:order,:version,:template,:templateVersion,:start,:end,:spec,:owner)",args)
                        audit(c,None,"issue-weekly-pm","order",order,dict(planId=row["id"],planVersion=row["version"],templateId=row["template_id"],templateVersion=revision["revision"],slot=window.key,supervisorId=row["supervisor_id"]))
                        count+=1
                        if count>=100:break
                    if count>=100:next_scan=iso;break
                    if future:next_scan=min(future).isoformat();break
                    cursor=cal.next_month(cursor);next_scan=cal.from_persian(cursor//100,cursor%100,1).isoformat()
                c.execute("UPDATE pm_weekly_plans SET cursor_month=:cursor,next_scan=:next WHERE id=:id AND version=:version",dict(cursor=cursor,next=next_scan,id=row["id"],version=row["version"]))
            return count
        return self.db.run_transaction(operation)

    def assign(self,user,id,input):
        person=text(input,"assigneeId",100)
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            row=c.one("SELECT * FROM orders WHERE id=:id",dict(id=id))
            if row is None:raise ApiError(404,"برگه پیدا نشد.")
            require_version(row,input)
            occurrence=c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id",dict(id=id))
            if occurrence is None:raise ApiError(404,"برگه هفتگی پیدا نشد.")
            if user.role!="supervisor" or user.id!=effective_owner(c,id,occurrence["supervisor_id"]):raise ApiError(403,"تخصیص این برگه با سرپرست مسئول آن است.")
            if row["status"]!="draft" or c.scalar("SELECT COUNT(*) FROM participants WHERE order_id=:id",dict(id=id)):raise ApiError(409,"پس از ثبت کار یا ارسال گزارش، تخصیص قابل تغییر نیست.")
            if not c.scalar("SELECT COUNT(*) FROM users WHERE id=:person AND active=1 AND role='technician' AND specialty=:spec",dict(person=person,spec=occurrence["specialty"])):raise ApiError(400,"یک تکنسین فعال همان تخصص انتخاب کنید.")
            j=parse(row["body"])
            if any(str(value).strip() for value in j.get("pmResults",{}).values()):raise ApiError(409,"پس از ثبت نتیجه، تغییر مجری به بازبینی جداگانه نیاز دارد.")
            j["assigneeId"]=person
            if c.execute("UPDATE orders SET assignee_id=:person,body=:body,version=version+1,updated_at=:now WHERE id=:id AND version=:version",dict(id=id,person=person,body=dump(j),now=now(),version=row["version"]))!=1:raise ApiError(409,"برگه هم‌زمان تغییر کرده است.")
            audit(c,user,"assign-weekly-pm","order",id,dict(previous=row.get("assignee_id"),assignee=person))
            updated=c.one("SELECT * FROM orders WHERE id=:id",dict(id=id))
            if self.work is None:raise RuntimeError("WorkService must be injected for assignment API")
            return self.work.view(c,updated,user,detail=True)
        return self.db.run_transaction(operation)

    def tasks(self,user,page=None,size=None,overdue=False,view=None,instant=None):
        if not user.active or user.role not in ("head","manager","admin","planner","supervisor","technician"):raise ApiError(403,"کارتابل برای این حساب در دسترس نیست.")
        page,size,offset=bounds(page,size);instant=instant or datetime.now(timezone.utc)
        states={None:"","":"","all":"","pending":" AND o.status IN ('draft','returned')","submitted":" AND o.status NOT IN ('draft','returned','completed','cancelled')","completed":" AND o.status IN ('completed','cancelled')"}
        if view not in states:raise ApiError(400,"نمای برگه‌های PM نامعتبر است.")
        scope="1=1" if user.role in ("head","manager","admin","planner") else owner_sql("p")+"=:viewer" if user.role=="supervisor" else "o.assignee_id=:viewer"
        from_sql=" FROM pm_weekly_occurrences p JOIN orders o ON o.id=p.order_id";where=" WHERE "+scope
        params=dict(viewer=user.id,now=cal.utc_text(instant),size=size,offset=offset)
        with self.db.transaction(write=False) as c:
            row=c.one("SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN o.status IN ('draft','returned') THEN 1 ELSE 0 END),0) AS pending,COALESCE(SUM(CASE WHEN o.status NOT IN ('draft','returned','completed','cancelled') THEN 1 ELSE 0 END),0) AS submitted,COALESCE(SUM(CASE WHEN o.status IN ('completed','cancelled') THEN 1 ELSE 0 END),0) AS completed,COALESCE(SUM(CASE WHEN p.end_utc<=:now AND o.status IN ('draft','returned') THEN 1 ELSE 0 END),0) AS overdue,COALESCE(SUM(CASE WHEN p.end_utc<=:now AND o.status IN ('draft','returned') AND o.assignee_id IS NULL THEN 1 ELSE 0 END),0) AS unassigned_overdue,COALESCE(SUM(CASE WHEN p.end_utc<=:now AND o.status IN ('draft','returned') AND o.assignee_id IS NOT NULL THEN 1 ELSE 0 END),0) AS assigned_overdue,COALESCE(SUM(CASE WHEN p.executed_at>=p.end_utc AND o.status<>'cancelled' THEN 1 ELSE 0 END),0) AS late_executed"+from_sql+where,params)
            counts=dict(total=row["total"],pending=row["pending"],submitted=row["submitted"],completed=row["completed"],overdue=row["overdue"],unassignedOverdue=row["unassigned_overdue"],assignedOverdue=row["assigned_overdue"],lateExecuted=row["late_executed"])
            total=(0 if view in ("submitted","completed") else counts["overdue"]) if overdue else counts[view] if view in ("pending","submitted","completed") else counts["total"]
            filter=where+states[view]+(" AND p.end_utc<=:now AND o.status IN ('draft','returned')" if overdue else "")
            order="p.end_utc ASC,o.id" if view=="pending" or overdue else "p.start_utc DESC,o.id"
            rows=c.query("SELECT o.*"+from_sql+filter+" ORDER BY "+order+" LIMIT :size OFFSET :offset",params)
            if self.work is None:raise RuntimeError("WorkService must be injected for task API")
            items=self.work.view_batch(c,rows,user)
            return page_result(items,total,page,size,counts=counts)

    def stage_import(self,user,seed_directory):
        require_chief(user)
        root=Path(seed_directory).resolve();path=root/"pm-import.json"
        if not path.is_file():raise ApiError(409,"بسته برگه‌ها در این نسخه نصب نشده است.")
        if path.stat().st_size>32*1024*1024:raise ApiError(413,"بسته برگه‌ها بیش از حد بزرگ است.")
        try:seed=json.loads(path.read_text(encoding="utf-8-sig"));templates=seed.get("templates",[])
        except (ValueError,UnicodeError):raise ApiError(400,"بستهٔ برگه‌ها JSON معتبر نیست.") from None
        if not isinstance(templates,list) or len(templates)>1000:raise ApiError(413,"ورود باید در بسته محدود انجام شود.")
        for item in templates:
            self._original(root,item)
            if sum(1 for _ in info.cells(item))>10000:raise ApiError(413,"ساختار برگه بیش از حد بزرگ است.")
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            count=0;day="2026-09-23"
            for item in templates:
                j=deepcopy(item);id=j.get("id","")
                if not isinstance(id,str) or not 3<=len(id)<=100:raise ApiError(400,"شناسه قالب نامعتبر است.")
                if c.scalar("SELECT COUNT(*) FROM pm_templates WHERE id=:id",dict(id=id)):continue
                spec={"electrical":"برق","mechanical":"مکانیک","utilities":"تاسیسات"}.get(j.get("specialty"),j.get("specialty"))
                if spec not in ("برق","مکانیک","تاسیسات"):raise ApiError(400,"تخصص برگه نامعتبر است.")
                j.update(specialty=spec,version=1)
                params=dict(id=id,title=j.get("title",""),spec=spec,hall=j.get("hall",""),period=j.get("period",""),mapping=j.get("mappingStatus","pending"),body=dump(j),actor=user.id,now=now())
                c.execute("INSERT INTO pm_templates(id,title,specialty,hall,period,version,mapping_status,body) VALUES(:id,:title,:spec,:hall,:period,1,:mapping,:body)",params)
                c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,1,:body,'ورود اصل برگه برای بازبینی',:actor,:now)",params)
                p=dict(id="plan-"+id,version=1,templateId=id,templateTitle=j.get("title",""),title=j.get("title",""),specialty=spec,hall=j.get("hall",""),assetId=None,supervisorId=None,startDate=day,slots=deepcopy(j.get("slots",[])),candidateSlots=deepcopy(j.get("candidateSlots")),active=False,mappingConfirmed=False,issues=deepcopy(j.get("issues",[])),calendarEligible=j.get("calendarEligible",True),effectiveFromUtc=None)
                for key in ("sourceRow","sourceSheet","candidateCalendarSources","scheduleSourceConflicts","originalCodeDeclarations","calendarSource"):p[key]=deepcopy(j.get(key))
                pp=dict(params,plan=p["id"],date=day,cursor=cal.month_key(date.fromisoformat(day)),body=dump(p))
                c.execute("INSERT INTO pm_weekly_plans(id,template_id,asset_id,supervisor_id,specialty,hall,version,active,mapping_confirmed,start_date,cursor_month,next_scan,body) VALUES(:plan,:id,NULL,NULL,:spec,:hall,1,0,0,:date,:cursor,:date,:body)",pp)
                c.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) VALUES(:plan,1,:body,'ورود زمان‌بندی برای بازبینی؛ غیرفعال',:actor,:now)",pp);count+=1
            audit(c,user,"stage-pm-import","pm-library","source-bundle",dict(inserted=count,total=len(templates),activePlans=0));return dict(inserted=count,total=len(templates),activePlans=0)
        return self.db.run_transaction(operation)

    @staticmethod
    def _original(root,form):
        source=form.get("source",{});hash=source.get("sha256","");name=PureWindowsPath(source.get("path","")).name;extension=Path(name).suffix
        if not re.fullmatch(r"[a-fA-F0-9]{64}",hash) or extension not in (".docx",".xlsx"):raise ApiError(404,"اصل برگه معتبر نیست.")
        directory=(Path(root)/"pm-originals").resolve();path=(directory/(hash+extension)).resolve()
        if path.parent!=directory or not path.is_file():raise ApiError(404,"اصل برگه در بسته نصب موجود نیست.")
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        if digest.hexdigest()!=hash.lower():raise ApiError(409,"اصل برگه تغییر کرده است؛ بسته معتبر را بازیابی کنید.")
        return path,name

    def original(self,user,id,seed_directory,order_id=None):
        if order_id is None:form=self.template(user,id)
        else:
            if self.work is None:raise RuntimeError("WorkService must be injected for original download")
            with self.db.connect() as c:
                row=self.work.get_row(c,order_id,user)
                form=issued_template(c,order_id) or parse(row["body"]).get("pmSheet")
                if form is None:raise ApiError(404,"اصل این برگه موجود نیست.")
        return self._original(seed_directory,form)

    def require_drained(self,c,instant,plan_id=None,template_id=None):
        today=cal.tehran_date(instant);current=cal.month_key(today)
        rows=c.query("SELECT id,start_date,cursor_month,body FROM pm_weekly_plans WHERE active=1 AND mapping_confirmed=1 AND next_scan<=:today AND (CAST(:plan AS TEXT) IS NULL OR id=:plan) AND (CAST(:template AS TEXT) IS NULL OR template_id=:template) ORDER BY id LIMIT 26",dict(today=today.isoformat(),plan=plan_id,template=template_id))
        def pending():raise ApiError(409,"صدور برگه‌های سررسیدشدهٔ قبلی هنوز کامل نشده است. ابتدا برگه‌های سررسیدشده را صادر کنید، سپس صفحه را تازه و تغییر را ثبت کنید.")
        if len(rows)>25:pending()
        for row in rows:
            p=parse(row["body"]);cursor=row["cursor_month"];activation=date.fromisoformat(row["start_date"]);effective=cal.parse_utc(p["effectiveFromUtc"])
            for _ in range(24):
                if cursor>current:break
                for slot in schedule.slots_for_month(p,cursor//100,cursor%100):
                    window=cal.get_week(cursor//100,slot.month,slot.ordinal);start,_=window.utc_window()
                    if window.start_date<activation or start<effective or start>instant:continue
                    if not c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences WHERE plan_id=:id AND persian_year=:year AND month=:month AND week=:week",dict(id=row["id"],year=window.persian_year,month=slot.month,week=slot.ordinal)):pending()
                cursor=cal.next_month(cursor)
            if cursor<=current:pending()

    def edit_plan(self,user,id,input,instant=None):
        require_chief(user);why=reason(input);start=validate_date(text(input,"startDate",10),"شروع برنامه");asset=text(input,"assetId",100);owner=text(input,"supervisorId",100)
        issue=validate_date(text(input,"issueFromDate",10),"شروع صدور برگه‌ها") if input.get("issueFromDate") is not None else None
        if issue and issue<start:raise ApiError(400,"شروع صدور برگه‌ها نباید پیش از شروع برنامه باشد.")
        setup_key,setup_hash=text(input,"setupRequestKey",50),text(input,"setupPayloadHash",64)
        if (setup_key or setup_hash) and (not re.fullmatch(r"[A-Za-z0-9._-]{16,50}",setup_key) or not re.fullmatch(r"[a-fA-F0-9]{64}",setup_hash)):raise ApiError(400,"شناسه راه‌اندازی برنامه معتبر نیست.")
        active,confirmed=bool_value(input,"active"),bool_value(input,"mappingConfirmed");slots=input.get("slots")
        if not isinstance(slots,list) or len(slots)>48:raise ApiError(400,"نوبت‌های برنامه باید یک فهرست معتبر حداکثر ۴۸ نوبت باشند.")
        canonical=[];seen=set()
        for slot in slots:
            if not isinstance(slot,dict):raise ApiError(400,"نوبت تقویم نامعتبر است.")
            month,week=slot.get("month"),slot.get("week")
            if type(month) is not int or type(week) is not int or not 1<=month<=12 or not 1<=week<=4 or (month,week) in seen:raise ApiError(400,"نوبت تکراری یا خارج از ۱۲ ماه و چهار هفته است.")
            canonical.append(dict(month=month,week=week));seen.add((month,week))
        if active and (not confirmed or not owner):raise ApiError(400,"فعال‌سازی به تأیید تطبیق، تجهیز و سرپرست معتبر نیاز دارد.")
        instant=instant or datetime.now(timezone.utc)
        def operation(c):
            nonlocal asset
            from .security import assert_live_actor
            assert_live_actor(c,user)
            row=c.one("SELECT * FROM pm_weekly_plans WHERE id=:id",dict(id=id))
            if row is None:raise ApiError(404,"برنامه پیدا نشد.")
            require_version(row,input);j=parse(row["body"]);self.require_drained(c,instant,plan_id=id)
            if asset and not c.scalar("SELECT COUNT(*) FROM assets WHERE id=:asset",dict(asset=asset)):raise ApiError(400,"تجهیز معتبر انتخاب کنید.")
            tr=c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=row["template_id"]))
            template=parse(tr["body"])
            if tr["mapping_status"]=="retired" or template.get("lifecycleState")=="retired":raise ApiError(409,"این برگه حذف شده است؛ ابتدا آن را بازگردانید.")
            if input.get("groupMemberCodes") is not None:
                roster=input["groupMemberCodes"]
                if not isinstance(roster,list) or any(not isinstance(code,str) for code in roster):raise ApiError(400,"فهرست کد پمپ‌ها نامعتبر است.")
                roster=[code.strip() for code in roster]
                if not roster and template.get("workingRoster") is not None:raise ApiError(400,"فهرست پمپ‌های تأییدشده نباید خالی شود.")
                if roster and info.apply_working_roster(template,roster,c):
                    tv=tr["version"];template["version"]=tv+1;j["minimumTemplateVersion"]=tv+1
                    if c.execute("UPDATE pm_templates SET body=:body,version=version+1 WHERE id=:id AND version=:version",dict(body=dump(template),id=tr["id"],version=tv))!=1:raise ApiError(409,"برگه هم‌زمان تغییر کرده است.")
                    c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,:revision,:body,:reason,:actor,:created)",dict(id=tr["id"],revision=tv+1,body=dump(template),reason=why,actor=user.id,created=now()))
                    audit(c,user,"revise-pm-group-roster","pm-template",tr["id"],dict(previous=tv,revision=tv+1,reason=why,memberCodes=roster,appliesTo="future-occurrences"))
            ids=[];codes=[];grouped=template.get("formKind")=="grouped-inspection" or len(template.get("assetMemberCodes",[]))>1
            if grouped:
                declared=info.member_codes(template) or list(dict.fromkeys(x.get("value","") for x in template.get("originalCodeDeclarations",[]) if x.get("kind")=="group-member-row"))
                for code in declared:
                    member=c.scalar("SELECT id FROM assets WHERE code=:code",dict(code=code))
                    if not member:
                        if active:raise ApiError(409,f"تجهیز {code} برگه گروهی در شناسنامه تجهیزات پیدا نشد.")
                        continue
                    ids.append(member);codes.append(code)
                if not asset and ids:asset=ids[0]
                if active and (not declared or asset not in ids):raise ApiError(400,"تجهیز مرجع باید عضو فهرست واقعی همین برگه گروهی باشد.")
            elif asset:ids.append(asset);codes.append(c.scalar("SELECT code FROM assets WHERE id=:id",dict(id=asset)))
            if active and not asset:raise ApiError(400,"تجهیز معتبر انتخاب کنید.")
            if owner and not c.scalar("SELECT COUNT(*) FROM users WHERE id=:id AND active=1 AND role='supervisor' AND specialty=:spec",dict(id=owner,spec=row["specialty"])):raise ApiError(400,"سرپرست فعال همان تخصص انتخاب کنید.")
            recurrence=schedule.normalize(input.get("recurrence",j.get("recurrence")),template);source=recurrence["mode"]=="source-period"
            if active and schedule.non_calendar_blockers(template):raise ApiError(409,"برگه فعالیت اجرایی یا اعضای معتبر ندارد؛ فعال‌سازی ممکن نیست.")
            if active and source and recurrence["meterPolicy"]!="calendar-only":raise ApiError(409,"موتور ساعت دستگاه هنوز فعال نیست؛ مبنای تقویمی باید صریح توسط رئیس انتخاب شود.")
            if active and not source and not canonical:raise ApiError(400,"برای برنامهٔ سالانه، ماه و هفتهٔ اجرا را تعیین کنید.")
            if active and not source and j.get("calendarEligible") is False:raise ApiError(409,"این برگه شرط ساعت یا دورهٔ چندساله دارد؛ مبنای تکرار پیوسته و تصمیم رئیس لازم است.")
            was=bool(j.get("activatedAt")) or row["active"]==1
            if not was and j.get("approvedAt"):
                was=any(parse(r["body"]).get("active") is True and parse(r["body"]).get("mappingConfirmed") is True for r in c.query("SELECT body FROM pm_plan_revisions WHERE plan_id=:id",dict(id=id)))
            first=not was and active and confirmed
            if first and issue and issue>start:raise ApiError(400,"صدور نخستین برگه‌ها باید از شروع برنامه باشد؛ موعدهای گذشته حذف نمی‌شوند.")
            first_issue=(issue or start) if first else start
            j.update(id=id,version=row["version"]+1,assetId=asset or None,assetIds=ids,assetCodes=codes,groupedSheet=grouped,supervisorId=owner or None,startDate=start.isoformat(),slots=canonical,recurrence=recurrence,active=active,mappingConfirmed=confirmed,effectiveFromUtc=cal.utc_text(cal.local_execution_utc(datetime.combine(first_issue,time.min))) if first else cal.utc_text(instant) if was else None)
            if first:j.update(issueFromDate=first_issue.isoformat(),approvedAt=cal.utc_text(instant),activatedAt=cal.utc_text(instant))
            elif was and not j.get("activatedAt"):j["activatedAt"]=j.get("approvedAt") or cal.utc_text(instant)
            if setup_key:j.update(setupRequestKey=setup_key,setupPayloadHash=setup_hash.lower())
            scan=first_issue if first else cal.tehran_date(instant)
            params=dict(id=id,asset=asset or None,owner=owner or None,start=start.isoformat(),active=int(active),confirmed=int(confirmed),cursor=cal.month_key(scan),next=scan.isoformat(),body=dump(j),version=row["version"])
            if c.execute("UPDATE pm_weekly_plans SET asset_id=:asset,supervisor_id=:owner,start_date=:start,active=:active,mapping_confirmed=:confirmed,version=version+1,cursor_month=:cursor,next_scan=:next,body=:body WHERE id=:id AND version=:version",params)!=1:raise ApiError(409,"برنامه هم‌زمان تغییر کرده است.")
            c.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) VALUES(:id,:revision,:body,:reason,:actor,:created)",dict(id=id,revision=j["version"],body=dump(j),reason=why,actor=user.id,created=cal.utc_text(instant)))
            audit(c,user,"revise-pm-weekly-plan","pm-plan",id,dict(previous=row["version"],revision=j["version"],reason=why,values=j,appliesTo="unissued-future-occurrences"));return j
        return self.db.run_transaction(operation)
