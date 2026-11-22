import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { PageControls } from './Catalog';
import { JalaliDate } from './JalaliDate';
import { defaultEmDowntimePeriod, emDowntimePeriodError } from './EmDowntime';
import type { PageProps } from './Orders';
import type { WorkOrder } from './types';
import { Badge, Empty, ErrorBox, Field, Icon, Loading, Modal, dateFa, num, todayIso, useResource, workTypes } from './ui';
import './activity-report.css';

const OrderDetail = lazy(() => import('./Orders').then(module => ({ default: module.OrderDetail })));
export type ActivityMetric = 'activityCount' | 'workMinutes' | 'personMinutes' | 'downtimeMinutes';
export type ActivityGrouping = 'hall' | 'line';
export interface ActivityTotals { activityCount: number; workMinutes: number; personMinutes: number; downtimeMinutes: number; unknownDowntimeCount: number; stoppedActivityCount: number; }
export interface ActivityGroup extends ActivityTotals { hall: string; line: string; unclassified?: boolean; }
export interface ActivityFilter { from: string; to: string; type: string; groupBy: ActivityGrouping; metric: ActivityMetric; }
interface ActivitySummary { totalGroups: number; totalOrders: number; totals: ActivityTotals; items: ActivityGroup[]; typeCounts: Record<string, number>; page: number; total: number; totalPages: number; }
interface ActivityOrders { orders: WorkOrder[]; totals: ActivityTotals; page: number; total: number; totalPages: number; }
const zero: ActivityTotals = { activityCount: 0, workMinutes: 0, personMinutes: 0, downtimeMinutes: 0, unknownDowntimeCount: 0, stoppedActivityCount: 0 };
const emptySummary: ActivitySummary = { totalGroups: 0, totalOrders: 0, totals: zero, items: [], typeCounts: {}, page: 1, total: 0, totalPages: 0 };
const emptyOrders: ActivityOrders = { orders: [], totals: zero, page: 1, total: 0, totalPages: 0 };
export const activityMetrics: Record<ActivityMetric, { title: string; unit: string }> = {
 activityCount: { title: 'تعداد فعالیت', unit: 'فعالیت' }, workMinutes: { title: 'مدت انجام کار', unit: 'دقیقه' },
 personMinutes: { title: 'زمان مشارکت افراد', unit: 'نفر‌دقیقه' }, downtimeMinutes: { title: 'توقف تولید', unit: 'دقیقه' }
};
export const mayViewActivities = (role: string) => ['head', 'manager', 'planner', 'supervisor', 'technician'].includes(role);
export function activityReportPath(filter: ActivityFilter, page = 1, group?: Pick<ActivityGroup, 'hall' | 'line'>) {
 const query = new URLSearchParams({ from: filter.from, to: filter.to, type: filter.type || 'ALL', groupBy: filter.groupBy, metric: filter.metric, page: String(page), pageSize: group ? '25' : '10' });
 if (group) { query.set('hall', group.hall); if (filter.groupBy === 'line') query.set('line', group.line); }
 return '/reports/activities' + (group ? '/orders' : '') + '?' + query;
}
export function activityGroupTitle(group: Pick<ActivityGroup, 'hall' | 'line'>, grouping: ActivityGrouping) { return grouping === 'hall' ? group.hall || 'سالن تعیین نشده' : group.line || 'خط تعیین نشده'; }

export function activityMeasure(group: ActivityTotals, metric: ActivityMetric): number | null {
 if (metric === 'downtimeMinutes' && group.stoppedActivityCount > 0 &&
     group.unknownDowntimeCount === group.stoppedActivityCount) return null;
 return group[metric];
}

function ActivityBars({ groups, filter, select }: { groups: ActivityGroup[]; filter: ActivityFilter; select: (group: ActivityGroup) => void }) {
 const { groupBy, metric } = filter;
 const unit = activityMetrics[metric].unit;
 const maximum = Math.max(1, ...groups.map(group => activityMeasure(group, metric) ?? 0));
 return groups.map(group => {
  const title = activityGroupTitle(group, groupBy);
  const value = activityMeasure(group, metric);
  const description = value === null ? 'مدت توقف نامشخص' : num(value, 0) + ' ' + unit;
  const hall = groupBy === 'line' ? (group.hall || 'سالن تعیین نشده') + ' · ' : '';
  return <button className="activity-bar" type="button" key={JSON.stringify([group.hall, group.line])}
   onClick={() => select(group)} aria-label={hall + title + '، ' + description + '؛ مشاهده فعالیت‌ها'}>
   <span><b>{title}</b><small>{hall}{num(group.activityCount, 0)} فعالیت</small></span>
   <span className="activity-bar-track" aria-hidden="true"><i style={{ width: (value ?? 0) / maximum * 100 + '%' }}/></span>
   <strong>{value === null ? 'نامشخص' : <>{num(value, 0)}<small>{unit}</small></>}</strong>
   <Icon name="arrow" size={14}/>
  </button>;
 });
}
function useAvailablePage(page: number, change: (value: number) => void, path: string, resource: { loading: boolean; error: string; loadedPath: string; data: { page: number; totalPages: number } }) {
 const { loading, error, loadedPath, data: { page: responsePage, totalPages } } = resource;
 useEffect(() => {
  if (loading || error || loadedPath !== path || responsePage !== page || !Number.isSafeInteger(totalPages) || totalPages < 0) return;
  const last = Math.max(1, totalPages); if (page > last) change(last);
 }, [page, change, path, loading, error, loadedPath, responsePage, totalPages]);
}

