"""Explicit chief activation with a durable idempotency claim and no backlog loss."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib
import uuid
from .common import ApiError
from .pm_hash_json import canonical_json
from . import pm_calendar as cal,pm_schedule as schedule,pm_form_info as info
from .pm_service import parse,dump,chief,validate_date


class PmSetupService:
    def __init__(self,db,pm,configuration=None):self.db=db;self.pm=pm;self.configuration=configuration or {}

    def _enabled(self):return bool(self.configuration.get("scheduling_enabled",True))

    def get(self,user,summary=False):
        if not user.active or user.role not in ("head","manager","planner","admin"):raise ApiError(403,"وضعیت راه‌اندازی برنامه برای این حساب در دسترس نیست.")
        result=self.build(datetime.now(timezone.utc))
        if summary:result.pop("items",None)
        return result

    def build(self,instant):
        today=cal.tehran_date(instant);year,month,_=cal.to_persian(today)
        with self.db.transaction(write=False) as c:
            supervisors=c.query("SELECT id,name,specialty FROM users WHERE active=1 AND role='supervisor'");items=[]
            rows=c.query("SELECT p.*,f.title AS template_title,f.period AS template_period,f.mapping_status,f.body AS template_body FROM pm_weekly_plans p JOIN pm_templates f ON f.id=p.template_id WHERE f.mapping_status<>'retired' ORDER BY f.hall,f.title,p.id")
            referenced=info.lookup_assets(c,[code for r in rows for code in info.member_codes(parse(r["template_body"]))],[r["asset_id"] for r in rows if r.get("asset_id")]);assets={a["code"]:a for a in referenced};asset_ids={a["id"] for a in referenced}
            for row in rows:
                plan=parse(row["body"]);template=parse(row["template_body"]);confirmed=row["mapping_confirmed"]==1;active=row["active"]==1;reasons=[];codes=info.member_codes(template);asset_id=row.get("asset_id")
                if not confirmed:
                    if row["mapping_status"]!="verified" or template.get("issues") or template.get("unresolvedAssetMembers"):reasons.append("تطبیق اصل برگه و تقویم باید بررسی شود.")
                    meta=info.metadata(template)
                    if template.get("scheduleSourceConflicts") or meta.get("periodConflict"):reasons.append("اختلاف دوره یا موعد منابع باید تعیین تکلیف شود.")
                    projection=deepcopy(meta);projection["sourceCodes"]=codes;info.apply_hall_projection(projection,[assets[code]["hall"] for code in codes if code in assets])
                    if any(projection.get(key) is True for key in ("hallConflict","masterHallConflict","sourceHallConflict")):reasons.append("اختلاف سالنِ برگه و شناسنامه باید بررسی شود.")
                    if not codes or any(code not in assets for code in codes):reasons.append("تجهیز یا فهرست کامل دستگاه‌های برگه تعیین نشده است.")
                    else:asset_id=assets[codes[0]]["id"]
                elif not asset_id or asset_id not in asset_ids:reasons.append("تجهیز معتبر برنامه تعیین نشده است.")
                owners=[s for s in supervisors if s["specialty"]==row["specialty"]];owner=row.get("supervisor_id");owner_name=None
                if owner:owner_name=next((s["name"] for s in owners if s["id"]==owner),None)
                elif len(owners)==1:owner=owners[0]["id"];owner_name=owners[0]["name"]
                if not owner or not owner_name:reasons.append("سرپرست همین واحد باید انتخاب شود.")
                period=info.period(template);periodic=schedule.source_period(plan)
                if period.meter_hours is not None and (not confirmed or plan.get("recurrence",{}).get("meterPolicy")!="calendar-only"):reasons.append("مبنای برگهٔ دارای ساعت دستگاه باید توسط رئیس تعیین شود.")
                if template.get("calendarEligible") is False and not periodic:reasons.append("مبنای تقویمی این برگه هنوز تأیید نشده است.")
                reasons+=schedule.non_calendar_blockers(template)
                if not periodic and not plan.get("slots"):reasons.append("هفتهٔ انجام در تقویم تعیین نشده است.")
                invalid=any(not isinstance(slot,dict) or type(slot.get("month")) is not int or type(slot.get("week")) is not int or not 1<=slot.get("month",0)<=12 or not 1<=slot.get("week",0)<=4 for slot in plan.get("slots",[]))
                if invalid:reasons.append("نوبت‌های تقویم معتبر نیستند.")
                due=0
                if not invalid:
                    for back in (0,1):
                        sy,sm=(year,month-back) if month-back>0 else (year-1,12)
                        if sy<1:continue
                        for slot in schedule.slots_for_month(plan,sy,sm):
                            window=cal.get_week(sy,sm,slot.ordinal)
                            due+=window.start_date<=today<window.end_exclusive_date
                items.append(dict(planId=row["id"],templateId=row["template_id"],version=row["version"],title=row["template_title"],hall=row["hall"],specialty=row["specialty"],period=row["template_period"],ready=not reasons,active=active,mappingConfirmed=confirmed,assetId=asset_id,supervisorId=owner,supervisorName=owner_name,slots=deepcopy(plan.get("slots",[])),recurrence=deepcopy(plan.get("recurrence") or dict(mode="annual-slots")),startDate=row["start_date"],dueThisWeek=due,blockedReasons=list(dict.fromkeys(reasons))))
            occurrences=c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences")
            return dict(today=today.isoformat(),currentWeekStart=(today-timedelta(days=(today.weekday()-5)%7)).isoformat(),defaultStartDate=today.isoformat(),workerEnabled=self._enabled(),summary=dict(total=len(items),active=sum(i["active"] for i in items),ready=sum(i["ready"] and not i["active"] for i in items),readyDueThisWeek=sum(i["dueThisWeek"] for i in items if i["ready"] and not i["active"]),needsReview=sum(not i["ready"] for i in items),issued=occurrences),items=items)

    def activate(self,user,input,instant=None):
        if not chief(user):raise ApiError(403,"فعال‌سازی برنامه فقط با رئیس است.")
        if not isinstance(input,dict):raise ApiError(400,"درخواست راه‌اندازی باید یک شیء معتبر باشد.")
        try:request_key=str(uuid.UUID(input.get("requestKey","")))
        except (ValueError,TypeError,AttributeError):raise ApiError(400,"شناسهٔ درخواست معتبر نیست.") from None
        reason=input.get("reason","")
        if not isinstance(reason,str) or not 3<=len(reason.strip())<=2000:raise ApiError(400,"علت راه‌اندازی برنامه را وارد کنید.")
        reason=reason.strip();start=validate_date(input.get("startDate"),"شروع برنامه");selected=input.get("plans")
        if not isinstance(selected,list) or not 1<=len(selected)<=250:raise ApiError(400,"بین یک تا ۲۵۰ برنامه انتخاب کنید.")
        ids=set()
        for p in selected:
            if not isinstance(p,dict) or not isinstance(p.get("id"),str) or not 1<=len(p["id"])<=100 or p["id"] in ids or type(p.get("version")) is not int or p["version"]<1:raise ApiError(400,"شناسه یا نسخهٔ برنامه تکراری یا نامعتبر است.")
            ids.add(p["id"])
        if input.get("includePast",True) is not True:raise ApiError(400,"همهٔ برگه‌های سررسیدشده باید از شروع برنامه صادر شوند؛ حذف برگه‌های عقب‌افتاده مجاز نیست.")
        instant=instant or datetime.now(timezone.utc)
        canonical=dict(startDate=start.isoformat(),includePast=True,reason=reason,plans=[dict(id=p["id"],version=p["version"]) for p in sorted(selected,key=lambda p:p["id"])])
        hash=hashlib.sha256(canonical_json(canonical).encode()).hexdigest()
        def claim(c):
            from .security import assert_live_actor
            assert_live_actor(c,user)
            c.execute("INSERT INTO pm_setup_requests(actor_id,request_key,payload_hash,body,created_at) VALUES(:actor,:key,:hash,:body,:now) ON CONFLICT(actor_id,request_key) DO NOTHING",dict(actor=user.id,key=request_key,hash=hash,body=dump(dict(issueFromDate=start.isoformat(),selection=canonical)),now=cal.utc_text(instant)))
            row=c.one("SELECT payload_hash,body FROM pm_setup_requests WHERE actor_id=:actor AND request_key=:key",dict(actor=user.id,key=request_key))
            if row["payload_hash"]!=hash:raise ApiError(409,"این شناسهٔ درخواست قبلاً با انتخاب دیگری استفاده شده است.")
            return parse(row["body"])["issueFromDate"]
        floor=self.db.run_transaction(claim);snapshot=self.build(instant);candidates={p["planId"]:p for p in snapshot["items"]};results=[];activated=already=0
        for selected_plan in selected:
            id=selected_plan["id"]
            try:
                current=self.pm.plan(user,id)
                if current.get("setupRequestKey")==request_key:
                    if current.get("setupPayloadHash")!=hash:raise ApiError(409,"این شناسهٔ درخواست قبلاً با انتخاب دیگری استفاده شده است.")
                    if current.get("active") is not True:raise ApiError(409,"برنامه پس از راه‌اندازی تغییر کرده است؛ دوباره بررسی کنید.")
                    already+=1;results.append(dict(id=id,status="already-applied"));continue
                if current["version"]!=selected_plan["version"]:raise ApiError(409,"برنامه هم‌زمان تغییر کرده است؛ تازه‌سازی کنید.")
                candidate=candidates.get(id)
                if not candidate or candidate.get("ready") is not True:raise ApiError(409,"این برنامه به تعیین تکلیف برگه، تجهیز یا موعد نیاز دارد.")
                if current["active"]:raise ApiError(409,"برنامه از قبل فعال است.")
                payload=dict(version=current["version"],reason=reason,assetId=candidate["assetId"],supervisorId=candidate["supervisorId"],startDate=start.isoformat(),slots=current.get("slots",[]),recurrence=current.get("recurrence") or dict(mode="annual-slots"),active=True,mappingConfirmed=True,issueFromDate=floor,setupRequestKey=request_key,setupPayloadHash=hash)
                self.pm.edit_plan(user,id,payload,instant);activated+=1;results.append(dict(id=id,status="activated"))
            except ApiError as ex:
                raced=None
                if ex.status==409:
                    try:raced=self.pm.plan(user,id)
                    except ApiError:pass
                if raced and raced.get("setupRequestKey")==request_key and raced.get("setupPayloadHash")==hash and raced.get("active") is True:already+=1;results.append(dict(id=id,status="already-applied"))
                else:results.append(dict(id=id,status="failed",error=str(ex)))
        generated=0
        if activated+already:
            for _ in range(50):
                if not self.pm.has_due(instant,user):break
                generated+=self.pm.generate(instant,user)
        return dict(activated=activated,alreadyApplied=already,generated=generated,issueFromDate=floor,requestKey=request_key,items=results,workerEnabled=self._enabled())
