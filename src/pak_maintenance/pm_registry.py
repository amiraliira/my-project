"""One library entry per source sheet, with equipment association and facets."""
from copy import deepcopy
from datetime import datetime,timezone
import threading
from .common import ApiError
from . import pm_form_info as info,pm_schedule as schedule,pm_calendar as cal
from .pm_service import parse,require_library,bounds,page_result


def strings(form,key):return list(dict.fromkeys(x for x in form.get(key,[]) if isinstance(x,str) and x))
def ambiguous(form):return not form.get("mappingConfirmed") and bool(form.get("identityIssues") or form.get("missingSourceMembers"))


class PmRegistry:
    def __init__(self,db):self.db=db;self._fingerprint=None;self._catalog=[];self._lock=threading.RLock()

    def catalog(self,c):
        fingerprint=tuple((r["name"],r["total"],r["version"]) for r in c.query("SELECT 'templates' AS name,COUNT(*) AS total,COALESCE(SUM(version),0) AS version FROM pm_templates UNION ALL SELECT 'plans' AS name,COUNT(*) AS total,COALESCE(SUM(version),0) AS version FROM pm_weekly_plans ORDER BY name"))
        with self._lock:
            if fingerprint==self._fingerprint:return deepcopy(self._catalog)
            plans={r["template_id"]:r for r in c.query("SELECT * FROM pm_weekly_plans")};catalog=[]
            for row in c.query("SELECT * FROM pm_templates WHERE mapping_status<>'retired' ORDER BY title,id"):
                source=parse(row["body"]);p=plans.get(row["id"]);plan=parse(p["body"]) if p else {};period=info.period(source)
                entry=dict(templateId=row["id"],planId=p["id"] if p else None,isCustom=source.get("origin")=="custom",hasOriginal=len(source.get("source",{}).get("sha256",""))==64,lifecycleState="active",retired=False,title=row["title"],hall=row["hall"],specialty=row["specialty"],period=period.source_text,periodClass=period.period_class,periodInfo=period.json(),intervalMonths=period.interval_months,intervalDays=period.interval_days,meterHours=period.meter_hours,triggerKind=period.trigger_kind,formCode=source.get("source",{}).get("formCode",""),formKind=source.get("formKind",""),mappingStatus=row["mapping_status"],issues=deepcopy(source.get("issues",[])),calendarEligible=source.get("calendarEligible",True),nonCalendarBlockers=schedule.non_calendar_blockers(source),version=row["version"],planVersion=p["version"] if p else 0,active=p is not None and p["active"]==1,mappingConfirmed=p is not None and p["mapping_confirmed"]==1,recurrence=deepcopy(plan.get("recurrence") or dict(mode="annual-slots")),scheduleMode="source-period" if schedule.source_period(plan) else "annual-slots",slots=deepcopy(plan.get("slots",[])),candidateSlots=deepcopy(source.get("candidateSlots",[])),supervisorId=plan.get("supervisorId"),sourcePeriodSuggestion=dict(unit="month",interval=period.interval_months) if period.interval_months is not None else dict(unit="week",interval=1) if period.period_class=="weekly" else None,startDate=plan.get("startDate"),groupedSheet=source.get("formKind")=="grouped-inspection" or len(source.get("assetMemberCodes",[]))>1,sourceCodes=deepcopy(source.get("assetMemberCodes",[])),candidateCodes=deepcopy(source.get("assetMemberCodes",[])),confirmedAssetIds=deepcopy(plan.get("assetIds",[])),referenceAssetId=plan.get("assetId"),memberCount=len(source.get("assetMemberCodes",[])),sourcePath=source.get("source",{}).get("path",""),identityIssues=deepcopy(source.get("unresolvedAssetMembers",[])))
                entry.update(info.metadata(source))
                if info.has_activities(source):entry["issues"]=[i for i in entry["issues"] if i!="no-actual-activities"]
                catalog.append(entry)
            self._fingerprint=fingerprint;self._catalog=catalog;return deepcopy(catalog)

    @staticmethod
    def _filter(forms,user,specialty=None,period_class=None):
        spec=user.specialty if user.role=="supervisor" else {"electrical":"برق","mechanical":"مکانیک","utilities":"تاسیسات"}.get(specialty,specialty or "")
        return [f for f in forms if (not spec or f["specialty"]==spec) and (not period_class or f["periodClass"]==period_class)]

    @staticmethod
    def _validate(user,q,hall,specialty,period_class,year,family=None):
        require_library(user)
        year=cal.to_persian(cal.tehran_date(datetime.now(timezone.utc)))[0] if year is None else year
        if type(year) is not int or not 1<=year<=cal.MAXIMUM_SUPPORTED_PERSIAN_YEAR:raise ApiError(400,"سال تقویم نامعتبر است.")
        for value,maximum in ((q,300),(hall,200),(specialty,100),(period_class,60),(family,60)):
            if value is not None and (not isinstance(value,str) or len(value)>maximum):raise ApiError(400,"فیلتر بیش از حد طولانی است.")
        return year

    @staticmethod
    def _associations(forms,assets):
        by_code={a["code"]:a["id"] for a in assets};linked={};associated=set()
        for form in forms:
            ids=strings(form,"confirmedAssetIds") if form.get("mappingConfirmed") else []
            if form.get("mappingConfirmed") and not ids and form.get("referenceAssetId"):ids=[form["referenceAssetId"]]
            association="confirmed" if ids else "source"
            if not ids:ids=list(dict.fromkeys(by_code[code] for code in strings(form,"sourceCodes") if code in by_code))
            for id in ids:
                entry=deepcopy(form);entry.update(missingSourceMembers=any(code not in by_code for code in strings(form,"sourceCodes")),association=association);entry["associationStatus"]="ambiguous" if ambiguous(entry) else "present"
                linked.setdefault(id,[]).append(entry);associated.add(form["templateId"])
        return linked,associated

    @staticmethod
    def _form_view(form,year):
        copy=deepcopy(form);copy["slots"]=schedule.year_slots(form,year)
        for key in ("sourceCodes","confirmedAssetIds","referenceAssetId"):copy.pop(key,None)
        return copy

    def get(self,user,page=None,page_size=None,q=None,hall=None,specialty=None,period_class=None,status=None,year=None):
        year=self._validate(user,q,hall,specialty,period_class,year);page,size,offset=bounds(page,page_size)
        if status not in (None,"","present","ambiguous","missing","active","inactive"):raise ApiError(400,"وضعیت فیلتر نامعتبر است.")
        with self.db.transaction(write=False) as c:
            catalog=self.catalog(c);filtered=self._filter(catalog,user,specialty,period_class)
            needed_codes=[code for f in filtered for code in strings(f,"sourceCodes")]
            linked,associated=self._associations(filtered,info.lookup_assets(c,needed_codes))
            search=(q or "").strip();query_ids=[id for id,forms in linked.items() if any(search.casefold() in str(f.get(k,"")).casefold() for f in forms for k in ("title","formCode","period","sourcePath"))] if search else []
            params=dict(hall=hall or "",search=search,q="%"+search.replace("~","~~").replace("%","~%").replace("_","~_")+"%",size=size,offset=offset)
            def membership(values,prefix):
                if not values:return "1=0"
                keys=[]
                for i,value in enumerate(values):key=f"{prefix}{i}";params[key]=value;keys.append(":"+key)
                return "a.id IN ("+",".join(keys)+")"
            base=" WHERE (:hall='' OR a.hall=:hall) AND (:search='' OR a.code LIKE :q ESCAPE '~' OR a.name LIKE :q ESCAPE '~' OR a.hall LIKE :q ESCAPE '~' OR a.line LIKE :q ESCAPE '~'"+(" OR "+membership(query_ids,"searchid") if query_ids else "")+")"
            present=membership(list(linked),"linked")
            amb=membership([id for id,forms in linked.items() if any(ambiguous(f) for f in forms)],"ambiguous")
            active=membership([id for id,forms in linked.items() if any(f.get("active") for f in forms)],"active")
            predicates={"present":present,"missing":"NOT ("+present+")","ambiguous":amb,"active":active,"inactive":present+" AND NOT ("+active+")"}
            where=base+(" AND "+predicates[status] if status in predicates else "")
            if status in (None,"") and (specialty or period_class or user.role=="supervisor"):where+=" AND "+present
            equipment_total=c.scalar("SELECT COUNT(*) FROM assets a"+base,params)
            with_forms=c.scalar("SELECT COUNT(*) FROM assets a"+base+" AND "+present,params)
            needs_review=c.scalar("SELECT COUNT(*) FROM assets a"+base+" AND "+amb,params)
            total=c.scalar("SELECT COUNT(*) FROM assets a"+where,params)
            selected=c.query("SELECT a.id,a.code,a.name,a.hall,a.line FROM assets a"+where+" ORDER BY a.hall,a.code,a.id LIMIT :size OFFSET :offset",params)
            items=[]
            for a in selected:
                forms=linked.get(a["id"],[]);coverage={}
                for spec in ("برق","مکانیک","تاسیسات"):
                    own=[f for f in forms if f["specialty"]==spec];coverage[spec]="missing" if not own else "ambiguous" if any(ambiguous(f) for f in own) else "present"
                items.append(dict(assetId=a["id"],code=a["code"],name=a["name"],hall=a["hall"],line=a["line"],forms=[self._form_view(f,year) for f in forms],coverage=coverage,status="missing" if not forms else "ambiguous" if any(ambiguous(f) for f in forms) else "present"))
            return page_result(items,total,page,size,year=year,halls=[r["hall"] for r in c.query("SELECT DISTINCT hall FROM assets ORDER BY hall")],periodClasses=sorted({f["periodClass"] for f in catalog}),summary=dict(equipmentTotal=equipment_total,withForms=with_forms,withoutForms=equipment_total-with_forms,needsReview=needs_review,templatesTotal=len(filtered),unlinkedTemplates=sum(f["templateId"] not in associated for f in filtered)))

    def unlinked(self,user,page=None,page_size=None,q=None,hall=None,specialty=None,period_class=None,year=None):
        year=self._validate(user,q,hall,specialty,period_class,year);page,size,offset=bounds(page,page_size)
        with self.db.transaction(write=False) as c:
            forms=self._filter(self.catalog(c),user,specialty,period_class);_,associated=self._associations(forms,info.lookup_assets(c,[code for f in forms for code in strings(f,"sourceCodes")]));search=(q or "").strip().casefold()
            forms=[f for f in forms if f["templateId"] not in associated and (not hall or f["hall"]==hall) and (not search or any(search in str(f.get(k,"")).casefold() for k in ("title","period","formCode","sourcePath")))]
            return page_result([self._form_view(f,year) for f in forms[offset:offset+size]],len(forms),page,size)

    def forms(self,user,page=None,page_size=None,q=None,hall=None,specialty=None,family=None,period_class=None,year=None):
        year=self._validate(user,q,hall,specialty,period_class,year,family);page,size,offset=bounds(page,page_size)
        with self.db.transaction(write=False) as c:
            accessible=self._filter(self.catalog(c),user,specialty);assets=info.lookup_assets(c,[code for f in accessible for code in strings(f,"sourceCodes")],[id for f in accessible for id in strings(f,"confirmedAssetIds")]+[f["referenceAssetId"] for f in accessible if f.get("referenceAssetId")]);by_code={a["code"]:a for a in assets};by_id={a["id"]:a for a in assets};names={a["code"]:a["name"] for a in assets};supervisors={u["id"]:u["name"] for u in c.query("SELECT id,name FROM users WHERE role='supervisor'")};scope=[]
            for f in accessible:
                copy=deepcopy(f);codes=strings(copy,"sourceCodes")
                if copy.get("mappingConfirmed") and strings(copy,"confirmedAssetIds"):codes=list(dict.fromkeys(by_id[id]["code"] for id in strings(copy,"confirmedAssetIds") if id in by_id))
                copy.update(memberCodes=codes,memberCount=len(codes),assetCount=sum(code in by_code for code in codes),identityStatus=("working-roster" if copy["family"]=="pumps" else "unlinked") if not codes else "missing-members" if any(code not in by_code for code in codes) or copy.get("identityIssues") else "present")
                info.apply_hall_projection(copy,[by_code[code]["hall"] for code in strings(copy,"sourceCodes") if code in by_code]);copy.update(equipmentLabel=info.equipment_label(copy,names),supervisorLabel=supervisors.get(copy.get("supervisorId")));scope.append(copy)
            search=info.normalize_search(q or "").casefold()
            forms=[f for f in scope if (not hall or f["hall"]==hall) and (not family or f["family"]==family) and (not period_class or f["periodClass"]==period_class) and (not search or any(search in info.normalize_search(str(f.get(k,"") or "")).casefold() for k in ("title","equipmentLabel","hall","period","formCode","sourcePath","familyLabel")) or any(search in info.normalize_search(code).casefold() for code in strings(f,"memberCodes")))]
            forms.sort(key=lambda f:(info.hall_order(f["hall"]),f["hall"],info.period_order(f),f["equipmentLabel"],0 if f["specialty"]=="برق" else 1,f["templateId"]))
            halls=sorted({f["hall"] for f in scope},key=lambda h:(info.hall_order(h),h));families=sorted({f["family"] for f in scope});periods=sorted({f["periodClass"] for f in scope},key=lambda p:min(info.period_order(f) for f in scope if f["periodClass"]==p))
            def facets(key,values,label):return [dict(value=v,label=label(v),count=sum(f[key]==v for f in scope)) for v in values]
            return page_result([self._form_view(f,year) for f in forms[offset:offset+size]],len(forms),page,size,year=year,halls=halls,families=families,periodClasses=periods,facets=dict(halls=facets("hall",halls,lambda x:x),families=facets("family",families,info.family_label),periodClasses=facets("periodClass",periods,info.period_label)),summary=dict(totalForms=len(forms),executableForms=sum(f["family"]!="control" for f in forms),pumpGroups=sum(f["family"]=="pumps" for f in forms),activeForms=sum(bool(f["active"]) for f in forms),needsEquipmentReview=sum(f["identityStatus"]!="present" and f["family"]!="control" for f in forms)))