export default function ActivityReport({ context, compact = false, initialOpen = true, revision = 0 }: { context: PageProps; compact?: boolean; initialOpen?: boolean; revision?: number }) {
 const [filter, setFilter] = useState<ActivityFilter>(() => ({ ...defaultEmDowntimePeriod(todayIso()), type: 'ALL', groupBy: 'hall', metric: 'activityCount' }));
 const [from, setFrom] = useState(filter.from), [to, setTo] = useState(filter.to), [open, setOpen] = useState(initialOpen);
 const [page, setPage] = useState(1), [selected, setSelected] = useState<ActivityGroup | null>(null), [error, setError] = useState('');
 const permitted = mayViewActivities(context.user.role), path = activityReportPath(filter, page);
 const summary = useResource<ActivitySummary>(path, emptySummary, 0, permitted && open);
 const previousRevision = useRef(revision);
 const reload = summary.reload;
 useEffect(() => {
  if (previousRevision.current === revision) return;
  previousRevision.current = revision;
  if (permitted && open) reload();
 }, [revision, permitted, open, reload]);
 useAvailablePage(page, setPage, path, summary);
 if (!permitted) return null;
 const current = summary.loadedPath === path, data = current ? summary.data : emptySummary;
 const measure = activityMetrics[filter.metric];
 function change(next: Partial<ActivityFilter>) { setSelected(null); setPage(1); setFilter(value => ({ ...value, ...next })); }
 function apply(event: FormEvent) {
  event.preventDefault(); const message = emDowntimePeriodError(from, to); setError(message); if (message) return;
  if (filter.from === from && filter.to === to && page === 1) summary.reload(); else change({ from, to });
 }
 return <section className={'panel activity-report' + (compact ? ' compact' : '')} aria-label="گزارش فعالیت‌ها">
  <div className="activity-report-heading"><button type="button" className="activity-report-toggle" aria-expanded={open} onClick={() => { setOpen(!open); setSelected(null); }}><Icon name="reports" size={18}/><h2>گزارش فعالیت‌ها</h2><Icon name="arrow" size={14}/></button><button type="button" className="icon-button" aria-label="تازه‌سازی گزارش فعالیت‌ها" disabled={!open || summary.loading || summary.refreshing} onClick={summary.reload}><Icon name="refresh" size={16}/></button></div>
  {open && <>
   <div className="activity-report-controls"><Field label="ماهیت کار"><select value={filter.type} onChange={event => change({ type: event.target.value })}><option value="ALL">مجموع فعالیت‌ها</option>{Object.entries(workTypes).map(([type, title]) => <option key={type} value={type}>{title}</option>)}</select></Field><Field label="تفکیک"><select value={filter.groupBy} onChange={event => change({ groupBy: event.target.value as ActivityGrouping })}><option value="hall">سالن</option><option value="line">خط</option></select></Field><Field label="شاخص"><select value={filter.metric} onChange={event => change({ metric: event.target.value as ActivityMetric })}>{Object.entries(activityMetrics).map(([value, item]) => <option key={value} value={value}>{item.title}</option>)}</select></Field></div>
   <details className="activity-period"><summary><Icon name="plans" size={14}/>{dateFa(filter.from)} تا {dateFa(filter.to)}</summary><form onSubmit={apply}><Field label="از تاریخ"><JalaliDate value={from} onChange={setFrom}/></Field><Field label="تا تاریخ"><JalaliDate value={to} onChange={setTo}/></Field><button type="submit" className="button secondary small">اعمال بازه</button></form></details>
   <ErrorBox text={error || summary.error} retry={summary.reload}/>
   <div className="activity-report-totals"><span><b>{current ? num(data.totalOrders, 0) : '—'}</b> فعالیت نهایی</span><span><b>{current ? num(data.totals.workMinutes, 0) : '—'}</b> دقیقه کار</span><span><b>{current ? num(data.totalGroups, 0) : '—'}</b> {filter.groupBy === 'hall' ? 'سالن' : 'خط'}</span></div>
   {filter.metric === 'downtimeMinutes' && data.totals.unknownDowntimeCount > 0 && <p className="activity-report-note">مدت توقف {num(data.totals.unknownDowntimeCount, 0)} فعالیت مشخص نیست؛ جمع فقط زمان‌های ثبت‌شده را شامل می‌شود.</p>}
   <div className="activity-chart" aria-label={measure.title + ' به تفکیک ' + (filter.groupBy === 'hall' ? 'سالن' : 'خط')} aria-busy={summary.loading || summary.refreshing}>
    {!current && !summary.error ? <Loading/> : data.items.length ?
     <ActivityBars groups={data.items} filter={filter} select={setSelected}/> :
     !summary.error && <Empty title="فعالیت نهایی در این بازه ثبت نشده است" detail="" icon="reports"/>}
   </div>
   {data.totalPages > 1 && <PageControls page={page} pageSize={10} total={data.total} busy={summary.loading} change={value => { setSelected(null); setPage(value); }}/>}
   <details className="activity-report-basis"><summary>مبنای گزارش</summary><p>گزارش‌های تأیید نهایی‌شده در حوزهٔ دسترسی شما نمایش داده می‌شوند. مدت انجام کار، زمان مشارکت افراد و توقف تولید شاخص‌های مستقل‌اند؛ توقف تولید، جمع زمان‌های ثبت‌شده است و هم‌پوشانی توقف‌ها از آن کسر نمی‌شود. تفکیک سالن و خط از محل فعلی تجهیز تعیین می‌شود.</p></details>
  </>}
  {open && selected && <GroupActivities key={JSON.stringify([filter, selected.hall, selected.line])} filter={filter} group={selected} context={context} close={() => setSelected(null)}/>}
 </section>;
}

