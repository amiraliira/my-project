import { lazy, Suspense } from 'react';
import Time24 from './Time24';
import {JalaliDate} from './JalaliDate';
import {PmExecution,pmStates} from './PmWork';
import { pmIsOverdue } from './pmAlarm';
import { OwnPerformance } from './Performance';
import './technician-pm.css';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, post, put } from './api';

import { OrderDetail } from './Orders';
import { CatalogSelect, PageControls } from './Catalog';
import { addQuantity, validQuantity } from './quantities';
import type { PageProps } from './Orders';
import type { ChecklistItem, OrderPage, OrderPart, PageResult, Part, Participant, WorkOrder } from './types';
import { Badge, DataTable, Empty, ErrorBox, Field, Icon, Loading,  dateFa, duration, errorText, num, requestId, statuses, todayIso, useResource, workTypes } from './ui';


const blankLine = '\u0001no-line';
function RegistrationForm({ context, existing, saved, reset }: { context: PageProps; existing?: WorkOrder; saved: () => void; reset: () => void }) {
 const { boot, user, notify } = context;
 const initialAsset = existing ? { hall: existing.hall, line: existing.line } : undefined;
 const self = existing?.participants.find(person => person.personId === user.id) || { personId: user.id, minutes: 0 };
 const initialParticipants = [self, ...(existing?.participants.filter(person => person.personId !== user.id) || [])];
 const createKey = useRef(requestId());
 const [savedOrder, setSavedOrder] = useState(existing), [selectedPartRecord, setSelectedPartRecord] = useState<Part | null>(null);
 const [type, setType] = useState(existing?.type || ''), [hall, setHall] = useState(initialAsset?.hall || ''), [line, setLine] = useState(initialAsset ? initialAsset.line || blankLine : ''), [assetId, setAsset] = useState(existing?.assetId || ''), [description, setDescription] = useState(existing?.description || ''), [workDate, setDate] = useState(existing?.workDate?.slice(0, 10) || todayIso()), [start, setStart] = useState(existing?.startTime?.slice(0, 5) || ''), [end, setEnd] = useState(existing?.endTime?.slice(0, 5) || ''), [participants, setParticipants] = useState<Participant[]>(initialParticipants), [companionSpecialties, setCompanionSpecialties] = useState(initialParticipants.map(person => person.specialty || (person.personId === user.id ? user.specialty : ''))), [parts, setParts] = useState<OrderPart[]>(existing?.parts || []), [selectedPart, setSelectedPart] = useState(''), [partQty, setPartQty] = useState(1), [noteType, setNoteType] = useState(existing?.noteType || ''), [note, setNote] = useState(existing?.note || ''), [component, setComponent] = useState(existing?.component || ''), [stopped, setStopped] = useState(existing?.productionStopped || false), [downtime, setDowntime] = useState(existing?.downtimeMinutes || 0), [checklist, setChecklist] = useState<ChecklistItem[]>(existing?.checklist || []), [error, setError] = useState(''), [busy, setBusy] = useState(false);
 const structureLocked = !!existing && (!!existing.planId || existing.createdBy !== user.id);
 const minutes = duration(start, end);
 const halls = boot.locations;
 const lines = [...new Set((boot.assetLines || []).filter(asset => asset.hall === hall).map(asset => asset.line || blankLine))];
 const specialties = boot.specialties;
 function timeChanged(value: string, isEnd = false) { if (isEnd) setEnd(value); else setStart(value); const next = duration(isEnd ? start : value, isEnd ? value : end); if (!existing) setParticipants(people => people.map(person => ({ ...person, minutes: next }))); }
 function addPart() { if (!selectedPart || !validQuantity(partQty)) { setError('مقدار قطعه باید مثبت، حداکثر یک میلیارد و با حداکثر شش رقم اعشار باشد.'); return; } const found = parts.find(part => part.partId === selectedPart); const quantity = found ? addQuantity(found.quantity, partQty) : partQty; if (quantity == null) { setError('مجموع مقدار این قطعه از حد مجاز بیشتر است.'); return; } setParts(current => found ? current.map(part => part.partId === selectedPart ? { ...part, quantity } : part) : [...current, { partId: selectedPart, quantity, code: selectedPartRecord?.code, name: selectedPartRecord?.name, unit: selectedPartRecord?.unit }]); setSelectedPart(''); setSelectedPartRecord(null); setPartQty(1); }
 async function save(event: FormEvent, submit: boolean) {
  event.preventDefault(); if (busy) return; setError('');
  if (!type || !assetId) { setError('ماهیت کار، سالن، خط و دستگاه را انتخاب کنید.'); return; }
  if (participants.length > 3) { setError('حداکثر سه انجام‌دهنده می‌توانند در یک کار ثبت شوند.'); return; }
  const selectedPeople = participants.filter(person => person.personId);
  if (new Set(selectedPeople.map(person => person.personId)).size !== selectedPeople.length) { setError('هر انجام‌دهنده را فقط یک بار انتخاب کنید.'); return; }
  if (selectedPeople.some(person => !Number.isInteger(person.minutes) || person.minutes < 0 || person.minutes > 1440 || minutes > 0 && person.minutes > minutes)) { setError('دقیقه مشارکت هر نفر باید عدد صحیح و حداکثر برابر مدت کار باشد.'); return; }
  if (submit && (!start || !end || !minutes || participants.some(person => !person.personId || !(person.minutes > 0)))) { setError('زمان شروع و پایان و دقیقه مشارکت همه انجام‌دهندگان را کامل کنید.'); return; }
  if (submit && description.trim().length < 3) { setError('شرح کار انجام‌شده را کامل کنید.'); return; }
  if (submit && type === 'PM' && checklist.some(item => !item.done || !item.text.trim())) { setError('پیش از ارسال کار PM، همه بندهای چک‌لیست را بررسی و علامت‌گذاری کنید.'); return; }
  const firstLine = description.trim().split('\n')[0].slice(0, 250);
  const title = existing?.title || (firstLine.length >= 3 ? firstLine : 'گزارش کار — تجهیز');
  setBusy(true);
  try {
   const payload = { version: savedOrder?.version, type, assetId, title, description, workDate, startTime: start || null, endTime: end || null, dueDate: existing?.dueDate || null, assigneeId: existing?.assigneeId || null, participants: selectedPeople.filter(person => person.minutes > 0).map(person => ({ personId: person.personId, minutes: person.minutes })), parts: parts.map(part => ({ partId: part.partId, quantity: part.quantity })), noteType, note: noteType ? note : '', component: noteType === 'technical' ? component : '', productionStopped: stopped, downtimeMinutes: stopped ? downtime : 0, checklist };
   const result = savedOrder ? await put<WorkOrder>('/orders/' + savedOrder.id, payload) : await post<WorkOrder>('/orders', payload, { 'X-Idempotency-Key': createKey.current });
   setSavedOrder(result); saved();
   if (submit) {
    try { await post('/orders/' + result.id + '/submit', { version: result.version }); }
    catch (submitError) {
     try { const current = await api<WorkOrder>('/orders/' + result.id); setSavedOrder(current); if (!['draft', 'returned'].includes(current.status)) { notify('گزارش برای تأیید ارسال شده است.'); saved(); reset(); return; } } catch { /* The saved id is retained for a safe retry. */ }
     throw submitError;
    }
   }
   notify(submit ? 'کار ثبت و برای تأیید ارسال شد.' : 'پیش‌نویس کار ذخیره شد.'); saved(); reset();
  } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
 }
 return <form className="registration-card registration-form" onSubmit={event => void save(event, true)}><div className="registration-form-heading"><h2>{existing ? 'ویرایش گزارش کار' : 'ثبت گزارش کار'}</h2>{existing && <span className="mono">{existing.number}</span>}</div><ErrorBox text={error}/>{structureLocked && <p className="registration-hint">مشخصات برگهٔ تخصیص‌یافته ثابت است؛ نتیجهٔ کار را ثبت کنید.</p>}{savedOrder && !existing && <p className="registration-hint">پیش‌نویس {savedOrder.number} ذخیره شد. ارسال مجدد از همین فرم انجام شود.</p>}
  <section className="registration-step"><h3>۱ · ماهیت کار</h3><div className="registration-chips">{Object.entries(workTypes).map(([value, title]) => <button key={value} type="button" disabled={structureLocked || busy} className={type === value ? 'selected' : ''} aria-pressed={type === value} onClick={() => setType(value)}>{value === 'PROJECT' ? 'پروژه' : value}{value !== 'PROJECT' && <span>{title}</span>}</button>)}</div></section>
  <section className={'registration-step ' + (!type ? 'pending' : '')}><Field label="۲ · سالن"><select value={hall} disabled={!type || structureLocked || busy} onChange={e => { setHall(e.target.value); setLine(''); setAsset(''); }}><option value="">— انتخاب سالن —</option>{halls.map(name => <option key={name} value={name}>{name}</option>)}</select></Field></section>
  <section className={'registration-step ' + (!hall ? 'pending' : '')}><Field label="۳ · خط"><select value={line} disabled={!hall || structureLocked || busy} onChange={e => { setLine(e.target.value); setAsset(''); }}><option value="">— انتخاب خط —</option>{lines.map(name => <option key={name} value={name}>{name === blankLine ? 'بدون خط' : name}</option>)}</select></Field></section>
  <section className={'registration-step ' + (!line ? 'pending' : '')}><Field label="۴ · دستگاه یا تجهیز"><CatalogSelect kind="assets" disabled={!line || structureLocked || busy} value={assetId} onChange={setAsset} filters={{ hall, line: line === blankLine ? '' : line }} fallbackLabel={existing ? existing.assetCode + ' — ' + existing.assetName : ''} placeholder="جستجوی دستگاه با نام یا کد"/></Field></section>
  <fieldset disabled={!assetId || busy} className={'registration-step registration-fields ' + (!assetId ? 'pending' : '')}><legend>۵ · شرح کار و قطعات مصرفی</legend><Field label="شرح کار"><textarea value={description} onChange={e => setDescription(e.target.value)} rows={3} placeholder="شرح مختصر کار انجام‌شده…"/></Field><Field label="قطعات مصرفی"><CatalogSelect kind="parts" disabled={!assetId || busy} value={selectedPart} onChange={setSelectedPart} onSelected={item => setSelectedPartRecord(item as Part)} placeholder="جستجوی قطعه با کد یا نام"/></Field><div className="registration-part-add"><Field label="مقدار مصرف"><input type="number" min="0.000001" max="1000000000" step="0.000001" value={partQty} onChange={e => setPartQty(Number(e.target.value))}/></Field><button type="button" className="button secondary" disabled={!selectedPart || partQty <= 0} onClick={addPart}><Icon name="plus" size={16}/>افزودن قطعه</button></div>{parts.map(part => { return <div className="selected-line" key={part.partId}><span>{part.code} — {part.name || part.partId}</span><b>{num(part.quantity, 6)} {part.unit}</b><button type="button" className="icon-button" aria-label="حذف قطعه" onClick={() => setParts(current => current.filter(item => item.partId !== part.partId))}><Icon name="close" size={17}/></button></div>; })}<div className="registration-note-grid"><Field label="نوع یادداشت"><select value={noteType} onChange={e => { setNoteType(e.target.value); if (!e.target.value) { setNote(''); setComponent(''); } }}><option value="">بدون یادداشت</option><option value="pm-change">اصلاح دوره یا برنامه PM</option><option value="technical">روش اجرای کار یا نکته فنی</option></select></Field>{noteType === 'technical' && <Field label="بخش / جزء دستگاه"><input value={component} onChange={e => setComponent(e.target.value)}/></Field>}</div>{noteType && <Field label="متن یادداشت"><textarea value={note} onChange={e => setNote(e.target.value)} rows={2}/></Field>}{(type === 'PM' || !!checklist.length) && <div className="registration-checklist"><h3>چک‌لیست انجام کار</h3>{checklist.map((item, i) => <div className="checklist-row" key={i}><input type="checkbox" aria-label={'انجام بند ' + (i + 1)} checked={item.done} onChange={e => setChecklist(current => current.map((entry, index) => index === i ? { ...entry, done: e.target.checked } : entry))}/>{structureLocked ? <span>{item.text}</span> : <input aria-label={'شرح بند ' + (i + 1)} value={item.text} onChange={e => setChecklist(current => current.map((entry, index) => index === i ? { ...entry, text: e.target.value } : entry))}/>} {!structureLocked && <button type="button" className="icon-button" aria-label="حذف بند" onClick={() => setChecklist(current => current.filter((_, index) => index !== i))}><Icon name="close" size={16}/></button>}</div>)}{!structureLocked && <button type="button" className="text-button" onClick={() => setChecklist(current => [...current, { text: '', done: false }])}><Icon name="plus" size={16}/>افزودن بند چک‌لیست</button>}</div>}</fieldset>
  <fieldset disabled={!assetId || busy} className={'registration-step registration-fields ' + (!assetId ? 'pending' : '')}><legend>۶ · زمان</legend><div className="registration-time-grid"><Field label="تاریخ (شمسی)"><JalaliDate value={workDate} onChange={setDate}/></Field><Field label="شروع"><Time24 label="ساعت شروع" value={start} disabled={!assetId || busy} onChange={value => timeChanged(value)}/></Field><Field label="پایان"><Time24 label="ساعت پایان" value={end} disabled={!assetId || busy} onChange={value => timeChanged(value, true)}/></Field></div><p className="registration-hint">مدت کار: <b>{num(minutes, 0)} دقیقه</b>{start && end && end < start ? ' · پایان در روز بعد محاسبه می‌شود.' : ''}</p><label className="checkbox"><input type="checkbox" checked={stopped} onChange={e => setStopped(e.target.checked)}/>توقف تولید</label>{stopped && <Field label="مدت توقف تولید (دقیقه)"><input type="number" required min="0" max="1440" step="1" value={downtime} onChange={e => setDowntime(Number(e.target.value))}/></Field>}</fieldset>
  <fieldset disabled={!assetId || busy} className={'registration-step registration-fields ' + (!assetId ? 'pending' : '')}><legend>۷ · انجام‌دهنده‌ها — حداکثر ۳ نفر</legend>{participants.map((person, index) => <div className="registration-person" key={index}><div className="registration-person-heading"><b>{index === 0 ? 'انجام‌دهنده اول · شما' : 'انجام‌دهنده ' + num(index + 1, 0)}</b>{index > 0 && <button type="button" className="icon-button" aria-label="حذف انجام‌دهنده" onClick={() => { setParticipants(current => current.filter((_, i) => i !== index)); setCompanionSpecialties(current => current.filter((_, i) => i !== index)); }}><Icon name="close" size={16}/></button>}</div><div className="registration-person-fields"><Field label="تخصص"><select value={index === 0 ? user.specialty : companionSpecialties[index] || ''} disabled={index === 0} onChange={e => { const specialty = e.target.value; setCompanionSpecialties(current => current.map((value, i) => i === index ? specialty : value)); setParticipants(current => current.map((value, i) => i === index ? { ...value, personId: '' } : value)); }}><option value="">— انتخاب تخصص —</option>{[...new Set([user.specialty, ...specialties])].filter(Boolean).map(specialty => <option key={specialty} value={specialty}>{specialty}</option>)}</select></Field><Field label="نام و نام خانوادگی">{index === 0 ? <input value={user.name} readOnly/> : <CatalogSelect kind="people" disabled={!companionSpecialties[index] || !assetId || busy} value={person.personId} onChange={id => setParticipants(current => current.map((value, i) => i === index ? { ...value, personId: id } : value))} onSelected={item => { if ('specialty' in item) setCompanionSpecialties(current => current.map((value, i) => i === index ? item.specialty : value)); }} filters={{ active: 'true', specialty: companionSpecialties[index] || '', excludeId: user.id }} fallbackLabel={person.personName} placeholder="انتخاب انجام‌دهنده"/>}</Field><Field label="دقیقه مشارکت"><input type="number" min="0" step="1" max={minutes || 1440} value={person.minutes} onChange={e => setParticipants(current => current.map((value, i) => i === index ? { ...value, minutes: Number(e.target.value) } : value))}/></Field></div></div>)}{participants.length < 3 && <button type="button" className="text-button" onClick={() => { setParticipants(current => [...current, { personId: '', minutes }]); setCompanionSpecialties(current => [...current, '']); }}><Icon name="plus" size={16}/>افزودن همکار</button>}</fieldset>
  <div className="registration-actions"><button type="submit" className="button primary" disabled={busy || !type || !assetId}><Icon name="check" size={18}/>{busy ? 'در حال ذخیره…' : 'ثبت و ارسال'}</button><button type="button" className="button secondary" disabled={busy || !type || !assetId} onClick={event => void save(event, false)}>ذخیره پیش‌نویس</button><button type="button" className="button ghost" disabled={busy} onClick={reset}>{existing ? 'انصراف از ویرایش' : 'پاک کردن فرم'}</button></div>
 </form>;
}

