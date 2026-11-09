import { useEffect, useMemo, useRef, useState } from 'react';
import type { User } from './types';
import { DataTable, Empty, ErrorBox, Field, Icon, Loading, dateFa, num, todayIso, useResource, workTypes } from './ui';

interface PerformancePerson { personId: string; name: string; code: string; specialty: string; active: boolean; minutes: number; hours: number; activityCount: number; score: number; pm: number; em: number; project: number; }
interface PerformanceDetail { orderId: string; number: string; workDate: string; type: string; title: string; personId: string; personName: string; specialty: string; minutes: number; hours: number; supervisorCoefficient: number | null; headCoefficient: number | null; score: number | null; source: string | null; }
interface PerformanceReport { period: { kind: string; year: number | null; month: number | null; startDate: string | null; endDate: string | null; label: string }; scope: 'own' | 'specialty' | 'all'; formula: string; years: number[]; people: PerformancePerson[]; totals: { peopleCount: number; minutes: number; hours: number; activityCount: number; score: number; unscoredCount?: number }; details: PerformanceDetail[]; detailPage: number; detailPageSize: number; detailTotal: number; }
const months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
const currentDate = () => {
 const parts = new Intl.DateTimeFormat('en-US-u-ca-persian', { timeZone: 'UTC', year: 'numeric', month: 'numeric' }).formatToParts(new Date(todayIso() + 'T12:00:00Z'));
 return { year: Number(parts.find(part => part.type === 'year')?.value), month: Number(parts.find(part => part.type === 'month')?.value) };
};

export default function Performance({ user, revision = 0, initialOpen = false }: { user: User; revision?: number; initialOpen?: boolean }) {
 if (user.role !== 'head') return null;
 return <PerformanceView key={'all:'+user.id+':'+user.role} user={user} revision={revision} own={false} initialOpen={initialOpen}/>;
}

export function OwnPerformance({ user, revision = 0 }: { user: User; revision?: number }) {
 return <PerformanceView key={'own:'+user.id+':'+user.role} user={user} revision={revision} own/>;
}

