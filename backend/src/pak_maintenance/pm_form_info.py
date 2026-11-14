"""Classify source declarations without confusing approval state and cadence."""
from copy import deepcopy
import re
from .common import ApiError
from .pm_schedule import read_period, non_calendar_blocker_codes, _DIGITS


def cells(template):
    return (cell for table in template.get("tables",[]) for row in table.get("rows",[]) for cell in row.get("cells",[]) if isinstance(cell,dict))


def lookup_assets(c,codes=(),ids=()):
    """Fetch only referenced members, in portable bounded parameter batches."""
    found={}
    for column,values in (("code",codes),("id",ids)):
        values=list(dict.fromkeys(values))
        for start in range(0,len(values),400):
            params={f"m{i}":value for i,value in enumerate(values[start:start+400])}
            expression=",".join(":"+key for key in params)
            for row in c.query(f"SELECT id,code,name,hall FROM assets WHERE {column} IN ({expression})",params):found[row["id"]]=row
    return list(found.values())


def member_codes(template): return list(dict.fromkeys(x for x in template.get("assetMemberCodes",[]) if isinstance(x,str) and x))


def period(template):
    declaration=next((d.get("text","") for d in template.get("originalPeriodDeclarations",[]) if (d.get("text") or "").strip()),None)
    return read_period(declaration if declaration is not None else template.get("period") or "")


def family(template):
    kind,title=template.get("formKind"),template.get("title","")
    return "control" if kind=="control" else "lubrication" if kind=="lubrication" else "pumps" if kind=="grouped-inspection" and "پمپ" in title else "tanks" if kind=="grouped-inspection" and "مخازن" in title else "equipment"


def family_label(value): return {"pumps":"پمپ‌ها","tanks":"مخازن","lubrication":"سرویس و روانکاری","control":"کنترل اسناد"}.get(value,"دستگاه‌ها")


def period_label(value): return {"weekly":"هفتگی","monthly":"ماهانه","bimonthly":"دوماهه","quarterly":"سه‌ماهه","six-monthly":"شش‌ماهه","ten-monthly":"ده‌ماهه","annual":"سالانه","multi-year":"دوره بلندمدت","other-monthly":"سایر دوره‌های ماهانه","day-interval":"دوره برحسب روز"}.get(value,"بدون دوره اجرایی")


def hall_order(value): return {"فرآیند":0,"فرایند":0,"شیر":1,"ماست":2,"خامه و کره":3,"تأسیسات":4,"تاسیسات":4,"اداری":5}.get(value,6)


def period_order(form): return 0 if form.get("periodClass")=="weekly" else form["intervalDays"]/30 if form.get("intervalDays") is not None else form.get("intervalMonths") if form.get("intervalMonths") is not None else 10000


def empty_pump_roster(template): return family(template)=="pumps" and not member_codes(template)


def has_activities(template): return any(c.get("activity") is True and str(c.get("text","")).strip() for c in cells(template))


def normalize_search(value):
    value=value or ""
    value=value.translate(_DIGITS).replace("ي","ی").replace("ك","ک").replace("آ","ا").replace("\u200c"," ")
    value=re.sub(r"(?<=[0-9])\s*[-‐‑–—−]\s*(?=[0-9])","-",value)
    return re.sub(r"\s+"," ",value).strip()


def canonical_hall(value):
    value=normalize_search(value)
    aliases={"شیر":("milk","شیر","بسته بندی شیر","سالن بسته بندی شیر","سالن بسته بندی مایعات"),"فرآیند":("process","فرایند","سالن فرایند"),"ماست":("yogurt","ماست","سالن بسته بندی ماست","سالن فرایند ماست","سالن ماست یونانی"),"خامه و کره":("cream","خامه و کره","سالن بسته بندی خامه وکره","سالن بسته بندی خامه و کره"),"تأسیسات":("utilities","تأسیسات","تاسیسات"),"اداری":("office","اداری","سالن اداری")}
    return next((hall for hall,values in aliases.items() if value in values),"")


def apply_hall_projection(form,master_halls):
    source,header=form.get("sourceHall", ""),form.get("headerHall") or ""
    halls=list(dict.fromkeys(canonical_hall(h) for h in master_halls))
    codes=form.get("sourceCodes",form.get("memberCodes",[]))
    agreement=len(codes)==1 and len(halls)==1 and bool(header) and halls[0]==header
    unit=canonical_hall(source)=="تأسیسات" and header not in ("","تأسیسات")
    form.update(displayHallDerived=agreement and unit,hall=header if agreement and unit else source,responsibilityHall=source)
    form["hallConflict"]=bool(form.get("hallConflict")) or bool(header and (any(h and h!=header for h in halls) or header!=canonical_hall(source) and not(agreement and unit)))


