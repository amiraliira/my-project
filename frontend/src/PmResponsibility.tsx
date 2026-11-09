import { useState } from 'react';
import { ApiError, post } from './api';
import { CatalogSelect } from './Catalog';
import type { PageProps } from './Orders';
import type { WorkOrder } from './types';
import { ErrorBox, Field, Loading, Modal, dateFa, errorText, useResource } from './ui';

interface ResponsibilityTransfer {
 id:string; fromSupervisorId:string; fromSupervisorName:string; toSupervisorId:string; toSupervisorName:string;
 actorName:string; reason:string; createdAt:string; orderVersion:number;
}
export interface PmResponsibilityPreview {
 orderId:string; version:number; number:string; title:string; specialty:string;
 originalSupervisorId:string; originalSupervisorName:string; supervisorId:string; supervisorName:string;
 eligible:boolean; blockedReason?:string|null; history:ResponsibilityTransfer[];
}
export const mayTransferPmResponsibility = (role:string) => role==='head'||role==='manager';

export default function PmResponsibility({id,context,close,saved}:{id:string;context:PageProps;close:()=>void;saved:()=>void}) {
 const resource=useResource<PmResponsibilityPreview|null>('/pm/tasks/'+encodeURIComponent(id)+'/responsibility',null,0,mayTransferPmResponsibility(context.user.role));
 const [busy,setBusy]=useState(false);
 if(!mayTransferPmResponsibility(context.user.role))return null;
 return <Modal title="اصلاح مسئول برگهٔ صادرشده" close={close} locked={busy}>
  <ErrorBox text={resource.error} retry={resource.reload}/>{resource.loading?<div className="modal-body"><Loading/></div>:resource.data?<ResponsibilityEditor key={resource.data.version} detail={resource.data} context={context} close={close} saved={saved} reload={resource.reload} onBusy={setBusy}/>:<div className="modal-footer"><button className="button ghost" onClick={close}>بستن</button></div>}
 </Modal>;
}

export function ResponsibilityEditor({detail,context,close,saved,reload,onBusy}:{detail:PmResponsibilityPreview;context:PageProps;close:()=>void;saved:()=>void;reload:()=>void;onBusy:(busy:boolean)=>void}) {
 const [supervisor,setSupervisor]=useState(''),[supervisorLabel,setSupervisorLabel]=useState(''),[reason,setReason]=useState(''),[review,setReview]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[conflict,setConflict]=useState(false);
 const allowed=mayTransferPmResponsibility(context.user.role)&&detail.eligible;
 const ready=allowed&&!!supervisor&&supervisor!==detail.supervisorId&&reason.trim().length>=3;
 async function save(){
  if(!ready||!review||busy||conflict)return;
  setBusy(true);onBusy(true);setError('');
  try{
   await post<WorkOrder>('/pm/tasks/'+encodeURIComponent(detail.orderId)+'/responsibility',{version:detail.version,supervisorId:supervisor,reason:reason.trim()});
   saved();context.notify('مسئول برگه اصلاح شد؛ برگه به کارتابل سرپرست انتخاب‌شده منتقل شد.');close();
  }catch(e){setError(errorText(e));setConflict(e instanceof ApiError&&e.status===409);}
  finally{setBusy(false);onBusy(false);}
 }
 return <form onSubmit={event=>{event.preventDefault();void save();}}>
  <div className="modal-body pm-detail"><h3>{detail.title}</h3><p><bdi>{detail.number}</bdi> · {detail.specialty}</p>
   <p>سرپرست مسئول فعلی: <b>{detail.supervisorName}</b></p><p className="pm-section-note">سرپرست هنگام صدور: {detail.originalSupervisorName}</p>
   <ErrorBox text={error}/>{conflict&&<div className="pm-conflict">وضعیت این برگه تغییر کرده است. آخرین وضعیت را دریافت و انتقال را دوباره بررسی کنید.<button type="button" className="button ghost small" disabled={busy} onClick={()=>{setConflict(false);setReview(false);reload();}}>دریافت آخرین وضعیت</button></div>}
   {!detail.eligible?<p className="pm-message warning">{detail.blockedReason||'فقط برگهٔ پیش‌نویسی که ثبت اجرای آن شروع نشده است قابل انتقال است.'}</p>:<fieldset disabled={busy||conflict||!allowed}>
    <Field label={'سرپرست جدید '+detail.specialty+' *'}><CatalogSelect kind="people" value={supervisor} filters={{role:'supervisor',specialty:detail.specialty,active:'true'}} placeholder="انتخاب سرپرست فعال از همین تخصص" onChange={value=>{setSupervisor(value);setSupervisorLabel('');setReview(false);}} onSelected={item=>setSupervisorLabel(item.name+' · '+item.code)}/></Field>
    {supervisor===detail.supervisorId&&<p className="pm-message warning">سرپرست جدید باید با مسئول فعلی متفاوت باشد.</p>}
    <Field label="علت اصلاح مسئول *"><textarea rows={3} maxLength={2000} value={reason} onChange={event=>{setReason(event.target.value);setReview(false);}} placeholder="علت ارسال برگه به کارتابل سرپرست صحیح"/></Field>
    <p className="pm-section-note">پس از انتقال، تخصیص قبلی مجری پاک می‌شود و سرپرست جدید مجری را تعیین می‌کند. مسئول هنگام صدور و سابقهٔ این اصلاح حفظ می‌شوند. برای تعیین مسئول نوبت‌های بعدی، برنامهٔ بازدید را جداگانه اصلاح کنید.</p>
    {review&&<div className="pm-review"><h4>انتقال آمادهٔ ثبت</h4><p>از: {detail.supervisorName}</p><p>به: {supervisorLabel||'سرپرست انتخاب‌شده'}</p><p>علت: {reason.trim()}</p><p>این برگه از کارتابل مسئول فعلی خارج و وارد کارتابل سرپرست جدید می‌شود.</p></div>}
   </fieldset>}
   <details className="pm-source-details"><summary>سابقهٔ اصلاح مسئول</summary>{detail.history.length?detail.history.map(transfer=><article className="pm-revisions" key={transfer.id}><p>{transfer.fromSupervisorName} ← {transfer.toSupervisorName}</p><p>{transfer.reason}</p><small>{transfer.actorName} · {dateFa(transfer.createdAt,true)}</small></article>):<p className="pm-section-note">مسئول این برگه از زمان صدور تغییر نکرده است.</p>}</details>
  </div><div className="modal-footer"><button type="button" className="button ghost" disabled={busy} onClick={close}>بستن</button>{allowed&&<><button type="button" className="button secondary" disabled={busy||conflict||!ready||!supervisorLabel} onClick={()=>setReview(true)}>بررسی انتقال</button><button className="button primary" disabled={busy||conflict||!ready||!review}>{busy?'ثبت انتقال…':'ثبت انتقال مسئول'}</button></>}</div>
 </form>;
}