const EmDowntime = lazy(() => import('./EmDowntime'));

export default function TechnicianRegistration(context: PageProps) {
 const [performanceRevision, setPerformanceRevision] = useState(0);
 const [tab, setTab] = useState<'registration' | 'pm'>('registration');
 const [pmView, setPmView] = useState<'pending' | 'submitted' | 'completed'>('pending'), [pmPage, setPmPage] = useState(1), [pmOverdue, setPmOverdue] = useState(false);
 const [recordPage, setRecordPage] = useState(1), [assignedPage, setAssignedPage] = useState(1);
 const [editing, setEditing] = useState<WorkOrder | undefined>(), [pmEditing, setPmEditing] = useState<WorkOrder | undefined>(), [formVersion, setFormVersion] = useState(0), [detail, setDetail] = useState(''), [openRecords, setOpenRecords] = useState(false), [status, setStatus] = useState(''), [loadError, setLoadError] = useState(''), [opening, setOpening] = useState(''), [pmBusy, setPmBusy] = useState(false);
 const formRef = useRef<HTMLDivElement>(null), pmFormRef = useRef<HTMLDivElement>(null), editRequest = useRef<AbortController | null>(null), tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
 useEffect(() => () => editRequest.current?.abort(), []);
 const reset = useCallback(() => { editRequest.current?.abort(); setEditing(undefined); setFormVersion(value => value + 1); }, []);
 const resetPm = useCallback(() => { editRequest.current?.abort(); setPmEditing(undefined); setPmBusy(false); }, []);
 const closeDetail = useCallback(() => setDetail(''), []);
 useEffect(() => { setRecordPage(1); }, [status]);
 const records = useResource<OrderPage>('/orders?' + new URLSearchParams({ paged: 'true', page: String(recordPage), pageSize: '25', status }), { orders: [], total: 0, page: 1, pageSize: 25, totalPages: 0 }, 30000, tab === 'registration' && openRecords);
 const tasks = useResource<OrderPage>('/orders?' + new URLSearchParams({ paged: 'true', page: String(assignedPage), pageSize: '25', view: 'registrationAssignedPending' }), { orders: [], total: 0, page: 1, pageSize: 25, totalPages: 0 }, 30000, tab === 'registration');
 // A single lightweight request keeps the pending badge fresh while the normal form is open.
 const pmTasks = useResource<TechnicianPmPage>('/pm/tasks?' + new URLSearchParams({ page: String(tab === 'pm' ? pmPage : 1), pageSize: tab === 'pm' ? '25' : '1', view: tab === 'pm' ? pmView : 'pending', overdue: String(tab === 'pm' && pmView === 'pending' && pmOverdue) }), emptyPmPage, 5000);
 const lastPmCounts = useRef({ pending: 0, submitted: 0, completed: 0, overdue: 0, total: 0 });
 if (pmTasks.data.counts) lastPmCounts.current = pmTasks.data.counts;
 const pmCounts = lastPmCounts.current;
 useEffect(() => { if (tab === 'pm' && !pmTasks.loading && !pmTasks.error && pmTasks.updatedAt && pmTasks.data.page === pmPage) { const lastPage = Math.max(1, pmTasks.data.totalPages); if (pmPage > lastPage) setPmPage(lastPage); } }, [tab, pmTasks.loading, pmTasks.error, pmTasks.updatedAt, pmTasks.data.page, pmTasks.data.totalPages, pmPage]);
 const orders = records.data.orders, assigned = tasks.data.orders, loading = tab === 'pm' ? pmTasks.loading : (openRecords && records.loading) || tasks.loading;
 const reload = () => { records.reload(); tasks.reload(); pmTasks.reload(); setPerformanceRevision(value => value + 1); };
 function chooseTab(next: 'registration' | 'pm') { if (pmBusy) return; setTab(next); setLoadError(''); }
 function choosePmView(next: 'pending' | 'submitted' | 'completed') { setPmView(next); setPmPage(1); }
 async function edit(order: WorkOrder) {
  if (pmBusy) return;
  editRequest.current?.abort(); const controller = new AbortController(); editRequest.current = controller; setLoadError(''); setOpening(order.id);
  try {
   const fullOrder = await api<WorkOrder>('/orders/' + order.id, { signal: controller.signal });
   if (controller.signal.aborted) return;
   if (!fullOrder.canEdit) { setDetail(order.id); return; }
   setDetail('');
   if (fullOrder.pmWeeklyPlanId) { setPmEditing(fullOrder); setTab('pm'); requestAnimationFrame(() => pmFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }
   else { setEditing(fullOrder); setFormVersion(value => value + 1); setTab('registration'); requestAnimationFrame(() => formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }
  } catch (e) { if (!controller.signal.aborted) setLoadError(errorText(e)); }
  finally { if (!controller.signal.aborted) setOpening(''); }
 }
 return <>
  <div className="registration-card registration-connection"><div><span>کارتابل من</span><b>{context.user.name}</b></div><div><button className="button secondary small" disabled={loading} onClick={reload}><Icon name="refresh" size={16}/>تازه‌سازی</button></div></div>
  <OwnPerformance user={context.user} revision={performanceRevision}/>
  <div className="technician-tabs" role="tablist" aria-label="بخش‌های کارتابل تکنسین" onKeyDown={event => {
   if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || pmBusy) return;
   event.preventDefault(); const index = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : tab === 'registration' ? 1 : 0;
   chooseTab(index === 0 ? 'registration' : 'pm'); tabRefs.current[index]?.focus();
  }}>
   <button ref={element => { tabRefs.current[0] = element; }} id="technician-tab-registration" type="button" role="tab" aria-selected={tab === 'registration'} aria-controls="technician-panel-registration" tabIndex={tab === 'registration' ? 0 : -1} className={tab === 'registration' ? 'active' : ''} disabled={pmBusy} onClick={() => chooseTab('registration')}><Icon name="plus" size={21}/><span>ثبت کار</span></button>
   <button ref={element => { tabRefs.current[1] = element; }} id="technician-tab-pm" type="button" role="tab" aria-selected={tab === 'pm'} aria-controls="technician-panel-pm" tabIndex={tab === 'pm' ? 0 : -1} className={(tab === 'pm' ? 'active ' : '') + (pmCounts.overdue > 0 ? 'has-overdue' : '')} disabled={pmBusy} onClick={() => chooseTab('pm')}><Icon name={pmCounts.overdue > 0 ? 'warning' : 'plans'} size={21}/><span>برگه‌های PM</span>{pmCounts.pending > 0 && <b className="technician-tab-count" aria-label={num(pmCounts.pending, 0) + ' برگه برای انجام'}>{num(pmCounts.pending, 0)}</b>}</button>
  </div>
  {pmCounts.overdue > 0 && <div className="pm-overdue-alarm technician-overdue-alarm" role="alert"><Icon name="warning" size={22}/><div><b>{num(pmCounts.overdue, 0)} برگهٔ PM شما عقب افتاده است</b><p>مهلت این برگه‌ها گذشته؛ برگه‌ها همچنان در کارتابل شما هستند و باید انجام یا اصلاح شوند.</p></div>{!pmEditing && <button type="button" className="button secondary small" disabled={pmBusy} onClick={() => { chooseTab('pm'); choosePmView('pending'); setPmOverdue(true); }}>دیدن برگه‌های عقب‌افتاده</button>}</div>}
  <ErrorBox text={loadError}/>
  {/* Both panels remain mounted: changing tabs never clears an unsaved report or PM result. */}
  <section id="technician-panel-registration" role="tabpanel" aria-labelledby="technician-tab-registration" hidden={tab !== 'registration'} className="technician-tab-panel">
   <ErrorBox text={records.error || tasks.error} retry={reload}/>
   {(records.loading && !records.updatedAt || tasks.loading && !tasks.updatedAt) && <Loading/>}
   {!!assigned.length && <section className="registration-card"><h2 className="registration-section-title"><Icon name="orders" size={18}/>کارهای دیگر تخصیص‌یافته</h2>{assigned.map(order => <button className="registration-task" key={order.id} disabled={!!opening} onClick={() => void edit(order)}><b>{order.title}</b><span>{order.assetCode} — {order.assetName}</span><small>موعد: {dateFa(order.dueDate)} · {statuses[order.status]}</small></button>)}<PageControls page={assignedPage} total={tasks.data.total} change={setAssignedPage} busy={tasks.loading}/></section>}
   <div ref={formRef}><RegistrationForm key={(editing?.id || 'new') + ':' + formVersion} context={context} existing={editing} saved={reload} reset={reset}/></div>
   <section className="registration-card registration-records"><button type="button" className="registration-records-toggle" aria-expanded={openRecords} onClick={() => setOpenRecords(!openRecords)}><h2>گزارش‌های من و وضعیت تأیید</h2><span>{openRecords ? 'بستن فهرست −' : 'نمایش فهرست +'}</span></button>{openRecords && <>{tab === 'registration' && <Suspense fallback={<Loading/>}><EmDowntime context={context}/></Suspense>}<div className="registration-records-filter"><label>وضعیت<select value={status} onChange={e => setStatus(e.target.value)}><option value="">همه وضعیت‌ها</option>{Object.entries(statuses).map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label></div><DataTable pagination={false} data={orders} rowKey={order => order.id} onRow={order => setDetail(order.id)} empty={<p className="registration-empty">گزارشی با این وضعیت ثبت نشده است.</p>} columns={[{ title: 'کار / دستگاه', cell: order => <><b className="cell-title">{order.title}</b><small>{order.assetCode} — {order.assetName}</small></> }, { title: 'ماهیت', cell: order => <Badge type={order.type}/> }, { title: 'تاریخ', cell: order => dateFa(order.workDate) }, { title: 'وضعیت', cell: order => <Badge status={order.status}/> }]}/><PageControls page={recordPage} total={records.data.total} change={setRecordPage} busy={records.loading}/></>}</section>
   
  </section>
  <section id="technician-panel-pm" role="tabpanel" aria-labelledby="technician-tab-pm" hidden={tab !== 'pm'} className="technician-tab-panel">
   <div ref={pmFormRef}>{pmEditing && <PmExecution key={pmEditing.id} order={pmEditing} context={context} saved={reload} reset={resetPm} onBusy={setPmBusy}/>}</div>
   <section className="registration-card technician-pm-inbox" hidden={!!pmEditing}>
    <div className="technician-pm-heading"><div><h2>برگه‌های PM من</h2><p>برگه را باز کنید، نتیجهٔ بازدید و زمان کار را ثبت کنید و برای تأیید بفرستید.</p></div><span className="technician-pm-refresh"><i aria-hidden="true"/>دریافت خودکار برگه‌های تخصیص‌یافته</span></div>
    <div className="technician-pm-filters" aria-label="وضعیت برگه‌های من">{([{ value: 'pending', title: 'برای انجام', count: pmCounts.pending }, { value: 'submitted', title: 'ارسال‌شده', count: pmCounts.submitted }, { value: 'completed', title: 'تأییدشده / بسته‌شده', count: pmCounts.completed }] as const).map(item => <button type="button" key={item.value} className={pmView === item.value ? 'active' : ''} aria-pressed={pmView === item.value} disabled={pmBusy} onClick={() => choosePmView(item.value)}>{item.title}<span>{num(item.count, 0)}</span></button>)}</div>
    {pmView === 'pending' && (pmCounts.overdue > 0 || pmOverdue) && <label className="checkbox technician-pm-overdue-filter"><input type="checkbox" checked={pmOverdue} onChange={event => { setPmOverdue(event.target.checked); setPmPage(1); }}/>فقط برگه‌های عقب‌افتاده ({num(pmCounts.overdue, 0)})</label>}
    <ErrorBox text={pmTasks.error} retry={pmTasks.reload}/>
    {pmTasks.loading && !pmTasks.data.items.length ? <Loading/> : pmTasks.data.items.map(order => <TechnicianPmCard key={order.id} order={order} opening={opening === order.id} disabled={!!opening || pmBusy} open={() => void edit(order)}/>)}
    {!pmTasks.loading && !pmTasks.error && !pmTasks.data.items.length && <Empty title={pmView === 'pending' ? pmOverdue ? 'برگهٔ عقب‌افتاده‌ای ندارید' : 'برگه‌ای برای انجام ندارید' : pmView === 'submitted' ? 'هنوز برگه‌ای ارسال نکرده‌اید' : 'برگهٔ نهایی‌شده‌ای ندارید'} detail={pmView === 'pending' ? 'پس از تخصیص سرپرست، برگه به‌صورت خودکار در همین بخش نمایش داده می‌شود.' : pmView === 'submitted' ? 'برگه‌های ارسال‌شده و وضعیت تأیید آن‌ها اینجا دیده می‌شوند.' : 'برگه‌های تأیید یا لغوشده با سابقهٔ خود در این فهرست می‌مانند.'} icon="plans"/>}
    <PageControls page={pmPage} total={pmTasks.data.total} change={setPmPage} busy={pmTasks.loading || pmBusy}/>
   </section>
  </section>
  {detail && <OrderDetail id={detail} context={context} close={closeDetail} changed={reload} edit={order => void edit(order)}/>}
 </>;
}

interface TechnicianPmPage extends PageResult<WorkOrder> { counts?: { pending: number; submitted: number; completed: number; overdue: number; total: number }; }
const emptyPmPage: TechnicianPmPage = { items: [], total: 0, page: 1, pageSize: 25, totalPages: 0 };
function TechnicianPmCard({ order, open, opening, disabled }: { order: WorkOrder; open: () => void; opening: boolean; disabled: boolean }) {
 const late = pmIsOverdue(order);
 const editable = order.canEdit === true;
 return <article className={'technician-pm-card' + (late ? ' overdue' : '') + (order.status === 'returned' ? ' returned' : '')}>
  <div className="technician-pm-card-title"><span className="technician-pm-icon"><Icon name="plans" size={23}/></span><div><h3>{order.title}</h3><p>{order.hall}{order.pmSpecialty ? ' · ' + order.pmSpecialty : ''}{order.pmAssetCodes?.length ? ' · ' + order.pmAssetCodes.join('، ') : ' · ' + order.assetCode}</p></div></div>
  <div className="technician-pm-card-meta"><span>مهلت انجام: <b>{dateFa(order.pmWindow?.endDate || order.dueDate)}</b></span><span className="technician-pm-state">{pmStates[order.pmExecutionState || ''] || statuses[order.status]}</span></div>
  {order.status === 'returned' && <p className="technician-pm-card-hint">برگه برای اصلاح برگشته است؛ توضیح سرپرست را در جزئیات برگه ببینید.</p>}
  {late && <p className="technician-pm-card-hint">مهلت این برگه گذشته است؛ نتیجهٔ واقعی انجام کار را ثبت کنید.{order.pmOverdueDays !== undefined && order.pmOverdueDays > 0 && ' · ' + num(order.pmOverdueDays, 0) + ' روز تأخیر'}</p>}
  {order.pmLateExecution && <p className="technician-pm-card-hint">انجام این کار پس از مهلت ثبت شده است.</p>}
  <button className={'button ' + (editable ? 'primary' : 'secondary')} type="button" disabled={disabled} onClick={open}>{opening ? 'در حال باز کردن برگه…' : editable ? order.status === 'returned' ? 'اصلاح برگه' : 'باز کردن و انجام برگه' : 'مشاهدهٔ برگه و وضعیت تأیید'}<Icon name="arrow" size={17}/></button>
 </article>;
}
