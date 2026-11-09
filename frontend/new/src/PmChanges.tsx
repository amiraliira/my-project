import { useEffect, useState } from 'react';
import { post } from './api';
import { PageControls, emptyPage } from './Catalog';
import type { PageProps } from './Orders';
import type { Asset, PageResult, User } from './types';
import { SheetTables, SourceCellText } from './PmLibrary';
import type { PmTemplate } from './PmLibrary';
import { isChief } from './permissions';
import { pmChangeNames, pmChangeStatuses } from './pmGovernance';
import { pmMonths } from './pmSchedule';
import type { PmChange } from './pmGovernance';
import { DataTable, Empty, ErrorBox, Field, Loading, Modal, dateFa, errorText, num, useResource } from './ui';
import './pm-governance.css';

export default function PmChanges({ context, revision, changed }: { context: PageProps; revision: number; changed: () => void }) {
 const [status, setStatus] = useState('pending'), [page, setPage] = useState(1), [selected, setSelected] = useState('');
 const chief = isChief(context.user.role);
 const resource = useResource<PageResult<PmChange>>('/pm/changes?' + new URLSearchParams({ status, page: String(page), pageSize: '25' }), emptyPage());
 useEffect(() => { if (revision) resource.reload(); }, [revision, resource.reload]);
 return <section className="pm-changes" aria-label={chief ? 'درخواست‌های تغییر برای تأیید رئیس' : 'درخواست‌های تغییر من'}><div className="pm-forms-intro"><div><h2>{chief ? 'درخواست‌های در انتظار تصمیم شما' : 'درخواست‌های تغییر من'}</h2><p>افزودن، اصلاح یا حذف برگه و تغییر برنامه پس از تأیید رئیس اعمال می‌شود؛ تا آن زمان برگهٔ منتشرشده حفظ می‌شود.</p></div><select aria-label="وضعیت درخواست تغییر" value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}><option value="pending">در انتظار تأیید</option><option value="approved">تأییدشده</option><option value="rejected">ردشده</option><option value="">همهٔ درخواست‌ها</option></select></div><ErrorBox text={resource.error} retry={resource.reload}/>{resource.loading ? <Loading/> : <DataTable pagination={false} data={resource.data.items} rowKey={item => item.id} onRow={item => setSelected(item.id)} empty={<Empty title="درخواستی با این وضعیت وجود ندارد" detail={chief ? 'درخواست همکاران پس از ثبت در این بخش قرار می‌گیرد.' : 'در برگه یا برنامه، تغییر موردنیاز را ثبت کنید تا برای رئیس ارسال شود.'} icon="plans"/>} columns={[{ title: 'نوع درخواست / برگه', cell: item => <><b>{pmChangeNames[item.operation] || item.operation}</b><small>{String(item.payload?.title || item.result?.title || 'مشاهدهٔ جزئیات برگه')}</small></> }, ...(chief ? [{ title: 'درخواست‌کننده', cell: (item: PmChange) => item.requesterName }] : []), { title: 'وضعیت', cell: item => <span className={'pm-change-status ' + item.status}>{pmChangeStatuses[item.status]}</span> }, { title: 'زمان ثبت', cell: item => dateFa(item.createdAt, true) }, { title: 'عملیات', cell: item => <button className="button secondary small" onClick={() => setSelected(item.id)}>{chief && item.status === 'pending' ? 'بررسی و تصمیم' : 'مشاهدهٔ درخواست'}</button> }]}/>}<PageControls page={page} total={resource.data.total} change={setPage} busy={resource.loading}/>{selected && <ChangeDetail id={selected} context={context} close={() => setSelected('')} changed={() => { resource.reload(); changed(); }}/>}</section>;
}