function PerformanceView({ user, revision = 0, own, initialOpen = false }: { user: User; revision?: number; own: boolean; initialOpen?: boolean }) {
 const current = currentDate();
 const [open, setOpen] = useState(initialOpen), [period, setPeriod] = useState('monthly'), [year, setYear] = useState(current.year), [month, setMonth] = useState(current.month), [personId, setPersonId] = useState(''), [detailPage, setDetailPage] = useState(1);
 const active = own || open, detailsId = 'performance-details-'+(own?'own-':'all-')+user.id;
 const query = new URLSearchParams({ scope: own ? 'own' : 'all', period, detailPage: String(detailPage), detailPageSize: '20' });
 if (period === 'monthly') { query.set('year', String(year)); query.set('month', String(month)); }
 if (!own && personId) query.set('personId', personId);
 const path = '/performance?' + query.toString();
 const resource = useResource<PerformanceReport | null>(path, null, active ? 30000 : 0, active);
 const { loading, refreshing, error, reload } = resource;
 const data = resource.loadedPath === path ? resource.data : null;
 const previousRevision = useRef(revision);
 useEffect(() => { const changed = previousRevision.current !== revision; previousRevision.current = revision; if (changed && active) reload(); }, [revision, active, reload]);
 const title = own ? 'عملکرد من' : 'عملکرد کارکنان فنی';
 const years = [...new Set([current.year, year, ...(data?.years || [])])].sort((a, b) => b - a);
 const people = useMemo(() => [...(data?.people || [])].sort((a, b) => b.score - a.score || b.minutes - a.minutes || a.name.localeCompare(b.name, 'fa')), [data?.people]);
 const ranks = useMemo(() => new Map(people.map((person, index) => [person.personId, index + 1])), [people]);
 const pages = Math.ceil((data?.detailTotal || 0) / (data?.detailPageSize || 20));
 function resetDetails() { setDetailPage(1); setPersonId(''); }
 return <section className={(user.role === 'technician' ? 'registration-card performance-card' : 'panel management-card performance-card') + (own ? ' own-performance' : '') + (open ? ' is-open' : '')} aria-label={title}>
  <button type="button" className="performance-toggle" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(!open)}><h2><Icon name="reports" size={19}/>{title}</h2><span>{data ? num(data.totals.score, 2) + ' امتیاز · ' : ''}{own ? open ? 'بستن ریز فعالیت‌ها −' : 'ریز فعالیت‌ها +' : open ? 'بستن −' : 'مشاهده جزئیات +'}</span></button>
  <div className="performance-quick-period">{period === 'all' ? 'کل دوره' : months[month - 1] + ' ' + num(year, 0).replace(/٬/g, '')}<span>کارهای نهایی‌شده</span></div>
  {active && <>
   {(open || !own) && <div className="performance-controls">
    <Field label="بازه عملکرد"><select value={period} onChange={event => { setPeriod(event.target.value); resetDetails(); }}><option value="monthly">ماهانه</option><option value="all">کل دوره</option></select></Field>
    {period === 'monthly' && <><Field label="ماه عملکرد"><select value={month} onChange={event => { setMonth(Number(event.target.value)); resetDetails(); }}>{months.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select></Field><Field label="سال عملکرد"><select value={year} onChange={event => { setYear(Number(event.target.value)); resetDetails(); }}>{years.map(value => <option key={value} value={value}>{num(value, 0).replace(/٬/g, '')}</option>)}</select></Field></>}
    <button type="button" className="button secondary small" disabled={loading || refreshing} onClick={reload}><Icon name="refresh" size={16}/>تازه‌سازی</button>
   </div>}
   <ErrorBox text={error} retry={reload}/>{loading && !data && <Loading/>}
   {data && <div className={loading ? 'performance-content updating' : 'performance-content'} aria-busy={loading || refreshing}>
    {open && <details className="performance-help"><summary>مبنای امتیاز</summary><div className="performance-formula"><b>{own ? 'مبنای امتیاز فعالیت من' : 'امتیاز هر فعالیت برای هر نفر'}</b><span>کار تکنسین: نمره سرپرست × نمره رئیس × ساعت مشارکت؛ کار سرپرست: نمره رئیس (۰ تا ۹) × ساعت مشارکت</span></div>
    <p className="performance-note">مجموع این امتیازها عملکرد دوره را نشان می‌دهد. ساعت مشارکت از دقیقه ثبت‌شده هر فرد محاسبه می‌شود؛ نمره صفر معتبر است و کارهای در انتظار تأیید وارد امتیاز نهایی نمی‌شوند. محاسبه با دقیقه دقیق ÷ ۶۰ انجام می‌شود؛ نمایش ساعت و امتیاز گرد شده است.</p></details>}
    {!!data.totals.unscoredCount && <p className="performance-legacy">{num(data.totals.unscoredCount, 0)} مشارکت نهایی سابقه نمره کامل ندارد؛ این فعالیت‌ها در ساعت و تعداد دیده می‌شوند و امتیاز محاسبه‌شده ندارند.</p>}
    <div className="performance-summary"><div><b>{num(data.totals.score, 2)}</b><span>{own ? 'امتیاز نهایی من' : 'مجموع امتیاز نفرات'}</span></div><div><b>{num(data.totals.hours, 2)}</b><span>{own ? 'ساعت تأییدشده من' : 'نفرساعت تأییدشده'}</span></div><div><b>{num(data.totals.activityCount, 0)}</b><span>{own ? 'فعالیت نهایی من' : 'فعالیت نهایی'}</span></div>{!own && <div><b>{num(data.totals.peopleCount, 0)}</b><span>نفر در حوزه دسترسی</span></div>}</div>
    {!own && <div className="performance-personnel"><DataTable key={period + '-' + year + '-' + month} data={people} rowKey={person => person.personId} onRow={person => { setPersonId(person.personId); setDetailPage(1); }} empty={<Empty title="فردی در حوزه دسترسی شما نیست" icon="users"/>} columns={[{ title: 'رتبه / نام', cell: person => <><b>{num(ranks.get(person.personId), 0)} · {person.name}</b><small className="mono">{person.code}</small></> }, { title: 'تخصص', cell: person => person.specialty }, { title: 'کار نهایی', cell: person => num(person.activityCount, 0) }, { title: 'ساعت', cell: person => num(person.hours, 2) }, { title: 'امتیاز عملکرد', cell: person => <b className="performance-score">{num(person.score, 2)}</b> }]}/></div>}
    <div id={detailsId}>{(!own||open)&&<><div className="performance-details-heading"><h3>{own ? 'ریز فعالیت‌های من' : 'ریز عملکرد نفرات'}</h3>{!own && <Field label="انجام‌دهنده"><select value={personId} onChange={event => { setPersonId(event.target.value); setDetailPage(1); }}><option value="">همه نفرات مجاز</option>{people.map(person => <option key={person.personId} value={person.personId}>{person.name}</option>)}</select></Field>}</div>
    {data.details.length ? <div className="performance-detail-list">{data.details.map(entry => <article className="performance-detail" key={entry.orderId + ':' + entry.personId}><div className="performance-detail-title"><div><b>{entry.title}</b><small>{entry.number} · {dateFa(entry.workDate)} · {workTypes[entry.type] || entry.type}</small></div>{!own && <span>{entry.personName}<small>{entry.specialty}</small></span>}</div><div className={'performance-calculation' + (entry.source === 'head-only' ? ' head-only' : '')}>{entry.source !== 'head-only' && <><div><span>سرپرست</span><b>{entry.supervisorCoefficient == null ? '—' : num(entry.supervisorCoefficient, 3)}</b></div><i>×</i></>}<div><span>رئیس</span><b>{entry.headCoefficient == null ? '—' : num(entry.headCoefficient, 3)}</b></div><i>×</i><div><span>ساعت فرد</span><b>{num(entry.hours, 2)}</b><small>{num(entry.minutes, 0)} دقیقه</small></div><i>=</i><div className="result"><span>امتیاز</span><b>{entry.score == null ? '—' : num(entry.score, 2)}</b></div></div>{entry.source === 'head-only' && <small className="performance-legacy">کار سرپرست · نمرهٔ مستقیم رئیس (۰ تا ۹) × ساعت مشارکت</small>}{entry.source === 'legacy-specialty' && <small className="performance-legacy">سابقه پیشین · نمره تخصصی ثبت‌شده در نسخه قبل</small>}{entry.score == null && <small className="performance-legacy">سابقه نمره کامل نیست؛ امتیاز محاسبه نشده است.</small>}</article>)}</div> : <div className="performance-empty">در این بازه فعالیت تأییدشده‌ای {personId ? 'برای این فرد ' : ''}ثبت نشده است.</div>}
    <div className="performance-pagination"><span>{num(data.detailTotal, 0)} مشارکت نهایی{pages > 1 ? ' · صفحه ' + num(data.detailPage, 0) + ' از ' + num(pages, 0) : ''}</span>{pages > 1 && <div><button type="button" className="button secondary small" disabled={loading || detailPage <= 1} onClick={() => setDetailPage(value => value - 1)}>قبلی</button><button type="button" className="button secondary small" disabled={loading || detailPage >= pages} onClick={() => setDetailPage(value => value + 1)}>بعدی</button></div>}</div></>}</div>
   </div>}
  </>}
  {!own && !open && error && <ErrorBox text={error} retry={reload}/>} 
 </section>;
}
