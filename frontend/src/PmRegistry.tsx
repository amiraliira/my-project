import {useEffect,useState} from 'react';
import {PageControls,emptyPage,ServerTable} from './Catalog';
import type {PageResult} from './types';
import {Empty,ErrorBox,Icon,Loading,SearchInput,num,useDebounce,useResource} from './ui';
import {pmMonths,persianYear,periodLabels,recurrenceLabel,formPeriodGroup} from './pmSchedule';
import type {PmRecurrence,ScheduleSlot} from './pmSchedule';

export interface RegistryForm {
 templateId:string;planId:string;title:string;formCode?:string;specialty:string;hall?:string;period:string;periodClass:string;intervalMonths?:number;intervalDays?:number;meterHours?:number;triggerKind?:string;
 formKind?:string;mappingStatus:string;issues:string[];calendarEligible?:boolean;version:number;planVersion:number;active:boolean;mappingConfirmed:boolean;
 association?:'source'|'confirmed'|'ambiguous';groupedSheet?:boolean;memberCount?:number;slots:ScheduleSlot[];scheduleMode?:string;recurrence?:PmRecurrence;supervisorId?:string;candidateCodes?:string[];
 family?:string;familyLabel?:string;equipmentLabel?:string;memberCodes?:string[];assetCount?:number;supervisorLabel?:string|null;identityStatus?:string;periodLabel?:string;periodConflict?:boolean;
}
interface Facet {value:string;label:string;count:number;}
interface FormsPage extends PageResult<RegistryForm> {halls?:string[];families?:string[];periodClasses?:string[];facets?:{halls:Facet[];families:Facet[];periodClasses:Facet[]};summary?:{totalForms:number;executableForms:number;pumpGroups:number;activeForms:number;needsEquipmentReview:number};}
const familyLabels:Record<string,string>={pumps:'پمپ‌ها',tanks:'مخزن‌ها',lubrication:'سرویس و روانکاری',equipment:'دستگاه‌ها',control:'برگه‌های کنترلی'};
/** The catalogue follows physical forms: a shared pump sheet is displayed once. */
export function PmForms({refresh,openPlan,openTemplate}:{refresh:number;openPlan:(id:string)=>void;openTemplate:(id:string)=>void}) {
 const[q,setQ]=useState(''),[hall,setHall]=useState(''),[periodClass,setPeriodClass]=useState(''),[specialty,setSpecialty]=useState(''),[family,setFamily]=useState(''),[page,setPage]=useState(1),search=useDebounce(q,220);
 useEffect(()=>setPage(1),[search,hall,periodClass,specialty,family]);
 const query=new URLSearchParams({q:search,hall,periodClass,specialty,family,page:String(page),pageSize:'25'});
 const resource=useResource<FormsPage>('/pm/registry/forms?'+query,emptyPage<RegistryForm>());
 useEffect(()=>{if(refresh)resource.reload();},[refresh,resource.reload]);
 const data=resource.data,filtered=!!(q||hall||periodClass||specialty||family),hallFacets=data.facets?.halls||data.halls?.map(value=>({value,label:value,count:0}))||[],periodFacets=data.facets?.periodClasses||data.periodClasses?.map(value=>({value,label:periodLabels[value]||value,count:0}))||[];
 const groups:Array<{hall:string;periods:Array<{key:string;label:string;forms:RegistryForm[]}>}>=[];
 for(const form of data.items){const hallName=form.hall||'سالن در برگه مشخص نشده';let group=groups.find(g=>g.hall===hallName);if(!group){group={hall:hallName,periods:[]};groups.push(group);}const heading=formPeriodGroup(form);let period=group.periods.find(p=>p.key===heading.key);if(!period){period={...heading,forms:[]};group.periods.push(period);}period.forms.push(form);}
 const reset=()=>{setQ('');setHall('');setPeriodClass('');setSpecialty('');setFamily('');setPage(1);};
 return <section className="pm-forms" aria-label="برگه‌های بازدید به ترتیب سالن و دوره">
  <div className="pm-forms-intro"><div><h2>برگه‌های بازدید</h2><p>سالن را انتخاب کنید؛ برگه‌ها به ترتیب دورهٔ بازدید و دستگاه نمایش داده می‌شوند.</p></div><button type="button" className="button ghost small" disabled={resource.loading} onClick={resource.reload}><Icon name="refresh" size={16}/>تازه‌سازی</button></div>
  <div className="pm-forms-filters"><SearchInput value={q} onChange={setQ} placeholder="نام دستگاه، کد یا نام برگه…"/>
   <label><span>۱. سالن</span><select aria-label="سالن برگه‌ها" value={hall} onChange={e=>setHall(e.target.value)}><option value="">همهٔ سالن‌ها</option>{hallFacets.map(f=><option key={f.value} value={f.value}>{f.label}</option>)}</select></label>
   <label><span>۲. دورهٔ بازدید</span><select aria-label="دورهٔ بازدید برگه‌ها" value={periodClass} onChange={e=>setPeriodClass(e.target.value)}><option value="">همهٔ دوره‌ها</option>{periodFacets.map(f=><option key={f.value} value={f.value}>{f.label||periodLabels[f.value]||f.value}</option>)}</select></label>
   <label><span>۳. واحد مسئول</span><select aria-label="واحد مسئول برگه‌ها" value={specialty} onChange={e=>setSpecialty(e.target.value)}><option value="">همهٔ واحدها</option><option>مکانیک</option><option>برق</option><option>تاسیسات</option></select></label>
  </div>
  <div className="pm-family-shortcuts" aria-label="نوع برگه"><span>نوع برگه:</span><button type="button" aria-pressed={!family} onClick={()=>setFamily('')}>همه</button>{(data.facets?.families||data.families?.map(value=>({value,label:familyLabels[value]||value,count:0}))||[]).map(f=><button type="button" key={f.value} aria-pressed={family===f.value} onClick={()=>setFamily(f.value)}>{f.label||familyLabels[f.value]||f.value}</button>)}</div>
  <div className="pm-forms-count"><span><b>{num(data.total,0)}</b> برگه {filtered?'در این انتخاب':'در کتابخانه'}{data.summary&&filtered&&data.summary.totalForms!==data.total?` از ${num(data.summary.totalForms,0)} برگه`:''}</span><p>هر ردیف یک برگهٔ اصلی است. برگهٔ گروهی پمپ‌ها یک بار نمایش داده می‌شود و همهٔ پمپ‌های همان سالن را پوشش می‌دهد.</p>{filtered&&<button type="button" className="text-button" onClick={reset}>نمایش همهٔ برگه‌ها</button>}</div>
  <ErrorBox text={resource.error} retry={resource.reload}/>
  {resource.loading?<Loading/>:data.items.length?<div className="pm-forms-groups">{groups.map(group=><section className="pm-hall-group" key={group.hall} aria-label={'برگه‌های '+group.hall}><header><Icon name="assets" size={20}/><h3>{group.hall}</h3></header>{group.periods.map(period=><div className="pm-period-group" key={period.key}><h4><Icon name="plans" size={16}/>{period.label}</h4><div className="pm-form-column-head" aria-hidden="true"><span>دستگاه / گروه</span><span>برگهٔ بازدید</span><span>واحد</span><span>دورهٔ روی برگه</span><span>برنامه</span><span>عملیات</span></div>{period.forms.map(form=><article className="pm-form-card" key={form.templateId} data-template-id={form.templateId}>
    <div className="pm-form-device"><small>دستگاه / گروه</small><strong>{form.equipmentLabel||form.familyLabel||form.title}</strong>{form.groupedSheet&&<span className="pm-group-label">یک برگهٔ گروهی{(form.memberCount||form.assetCount)?` · ${num(form.memberCount||form.assetCount,0)} دستگاه`:''}</span>}{form.memberCodes?.length?<details className="pm-roster-preview"><summary>کد دستگاه‌ها</summary><p><bdi dir="ltr">{form.memberCodes.join('، ')}</bdi></p></details>:form.identityStatus==='working-roster'?<span className="pm-period-check">فعالیت‌ها آماده؛ فهرست پمپ‌های سالن {form.hall||'شیر'} تکمیل شود.</span>:null}</div>
    <div className="pm-form-title"><small>برگهٔ بازدید</small><button type="button" className="pm-form-open" onClick={()=>openTemplate(form.templateId)}>{form.title}</button>{form.formCode&&<bdi className="pm-form-code" dir="ltr">{form.formCode}</bdi>}</div>
    <div className="pm-form-specialty"><small>واحد مسئول</small><span className={'pm-specialty '+(form.specialty==='برق'?'electrical':form.specialty==='مکانیک'?'mechanical':'utilities')}>{form.specialty}</span></div>
    <div className="pm-form-period"><small>دورهٔ روی برگه</small><strong>{form.periodLabel||form.period||'در اصل برگه مشخص نشده'}</strong>{form.period&&form.periodLabel&&form.period!==form.periodLabel&&<span>{form.period}</span>}{form.periodConflict&&<span className="pm-period-check">دوره‌های درج‌شده نیازمند بررسی</span>}</div>
    <div className="pm-form-program"><small>برنامهٔ انجام</small><span className={'pm-program-status '+(form.active?'active':'')}>{form.active?'فعال':form.mappingConfirmed?'تأییدشده؛ غیرفعال':'برای تنظیم برنامه'}</span>{form.supervisorLabel&&<span>{form.supervisorLabel}</span>}{form.active&&<span>{recurrenceLabel(form.recurrence)}</span>}</div>
    <div className="pm-form-actions"><button type="button" className="button secondary small" onClick={()=>openTemplate(form.templateId)}>مشاهدهٔ برگه</button>{form.planId&&<button type="button" className="button ghost small" onClick={()=>openPlan(form.planId)}>تنظیم برنامه</button>}</div>
   </article>)}</div>)}</section>)}</div>:<Empty title="برگه‌ای با این انتخاب پیدا نشد" detail="سالن، دوره یا واحد را تغییر دهید یا همهٔ برگه‌ها را نمایش دهید." icon="plans" action={filtered?<button className="button secondary" onClick={reset}>نمایش همهٔ برگه‌ها</button>:undefined}/>}
  {!resource.loading&&<PageControls page={data.page} total={data.total} pageSize={data.pageSize} change={setPage} busy={resource.loading}/>}
 </section>;
}
interface RegistryAsset {assetId:string;code:string;name:string;hall:string;line:string;forms:RegistryForm[];status:string;coverage?:Record<string,string>;}
interface RegistryPage extends PageResult<RegistryAsset> {summary?:{equipmentTotal:number;withForms:number;withoutForms:number;needsReview:number;templatesTotal:number;unlinkedTemplates:number};halls?:string[];periodClasses?:string[];}
export function PmRegistry({refresh,openPlan,openTemplate}:{refresh:number;openPlan:(id:string,assetId?:string)=>void;openTemplate:(id:string)=>void}) {
 const [q,setQ]=useState(''),[hall,setHall]=useState(''),[specialty,setSpecialty]=useState(''),[periodClass,setPeriodClass]=useState(''),[status,setStatus]=useState(''),[year,setYear]=useState(persianYear()),[page,setPage]=useState(1);
 const search=useDebounce(q,220);
 useEffect(()=>setPage(1),[search,hall,specialty,periodClass,status,year]);
 const query=new URLSearchParams({page:String(page),pageSize:'25',q:search,hall,specialty,periodClass,status,year:String(year)});
 const resource=useResource<RegistryPage>('/pm/registry?'+query,{...emptyPage<RegistryAsset>()});
 useEffect(()=>{if(refresh)resource.reload();},[refresh,resource.reload]);
 const data=resource.data,summary=data.summary,formFiltered=!!(specialty||periodClass);
 return <section className="pm-registry" aria-label="جدول تجهیزات و زمان‌بندی PM">
  {summary&&<div className="pm-registry-stats">
   <div><b>{num(summary.equipmentTotal,0)}</b><span>تجهیز در محدودهٔ جستجو</span></div><div><b>{num(summary.withForms,0)}</b><span>{formFiltered?'دارای برگهٔ منطبق با فیلتر':'دارای برگهٔ شناسایی‌شده'}</span></div>
   <div className="warning"><b>{num(summary.needsReview,0)}</b><span>نیازمند بررسی تطبیق</span></div><div><b>{num(summary.withoutForms,0)}</b><span>{formFiltered?'بدون برگهٔ منطبق با فیلتر':'برگهٔ متصل‌نشده'}</span></div><div><b>{num(summary.templatesTotal,0)}</b><span>{formFiltered?'برگه در واحد و دورهٔ انتخابی':'اصل برگه در کتابخانه'}</span></div>
  </div>}
  <div className="pm-registry-filters">
   <SearchInput value={q} onChange={setQ} placeholder="کد یا نام دستگاه، برگه یا سالن…"/>
   <label><span>سالن</span><select aria-label="سالن جدول PM" value={hall} onChange={e=>setHall(e.target.value)}><option value="">همه سالن‌ها</option>{(data.halls||[]).map(h=><option key={h}>{h}</option>)}</select></label>
   <label><span>واحد</span><select aria-label="واحد جدول PM" value={specialty} onChange={e=>setSpecialty(e.target.value)}><option value="">همه واحدها</option><option>مکانیک</option><option>برق</option><option>تاسیسات</option></select></label>
   <label><span>دورهٔ برگه</span><select aria-label="دورهٔ جدول PM" value={periodClass} onChange={e=>setPeriodClass(e.target.value)}><option value="">همه دوره‌ها</option>{Object.entries(periodLabels).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
   <label><span>وضعیت برگه</span><select aria-label="وضعیت برگهٔ تجهیز" value={status} onChange={e=>setStatus(e.target.value)}><option value="">همه تجهیزات</option><option value="present">دارای برگهٔ شناسایی‌شده</option><option value="ambiguous">تطبیق مبهم</option><option value="missing">برگهٔ متصل‌نشده</option></select></label>
   <label><span>سال تقویم</span><input aria-label="سال تقویم PM" type="number" min="1300" max="1600" value={year} onChange={e=>{const v=Number(e.target.value);if(Number.isInteger(v)&&v>=1300&&v<=1600)setYear(v);}}/></label>
   <button className="button ghost small" onClick={resource.reload} disabled={resource.loading}><Icon name="refresh" size={16}/>تازه‌سازی</button>
  </div>
  {formFiltered&&<p className="pm-section-note">آمار برگه‌ها بر اساس واحد و دورهٔ انتخابی است. «بدون برگهٔ منطبق با فیلتر» به معنی نبودن سایر برگه‌های آن دستگاه نیست.</p>}
  <div className="pm-registry-legend"><span><i className="approved"/>برنامه فعال</span><span><i className="proposed"/>نوبت پیشنهادی / غیرفعال</span><span><i className="blank"/>بدون نوبت</span><small>چهار هفتهٔ شنبه تا جمعه از اولین شنبهٔ هر ماه · انتخاب نام برگه برای اصلاح</small></div>
  <ErrorBox text={resource.error} retry={resource.reload}/>
  {resource.loading?<Loading/>:data.items.length?<>
   <div className="pm-registry-scroll" tabIndex={0} aria-label="جدول زمان‌بندی تجهیزات؛ پیمایش افقی ماه‌ها">
    <table className="pm-registry-table" dir="rtl"><thead><tr><th rowSpan={2} className="pm-sticky-equipment">تجهیز و محل</th><th rowSpan={2} className="pm-sticky-form">برگه و عملیات رئیس</th><th rowSpan={2}>واحد</th><th rowSpan={2}>دورهٔ درج‌شده / اجرا</th><th rowSpan={2}>وضعیت</th>{pmMonths.map(name=><th key={name} colSpan={4}>{name}</th>)}</tr><tr>{pmMonths.flatMap((name)=>[1,2,3,4].map(week=><th className="pm-week-head" key={name+week}>{num(week,0)}</th>))}</tr></thead>
    <tbody>{data.items.flatMap(asset=>{const forms=asset.forms.length?asset.forms:[null];return forms.map((form,index)=><tr key={asset.assetId+':'+(form?.templateId||'missing')} data-asset-id={asset.assetId}>
     {index===0&&<td rowSpan={forms.length} className="pm-sticky-equipment"><bdi dir="ltr" className="pm-equipment-code">{asset.code}</bdi><strong>{asset.name}</strong><small>{asset.hall||'سالن تعیین نشده'}{asset.line?' · '+asset.line:''}</small><span className={'pm-coverage '+asset.status}>{asset.status==='missing'?'برگهٔ متصل‌نشده':asset.status==='ambiguous'?'تطبیق نیازمند بررسی':`${num(asset.forms.length,0)} برگهٔ مرتبط`}</span>{asset.coverage&&<div className="pm-unit-coverage">{['مکانیک','برق','تاسیسات'].map(spec=><span key={spec} className={asset.coverage?.[spec]||'missing'}>{spec}: {asset.coverage?.[spec]==='present'?'شناسایی شد':asset.coverage?.[spec]==='ambiguous'?'نیازمند بررسی':'متصل نشده'}</span>)}</div>}</td>}
     <td className="pm-sticky-form">{form?<><button type="button" className="pm-form-open" onClick={()=>form.planId?openPlan(form.planId,asset.assetId):openTemplate(form.templateId)}>{form.title}</button><small>کد فرم: <bdi dir="ltr">{form.formCode||'در منبع درج نشده'}</bdi>{form.groupedSheet?` · گروهی، ${num(form.memberCount||0,0)} تجهیز`:''}</small><div className="pm-inline-actions"><button className="button ghost small" onClick={()=>openTemplate(form.templateId)}>اصل برگه</button>{form.planId&&<button className="button secondary small" onClick={()=>openPlan(form.planId,asset.assetId)}>اصلاح و زمان‌بندی</button>}</div></>:<><b>برگه‌ای به این تجهیز متصل نشده</b><small>کتابخانهٔ برگه‌های بدون تطبیق را بررسی کنید.</small></>}</td>
     <td>{form?.specialty||'—'}</td><td>{form?<><strong>{form.period||'دوره در منبع مشخص نشده'}</strong>{!!form.meterHours&&<small className="pm-meter-note">شرط {num(form.meterHours,0)} ساعت دستگاه</small>}<small>اجرا: {recurrenceLabel(form.recurrence)}</small></>:'—'}</td>
     <td>{form?<><span className={'pm-coverage '+(form.active?'present':'ambiguous')}>{form.active?'فعال':form.mappingConfirmed?'تأییدشده؛ غیرفعال':'موعد تأیید نشده'}</span>{form.association!=='confirmed'&&<small>ارتباط از متن منبع؛ بازبینی لازم</small>}{!!form.issues.length&&<small className="pm-meter-note">{num(form.issues.length,0)} نکتهٔ منبع</small>}</>:'—'}</td>
     {pmMonths.flatMap((name,month)=>[1,2,3,4].map(week=>{const selected=!!form?.slots?.some(s=>s.month===month+1&&s.week===week);return <td className={'pm-week-cell '+(selected?(form?.active?'approved':'proposed'):'')} key={name+week} aria-label={`${name} هفتهٔ ${num(week,0)}: ${selected?'دارای نوبت':'بدون نوبت'}`}><span>{selected?'●':'·'}</span></td>;}))}
    </tr>);})}</tbody></table>
   </div><PageControls page={data.page} total={data.total} pageSize={data.pageSize} change={setPage} busy={resource.loading}/>
  </>:<Empty title="تجهیزی با این انتخاب‌ها پیدا نشد" detail="فیلتر سالن، واحد، دوره یا عبارت جستجو را تغییر دهید." icon="assets"/>}
 </section>;
}

export function PmUnlinked({refresh,openPlan,openTemplate}:{refresh:number;openPlan:(id:string)=>void;openTemplate:(id:string)=>void}) {
 const[q,setQ]=useState(''),[periodClass,setPeriodClass]=useState(''),[page,setPage]=useState(1),search=useDebounce(q,220);
 useEffect(()=>setPage(1),[search,periodClass]);
 const query=new URLSearchParams({q:search,periodClass,page:String(page),pageSize:'25'}),resource=useResource<PageResult<RegistryForm>>('/pm/registry/unlinked?'+query,emptyPage());
 useEffect(()=>{if(refresh)resource.reload();},[refresh,resource.reload]);
 return <><div className="pm-toolbar"><SearchInput value={q} onChange={setQ} placeholder="جستجوی برگهٔ بدون تطبیق، دوره یا سالن…"/><select aria-label="دورهٔ برگه‌های بدون تطبیق" value={periodClass} onChange={e=>setPeriodClass(e.target.value)}><option value="">همه دوره‌ها</option>{Object.entries(periodLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select><button className="button ghost small" onClick={resource.reload}>تازه‌سازی</button></div>
  <p className="pm-unlinked-help">این برگه‌ها هنوز ارتباط قابل اتکایی با یک تجهیز ندارند. وجود برگهٔ مبهم به معنی نبودن برگه نیست؛ کد دستگاه و اصل فایل را در اصلاح رئیس بررسی کنید.</p><ErrorBox text={resource.error} retry={resource.reload}/>
  {resource.loading?<Loading/>:<ServerTable result={resource.data} changePage={setPage} rowKey={f=>f.templateId} columns={[
   {title:'برگه / کد فرم',cell:f=><><strong>{f.title}</strong><small><bdi dir="ltr">{f.formCode||'کد فرم درج نشده'}</bdi></small></>},
   {title:'سالن / واحد',cell:f=><>{f.hall||'سالن نامشخص'}<small>{f.specialty}</small></>},
   {title:'دورهٔ اصل برگه',cell:f=><>{f.period||'دوره نامشخص'}{!!f.meterHours&&<small>شرط {num(f.meterHours,0)} ساعت دستگاه</small>}</>},
   {title:'کدهای پیشنهادی منبع',cell:f=><bdi dir="ltr">{f.candidateCodes?.join('، ')||'کد قابل اتکا یافت نشد'}</bdi>},
   {title:'اصلاح رئیس',cell:f=><div className="pm-inline-actions"><button className="button ghost small" onClick={()=>openTemplate(f.templateId)}>اصل برگه و فعالیت‌ها</button>{f.planId&&<button className="button secondary small" onClick={()=>openPlan(f.planId)}>تعیین تجهیز و برنامه</button>}</div>}
  ]} empty={<Empty title="برگهٔ بدون تطبیقی در این فهرست نیست" detail="برای بررسی همه برگه‌ها، دفتر برگه‌ها را باز کنید." icon="plans"/>}/>}</>;
}