function ChangeDetail({ id, context, close, changed }: { id: string; context: PageProps; close: () => void; changed: () => void }) {
 const resource = useResource<PmChange | null>('/pm/changes/' + encodeURIComponent(id), null), request = resource.data;
 const [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
 const original = request?.before && Array.isArray(request.before.tables) ? request.before as unknown as PmTemplate : null;
 const chief = isChief(context.user.role), payload = request?.payload || {};
 const activityChanges = Array.isArray(payload.changes) ? payload.changes as { cellId: string; text: string }[] : [];
 const activities = Array.isArray(payload.activities) ? payload.activities as { id?: string; text: string }[] : [];
 const currentCells = new Map(original?.tables.flatMap(table => table.rows.flatMap(row => row.cells.map(cell => [cell.id, cell.text] as const))) || []);
 const act = async (decision: 'approve' | 'reject') => { if (!request || busy || reason.trim().length < 3) return; setBusy(true); setError(''); try { await post('/pm/changes/' + encodeURIComponent(id) + '/decision', { version: request.version, decision, reason: reason.trim() }); context.notify(decision === 'approve' ? 'درخواست تأیید و تغییرات اعمال شد.' : 'درخواست رد شد؛ برگهٔ منتشرشده حفظ شد.'); changed(); close(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } };
 return <Modal title="بررسی درخواست تغییر PM" close={close} wide locked={busy}><div className="modal-body pm-change-review"><ErrorBox text={resource.error} retry={resource.reload}/><ErrorBox text={error}/>{resource.loading ? <Loading/> : request && <><div className="pm-detail-head"><div><h3>{pmChangeNames[request.operation] || request.operation}</h3><small>{request.requesterName} · {dateFa(request.createdAt, true)}</small></div><span className={'pm-change-status ' + request.status}>{pmChangeStatuses[request.status]}</span></div><p>{String(payload.reason || request.reason || 'علتی ثبت نشده است.')}</p>{request.baseVersion != null && <p className="pm-section-note">نسخهٔ مبنای درخواست: {num(request.baseVersion, 0)}</p>}<ChangeFields payload={payload} before={request.before} context={context}/>{activityChanges.length > 0 && <section><h4>تغییر فعالیت‌ها</h4>{activityChanges.map((change, index) => <article className="pm-change-diff" key={change.cellId}><b>فعالیت {num(index + 1, 0)}</b><div><small>متن پیش از درخواست</small><p>{currentCells.get(change.cellId) || 'متن پیشین در دسترس نیست'}</p></div><div><small>متن پیشنهادی</small><p>{change.text}</p></div></article>)}</section>}{activities.length > 0 && <section><h4>فعالیت‌های پیشنهادی برگه</h4><ol>{activities.map((activity, index) => <li key={activity.id || index}>{activity.text}</li>)}</ol></section>}{original && <details className="pm-source-details"><summary>مشاهدهٔ برگه پیش از درخواست</summary><SheetTables sheet={original} renderCell={cell => <SourceCellText cell={cell}/>}/></details>}{request.decisionReason && <div className="info-note"><b>علت تصمیم رئیس</b><p>{request.decisionReason}</p></div>}{chief && request.status === 'pending' && <Field label="توضیح تصمیم *" hint="علت تأیید یا رد، همراه درخواست نگهداری می‌شود."><textarea rows={2} minLength={3} maxLength={2000} disabled={busy} value={reason} onChange={e => setReason(e.target.value)}/></Field>}</>}</div><div className="modal-footer"><button className="button ghost" disabled={busy} onClick={close}>بستن</button>{chief && request?.status === 'pending' && <><button className="button secondary" disabled={busy || reason.trim().length < 3} onClick={() => void act('reject')}>رد درخواست</button><button className="button primary" disabled={busy || reason.trim().length < 3} onClick={() => void act('approve')}>{busy ? 'ثبت تصمیم…' : 'تأیید و اعمال تغییر'}</button></>}</div></Modal>;
}

function ChangeFields({ payload, before, context }: { payload: Record<string, unknown>; before?: Record<string, unknown> | null; context: PageProps }) {
 const labels: Record<string, string> = { title: 'نام برگه', hall: 'سالن', specialty: 'واحد مسئول', period: 'دورهٔ بازدید', formCode: 'کد فرم', startDate: 'شروع اعتبار', active: 'صدور خودکار', mappingConfirmed: 'تأیید تطبیق', assetId: 'تجهیز', supervisorId: 'سرپرست' };
 const fields = Object.entries(labels).filter(([key]) => key in payload);
 return <>{fields.length > 0 && <div className="table-wrap"><table className="pm-change-fields"><thead><tr><th>مشخصه</th>{before && <th>پیش از درخواست</th>}<th>پیشنهادی</th></tr></thead><tbody>{fields.map(([key, label]) => <tr key={key}><th>{label}</th>{before && <td><ReviewValue name={key} value={before[key]} context={context}/></td>}<td><ReviewValue name={key} value={payload[key]} context={context}/></td></tr>)}</tbody></table></div>}{payload.recurrence != null && <div className="pm-change-diff"><b>دورهٔ انجام</b>{before && <div><small>پیش از درخواست</small><p><RecurrenceSummary recurrence={before.recurrence}/></p></div>}<div><small>پیشنهادی</small><p><RecurrenceSummary recurrence={payload.recurrence}/></p></div></div>}{Array.isArray(payload.slots) && <div className="pm-change-diff"><b>نوبت‌های سالانه</b>{before && <div><small>پیش از درخواست</small><p><SlotsSummary slots={before.slots}/></p></div>}<div><small>پیشنهادی</small><p><SlotsSummary slots={payload.slots}/></p></div></div>}{Array.isArray(payload.groupMemberCodes) && <details className="pm-source-details" open><summary>دستگاه‌های گروه در پیشنهاد ({num(payload.groupMemberCodes.length, 0)})</summary><p dir="ltr">{payload.groupMemberCodes.join('، ')}</p></details>}</>;
}
function ReviewValue({ name, value, context }: { name: string; value: unknown; context: PageProps }) {
 if (name === 'assetId' || name === 'supervisorId') return <ReferenceName kind={name === 'assetId' ? 'assets' : 'people'} id={typeof value === 'string' ? value : ''} context={context}/>;
 return <>{typeof value === 'boolean' ? value ? 'فعال / تأییدشده' : 'غیرفعال / تأییدنشده' : name === 'startDate' && typeof value === 'string' ? dateFa(value) : String(value || '—')}</>;
}
function ReferenceName({ kind, id, context }: { kind: 'assets' | 'people'; id: string; context: PageProps }) {
 const known = (kind === 'assets' ? context.boot.assets : context.boot.people).find(item => item.id === id);
 const resource = useResource<PageResult<Asset | User>>('/catalog/' + kind + '?' + new URLSearchParams({ ids: id, pageSize: '1' }), emptyPage(), 0, !!id && !known);
 const item = known || resource.data.items.find(item => item.id === id);
 return <>{!id ? 'تعیین نشده' : item ? item.code + ' — ' + item.name : resource.loading ? 'دریافت مشخصات…' : 'مشخصات مرجع در دسترس نیست'}</>;
}
function SlotsSummary({ slots }: { slots: unknown }) {
 if (!Array.isArray(slots) || !slots.length) return <>نوبتی ثبت نشده</>;
 return <>{slots.map(slot => pmMonths[Number(slot.month) - 1] + '، هفتهٔ ' + num(slot.week, 0)).join(' · ')}</>;
}
function RecurrenceSummary({ recurrence }: { recurrence: unknown }) {
 if (!recurrence || typeof recurrence !== 'object') return <>تعیین نشده</>;
 const value = recurrence as Record<string, unknown>;
 return value.mode === 'source-period' ? <>هر {num(value.interval, 0)} {value.unit === 'week' ? 'هفته' : 'ماه'}؛ نخستین نوبت سال {num(value.anchorYear, 0)}، ماه {num(value.anchorMonth, 0)}، هفتهٔ {num(value.week, 0)}</> : <>نوبت‌های مشخص تقویم سالانه</>;
}
