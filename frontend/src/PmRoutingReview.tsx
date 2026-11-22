import {useState} from 'react';
import {ApiError,post} from './api';
import type {PageProps} from './Orders';
import {CatalogSelect,PageControls} from './Catalog';
import PmResponsibility from './PmResponsibility';
import PmSupervisorSelect,{pmSupervisorLabel} from './PmSupervisorSelect';
import {Empty,ErrorBox,Field,Loading,Modal,errorText,num,statuses,useResource} from './ui';
import './pm-routing-review.css';

type ReviewSection = 'plans' | 'issued' | 'history';
type Target = {id:string;name:string;code:string;specialty:string};
interface ReviewItem {
 id:string; title:string; hall:string; specialty:string;
 supervisorId?:string|null; supervisorName?:string; supervisorCode?:string;
 version?:number; active?:boolean; status?:string; canReviewTransfer?:boolean;
}
interface ReviewPage {
 target:Target;
 items:ReviewItem[];
 counts:Record<ReviewSection,number>;
 total:number; page:number; pageSize:number;
}
const sections:Record<ReviewSection,string> = {
 plans:'برنامه‌های آینده', issued:'برگه‌های باز', history:'سوابق بسته'
};

export default function PmRoutingReview({context,revision,changed}: {
 context:PageProps; revision:number; changed:()=>void;
}) {
 const [target,setTarget]=useState(''), [source,setSource]=useState(''), [specialty,setSpecialty]=useState('');
 const [selected,setSelected]=useState<{target:string;source:string;specialty:string}|null>(null);
 const [section,setSection]=useState<ReviewSection>('plans'), [page,setPage]=useState(1);
 const [transfer,setTransfer]=useState<{item:ReviewItem;target:Target}|null>(null);
 const [planTransfer,setPlanTransfer]=useState<{item:ReviewItem;target:Target}|null>(null);
 const units=useResource<{specialties:string[]}>('/pm/routing-review',{specialties:[]});
 const query=new URLSearchParams({
  targetId:selected?.target||'', sourceId:selected?.source||'', specialty:selected?.specialty||'',
  section, page:String(page), pageSize:'20', revision:String(revision)
 });
 const report=useResource<ReviewPage|null>('/pm/routing-review?'+query,null,0,!!selected);
 const data=report.data;
 const chooseSection=(next:ReviewSection)=>{setSection(next);setPage(1);};
 const clearSelection=()=>{setSelected(null);setPage(1);};
 return <div className="pm-routing-review">
  <p className="pm-section-note">سرپرست درست را بر اساس نام، واحد و کد پرسنلی انتخاب کنید. فهرست نشان می‌دهد کدام برنامه‌ها و برگه‌ها هنوز به او ارجاع نشده‌اند.</p>
  <form className="pm-routing-filters" onSubmit={event=>{
   event.preventDefault();
   if(!target||target===source)return;
   if(selected?.target===target&&selected.source===source&&selected.specialty===specialty&&page===1)report.reload();
   setPage(1);
   setSelected({target,source,specialty});
  }}>
   <PmSupervisorSelect label="سرپرست صحیح *" value={target} onChange={value=>{setTarget(value);clearSelection();}}/>
   <Field label="مسئول فعلی — اختیاری"><CatalogSelect kind="people" value={source}
    filters={{role:'supervisor'}} placeholder="همهٔ مسئولان فعلی"
    onChange={value=>{setSource(value);clearSelection();}}/></Field>
   <Field label="واحد فنی روی برگه"><select value={specialty} onChange={event=>{setSpecialty(event.target.value);clearSelection();}}>
    <option value="">همهٔ واحدها</option>{units.data.specialties.map(unit=><option key={unit} value={unit}>{unit}</option>)}
   </select></Field>
   <button className="button primary" disabled={!target||target===source||report.loading&&!!selected}>بررسی ارجاع‌ها</button>
  </form>
  <ErrorBox text={units.error} retry={units.reload}/>
  {target&&target===source&&<p className="pm-message warning">مسئول فعلی و سرپرست صحیح باید متفاوت باشند.</p>}
  {selected&&<>
   <ErrorBox text={report.error} retry={report.reload}/>
   {data?<div className="pm-routing-result">
    <p>سرپرست صحیح: <strong>{data.target.name}</strong> · {data.target.specialty} · کد {data.target.code}</p>
    <div className="pm-routing-sections" aria-label="بخش‌های بررسی ارجاع">
     {(Object.keys(sections) as ReviewSection[]).map(key=><button type="button" key={key}
      className={section===key?'button primary':'button secondary'} aria-pressed={section===key}
      onClick={()=>chooseSection(key)}>{sections[key]} <b>{num(data.counts[key],0)}</b></button>)}
    </div>
    {section==='plans'&&<p className="pm-section-note">رئیس می‌تواند مسئول نوبت‌های هنوز صادرنشده را اصلاح کند. دوره، تجهیز و وضعیت صدور حفظ می‌شوند.</p>}
    {section==='issued'&&<p className="pm-section-note">برگهٔ باز می‌تواند در انتظار انجام یا در حال بررسی باشد. انتقال فقط پس از بررسیِ برگه‌ای که هنوز شروع نشده مجاز است.</p>}
    {section==='history'&&<p className="pm-section-note">برگه‌های تکمیل‌شده و لغوشده برای حفظ سابقه در این فهرست می‌مانند.</p>}
    {data.items.length===0?<Empty title="موردی برای این بخش باقی نمانده است"/>:
     <ul className="pm-routing-list">
      {data.items.map(item=><li key={item.id}>
       <div><strong>{item.title}</strong><small>{[item.hall,item.specialty].filter(Boolean).join(' · ')}</small>
        <span>{item.supervisorName||'بدون مسئول'}{item.supervisorCode&&<> · کد <bdi>{item.supervisorCode}</bdi></>}</span>
       </div>
       {section==='plans'?<div><small>{item.active?'صدور فعال':'صدور غیرفعال'}</small>
        {context.user.role==='head'&&Number.isInteger(item.version)&&<button className="button secondary small" onClick={()=>setPlanTransfer({item,target:data.target})}>بررسی اصلاح مسئول برنامه</button>}</div>:
       <div><small>{statuses[item.status||'']||'برگهٔ صادرشده'}</small>
        {section==='issued'&&item.canReviewTransfer&&<button className="button secondary small" onClick={()=>setTransfer({item,target:data.target})}>بررسی انتقال</button>}
       </div>}
      </li>)}
     </ul>}
    <PageControls page={data.page} total={data.total} pageSize={data.pageSize} change={setPage} busy={report.refreshing}/>
   </div>:report.loading||report.refreshing?<Loading/>:null}
  </>}
  {transfer&&<PmResponsibility id={transfer.item.id} proposedSupervisor={transfer.target}
   reviewedSupervisorId={transfer.item.supervisorId||undefined} context={context}
   close={()=>setTransfer(null)} saved={()=>{setPage(1);changed();}}/>}
  {planTransfer&&<PlanResponsibility item={planTransfer.item} target={planTransfer.target}
   close={()=>setPlanTransfer(null)} refresh={report.reload} saved={()=>{
    setPlanTransfer(null);setPage(1);changed();context.notify('مسئول نوبت‌های هنوز صادرنشده اصلاح شد.');
   }}/>}
 </div>;
}

