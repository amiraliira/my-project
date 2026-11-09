import Time24 from './Time24';
import { useState } from 'react';
import { post } from './api';
import { CatalogSelect } from './Catalog';
import type { PageProps } from './Orders';
import type { OrderPart, Part, User, WorkOrder } from './types';
import { validQuantity } from './quantities';
import { SupervisorGradeInputs } from './Grading';
import { participantGradePayload } from './scoring';
import { ErrorBox, Field, Icon, Modal, duration, errorText, num } from './ui';

interface DraftPart { partId: string; code?: string; name?: string; unit?: string; quantity: string; }
export interface CorrectionDraft { startTime: string; endTime: string; minutes: Record<string, string>; parts: DraftPart[]; reason: string; grades: Record<string, string>; }

export function initialCorrection(order: WorkOrder): CorrectionDraft {
 return { startTime: order.startTime, endTime: order.endTime, minutes: Object.fromEntries(order.participants.map(person => [person.personId, String(person.minutes)])), parts: order.parts.map(part => ({ ...part, quantity: String(part.quantity) })), reason: '', grades: {} };
}

function quantity(value: string) {
 if (!/^(?:0|[1-9]\d{0,9})(?:\.\d{1,6})?$/.test(value.trim()) || !validQuantity(Number(value))) throw new Error('مقدار قطعه باید مثبت، حداکثر یک میلیارد و با حداکثر شش رقم اعشار باشد.');
 return value.trim();
}

export function correctionPayload(order: WorkOrder, user: User, draft: CorrectionDraft) {
 if (user.role !== 'supervisor' || !user.specialty || order.canReviewCorrect !== true) throw new Error('این گزارش در مرحلهٔ قابل اصلاح شما نیست.');
 const reason = draft.reason.trim();
 if (reason.length > 2000) throw new Error('توضیح حداکثر ۲۰۰۰ نویسه است.');
 if (![draft.startTime, draft.endTime].every(value => /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value))) throw new Error('زمان واقعی شروع و پایان را کامل کنید.');
 const total = duration(draft.startTime, draft.endTime);
 if (total <= 0) throw new Error('زمان شروع و پایان نباید یکسان باشد.');
 const participants = order.participants.filter(person => person.specialty === user.specialty).map(person => {
  const value = draft.minutes[person.personId] ?? '';
  if (!/^\d{1,4}$/.test(value) || Number(value) < 1 || Number(value) > total) throw new Error('زمان هر نفر باید دقیقهٔ صحیح و مثبت و حداکثر برابر بازهٔ کار باشد.');
  return { personId: person.personId, minutes: Number(value) };
 });
 if (order.participants.some(person => person.specialty !== user.specialty && person.minutes > total)) throw new Error('بازهٔ کار از زمان ثبت‌شدهٔ نفرات تخصص دیگر کوتاه‌تر است.');
 if (order.pmWeeklyPlanId && (participants.length !== 1 || participants[0].minutes !== total)) throw new Error('در برگهٔ PM، دقیقهٔ مجری باید با فاصلهٔ شروع و پایان برابر باشد.');
 if (draft.parts.length > 200 || new Set(draft.parts.map(part => part.partId)).size !== draft.parts.length) throw new Error('حداکثر ۲۰۰ قطعهٔ بدون تکرار انتخاب کنید.');
 const parts = draft.parts.map(part => { if (!part.partId) throw new Error('قطعهٔ معتبر انتخاب کنید.'); return { partId: part.partId, quantity: quantity(part.quantity) }; });
 const partsKey = (rows: { partId: string; quantity: number | string }[]) => JSON.stringify(rows.map(part => [part.partId, Number(part.quantity)]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))));
 const changed = draft.startTime !== order.startTime || draft.endTime !== order.endTime || participants.some(person => person.minutes !== order.participants.find(old => old.personId === person.personId)?.minutes) || partsKey(parts) !== partsKey(order.parts);
 if (changed && reason.length < 3) throw new Error('علت اصلاح زمان یا قطعات را بنویسید؛ حداقل سه نویسه.');
 const grades = participantGradePayload(order, draft.grades, 'supervisor');
 if (!grades?.grades) throw new Error('نمرهٔ جداگانهٔ همهٔ مجریان تخصص خودتان را از صفر تا سه کامل کنید.');
 return { version: order.version, reason, startTime: draft.startTime, endTime: draft.endTime, participants, parts, grades: grades.grades };
}

