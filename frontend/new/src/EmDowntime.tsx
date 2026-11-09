import { lazy, Suspense, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { PageControls } from './Catalog';
import { JalaliDate } from './JalaliDate';
import type { PageProps } from './Orders';
import type { WorkOrder } from './types';
import { Badge, Empty, ErrorBox, Field, Icon, Loading, Modal, dateFa, num, todayIso, useResource } from './ui';
import './em-downtime.css';

const OrderDetail = lazy(() => import('./Orders').then(module => ({ default: module.OrderDetail })));
export interface EmDowntimeTotals { downtimeMinutes: number; repairMinutes: number; personMinutes: number; unknownDowntimeCount: number; }
export interface EmDowntimeLine extends EmDowntimeTotals { hall: string; line: string; unclassified: boolean; activityCount: number; }
interface EmDowntimeSummary {
 from: string; to: string; metric: string; status: string; totalLines: number; totalOrders: number;
 totals: EmDowntimeTotals; items: EmDowntimeLine[]; page: number; pageSize: number; total: number; totalPages: number;
}
interface EmDowntimeOrders { orders: WorkOrder[]; totals: EmDowntimeTotals; page: number; pageSize: number; total: number; totalPages: number; }
interface Period { from: string; to: string; }
const zeroTotals: EmDowntimeTotals = { downtimeMinutes: 0, repairMinutes: 0, personMinutes: 0, unknownDowntimeCount: 0 };
const emptySummary: EmDowntimeSummary = { from: '', to: '', metric: 'reported-em-downtime-v1', status: 'completed', totalLines: 0, totalOrders: 0, totals: zeroTotals, items: [], page: 1, pageSize: 10, total: 0, totalPages: 0 };
const emptyOrders: EmDowntimeOrders = { orders: [], totals: zeroTotals, page: 1, pageSize: 25, total: 0, totalPages: 0 };
export const mayViewEmDowntime = (role: string) => ['head', 'manager', 'planner', 'supervisor', 'technician'].includes(role);
function dayNumber(value: string) {
 if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
 const instant = new Date(value + 'T00:00:00Z');
 return Number.isFinite(instant.getTime()) && instant.toISOString().slice(0, 10) === value ? instant.getTime() / 86400000 : null;
}
export function emDowntimePeriodError(from: string, to: string) {
 const first = dayNumber(from), last = dayNumber(to);
 if (first === null || last === null) return 'تاریخ شروع و پایان را کامل کنید.';
 if (last < first) return 'تاریخ پایان نباید پیش از تاریخ شروع باشد.';
 return last - first + 1 > 366 ? 'بازهٔ گزارش حداکثر ۳۶۶ روز است.' : '';
}
export function defaultEmDowntimePeriod(today: string): Period {
 const last = dayNumber(today);
 if (last === null) throw new Error('A valid current date is required');
 return { from: new Date((last - 29) * 86400000).toISOString().slice(0, 10), to: today };
}
export function emDowntimePath(period: Period, page = 1, group?: Pick<EmDowntimeLine, 'hall' | 'line'>) {
 const query = new URLSearchParams({ from: period.from, to: period.to, page: String(page), pageSize: group ? '25' : '10' });
 if (group) { query.set('hall', group.hall); query.set('line', group.line); }
 return '/reports/em-downtime' + (group ? '/orders' : '') + '?' + query;
}
export function emLineTitle(line: Pick<EmDowntimeLine, 'line'>) { return line.line || 'خط تعیین نشده'; }
export function knownEmDowntime(value: unknown): value is number { return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 1440; }
export function reportedEmDowntime(order: WorkOrder & { reportedDowntimeMinutes?: number | null }): number | null {
 const value = Object.prototype.hasOwnProperty.call(order, 'reportedDowntimeMinutes') ? order.reportedDowntimeMinutes : order.downtimeMinutes;
 return knownEmDowntime(value) ? value : null;
}
function useAvailablePage(page: number, change: (page: number) => void, path: string, resource: { loading: boolean; error: string; loadedPath: string; data: { page: number; totalPages: number } }) {
 const { loading, error, loadedPath, data: { page: responsePage, totalPages } } = resource;
 useEffect(() => {
  if (loading || error || loadedPath !== path || responsePage !== page || !Number.isSafeInteger(totalPages) || totalPages < 0) return;
  const last = Math.max(1, totalPages);
  if (page > last) change(last);
 }, [page, change, path, loading, error, loadedPath, responsePage, totalPages]);
}

export default function EmDowntime({ context }: { context: PageProps }) {
 const [period, setPeriod] = useState<Period>(() => defaultEmDowntimePeriod(todayIso()));
 const [from, setFrom] = useState(period.from), [to, setTo] = useState(period.to), [page, setPage] = useState(1);
 const [selected, setSelected] = useState<EmDowntimeLine | null>(null), [error, setError] = useState('');
 const permitted = mayViewEmDowntime(context.user.role);
 const path = emDowntimePath(period, page);
 const summary = useResource<EmDowntimeSummary>(path, emptySummary, 30000, permitted);
 useAvailablePage(page, setPage, path, summary);
 if (!permitted) return null;
 function apply(event: FormEvent) {
  event.preventDefault(); const message = emDowntimePeriodError(from, to); setError(message); if (message) return;
  setSelected(null); setPage(1);
  if (period.from === from && period.to === to && page === 1) summary.reload();
  else setPeriod({ from, to });
 }
 const maximum = Math.max(1, ...summary.data.items.map(line => line.downtimeMinutes));
 return <section className="panel em-downtime" aria-label="توقف ثبت‌شدهٔ EM">
  <div className="panel-head"><div><h2>توقف ثبت‌شدهٔ EM</h2><span className="em-period-label">{dateFa(period.from)} تا {dateFa(period.to)}</span></div><button type="button" className="icon-button" aria-label="تازه‌سازی نمودار توقفات" disabled={summary.loading || summary.refreshing} onClick={summary.reload}><Icon name="refresh" size={17}/></button></div>
  <form className="em-downtime-filters" onSubmit={apply}><Field label="از تاریخ"><JalaliDate value={from} onChange={setFrom}/></Field><Field label="تا تاریخ"><JalaliDate value={to} onChange={setTo}/></Field><button className="button secondary small" type="submit">نمایش گزارش</button></form>
  <ErrorBox text={error || summary.error} retry={summary.reload}/>
  <div className="em-downtime-totals" aria-label="جمع گزارش توقفات"><div><span>توقف ثبت‌شده</span><strong>{!summary.loadedPath || summary.data.totalOrders > 0 && summary.data.totals.unknownDowntimeCount === summary.data.totalOrders ? '—' : num(summary.data.totals.downtimeMinutes, 0)}<small>دقیقه</small></strong></div><div><span>گزارش نهایی</span><strong>{summary.loadedPath ? num(summary.data.totalOrders, 0) : '—'}<small>فعالیت</small></strong></div><div><span>خط و سالن</span><strong>{summary.loadedPath ? num(summary.data.totalLines, 0) : '—'}<small>گروه</small></strong></div></div>
  {summary.data.totals.unknownDowntimeCount > 0 && <p className="em-downtime-unknown" role="status">مدت توقف {num(summary.data.totals.unknownDowntimeCount, 0)} گزارش مشخص نشده است؛ جمع نمودار فقط مدت‌های مشخص را شامل می‌شود.</p>}
  {summary.loading && !summary.loadedPath ? <Loading/> : summary.data.items.length ? <div className="em-downtime-chart" aria-label="نمودار توقفات به تفکیک خط و سالن">{summary.data.items.map(line => <button type="button" className="em-downtime-bar" key={JSON.stringify([line.hall, line.line])} onClick={() => setSelected(line)} aria-label={emLineTitle(line) + '، ' + (line.hall || 'سالن تعیین نشده') + '، ' + (line.activityCount > 0 && line.unknownDowntimeCount === line.activityCount ? 'مدت توقف نامشخص' : num(line.downtimeMinutes, 0) + ' دقیقه توقف') + '؛ مشاهده فعالیت‌ها'}><span className="em-line-label"><b>{emLineTitle(line)}</b><small>{line.hall || 'سالن تعیین نشده'} · {num(line.activityCount, 0)} فعالیت{line.unknownDowntimeCount > 0 ? ' · ' + num(line.unknownDowntimeCount, 0) + ' مدت نامشخص' : ''}</small></span><span className="em-bar-track" aria-hidden="true"><i style={{ width: line.downtimeMinutes / maximum * 100 + '%' }}/></span><strong>{line.activityCount > 0 && line.unknownDowntimeCount === line.activityCount ? 'نامشخص' : <>{num(line.downtimeMinutes, 0)}<small>دقیقه</small></>}</strong><Icon name="arrow" size={15}/></button>)}</div> : !summary.error && <Empty title="توقفی در این بازه ثبت نشده است" detail="فقط گزارش‌های نهاییِ رفع خرابی با توقف تولید نمایش داده می‌شوند." icon="reports"/>}
  <PageControls page={page} pageSize={10} total={summary.data.total} busy={summary.loading} change={next => { setSelected(null); setPage(next); }}/>
  <details className="em-downtime-guide"><summary>مبنای گزارش</summary><p>این نمودار، جمع مدت توقف تولیدِ ثبت‌شده در گزارش‌های نهایی EM را نشان می‌دهد. مدت تعمیر و زمان مشارکت افراد، شاخص‌های جداگانه‌اند. چون زمان دقیق شروع و پایان هر توقف جداگانه ثبت نشده است، هم‌پوشانی توقف‌ها از این جمع حذف نمی‌شود. گروه هر گزارش از سالن و خط فعلیِ تجهیز تعیین می‌شود.</p></details>
  {selected && <LineActivities key={JSON.stringify([period.from, period.to, selected.hall, selected.line])} group={selected} period={period} context={context} close={() => setSelected(null)}/>}
 </section>;
}

function LineActivities({ group, period, context, close }: { group: EmDowntimeLine; period: Period; context: PageProps; close: () => void }) {
 const [page, setPage] = useState(1), [detail, setDetail] = useState('');
 const path = emDowntimePath(period, page, group);
 const activities = useResource<EmDowntimeOrders>(path, emptyOrders, 30000);
 useAvailablePage(page, setPage, path, activities);
 return <><Modal title={'فعالیت‌های EM · ' + emLineTitle(group)} wide close={close}><div className="modal-body em-line-activities">
  <p className="em-period-label">{group.hall || 'سالن تعیین نشده'} · {dateFa(period.from)} تا {dateFa(period.to)}</p>
  <ErrorBox text={activities.error} retry={activities.reload}/>
  <div className="em-downtime-totals em-activity-totals"><div><span>توقف ثبت‌شده</span><strong>{!activities.loadedPath || activities.data.total > 0 && activities.data.totals.unknownDowntimeCount === activities.data.total ? '—' : num(activities.data.totals.downtimeMinutes, 0)}<small>دقیقه</small></strong></div><div><span>زمان تعمیر</span><strong>{activities.loadedPath ? num(activities.data.totals.repairMinutes, 0) : '—'}<small>دقیقه</small></strong></div><div><span>زمان افراد</span><strong>{activities.loadedPath ? num(activities.data.totals.personMinutes, 0) : '—'}<small>نفر‌دقیقه</small></strong></div></div>
  {activities.data.totals.unknownDowntimeCount > 0 && <p className="em-downtime-unknown">مدت توقف {num(activities.data.totals.unknownDowntimeCount, 0)} گزارش مشخص نشده است.</p>}
  {activities.loading && !activities.loadedPath ? <Loading/> : activities.data.orders.map(order => <article className="em-activity" key={order.id}><div className="em-activity-heading"><div><h3>{order.title}</h3><p>{order.assetCode} · {order.assetName}</p><small>{dateFa(order.workDate)} · <bdi>{order.startTime}–{order.endTime}</bdi> · <bdi>{order.number}</bdi></small></div><Badge status={order.status}/></div>
   <p className="em-activity-description">{order.description || 'شرح فعالیت ثبت نشده است.'}</p><div className="em-activity-measures"><span>توقف: <b>{reportedEmDowntime(order) === null ? 'مدت نامشخص' : num(reportedEmDowntime(order), 0) + ' دقیقه'}</b></span><span>تعمیر: <b>{num(order.durationMinutes, 0)} دقیقه</b></span></div>
   <div className="em-activity-people"><h4>انجام‌دهندگان</h4>{order.participants.length ? order.participants.map(person => <span key={person.personId}>{person.personName || 'نام ثبت نشده'}<small>{num(person.minutes, 0)} دقیقه</small></span>) : <p className="muted">انجام‌دهنده ثبت نشده است.</p>}</div>
   <details className="em-activity-materials"><summary>قطعات مصرفی · {num(order.parts.length, 0)} ردیف</summary>{order.parts.length ? <ul>{order.parts.map(part => <li key={part.partId}><span><bdi>{part.code}</bdi> · {part.name || 'قطعه ثبت‌شده'}</span><strong>{num(part.quantity, 6)} {part.unit}</strong></li>)}</ul> : <p className="muted">قطعه‌ای مصرف نشده است.</p>}</details>
   <button type="button" className="button secondary small" onClick={() => setDetail(order.id)}>مشاهده گزارش</button>
  </article>)}
  {!activities.loading && !activities.error && !activities.data.orders.length && <Empty title="فعالیتی در این بازه وجود ندارد" detail=""/>}
  <PageControls page={page} pageSize={25} total={activities.data.total} busy={activities.loading} change={next => { setDetail(''); setPage(next); }}/>
 </div><div className="modal-footer"><button type="button" className="button ghost" onClick={close}>بستن</button></div></Modal>
 {detail && <Suspense fallback={<Loading/>}><OrderDetail id={detail} context={context} close={() => setDetail('')} changed={activities.reload} edit={() => {}}/></Suspense>}
 </>;
}