function PlanResponsibility({item,target,close,saved,refresh}: {
 item:ReviewItem; target:Target; close:()=>void; saved:()=>void; refresh:()=>void;
}) {
 const [reason,setReason]=useState(''), [busy,setBusy]=useState(false);
 const [error,setError]=useState(''), [stale,setStale]=useState(false);
 const save=async()=>{
  if(busy||stale||reason.trim().length<3)return;
  setBusy(true);setError('');
  try {
   await post('/pm/plans/'+encodeURIComponent(item.id)+'/responsibility',{
    version:item.version, previousSupervisorId:item.supervisorId??null, supervisorId:target.id, reason:reason.trim()
   });
   saved();
  } catch(cause) {
   setError(errorText(cause));
   setStale(cause instanceof ApiError&&cause.status===409);
  } finally {setBusy(false);}
 };
 return <Modal title="اصلاح مسئول نوبت‌های بعدی PM" close={close} locked={busy}>
  <form onSubmit={event=>{event.preventDefault();void save();}}>
   <div className="modal-body pm-detail">
    <h3>{item.title}</h3>
    <p>{[item.hall,item.specialty].filter(Boolean).join(' · ')}</p>
    <p>مسئول فعلی: <strong>{item.supervisorName||'بدون مسئول'}</strong>{item.supervisorCode&&<> · کد <bdi>{item.supervisorCode}</bdi></>}</p>
    <p>سرپرست جدید: <strong>{pmSupervisorLabel(target)}</strong></p>
    <p className="pm-section-note">این انتخاب فقط در برگه‌هایی اعمال می‌شود که هنوز صادر نشده‌اند.</p>
    <ErrorBox text={error}/>
    <Field label="علت اصلاح مسئول *"><textarea required minLength={3} maxLength={2000} rows={3}
     value={reason} disabled={busy||stale} onChange={event=>setReason(event.target.value)}/></Field>
   </div>
   <div className="modal-footer">
    <button className="button ghost" type="button" disabled={busy} onClick={close}>بستن</button>
    {stale?<button className="button secondary" type="button" onClick={()=>{close();refresh();}}>بازبینی دوباره</button>:
     <button className="button primary" type="submit" disabled={busy||reason.trim().length<3}>{busy?'در حال ثبت…':'ثبت مسئول نوبت‌های بعدی'}</button>}
   </div>
  </form>
 </Modal>;
}