export function correctionChanges(order: WorkOrder, draft: CorrectionDraft) {
 const changes: { label: string; before: string; after: string }[] = [];
 if (draft.startTime !== order.startTime || draft.endTime !== order.endTime) changes.push({ label: 'بازهٔ انجام کار', before: order.startTime + ' — ' + order.endTime, after: draft.startTime + ' — ' + draft.endTime });
 for (const person of order.participants) if (Number(draft.minutes[person.personId]) !== person.minutes) changes.push({ label: person.personName || person.personId, before: num(person.minutes, 0) + ' دقیقه', after: num(draft.minutes[person.personId], 0) + ' دقیقه' });
 const label = (part: Pick<OrderPart, 'partId' | 'code' | 'name'>) => [part.code, part.name].filter(Boolean).join(' — ') || part.partId;
 for (const old of order.parts) {
  const next = draft.parts.find(part => part.partId === old.partId);
  if (!next || Number(next.quantity) !== Number(old.quantity)) changes.push({ label: label(old), before: num(old.quantity, 6) + ' ' + (old.unit || ''), after: next ? num(next.quantity, 6) + ' ' + (next.unit || '') : 'حذف از مصرف این گزارش' });
 }
 for (const part of draft.parts) if (!order.parts.some(old => old.partId === part.partId)) changes.push({ label: label(part), before: 'در مصرف گزارش نبود', after: num(part.quantity, 6) + ' ' + (part.unit || '') });
 return changes;
}

