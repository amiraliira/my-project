import type { Dispatch, SetStateAction } from 'react';
import type { PageProps } from './Orders';
import type { WorkOrder } from './types';
import { DataTable, Field, dateFa, num } from './ui';
import { headGrade, headGradeMaximum, isHeadOnly, supervisorGrade, validCoefficient } from './scoring';
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

export function SupervisorGradeInputs({ order, context, values, setValues, busy }: { order: WorkOrder; context: PageProps; values: Record<string, string>; setValues: Dispatch<SetStateAction<Record<string, string>>>; busy: boolean }) {
 const people = order.participants.filter(person => !!order.decisionSpecialty && person.specialty === order.decisionSpecialty && !isHeadOnly(person) && person.personId !== context.user.id);
 return <div className="individual-grade-inputs"><p className="management-note">فقط برای انجام‌دهندگانی که به نمرهٔ سرپرست نیاز دارند، نمره جداگانه وارد کنید. کار خود سرپرستان را رئیس ارزیابی می‌کند. مقدار صفر و اعداد اعشاری مانند <span dir="ltr">0.5</span> و <span dir="ltr">1.25</span> مجازند؛ بازه از ۰ تا ۳ است.</p>{people.map(person => { const name = person.personName || context.boot.people.find(entry => entry.id === person.personId)?.name || person.personId; return <div className="individual-grade-row" key={person.personId}><div><b>{name}</b><small>{person.specialty} · {num(person.minutes, 0)} دقیقه مشارکت</small></div><Field label={'نمره سرپرست برای ' + name}><input type="number" min="0" max="3" step="any" inputMode="decimal" dir="ltr" disabled={busy} required value={values[person.personId] ?? ''} placeholder="0 – 3" onChange={event => { const value = event.target.value; setValues(current => ({ ...current, [person.personId]: value })); }}/></Field></div>; })}</div>;
}

export function HeadGradeInputs({ order, context, values, setValues, busy }: { order: WorkOrder; context: PageProps; values: Record<string, string>; setValues: Dispatch<SetStateAction<Record<string, string>>>; busy: boolean }) {
 return <div className="individual-grade-inputs"><p className="management-note">نمرهٔ رئیس برای کار تکنسین از ۰ تا ۳ و برای کار خودِ سرپرست از ۰ تا ۹ است؛ صفر و اعشار مجازند.</p>{order.participants.map(person => {
  const name = person.personName || context.boot.people.find(entry => entry.id === person.personId)?.name || person.personId;
  const supervisor = supervisorGrade(order, person.personId), value = values[person.personId] ?? '', direct = isHeadOnly(person), maximum = headGradeMaximum(person);
  const score = direct ? person.minutes / 60 * Number(value) : supervisor ? person.minutes / 60 * supervisor.coefficient * Number(value) : null;
  return <div className="individual-grade-row" key={person.personId}><div><b>{name}</b><small>{person.specialty} · {num(person.minutes, 0)} دقیقه مشارکت</small><small>{direct ? 'کار سرپرست · ارزیابی مستقیم رئیس' : <>نمرهٔ سرپرست: <b>{supervisor == null ? 'ثبت نشده' : num(supervisor.coefficient, 12)}</b></>}</small></div><Field label={'نمره رئیس برای ' + name + (direct ? ' (۰ تا ۹)' : ' (۰ تا ۳)')}><input type="number" min="0" max={maximum} step="any" inputMode="decimal" dir="ltr" disabled={busy} required value={value} placeholder={'0 – ' + maximum} onChange={event => { const next = event.target.value; setValues(current => ({ ...current, [person.personId]: next })); }}/>{score != null && validCoefficient(value, maximum) && <small>امتیاز این فعالیت پس از تأیید: {num(score, 3)}</small>}</Field></div>;
 })}</div>;
}
