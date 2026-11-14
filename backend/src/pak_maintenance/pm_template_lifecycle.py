"""Chief-only form designer and reversible retirement; issued work is immutable."""
from copy import deepcopy
from datetime import datetime,timezone,date
import hashlib
import re
import uuid
from .common import ApiError,now,audit
from .pm_hash_json import canonical_json
from . import pm_form_info as info,pm_schedule as schedule,pm_calendar as cal


class TemplateLifecycleMixin:
    def _design(self,input,current=None):
        if not isinstance(input,dict):raise ApiError(400,"اطلاعات طراحی برگه معتبر نیست.")
        def field(key,max,required=True,obj=input):
            raw=obj.get(key)
            if raw is None and not required:return ""
            if not isinstance(raw,str):raise ApiError(400,"متن اطلاعات برگه معتبر نیست.")
            value=raw.strip()
            if len(value)>max or required and not value or any(ord(ch)<32 and ch not in "\r\n\t" for ch in value):raise ApiError(400,"اطلاعات برگه خالی یا بیش از حد مجاز است.")
            return value
        title,hall,spec,period,code=field("title",200),field("hall",150),field("specialty",30),field("period",100),field("formCode",80,False)
        if spec not in ("برق","مکانیک","تاسیسات"):raise ApiError(400,"تخصص برگه را انتخاب کنید.")
        cadence=schedule.read_period(period)
        if cadence.trigger_kind!="calendar" or cadence.interval_days is not None or not(cadence.period_class=="weekly" or cadence.interval_months is not None and 1<=cadence.interval_months<=120):raise ApiError(400,"دوره برگه باید هفتگی یا ماهانه تا ۱۲۰ ماه باشد.")
        activities=input.get("activities")
        if not isinstance(activities,list) or not 1<=len(activities)<=200:raise ApiError(400,"بین ۱ تا ۲۰۰ فعالیت وارد کنید.")
        existing={c["id"] for c in info.cells(current or {}) if c.get("activity") is True};seen=set();rows=[]
        for activity in activities:
            if isinstance(activity,str):obj=dict(text=activity);id=""
            elif isinstance(activity,dict):obj=activity;id=field("id",64,False,obj)
            else:raise ApiError(400,"شرح فعالیت معتبر نیست.")
            value=field("text",2000,True,obj)
            if id and (id not in existing or id in seen):raise ApiError(400,"شناسه فعالیت تکراری یا متعلق به برگه دیگری است.")
            if id:seen.add(id)
            rows.append(dict(id=id,text=value))
        return dict(title=title,hall=hall,specialty=spec,period=period,formCode=code,activities=rows)

    @staticmethod
    def _custom_table(id,design):
        def cell(id,text,activity=False,editable=False):return dict(id=id,text=text,colSpan=1,activity=activity,chiefEditable=activity,editable=editable)
        rows=[dict(id="header-"+id,cells=[cell("heading-number-"+id,"ردیف"),cell("heading-activity-"+id,"شرح فعالیت"),cell("heading-result-"+id,"نتیجه بررسی")])]
        for n,a in enumerate(design["activities"],1):
            aid=a["id"] or "act-"+uuid.uuid4().hex
            rows.append(dict(id="row-"+aid,cells=[cell("number-"+aid,str(n)),cell(aid,a["text"],True),cell("result-"+aid,"",editable=True)]))
        return dict(id="table-"+id,direction="rtl",rows=rows)

    def create_template(self,user,input):
        from .pm_service import require_chief,reason,dump,parse,template_view
        require_chief(user);design=self._design(input);key=input.get("requestKey")
        if not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9._-]{16,100}",key):raise ApiError(400,"شناسه درخواست معتبر نیست؛ صفحه را تازه‌سازی کنید.")
        digest=hashlib.sha256(canonical_json(design).encode()).hexdigest();id="custom-"+hashlib.sha256((user.id+"\n"+key).encode()).hexdigest()[:32]
        why=reason(input) if input.get("reason") is not None else "طراحی برگه جدید"
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            def replay(row):
                body=parse(row["body"])
                if body.get("creationPayloadHash")!=digest or body.get("creationActorId")!=user.id:raise ApiError(409,"این درخواست قبلاً با اطلاعات دیگری ثبت شده است.")
                reply=template_view(row);reply["replayed"]=True;return reply
            old=c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id))
            if old:return replay(old)
            j=dict(id=id,version=1,origin="custom",isCustom=True,lifecycleState="active",retired=False,mappingStatus="pending",formKind="inspection",calendarEligible=True,issues=[],assetMemberCodes=[],source=dict(kind="custom",formCode=design["formCode"]),creationPayloadHash=digest,creationActorId=user.id,createdAt=now(),tables=[self._custom_table(id,design)])
            for key in ("title","hall","specialty","period","formCode"):j[key]=design[key]
            params=dict(id=id,title=j["title"],spec=j["specialty"],hall=j["hall"],period=j["period"],body=dump(j),reason=why,actor=user.id,created=now())
            inserted=c.execute("INSERT INTO pm_templates(id,title,specialty,hall,period,version,mapping_status,body) VALUES(:id,:title,:spec,:hall,:period,1,'pending',:body) ON CONFLICT(id) DO NOTHING",params)
            if not inserted:return replay(c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id)))
            current=cal.tehran_date(datetime.now(timezone.utc));day=current.isoformat();plan="plan-"+id
            p=dict(id=plan,version=1,templateId=id,templateTitle=j["title"],title=j["title"],specialty=j["specialty"],hall=j["hall"],assetId=None,assetIds=[],assetCodes=[],supervisorId=None,startDate=day,slots=[],recurrence=dict(mode="annual-slots"),active=False,mappingConfirmed=False,calendarEligible=True,issues=[],effectiveFromUtc=None)
            c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,1,:body,:reason,:actor,:created)",params)
            pp=dict(params,plan=plan,date=day,cursor=cal.month_key(current),body=dump(p))
            c.execute("INSERT INTO pm_weekly_plans(id,template_id,asset_id,supervisor_id,specialty,hall,version,active,mapping_confirmed,start_date,cursor_month,next_scan,body) VALUES(:plan,:id,NULL,NULL,:spec,:hall,1,0,0,:date,:cursor,:date,:body)",pp)
            c.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) VALUES(:plan,1,:body,'پیش‌نویس زمان‌بندی برگه جدید',:actor,:created)",pp)
            audit(c,user,"create-pm-template","pm-template",id,dict(planId=plan,title=j["title"],specialty=j["specialty"],hall=j["hall"],activities=len(design["activities"]),activePlan=False))
            return template_view(c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id)))
        return self.db.run_transaction(operation)

    def design_template(self,user,id,input):
        from .pm_service import require_chief,reason,dump,parse,template_view,require_version
        require_chief(user);why=reason(input)
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            old=c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id))
            if old is None:raise ApiError(404,"برگه پیدا نشد.")
            require_version(old,input);j=parse(old["body"])
            if old["mapping_status"]=="retired":raise ApiError(409,"برگه حذف شده است؛ ابتدا آن را بازگردانید.")
            if j.get("origin")!="custom":raise ApiError(409,"ساختار برگه اصلی حفظ می‌شود؛ شرح فعالیت آن را از بخش اصلاح فعالیت تغییر دهید.")
            if c.scalar("SELECT COUNT(*) FROM pm_weekly_plans WHERE template_id=:id AND active=1",dict(id=id)):raise ApiError(409,"ابتدا زمان‌بندی این برگه را متوقف کنید، سپس طراحی را تغییر دهید.")
            design=self._design(input,j);identity=any(j.get(key)!=design[key] for key in ("hall","specialty","period"))
            for key in ("title","hall","specialty","period","formCode"):j[key]=design[key]
            j["source"]["formCode"]=design["formCode"];j["tables"]=[self._custom_table(id,design)];j["version"]=old["version"]+1
            params=dict(id=id,version=old["version"],title=j["title"],hall=j["hall"],spec=j["specialty"],period=j["period"],body=dump(j),revision=j["version"],reason=why,actor=user.id,created=now())
            if c.execute("UPDATE pm_templates SET title=:title,hall=:hall,specialty=:spec,period=:period,version=version+1,body=:body WHERE id=:id AND version=:version",params)!=1:raise ApiError(409,"برگه هم‌زمان تغییر کرده است.")
            c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,:revision,:body,:reason,:actor,:created)",params)
            for row in c.query("SELECT * FROM pm_weekly_plans WHERE template_id=:id",dict(id=id)):
                p=parse(row["body"]);p.update(version=row["version"]+1,title=j["title"],templateTitle=j["title"],specialty=j["specialty"],hall=j["hall"])
                if identity:
                    p.update(mappingConfirmed=False,assetId=None,assetIds=[],assetCodes=[],supervisorId=None,recurrence=dict(mode="annual-slots"),slots=[],effectiveFromUtc=None);p.pop("approvedAt",None);p.pop("activatedAt",None)
                pp=dict(params,plan=row["id"],pv=row["version"],revision=p["version"],body=dump(p),confirmed=int(bool(p.get("mappingConfirmed"))),asset=p.get("assetId"),supervisor=p.get("supervisorId"))
                if c.execute("UPDATE pm_weekly_plans SET version=version+1,specialty=:spec,hall=:hall,mapping_confirmed=:confirmed,asset_id=:asset,supervisor_id=:supervisor,body=:body WHERE id=:plan AND version=:pv",pp)!=1:raise ApiError(409,"زمان‌بندی هم‌زمان تغییر کرده است.")
                c.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) VALUES(:plan,:revision,:body,:reason,:actor,:created)",pp)
            audit(c,user,"design-pm-template","pm-template",id,dict(previous=old["version"],revision=j["version"],reason=why,identityChanged=identity,appliesTo="future-occurrences"))
            return template_view(c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id)))
        return self.db.run_transaction(operation)

    def retire_template(self,user,id,input):return self._lifecycle(user,id,input,True)
    def restore_template(self,user,id,input):return self._lifecycle(user,id,input,False)

    def _lifecycle(self,user,id,input,retire):
        from .pm_service import require_chief,reason,dump,parse,template_view,require_version
        require_chief(user);why=reason(input)
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            old=c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id))
            if old is None:raise ApiError(404,"برگه پیدا نشد.")
            require_version(old,input);retired=old["mapping_status"]=="retired"
            if retired==retire:raise ApiError(409,"برگه قبلاً حذف شده است." if retire else "برگه حذف نشده است.")
            if retire:self.require_drained(c,datetime.now(timezone.utc),template_id=id)
            j=parse(old["body"]);created=now()
            if retire:j["mappingStatusBeforeRetirement"]=old["mapping_status"]
            mapping="retired" if retire else j.get("mappingStatusBeforeRetirement") or "pending"
            if not retire and mapping=="retired":mapping="pending"
            j.update(version=old["version"]+1,mappingStatus=mapping,lifecycleState="retired" if retire else "active",retired=retire);j["retiredAt" if retire else "restoredAt"]=created
            params=dict(id=id,version=old["version"],mapping=mapping,body=dump(j),revision=j["version"],reason=why,actor=user.id,created=created)
            if c.execute("UPDATE pm_templates SET version=version+1,mapping_status=:mapping,body=:body WHERE id=:id AND version=:version",params)!=1:raise ApiError(409,"برگه هم‌زمان تغییر کرده است.")
            c.execute("INSERT INTO pm_template_revisions(template_id,revision,body,reason,actor_id,created_at) VALUES(:id,:revision,:body,:reason,:actor,:created)",params)
            for row in c.query("SELECT * FROM pm_weekly_plans WHERE template_id=:id",dict(id=id)):
                p=parse(row["body"]);p.update(version=row["version"]+1,active=False,mappingConfirmed=False,retired=retire,lifecycleState="retired" if retire else "active")
                if not retire:p.pop("approvedAt",None);p.pop("activatedAt",None);p["effectiveFromUtc"]=None
                pp=dict(params,plan=row["id"],pv=row["version"],revision=p["version"],body=dump(p))
                if c.execute("UPDATE pm_weekly_plans SET version=version+1,active=0,mapping_confirmed=0,body=:body WHERE id=:plan AND version=:pv",pp)!=1:raise ApiError(409,"زمان‌بندی هم‌زمان تغییر کرده است.")
                c.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) VALUES(:plan,:revision,:body,:reason,:actor,:created)",pp)
            audit(c,user,"retire-pm-template" if retire else "restore-pm-template","pm-template",id,dict(previous=old["version"],revision=j["version"],reason=why,issuedWorkPreserved=True,activePlans=0))
            return template_view(c.one("SELECT * FROM pm_templates WHERE id=:id",dict(id=id)))
        return self.db.run_transaction(operation)
