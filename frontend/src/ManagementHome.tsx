import { lazy, Suspense, useCallback, useState } from 'react';
import { PageControls } from './Catalog';
import type { PageProps } from './Orders';
import type { Dashboard, InboxPage, OrderPage, PageResult, User, WorkOrder } from './types';
import Performance, { OwnPerformance } from './Performance';
import { Badge, DataTable, Empty, ErrorBox, Icon, Loading, SearchInput, dateFa, num, roles, statuses, todayIso, useDebounce, useResource, workTypes } from './ui';

const OrderDetail = lazy(() => import('./Orders').then(module => ({ default: module.OrderDetail })));
const OrderEditor = lazy(() => import('./Orders').then(module => ({ default: module.OrderEditor })));
const PmTaskList = lazy(() => import('./PmWork').then(module => ({ default: module.PmTaskList })));
const EmDowntime = lazy(() => import('./EmDowntime'));
const emptyInbox: InboxPage = { orders: [], total: 0, page: 1, pageSize: 25, totalPages: 0, counts: { total: 0, pm: 0, em: 0, project: 0, overdue: 0 }, stage: null, specialty: null };
interface PmSummary extends PageResult<WorkOrder> { counts?: { pending: number; submitted: number; completed: number; overdue: number; total: number }; }
const roleTitles: Record<string, string> = { supervisor: 'کارتابل سرپرست', planner: 'برنامه‌ریزی نگهداری', head: 'نمای مدیریت', manager: 'مدیریت فنی', admin: 'مدیریت سامانه' };
type Section = 'overview' | 'approvals' | 'pm' | 'history' | 'personnel' | 'repair';
const sectionTitles: Record<Section, string> = { overview: 'نمای مدیریت', approvals: 'کارتابل تأیید', pm: 'برگه‌های PM', history: 'گزارش‌های کار', personnel: 'عملکرد کارکنان', repair: 'تعمیرات EM' };

