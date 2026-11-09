import {useRef,useState} from 'react';
import {ApiError,post,put} from './api';
import {SheetTables,SourceCellText} from './PmLibrary';
import type {PmTemplate} from './PmLibrary';
import type {PageProps} from './Orders';
import {ErrorBox,Field,Icon,errorText,num,requestId} from './ui';
import {isChief} from './permissions';
import {isPendingPmChange,usePmRequestKey} from './pmGovernance';
import type {PmMutation} from './pmGovernance';

interface ActivityDraft {key:string;id?:string;text:string;cloneFromId?:string;tableId:string;}
interface ActivityReview {sheet:PmTemplate;payload:Record<string,unknown>;}
export function activityDrafts(template:PmTemplate):ActivityDraft[]{
 return template.tables.flatMap(table=>table.rows.flatMap(row=>row.cells.filter(cell=>cell.activity).map(cell=>({key:cell.id,id:cell.id,text:cell.text,tableId:table.id}))));
}

export default function PmActivityEditor({template,context,cancel,saved,onBusy}:{template:PmTemplate;context:PageProps;cancel:()=>void;saved:()=>void;onBusy:(value:boolean)=>void}){
 const original=activityDrafts(template),[items,setItems]=useState<ActivityDraft[]>(()=>original),[reason,setReason]=useState(''),[busy,setBusy]=useState<'preview'|'save'|''>(''),[error,setError]=useState(''),[review,setReview]=useState<ActivityReview|null>(null),[conflict,setConflict]=useState(false),payload=usePmRequestKey(),revision=useRef(0);
 const retained=new Set(items.flatMap(item=>item.id?[item.id]:[])),deleted=original.filter(item=>!retained.has(item.id!)),added=items.filter(item=>!item.id),modified=items.filter(item=>item.id&&original.find(old=>old.id===item.id)?.text!==item.text),changed=deleted.length+added.length+modified.length;
 const ready=changed>0&&reason.trim().length>=3&&items.every(item=>item.text.trim());
 const invalidate=()=>{revision.current++;setReview(null);};
 const inspect=async()=>{
  if(busy||!ready||conflict)return;
  const current=revision.current,request=payload({version:template.version,reason:reason.trim(),activities:items.map(item=>({id:item.id,text:item.text.trim(),...(item.cloneFromId?{cloneFromId:item.cloneFromId}:{})}))});
  setReview(null);setBusy('preview');onBusy(true);setError('');
  try{
   const sheet=await post<PmTemplate>('/pm/templates/'+encodeURIComponent(template.id)+'/preview-activities',request);
   if(current===revision.current)setReview({sheet,payload:request});
  }catch(e){if(current===revision.current){setError(errorText(e));setConflict(e instanceof ApiError&&e.status===409);}}
  finally{setBusy('');onBusy(false);}
 };
 const save=async()=>{
  if(busy||!ready||!review||conflict)return;
  setBusy('save');onBusy(true);setError('');
  try{
   const result=await put<PmMutation<PmTemplate>>('/pm/templates/'+encodeURIComponent(template.id),review.payload);
   context.notify(isPendingPmChange(result)?'افزودن، حذف و اصلاح فعالیت‌ها برای تأیید رئیس ارسال شد.':'نسخهٔ جدید فعالیت‌ها ذخیره شد؛ برگه‌های صادرشدهٔ قبلی حفظ شدند.');saved();
  }catch(e){setError(errorText(e));if(e instanceof ApiError&&e.status===409){setConflict(true);setReview(null);}}
  finally{setBusy('');onBusy(false);}
 };
 return <section className="pm-activities" aria-label="ویرایش تعداد و شرح فعالیت‌های PM">
  <p className="pm-section-note">فعالیت تازه را کنار فعالیت مشابه اضافه کنید تا شکل ردیف یا ستون و خانه‌های نتیجه حفظ شود. فایل اصلی و برگه‌های اجرای قبلی تغییر نمی‌کنند.</p>
  <p className="pm-section-note">خانه‌های ثابتِ ردیف یا ستونِ الگو هم کپی می‌شوند؛ پیش‌نمایش کامل را بررسی کنید.</p>
  <ErrorBox text={error}/>{conflict&&<div className="pm-conflict">نسخهٔ برگه تغییر کرده است. تغییرات شما در این صفحه حفظ شده‌اند؛ متن‌ها را نگه دارید و نسخهٔ تازه را باز کنید.</div>}
  {busy==='preview'&&<p role="status" aria-live="polite">در حال دریافت پیش‌نمایش کامل برگه…</p>}
  <fieldset disabled={!!busy||conflict}>
   <div className="pm-activity-toolbar"><b>{num(items.length,0)} فعالیت</b><span>{num(added.length,0)} افزودن · {num(deleted.length,0)} حذف · {num(modified.length,0)} اصلاح متن</span></div>
   <div className="pm-activity-list">{items.map((item,index)=><article key={item.key} className={!item.id||modified.includes(item)?'changed':''}>
    <div className="pm-activity-number"><b>{num(index+1,0)}</b><small>جدول {num(template.tables.findIndex(table=>table.id===item.tableId)+1,0)}</small></div>
    <div><label className="pm-activity-label" htmlFor={'activity-'+item.key}>شرح فعالیت {num(index+1,0)}{!item.id?' — تازه':''}</label>
     <textarea id={'activity-'+item.key} rows={3} maxLength={10000} value={item.text} onChange={event=>{setItems(current=>current.map(old=>old.key===item.key?{...old,text:event.target.value}:old));invalidate();}}/>
     <div className="pm-inline-actions"><button type="button" className="button secondary small" disabled={items.length>=200} onClick={()=>{setItems(current=>{const at=current.findIndex(old=>old.key===item.key);const next=[...current];next.splice(at+1,0,{key:requestId(),text:'',cloneFromId:item.id||item.cloneFromId,tableId:item.tableId});return next;});invalidate();}}><Icon name="plus" size={16}/>افزودن فعالیت بعد از این</button><button type="button" className="button ghost small" disabled={items.filter(old=>old.tableId===item.tableId).length<=1} onClick={()=>{setItems(current=>current.filter(old=>old.key!==item.key));invalidate();}}>حذف فعالیت</button></div>
    </div>
   </article>)}</div>
   <Field label="علت اصلاح فعالیت‌ها *"><textarea rows={2} minLength={3} maxLength={2000} value={reason} onChange={event=>{setReason(event.target.value);invalidate();}}/></Field>
   {review&&<div className="pm-review"><h4>تغییرات آمادهٔ ثبت</h4>{deleted.map(item=><p key={item.key}><b>حذف: </b><del>{item.text}</del></p>)}{items.filter(item=>!item.id||modified.includes(item)).map(item=><p key={item.key}><b>{item.id?'اصلاح متن: ':'افزودن: '}</b>{item.text}</p>)}<p>تعداد نهایی: {num(items.length,0)} فعالیت · علت: {reason}</p><h4>پیش‌نمایش کامل برگه · نسخهٔ {num(review.sheet.version,0)}</h4><div className="pm-paper-preview"><SheetTables sheet={review.sheet} renderCell={cell=><SourceCellText cell={cell}/>}/></div></div>}
   <div className="pm-inline-actions"><button type="button" className="button ghost" onClick={cancel}>انصراف از اصلاح</button><button type="button" className="button secondary" disabled={!ready} onClick={()=>void inspect()}>{busy==='preview'?'دریافت پیش‌نمایش…':'پیش‌نمایش تغییرات'}</button><button type="button" className="button primary" disabled={!ready||!review} onClick={()=>void save()}>{busy==='save'?'ثبت…':isChief(context.user.role)?'ثبت نسخهٔ جدید فعالیت‌ها':'ارسال اصلاحات برای رئیس'}</button></div>
  </fieldset>
 </section>;
}
