import { useEffect, useRef, useState } from 'react';
import { PageControls } from './Catalog';
import { Badge, Empty, ErrorBox, Icon, Loading, Modal, dateFa, num, useResource } from './ui';
import './activity-report.css';

type PerformancePeriod = { kind: string; year: number | null; month: number | null; label: string };
interface HallTotal {
 hall: string;
 minutes: number;
 activityCount: number;
}
interface PersonnelActivityRow {
 orderId: string;
 workDate: string;
 type: string;
 title: string;
 description: string;
 assetName: string;
 line: string;
 minutes: number;
 participants: { personId: string; personName: string; minutes: number }[];
 parts: { partId: string; name: string; code: string; quantity: number; unit: string }[];
}
interface ReportPage<T> { items: T[]; total: number; minutes: number; activityCount: number }
const hallSize = 12, activitySize = 10;
const emptyHalls: ReportPage<HallTotal> = { items: [], total: 0, minutes: 0, activityCount: 0 };
const emptyActivities: ReportPage<PersonnelActivityRow> = { ...emptyHalls, items: [] };

export function personnelActivityPath(personId: string, period: PerformancePeriod, page: number, hall?: string) {
 const query = new URLSearchParams({
  personId, period: period.kind, page: String(page), pageSize: String(hall === undefined ? hallSize : activitySize),
 });
 if (period.kind === 'monthly') {
  if (period.year != null) query.set('year', String(period.year));
  if (period.month != null) query.set('month', String(period.month));
 }
 if (hall !== undefined) query.set('hall', hall);
 return '/performance/halls' + (hall === undefined ? '' : '/activities') + '?' + query.toString();
}

const displayHall = (hall: string) => hall || 'سالن تعیین نشده';

export default function PersonnelActivity({ person, period, close }: {
 person: { personId: string; name: string }; period: PerformancePeriod; close: () => void;
}) {
 const [hall, setHall] = useState<string | undefined>(), [hallPage, setHallPage] = useState(1);
 const [activityPage, setActivityPage] = useState(1);
 const heading = useRef<HTMLHeadingElement>(null), back = useRef<HTMLButtonElement>(null);
 const returned = useRef(false);
 const hallPath = personnelActivityPath(person.personId, period, hallPage);
 const activitiesPath = hall === undefined ? '' : personnelActivityPath(person.personId, period, activityPage, hall);
 const halls = useResource(hallPath, emptyHalls);
 const activities = useResource(activitiesPath, emptyActivities, 0, hall !== undefined);
 const hallsReady = halls.loadedPath === hallPath;
 const activitiesReady = activities.loadedPath === activitiesPath && hall !== undefined;
 const chartRows = hallsReady ? halls.data.items : [];
 const orders = activitiesReady ? activities.data.items : [];
 const maximum = Math.max(1, ...chartRows.map(row => row.minutes));
 const totals = hall === undefined ? (hallsReady ? halls.data : null) : (activitiesReady ? activities.data : null);
 useEffect(() => {
  if (hall !== undefined) back.current?.focus();
  else if (returned.current) heading.current?.focus();
 }, [hall]);
 function chart() { returned.current = true; setHall(undefined); }
 return <Modal title={'سالن‌های فعالیت · ' + person.name} wide close={close}>
  <div className="modal-body personnel-activity">
   <div className="personnel-activity-context">
    <div><Icon name="plans" size={17}/><span>{period.label}</span></div>
    <button type="button" className="button ghost small"
     disabled={hall === undefined ? halls.loading : activities.loading}
     onClick={hall === undefined ? halls.reload : activities.reload}>
     <Icon name="refresh" size={15}/>تازه‌سازی
    </button>
   </div>
   {totals && <div className="activity-report-totals">
    <span><b>{num(totals.activityCount, 0)}</b> فعالیت نهایی</span>
    <span><b>{num(totals.minutes, 0)}</b> دقیقه مشارکت</span>
    {hall === undefined && <span><b>{num(totals.total, 0)}</b> سالن</span>}
   </div>}
   {hall === undefined ? <>
    <h3 ref={heading} tabIndex={-1}>زمان مشارکت به تفکیک سالن</h3>
    <ErrorBox text={halls.error} retry={halls.reload}/>
    {!hallsReady && !halls.error && <Loading/>}
    {hallsReady && !chartRows.length && !halls.error && <Empty title="فعالیتی در این بازه ثبت نشده است" icon="reports"/>}
    <div className="personnel-halls">
     {chartRows.map(row => <button key={row.hall} type="button" className="activity-bar"
      aria-label={displayHall(row.hall) + '، ' + num(row.minutes, 0) + ' دقیقه؛ مشاهده فعالیت‌ها'}
      onClick={() => { setActivityPage(1); setHall(row.hall); }}>
      <span><b>{displayHall(row.hall)}</b><small>{num(row.activityCount, 0)} فعالیت</small></span>
      <span className="activity-bar-track" aria-hidden="true"><i style={{ width: row.minutes / maximum * 100 + '%' }}/></span>
      <strong>{num(row.minutes, 0)}<small>دقیقه</small></strong>
      <Icon name="arrow" size={15}/>
     </button>)}
    </div>
    {hallsReady && halls.data.total > hallSize && <PageControls page={hallPage} total={halls.data.total}
     pageSize={hallSize} busy={halls.loading} change={setHallPage}/>}
   </> : <>
    <div className="personnel-hall-heading">
     <h3>فعالیت‌ها · {displayHall(hall)}</h3>
     <button ref={back} type="button" className="button secondary small" onClick={chart}>بازگشت به سالن‌ها</button>
    </div>
    <ErrorBox text={activities.error} retry={activities.reload}/>
    {!activitiesReady && !activities.error && <Loading/>}
    {activitiesReady && !orders.length && !activities.error && <Empty title="فعالیتی در این سالن ثبت نشده است" icon="reports"/>}
    {orders.map(order => <article key={order.orderId} className="activity-record">
     <div className="activity-record-heading">
      <div>
       <h3>{order.title || 'فعالیت بدون عنوان'}</h3>
       {order.type !== 'GENERAL' && <p>{[order.assetName, order.line].filter(Boolean).join(' · ')}</p>}
       <small>{dateFa(order.workDate)}</small>
      </div>
      <Badge type={order.type}/>
     </div>
     {order.description && <p className="activity-description">{order.description}</p>}
     <div className="activity-record-time"><span>مشارکت {person.name} <b>{num(order.minutes, 0)} دقیقه</b></span></div>
     {order.participants.length > 0 && <details className="activity-materials">
      <summary>نفرات · {num(order.participants.length, 0)} نفر</summary>
      <ul>{order.participants.map(peer => <li key={peer.personId}>
       <span>{peer.personName}</span><b>{num(peer.minutes, 0)} دقیقه</b>
      </li>)}</ul>
     </details>}
     {order.parts.length > 0 && <details className="activity-materials">
      <summary>قطعات مصرفی · {num(order.parts.length, 0)} ردیف</summary>
      <ul>{order.parts.map(part => <li key={part.partId}>
       <span>{part.name}{part.code && <small> · <bdi>{part.code}</bdi></small>}</span>
       <b>{num(part.quantity, 6)} {part.unit}</b>
      </li>)}</ul>
     </details>}
    </article>)}
    {activitiesReady && activities.data.total > activitySize && <PageControls page={activityPage}
     total={activities.data.total} pageSize={activitySize} busy={activities.loading} change={setActivityPage}/>}
   </>}
  </div>
  <div className="modal-footer"><button type="button" className="button ghost" onClick={close}>بستن</button></div>
 </Modal>;
}