export default function ManagementHome({ navigate, ...context }: PageProps & { navigate: (id: string) => void }) {
 const role = context.user.role, isAdmin = role === 'admin', canDecide = role === 'head' || role === 'supervisor';
 const canRegister = ['supervisor', 'planner', 'head', 'manager'].includes(role);
 const [section, setSection] = useState<Section>('overview'), [inboxPage, setInboxPage] = useState(1), [workPage, setWorkPage] = useState(1);
 const [performanceRevision, setPerformanceRevision] = useState(0), [detail, setDetail] = useState('');
 const [editor, setEditor] = useState<WorkOrder | null>(null), [create, setCreate] = useState(false), [assign, setAssign] = useState(false);
 const [type, setType] = useState(''), [status, setStatus] = useState(''), [q, setQ] = useState(''), [pmOverdue, setPmOverdue] = useState(false);
 const search = useDebounce(q), today = todayIso();
 const inbox = useResource<InboxPage>('/inbox?' + new URLSearchParams({ paged: 'true', page: String(inboxPage), pageSize: section === 'approvals' ? '25' : '5', q: search, type }), emptyInbox, 30000, canDecide && (section === 'overview' || section === 'approvals'));
 const works = useResource<OrderPage>('/orders?' + new URLSearchParams({ paged: 'true', page: String(workPage), pageSize: '25', q: search, type, status }), { orders: [], total: 0, page: 1, pageSize: 25, totalPages: 0 }, 30000, section === 'history');
 const dashboard = useResource<Dashboard | null>('/dashboard', null, 30000, section === 'overview' || section === 'repair');
 const pm = useResource<PmSummary>('/pm/tasks?page=1&pageSize=1&view=pending&overdue=false', { items: [], total: 0, page: 1, pageSize: 1, totalPages: 0 }, 30000, section === 'overview' && !isAdmin);
 const assignedIds = [...new Set(works.data.orders.map(order => order.assigneeId).filter((id): id is string => !!id))].sort();
 const assignees = useResource<PageResult<User>>('/catalog/people?' + new URLSearchParams({ ids: assignedIds.join(','), pageSize: '100' }), { items: [], total: 0, page: 1, pageSize: 100, totalPages: 0 }, 0, section === 'history' && assignedIds.length > 0);
 const closeDetail = useCallback(() => setDetail(''), []);
 const closeEditor = useCallback(() => { setEditor(null); setCreate(false); setAssign(false); }, []);
 function openSection(next: Section, filter: { status?: string; overdue?: boolean } = {}) { setSection(next); setQ(''); setType(''); setStatus(filter.status || ''); setPmOverdue(!!filter.overdue); setInboxPage(1); setWorkPage(1); window.scrollTo({ top: 0, behavior: 'auto' }); }
 function reload() { inbox.reload(); works.reload(); dashboard.reload(); pm.reload(); assignees.reload(); setPerformanceRevision(value => value + 1); }
 const updating = inbox.refreshing || works.refreshing || dashboard.refreshing || pm.refreshing;
 const updatedAt = Math.max(inbox.updatedAt, works.updatedAt, dashboard.updatedAt, pm.updatedAt);
 const repair = dashboard.data?.repair || [];
 const modules = isAdmin ? [
  { title: 'کاربران و دسترسی‌ها', icon: 'users', note: 'حساب‌ها و نقش‌ها', action: () => navigate('users') },
  { title: 'انتقال اطلاعات', icon: 'imports', note: 'ورود و آرشیو', action: () => navigate('imports') },
  { title: 'رویدادها و ممیزی', icon: 'audit', note: 'سوابق تغییرات', action: () => navigate('audit') },
  { title: 'تنظیمات سامانه', icon: 'settings', note: 'تنظیمات و پشتیبان', action: () => navigate('settings') }
 ] : [
  { title: 'برگه‌ها و تقویم PM', icon: 'plans', note: 'برنامه و اجرا', action: () => openSection('pm') },
  { title: 'گزارش‌های کار', icon: 'orders', note: 'ثبت و پیگیری', action: () => openSection('history') },
  ...(['head', 'planner'].includes(role) ? [{ title: 'کارکرد خطوط و OEE', icon: 'reports', note: 'کارکرد و توقف فنی', action: () => navigate('oee') }] : []),
  { title: 'تجهیزات و شناسنامه', icon: 'assets', note: 'دستگاه‌ها و سوابق', action: () => navigate('assets') },
  { title: 'قطعات و مصرف', icon: 'stock', note: 'کاتالوگ و مصرف', action: () => navigate('stock') },
  { title: role === 'head' ? 'عملکرد کارکنان' : 'گزارش‌ها', icon: 'reports', note: 'فعالیت و عملکرد', action: () => role === 'head' ? openSection('personnel') : navigate('reports') }
 ];
 function metric(title: string, value: number | undefined, icon: string, next: Section, ready: boolean, filter: { status?: string; overdue?: boolean } = {}) {
  return <button type="button" className="overview-metric" onClick={() => openSection(next, filter)}><span><Icon name={icon} size={18}/>{title}</span><strong>{ready ? num(value, 0) : '—'}</strong><small>مشاهده<Icon name="arrow" size={13}/></small></button>;
 }
 const filters = <div className="management-filter-row"><SearchInput value={q} onChange={value => { setQ(value); setInboxPage(1); setWorkPage(1); }} placeholder="جستجوی گزارش"/><select aria-label="ماهیت کار" value={type} onChange={event => { setType(event.target.value); setInboxPage(1); setWorkPage(1); }}><option value="">همهٔ ماهیت‌ها</option>{Object.entries(workTypes).map(([id, label]) => <option value={id} key={id}>{id} · {label}</option>)}</select></div>;
 return <div className="management-workspace">
  <header className="workspace-heading"><div><span className="workspace-role">{roles[role]}{context.user.specialty ? ' · ' + context.user.specialty : ''}</span><h1>{section === 'overview' ? roleTitles[role] : sectionTitles[section]}</h1></div><div className="button-row">{canRegister && <><button type="button" className="button primary" onClick={() => { setCreate(true); setAssign(false); setEditor(null); }}><Icon name="plus" size={17}/>ثبت گزارش کار</button><button type="button" className="button secondary" onClick={() => { setCreate(false); setAssign(true); setEditor(null); }}><Icon name="plans" size={17}/>تخصیص کار PM</button></>}</div></header>
  <div className="workspace-status"><div>{section !== 'overview' && <button className="text-button" onClick={() => openSection('overview')}><Icon name="arrow" size={14}/>نمای مدیریت</button>}</div><span>{updatedAt ? 'به‌روزرسانی: ' + dateFa(new Date(updatedAt).toISOString(), true) : ''}</span><button type="button" className="icon-button" onClick={reload} disabled={updating} aria-label="تازه‌سازی"><Icon name="refresh" size={16}/></button></div>
  <ErrorBox text={inbox.error || works.error || dashboard.error || pm.error || assignees.error} retry={reload}/>
  {section === 'overview' && <>
   <div className="overview-metrics">{canDecide ? metric('در انتظار تأیید من', inbox.data.counts.total, 'orders', 'approvals', !!inbox.loadedPath) : metric('گزارش‌های کار', dashboard.data?.total, 'orders', 'history', !!dashboard.loadedPath)}{!isAdmin && <>{metric('PM در انتظار انجام', pm.data.counts?.pending, 'plans', 'pm', !!pm.loadedPath)}{metric('PM گذشته از موعد', pm.data.counts?.overdue, 'warning', 'pm', !!pm.loadedPath, { overdue: true })}</>}{metric('تکمیل نهایی', dashboard.data?.completed, 'check', 'history', !!dashboard.loadedPath, { status: 'completed' })}</div>
   {!isAdmin && <div className="overview-panels">
    <section className="panel overview-actions"><div className="panel-head"><h2>{canDecide ? 'در انتظار تأیید' : 'پیگیری کارها'}</h2><button className="text-button" onClick={() => openSection(canDecide ? 'approvals' : 'history')}>مشاهده همه<Icon name="arrow" size={14}/></button></div>{inbox.loading && canDecide ? <Loading/> : canDecide && inbox.data.orders.length ? inbox.data.orders.slice(0, 3).map(order => <button type="button" className="overview-task" key={order.id} onClick={() => setDetail(order.id)}><span className="overview-task-icon"><Icon name={order.type === 'PM' ? 'plans' : 'orders'} size={18}/></span><span><b>{order.title}</b><small>{order.hall} · {order.assetName}</small><small className="mono">{order.number}</small></span><Icon name="arrow" size={15}/></button>) : <Empty title={canDecide ? 'گزارشی در انتظار تأیید نیست' : 'گزارش‌های کار'} detail={canDecide ? '' : 'فهرست گزارش‌ها را باز کنید.'} icon="check" action={!canDecide && <button className="button secondary small" onClick={() => openSection('history')}>مشاهده گزارش‌ها</button>}/>}</section>
    <Suspense fallback={<Loading/>}><EmDowntime context={context}/></Suspense>
   </div>}
   <section className="overview-modules"><h2>بخش‌های سامانه</h2><div>{modules.map(module => <button type="button" key={module.title} onClick={module.action}><Icon name={module.icon} size={22}/><span><b>{module.title}</b><small>{module.note}</small></span><Icon name="arrow" size={14}/></button>)}</div></section>
   <OwnPerformance user={context.user} revision={performanceRevision}/>
  </>}
  {section === 'approvals' && canDecide && <section className="panel management-card">{filters}{inbox.loading ? <Loading/> : <DataTable pagination={false} data={inbox.data.orders} rowKey={order => order.id} onRow={order => setDetail(order.id)} empty={<Empty title="گزارشی در انتظار تأیید نیست" detail=""/>} columns={[{ title: 'گزارش', cell: order => <><b>{order.title}</b><small className="mono">{order.number}</small></> }, { title: 'دستگاه / سالن', cell: order => <>{order.assetName}<small>{order.hall}</small></> }, { title: 'ماهیت', cell: order => <Badge type={order.type}/> }, { title: 'زمان تعمیر', cell: order => num(order.durationMinutes, 0) + ' دقیقه' }, { title: 'تاریخ', cell: order => dateFa(order.workDate) }]}/>}<PageControls page={inboxPage} total={inbox.data.total} change={setInboxPage} busy={inbox.loading}/></section>}
  {section === 'pm' && <Suspense fallback={<Loading/>}><PmTaskList context={context} initialOverdue={pmOverdue}/></Suspense>}
  {section === 'personnel' && role === 'head' && <Performance user={context.user} revision={performanceRevision} initialOpen/>}
  {section === 'repair' && <section className="panel management-card"><p className="management-note">مدت تعمیر از زمان توقف تولید جداست.</p>{dashboard.loading ? <Loading/> : <DataTable data={repair} rowKey={asset => asset.id} empty={<Empty title="گزارش تعمیراتی ثبت نشده است" detail=""/>} columns={[{ title: 'کد تجهیز', cell: asset => <span className="mono">{asset.code}</span> }, { title: 'دستگاه', cell: asset => asset.name }, { title: 'مدت تعمیر EM', cell: asset => num(asset.minutes, 0) + ' دقیقه' }]}/>}</section>}
  {section === 'history' && <Suspense fallback={<Loading/>}><EmDowntime context={context}/></Suspense>}
  {section === 'history' && <section className="panel management-card">{filters}<div className="management-filter-row"><select value={status} onChange={event => { setStatus(event.target.value); setWorkPage(1); }} aria-label="وضعیت گزارش"><option value="">همهٔ وضعیت‌ها</option>{Object.entries(statuses).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></div>{works.loading ? <Loading/> : <DataTable pagination={false} data={works.data.orders} rowKey={order => order.id} onRow={order => setDetail(order.id)} empty={<Empty title="گزارشی یافت نشد" detail=""/>} columns={[{ title: 'گزارش / دستگاه', cell: order => <><b>{order.title}</b><small>{order.assetCode} · {order.assetName}</small><small className="mono">{order.number}</small></> }, { title: 'تاریخ / سالن', cell: order => <>{dateFa(order.workDate)}<small>{order.hall}</small></> }, { title: 'ماهیت', cell: order => <Badge type={order.type}/> }, { title: 'زمان تعمیر', cell: order => num(order.durationMinutes, 0) + ' دقیقه' }, { title: 'مجری', cell: order => assignees.data.items.find(person => person.id === order.assigneeId)?.name || order.assigneeName || '—' }, { title: 'وضعیت', cell: order => <><Badge status={order.status}/>{order.dueDate && order.dueDate < today && !['completed', 'cancelled'].includes(order.status) && <small>موعد گذشته</small>}</> }]}/>}<PageControls page={workPage} total={works.data.total} change={setWorkPage} busy={works.loading}/></section>}
  {detail && !editor && !create && !assign && <Suspense fallback={<Loading/>}><OrderDetail id={detail} context={context} close={closeDetail} changed={reload} edit={order => { setDetail(''); setEditor(order); }}/></Suspense>}
  {(editor || create || assign) && <Suspense fallback={<Loading/>}><OrderEditor existing={editor || undefined} context={context} close={closeEditor} saved={reload} defaultType={assign ? 'PM' : 'EM'} assignmentMode={assign} onSubmitted={order => { if (role === 'supervisor' && order.canDecide) setDetail(order.id); }}/></Suspense>}
 </div>;
}
export const managementRoleTitle = (user: User) => roleTitles[user.role] || roles[user.role];