export function GroupActivities({ filter, group, context, close }: { filter: ActivityFilter; group: ActivityGroup; context: PageProps; close: () => void }) {
 const [page, setPage] = useState(1), [detail, setDetail] = useState('');
 const path = activityReportPath(filter, page, group), resource = useResource<ActivityOrders>(path, emptyOrders);
 useAvailablePage(page, setPage, path, resource);
 const current = resource.loadedPath === path, data = current ? resource.data : emptyOrders;
 return <><Modal title={'فعالیت‌ها · ' + activityGroupTitle(group, filter.groupBy)} wide close={close}><div className="modal-body activity-group-details">
  <div className="activity-context">{filter.type !== 'ALL' && <Badge type={filter.type}/>}<span>{filter.type === 'ALL' ? 'مجموع فعالیت‌ها · ' : ''}{filter.groupBy === 'line' ? (group.hall || 'سالن تعیین نشده') + ' · ' : ''}{dateFa(filter.from)} تا {dateFa(filter.to)}</span></div>
  <ErrorBox text={resource.error} retry={resource.reload}/><div className="activity-report-totals"><span><b>{current ? num(data.totals.activityCount, 0) : '—'}</b> فعالیت</span><span><b>{current ? num(data.totals.workMinutes, 0) : '—'}</b> دقیقه کار</span><span><b>{current ? num(data.totals.personMinutes, 0) : '—'}</b> نفر‌دقیقه</span></div>
  {!current && !resource.error ? <Loading/> : data.orders.map(order => <article className="activity-record" key={order.id}><div className="activity-record-heading"><div><h3>{order.title}</h3><p>{order.type === 'GENERAL' ? order.hall : order.assetName + (order.hall ? ' · ' + order.hall : '')}</p><small>{dateFa(order.workDate)} · <bdi>{order.startTime}–{order.endTime}</bdi></small></div><Badge type={order.type}/></div><p className="activity-description">{order.description || 'شرح فعالیت ثبت نشده است.'}</p><div className="activity-record-time"><span>مدت کار: <b>{num(order.durationMinutes, 0)} دقیقه</b></span>{order.productionStopped && <span>توقف تولید: <b>{order.reportedDowntimeMinutes == null ? 'مشخص نشده' : num(order.reportedDowntimeMinutes, 0) + ' دقیقه'}</b></span>}</div><div className="activity-people">{order.participants.map(person => <span key={person.personId}>{person.personName || 'نام ثبت نشده'}<small>{num(person.minutes, 0)} دقیقه</small></span>)}</div><details className="activity-materials"><summary>قطعات مصرفی · {num(order.parts.length, 0)} ردیف</summary>{order.parts.length ? <ul>{order.parts.map(part => <li key={part.partId}><span>{part.name || 'قطعه ثبت‌شده'}{part.code && <small> · <bdi>{part.code}</bdi></small>}</span><b>{num(part.quantity, 6)} {part.unit}</b></li>)}</ul> : <p>قطعه‌ای مصرف نشده است.</p>}</details><button type="button" className="text-button" onClick={() => setDetail(order.id)}>مشاهده گزارش<Icon name="arrow" size={14}/></button></article>)}
  {current && !data.orders.length && !resource.error && <Empty title="فعالیتی در این بازه وجود ندارد" detail=""/>}
  {data.totalPages > 1 && <PageControls page={page} pageSize={25} total={data.total} busy={resource.loading} change={value => { setDetail(''); setPage(value); }}/>}
 </div><div className="modal-footer"><button type="button" className="button ghost" onClick={close}>بستن</button></div></Modal>{detail && <Suspense fallback={<Loading/>}><OrderDetail id={detail} context={context} close={() => setDetail('')} changed={resource.reload} edit={() => {}}/></Suspense>}</>;
}
