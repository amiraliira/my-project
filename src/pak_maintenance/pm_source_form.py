"""Read-only source evidence policy; original document bytes are never edited."""
from copy import deepcopy
import hashlib
from .pm_form_info import cells,member_codes,period
from .pm_schedule import read_period

POLICY_VERSION="source-form-1.7"


def period_status(form):
    periods={tuple(read_period(x.get("text","")).json()[k] for k in ("periodClass","intervalMonths","intervalDays","meterHours")) for x in form.get("originalPeriodDeclarations",[])}
    return "conflict" if len(periods)>1 else "missing" if period(form).period_class=="unknown" else "documented" if len(periods)==1 else "recorded"


def pump(form): return form.get("formKind")=="grouped-inspection" and "پمپ" in form.get("title","")


def family(form): return ("pumps-process" if form.get("hall") in ("فرآیند","فرایند") else "pumps-other") if pump(form) else {"lubrication":"lubrication","control":"control"}.get(form.get("formKind"),"equipment")


def family_label(value): return {"pumps-process":"پمپ‌های سالن فرایند","pumps-other":"پمپ‌های سایر سالن‌ها","lubrication":"سرویس و روانکاری","control":"کنترل برگه‌ها"}.get(value,"برگه‌های تجهیزات")


def members(c): return c.query("SELECT id,code,name,hall FROM assets ORDER BY hall,code,id")


def working_template(source,assets):
    form=deepcopy(source);form.update(period=period(source).source_text,sourcePeriodStatus=period_status(source),sourcePolicyVersion=POLICY_VERSION)
    if any(c.get("activity") is True for c in cells(form)): form["issues"]=[i for i in form.get("issues",[]) if i!="no-actual-activities"]
    if not(pump(form) and form.get("hall")=="شیر" and not member_codes(form) and any(c.get("activity") is True for c in cells(form))): return form
    roster=sorted((a for a in assets if a["hall"]=="سالن بسته بندی شیر" and "پمپ" in a["name"]),key=lambda a:a["code"])
    if not roster or not form.get("tables"): return form
    rows=form["tables"][0].get("rows",[])
    headers=next((r for r in rows if any(c.get("activity") is True for c in r.get("cells",[]))),None)
    if headers is None: return form
    before=next((i for i,r in enumerate(rows) if any("نام وامضاء" in c.get("text","") for c in r.get("cells",[]))),len(rows))
    digest=lambda value:hashlib.sha256(value.encode()).hexdigest()[:24]
    for asset in roster:
        result=[]
        for header in headers["cells"]:
            column=header.get("sourceRef",{}).get("column",len(result)+1)
            result.append(dict(id=digest(f"{form.get('id','')}:master-roster:{asset['code']}:{column}"),text=asset["name"] if column==1 else asset["code"] if column==2 else "",colSpan=1,activity=False,chiefEditable=False,editable=header.get("activity") is True,derivedFrom="validated-equipment-catalog",assetId=asset["id"],assetCode=asset["code"]))
        rows.insert(before,dict(id=digest(f"{form.get('id','')}:master-roster:{asset['code']}"),cells=result,derivedFrom="validated-equipment-catalog"));before+=1
    form["assetMemberCodes"]=[a["code"] for a in roster]
    form["workingRoster"]=dict(source="validated-equipment-catalog",hall="سالن بسته بندی شیر",originalRosterEmpty=True,policyVersion=POLICY_VERSION)
    return form