export default function ReviewCorrection({ order, context, close, saved }: { order: WorkOrder; context: PageProps; close: () => void; saved: () => void }) {
 const [draft, setDraft] = useState(() => initialCorrection(order)), [reviewed, setReviewed] = useState<ReturnType<typeof correctionPayload> | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
 const [selected, setSelected] = useState(''), [selectedPart, setSelectedPart] = useState<Part | null>(null), [newQuantity, setNewQuantity] = useState('1');
 const allowed = context.user.role === 'supervisor' && order.canReviewCorrect === true;
 function update(change: Partial<CorrectionDraft>) { setDraft(old => ({ ...old, ...change })); setReviewed(null); setError(''); }
 function time(value: string, end = false) {
  const change = { startTime: end ? draft.startTime : value, endTime: end ? value : draft.endTime };
  const total = duration(change.startTime, change.endTime);
  update(order.pmWeeklyPlanId && total > 0 ? { ...change, minutes: Object.fromEntries(order.participants.map(person => [person.personId, String(total)])) } : change);
 }
 function addPart() {
  try {
   const value = quantity(newQuantity);
   if (!selected || selectedPart?.id !== selected) throw new Error('قطعه را از فهرست انتخاب کنید.');
   if (draft.parts.length >= 200) throw new Error('حداکثر ۲۰۰ قطعه در یک گزارش ثبت می‌شود.');
   if (draft.parts.some(part => part.partId === selected)) throw new Error('این قطعه در فهرست هست؛ مقدار همان ردیف را اصلاح کنید.');
   update({ parts: [...draft.parts, { partId: selected, code: selectedPart.code, name: selectedPart.name, unit: selectedPart.unit, quantity: value }] });
   setSelected(''); setSelectedPart(null); setNewQuantity('1');
  } catch (cause) { setError(errorText(cause)); }
 }
 function preview() { try { setReviewed(correctionPayload(order, context.user, draft)); setError(''); } catch (cause) { setError(errorText(cause)); } }
 async function save() {
  if (!reviewed || busy || !allowed) return;
  setBusy(true); setError('');
  try {
   const result = await post<WorkOrder>('/orders/' + encodeURIComponent(order.id) + '/review-correction', reviewed);
   context.notify(result.status === 'head' ? 'اصلاحات و تأیید شما ثبت شد؛ گزارش برای رئیس ارسال شد.' : 'اصلاحات و تأیید شما ثبت شد؛ گزارش منتظر تأیید تخصص‌های باقی‌مانده است.'); saved();
  } catch (cause) { setError(errorText(cause) + ' اطلاعات واردشده حفظ شده است؛ در صورت تغییر هم‌زمان، گزارش را دوباره باز کنید.'); }
  finally { setBusy(false); }
 }
 const changes = reviewed ? correctionChanges(order, draft) : [];
 const workingOrder = { ...order, participants: order.participants.map(person => ({ ...person, minutes: Number(draft.minutes[person.personId]) || 0 })) };
 return <Modal title="اصلاح و تأیید گزارش" close={close} locked={busy} wide><div className="modal-body">
  <ErrorBox text={error}/>
  {!allowed ? <p>این گزارش در مرحلهٔ قابل اصلاح شما نیست.</p> : <>
   <p className="info-note">تأییدهای قبلیِ تخصص‌های دیگر حفظ می‌شوند.</p>
   <div className="form-grid"><Field label="شروع واقعی کار"><Time24 label="ساعت شروع" value={draft.startTime} disabled={busy} required onChange={value => time(value)}/></Field><Field label="پایان واقعی کار"><Time24 label="ساعت پایان" value={draft.endTime} disabled={busy} required onChange={value => time(value, true)}/></Field></div>
   <p className="form-hint">بازهٔ کار: {num(duration(draft.startTime, draft.endTime), 0)} دقیقه{draft.endTime < draft.startTime ? ' · پایان در روز بعد' : ''}. {order.pmWeeklyPlanId ? 'زمان مجری PM با این بازه برابر است.' : 'زمان مشارکت هر نفر جدا ثبت می‌شود.'}</p>
   <h3 className="section-title">مدت مشارکت نفرات</h3>
   {order.participants.map(person => <div className="form-row" key={person.personId}><span>{person.personName || person.personId}<small className="muted"> · {person.specialty}</small></span>{person.specialty === context.user.specialty ? <Field label={'دقیقهٔ مشارکت ' + (person.personName || person.personId)}><input type="number" min="1" max="1440" step="1" value={draft.minutes[person.personId]} disabled={busy} onChange={event => update({ minutes: { ...draft.minutes, [person.personId]: event.target.value } })}/></Field> : <span>{num(person.minutes, 0)} دقیقه · سرپرست همان تخصص اصلاح می‌کند</span>}</div>)}
   <h3 className="section-title">قطعات مصرفی همین گزارش</h3>
   {draft.parts.map(part => <div className="form-row" key={part.partId}><span>{part.code} — {part.name || part.partId}<small className="muted"> · {part.unit}</small></span><Field label={'مقدار مصرف ' + (part.name || part.partId)}><input inputMode="decimal" value={part.quantity} disabled={busy} onChange={event => update({ parts: draft.parts.map(line => line.partId === part.partId ? { ...line, quantity: event.target.value } : line) })}/></Field><button type="button" className="icon-button" aria-label={'حذف قطعه ' + (part.name || part.partId)} disabled={busy} onClick={() => update({ parts: draft.parts.filter(line => line.partId !== part.partId) })}><Icon name="close"/></button></div>)}
   {!draft.parts.length && <p className="muted">قطعه‌ای برای مصرف این گزارش ثبت نشده است.</p>}
   <div className="form-row"><CatalogSelect kind="parts" value={selected} onChange={value => { setSelected(value); setSelectedPart(null); }} onSelected={value => setSelectedPart(value as Part)} placeholder="جستجوی قطعه برای افزودن" disabled={busy}/><Field label="مقدار قطعهٔ جدید"><input inputMode="decimal" value={newQuantity} disabled={busy} onChange={event => setNewQuantity(event.target.value)}/></Field><button type="button" className="button secondary small" disabled={busy || selectedPart?.id !== selected || !selected} onClick={addPart}>افزودن قطعه</button></div>
   <h3 className="section-title">نمره و تأیید سرپرست</h3><SupervisorGradeInputs order={workingOrder} context={context} values={draft.grades} setValues={value => update({ grades: typeof value === 'function' ? value(draft.grades) : value })} busy={busy}/>
   <Field label="علت اصلاح"><textarea value={draft.reason} maxLength={2000} rows={2} disabled={busy} onChange={event => update({ reason: event.target.value })}/></Field>
   {reviewed && <section className="info-note" aria-label="پیش‌نمایش اصلاح و تأیید گزارش"><h3>آمادهٔ ثبت و تأیید</h3>{changes.length ? changes.map((change, index) => <p key={index}><b>{change.label}</b>: {change.before} ← {change.after}</p>) : <p>زمان و قطعات بدون تغییر تأیید می‌شوند.</p>}{reviewed.grades.map(grade => <p key={grade.personId}>نمرهٔ {order.participants.find(person => person.personId === grade.personId)?.personName || grade.personId}: {num(grade.coefficient, 3)}</p>)}{reviewed.reason && <p>توضیح: {reviewed.reason}</p>}<p>با ثبت، تأیید شما هم انجام می‌شود. مصرف قطعه پس از تأیید نهایی رئیس ثبت می‌شود.</p></section>}
  </>}
 </div><div className="modal-footer"><button type="button" className="button ghost" disabled={busy} onClick={close}>انصراف</button>{allowed && <><button type="button" className="button secondary" disabled={busy} onClick={preview}>بررسی و تأیید</button><button type="button" className="button primary" disabled={busy || !reviewed} onClick={() => void save()}>{busy ? 'در حال ثبت و تأیید…' : 'ثبت اصلاحات و تأیید'}</button></>}</div></Modal>;
}
