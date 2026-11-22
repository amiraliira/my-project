import type { Dispatch, SetStateAction } from 'react';
import type { PageProps } from './Orders';
import type { WorkOrder } from './types';
import { DataTable, Field, dateFa, num } from './ui';
import { approvalParticipants, headGrade, headGradeMaximum, isHeadOnly, normalizeCoefficient, supervisorGrade } from './scoring';
import { mayViewGrades } from './permissions';

export function ParticipantGrades({ order, context }: { order: WorkOrder; context: PageProps }) {
 if (!mayViewGrades(context.user.role)) return null;
 const chief = context.user.role === 'head';
 const participants = chief ? order.participants : order.participants.filter(person => person.specialty === context.user.specialty);
 if (!participants.length) return null;
 return <section className="participant-grades"><h3 className="section-title">{chief ? 'ارزیابی جداگانهٔ انجام‌دهندگان' : 'ارزیابی انجام‌دهندگان تخصص من'}</h3><DataTable data={participants} rowKey={person => person.personId} columns={[
  { title: 'انجام‌دهنده / تخصص', cell: person => <><b>{person.personName || context.boot.people.find(entry => entry.id === person.personId)?.name}</b><small>{person.specialty}</small></> },
  { title: 'زمان مشارکت', cell: person => num(person.minutes, 0) + ' دقیقه' },
  { title: 'نمره سرپرست (۰ تا ۳)', cell: person => { const outside = context.user.role === 'supervisor' && person.specialty !== context.user.specialty; const grade = supervisorGrade(order, person.personId); return isHeadOnly(person) ? <span className="muted">با رئیس · بدون نمره سرپرست</span> : outside ? <span className="muted">خارج از حوزه تخصص شما</span> : grade ? <><b className="management-score">{num(grade.coefficient, 12)}</b>{grade.source === 'legacy-specialty' && <small>ثبت قدیمی · ضریب تخصص</small>}</> : <span className="muted">در انتظار ارزیابی</span>; } },
  { title: 'سرپرست تأییدکننده', cell: person => { const grade = supervisorGrade(order, person.personId); return context.user.role === 'supervisor' && person.specialty !== context.user.specialty ? '—' : grade?.actorName || '—'; } },
  ...(chief ? [{ title: 'نمره رئیس', cell: (person: WorkOrder['participants'][number]) => { const grade = headGrade(order, person.personId); return grade ? <><b className="management-score">{num(grade.coefficient, 12)}</b>{grade.source === 'legacy-order' && <small>ثبت قدیمی · نمره مشترک گزارش</small>}</> : <span className="muted">در انتظار ارزیابی</span>; } },
  { title: 'امتیاز فعالیت فرد', cell: (person: WorkOrder['participants'][number]) => person.finalScore == null ? '—' : <b>{num(person.finalScore, 3)}</b> }] : []),
  { title: 'تاریخ ارزیابی سرپرست', cell: person => { const grade = supervisorGrade(order, person.personId); return context.user.role === 'supervisor' && person.specialty !== context.user.specialty ? '—' : grade?.createdAt ? dateFa(grade.createdAt, true) : '—'; } }
 ]}/>{chief && <p className="management-note">تکنسین: نمرهٔ سرپرست × نمرهٔ رئیس × ساعت فرد. کار سرپرست: نمرهٔ رئیس (۰ تا ۹) × ساعت فرد.</p>}</section>;
}

interface GradeInputProps {
 order: WorkOrder;
 context: PageProps;
 values: Record<string, string>;
 setValues: Dispatch<SetStateAction<Record<string, string>>>;
 busy: boolean;
}

function CoefficientInput({ value, maximum, fractionalLimit, busy, onChange }: {
 value: string; maximum: number; fractionalLimit?: number; busy: boolean; onChange: (value: string) => void;
}) {
 const invalid = !!value.trim() && normalizeCoefficient(value, maximum, fractionalLimit) === null;
 return <>
  <input type="text" inputMode="decimal" autoComplete="off" dir="ltr" maxLength={64}
   disabled={busy} required aria-invalid={invalid || undefined} value={value}
   placeholder={'0 – ' + maximum} onChange={event => onChange(event.target.value)}/>
  {invalid && <small className="grade-input-error">نمره‌ای بین صفر و {num(maximum, 0)} وارد کنید.</small>}
 </>;
}

export function SupervisorGradeInputs({ order, context, values, setValues, busy }: GradeInputProps) {
 const people = approvalParticipants(order, 'supervisor').filter(person => person.personId !== context.user.id);
 return <div className="individual-grade-inputs">
  <p className="management-note">برای هر نفر نمره‌ای از ۰ تا ۳ وارد کنید. صفر و اعشار، مثل ۱٫۲۵، پذیرفته می‌شوند.</p>
  {people.map(person => {
   const name = person.personName || context.boot.people.find(entry => entry.id === person.personId)?.name || person.personId;
   return <div className="individual-grade-row" key={person.personId}>
    <div><b>{name}</b><small>{person.specialty} · {num(person.minutes, 0)} دقیقه مشارکت</small></div>
    <Field label={'نمره سرپرست برای ' + name}>
     <CoefficientInput value={values[person.personId] ?? ''} maximum={3} busy={busy}
      onChange={value => setValues(current => ({ ...current, [person.personId]: value }))}/>
    </Field>
   </div>;
  })}
 </div>;
}

export function HeadGradeInputs({ order, context, values, setValues, busy }: GradeInputProps) {
 return <div className="individual-grade-inputs"><p className="management-note">نمرهٔ رئیس برای کار تکنسین از ۰ تا ۳ و برای کار خودِ سرپرست از ۰ تا ۹ است؛ صفر و اعشار مجازند.</p>{order.participants.map(person => {
  const name = person.personName || context.boot.people.find(entry => entry.id === person.personId)?.name || person.personId;
  const supervisor = supervisorGrade(order, person.personId), value = values[person.personId] ?? '', direct = isHeadOnly(person), maximum = headGradeMaximum(person);
  const coefficient = normalizeCoefficient(value, maximum, 28);
  const weight = direct ? 1 : supervisor?.coefficient;
  const score = coefficient === null || weight == null ? null : person.minutes / 60 * weight * Number(coefficient);
  return <div className="individual-grade-row" key={person.personId}>
   <div><b>{name}</b><small>{person.specialty} · {num(person.minutes, 0)} دقیقه مشارکت</small>
    <small>{direct ? 'کار سرپرست · ارزیابی مستقیم رئیس' : <>نمرهٔ سرپرست: <b>{supervisor == null ? 'ثبت نشده' : num(supervisor.coefficient, 12)}</b></>}</small>
   </div>
   <Field label={'نمره رئیس برای ' + name + (direct ? ' (۰ تا ۹)' : ' (۰ تا ۳)')}>
    <CoefficientInput value={value} maximum={maximum} fractionalLimit={28} busy={busy}
     onChange={next => setValues(current => ({ ...current, [person.personId]: next }))}/>
    {score != null && <small>امتیاز این فعالیت پس از تأیید: {num(score, 3)}</small>}
   </Field>
  </div>;
 })}</div>;
}