def metadata(template):
    p=period(template); declarations=template.get("originalPeriodDeclarations",[])
    signatures={tuple(read_period(d["text"]).json()[key] for key in ("periodClass","intervalMonths","intervalDays","meterHours","triggerKind")) for d in declarations if d.get("text")}
    halls=list(dict.fromkeys(canonical_hall(d.get("hall","")) for d in template.get("originalHallDeclarations",[]) if canonical_hall(d.get("hall",""))))
    count=sum(1 for c in cells(template) if c.get("activity") is True and str(c.get("text","")).strip())
    return dict(period=p.source_text,periodClass=p.period_class,periodLabel=period_label(p.period_class),periodInfo=p.json(),intervalMonths=p.interval_months,intervalDays=p.interval_days,meterHours=p.meter_hours,triggerKind=p.trigger_kind,periodSource="original-declaration" if declarations else "imported-period",sourcePeriodDeclarations=deepcopy(declarations),periodConflict=len(signatures)>1,family=family(template),familyLabel=family_label(family(template)),groupRosterEditable=empty_pump_roster(template) or template.get("workingRoster") is not None,sourceHall=template.get("hall",""),headerHall=halls[0] if len(halls)==1 else None,hallConflict=len(halls)>1,sourceHallDeclarations=deepcopy(template.get("originalHallDeclarations",[])),hasExecutableActivities=count>0,activityCount=count,nonCalendarBlockerCodes=non_calendar_blocker_codes(template))


def equipment_label(form,names=None):
    hall=form.get("hall",""); f=form.get("family")
    if f=="pumps": return "پمپ‌های سالن "+hall
    if f=="tanks": return "مخازن سالن "+hall
    codes=form.get("memberCodes",form.get("sourceCodes",[]))
    if len(codes)==1: return codes[0]+" · "+(names or {}).get(codes[0],form.get("title",""))
    return "گروه تجهیزات "+hall if len(codes)>1 else form.get("title","")


def apply_working_roster(template,codes,c):
    if not(empty_pump_roster(template) or template.get("workingRoster") is not None): raise ApiError(400,"فهرست اصلی اعضای این برگه گروهی حفظ می‌شود.")
    table=next((t for t in template.get("tables",[]) if t.get("sourceSheet")),None)
    if table is None: raise ApiError(409,"قالب اصلی برگه گروهی پیدا نشد.")
    rows=[r for r in table.get("rows",[]) if 4<=r.get("sourceRow",0)<=49]
    if not codes or len(codes)>len(rows) or len(set(codes))!=len(codes): raise ApiError(400,"کدهای واقعی پمپ‌ها را بدون تکرار و در ظرفیت برگه وارد کنید.")
    permitted={"شیر":["شیر","سالن بسته بندی شیر"],"فرآیند":["فرآیند","سالن فرایند"],"فرایند":["فرآیند","سالن فرایند"],"ماست":["ماست","سالن بسته بندی ماست","سالن فرایند ماست"],"خامه و کره":["خامه و کره","سالن بسته بندی خامه وکره"]}.get(template.get("hall"),[template.get("hall")])
    permitted={normalize_search(h) for h in permitted}
    members=[]
    for code in codes:
        if not re.fullmatch(r"39-[0-9]+",code): raise ApiError(400,"اعضای این برگه باید کد معتبر پمپ داشته باشند.")
        member=c.one("SELECT name,hall FROM assets WHERE code=:code",{"code":code})
        if member is None: raise ApiError(400,f"پمپ {code} در شناسنامه تجهیزات موجود نیست.")
        if normalize_search(member["hall"]) not in permitted: raise ApiError(400,f"پمپ {code} متعلق به سالن این برگه نیست؛ ابتدا سالن پمپ را در شناسنامه تجهیزات اصلاح کنید.")
        members.append(member)
    if member_codes(template)==codes: return False
    for index,row in enumerate(rows):
        for cell in row.get("cells",[]):
            column=cell.get("sourceRef",{}).get("column",0)
            if not 1<=column<=9: continue
            cell["text"]=(members[index]["name"] if column==1 else codes[index] if column==2 else "") if index<len(members) else ""
            cell["editable"]=index<len(members) and 4<=column<=7
    template["assetMemberCodes"]=list(codes); template["assetMembers"]=[dict(code=code,workingRoster=True) for code in codes]
    template["workingRoster"]=dict(source="chief-explicit-selection",originalRosterEmpty=True,memberCodes=list(codes))
    template["issues"]=[i for i in template.get("issues",[]) if i!="no-actual-activities"]
    return True
