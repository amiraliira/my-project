import { useCallback, useEffect, useState } from 'react';
import { CatalogSelect, PageControls, emptyPage } from './Catalog';
import { JalaliDate } from './JalaliDate';
import type { PageProps } from './Orders';
import { mayViewPmArchive } from './permissions';
import { archivePath, archiveStatus, archiveStatuses, emptyArchiveFilters } from './pmArchiveModel';
import type { PmArchiveDetail, PmArchiveEntry, PmArchiveFilters, PmArchivePage } from './pmArchiveModel';
import PmArchiveDocument from './PmArchiveDocument';
import { Empty, ErrorBox, Field, Icon, Loading, Modal, SearchInput, dateFa, num, todayIso, useDebounce, useResource } from './ui';
import './pm-archive.css';

export default function PmArchive({ context }: { context: PageProps }) {
 if (!mayViewPmArchive(context.user.role)) return <Empty title="بایگانی در اختیار کارشناس نت است" icon="lock"/>;
 return <ArchiveList context={context}/>;
}
function ArchiveList({ context }: { context: PageProps }) {
 const [filters, setFilters] = useState(emptyArchiveFilters), [page, setPage] = useState(1), [selected, setSelected] = useState('');
 // Apply filters as one value: changing search while on a later page never fetches that later page.
 const applied = useDebounce(filters, 350), [previousFilters, setPreviousFilters] = useState(applied);
 const effectivePage = previousFilters === applied ? page : 1;
 useEffect(() => { if (previousFilters !== applied) { setPreviousFilters(applied); setPage(1); } }, [applied, previousFilters]);
 const invalidRange = !!(applied.from && applied.to && applied.from > applied.to);
 const records = useResource<PmArchivePage>(archivePath(applied, effectivePage), emptyPage(), 0, !invalidRange);
 const change = <K extends keyof PmArchiveFilters>(name: K, value: PmArchiveFilters[K]) => setFilters(current => ({ ...current, [name]: value }));
 const close = useCallback(() => setSelected(''), []);
 const filtered = Object.values(filters).some(Boolean);
 useEffect(() => { if (!records.loading && !records.error && records.updatedAt && records.data.page === effectivePage && effectivePage > Math.max(1, records.data.totalPages)) setPage(Math.max(1, records.data.totalPages)); }, [records.loading, records.error, records.updatedAt, records.data.page, records.data.totalPages, effectivePage]);
 return <div className="pm-archive">
  <div className="pm-archive-heading"><div><h2><Icon name="imports"/>بایگانی برگه‌های انجام‌شده</h2><p>نام برگه، شماره، کد دستگاه یا نام انجام‌دهنده را جستجو کنید؛ هر ارسال با نسخه و مسیر تأیید خودش نگهداری می‌شود.</p></div><button type="button" className="button secondary small" disabled={records.loading || invalidRange} onClick={records.reload}><Icon name="refresh" size={16}/>تازه‌سازی</button></div>
  <div className="pm-archive-search"><SearchInput value={filters.q} onChange={value => change('q', value)} placeholder="جستجوی برگه، شماره، دستگاه یا انجام‌دهنده…"/><Field label="سالن"><select value={filters.hall} onChange={event => { change('hall', event.target.value); change('assetId', ''); }}><option value="">همهٔ سالن‌ها</option>{context.boot.locations.map(hall => <option key={hall} value={hall}>{hall}</option>)}</select></Field><Field label="وضعیت همین نسخه"><select value={filters.status} onChange={event => change('status', event.target.value)}><option value="">همهٔ وضعیت‌ها</option>{Object.entries(archiveStatuses).filter(([key]) => !['supervisor', 'head'].includes(key)).map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></Field></div>
  <details className="pm-archive-advanced"><summary><Icon name="search" size={16}/>جستجوی دقیق با دستگاه، مجری و تاریخ</summary><div className="pm-archive-filter-grid"><Field label="دستگاه"><CatalogSelect kind="assets" value={filters.assetId} onChange={value => change('assetId', value)} filters={filters.hall ? { hall: filters.hall } : {}} placeholder="همهٔ دستگاه‌ها / جستجوی نام یا کد"/></Field><Field label="انجام‌دهنده"><CatalogSelect kind="people" value={filters.personId} onChange={value => change('personId', value)} filters={{ role: 'technician' }} placeholder="همهٔ انجام‌دهندگان / جستجوی نام"/></Field><ArchiveDate label="از تاریخ انجام (شمسی)" value={filters.from} change={value => change('from', value)}/><ArchiveDate label="تا تاریخ انجام (شمسی)" value={filters.to} change={value => change('to', value)}/></div></details>
  <div className="pm-archive-list-summary"><span role="status">{records.loading ? 'در حال جستجو…' : invalidRange ? 'بازهٔ تاریخ را اصلاح کنید.' : num(records.data.total, 0) + ' نسخهٔ ثبت‌شده'}</span>{filtered && <button type="button" className="text-button" onClick={() => { setFilters(emptyArchiveFilters()); setPage(1); }}>پاک کردن فیلترها</button>}</div>
  <ErrorBox text={invalidRange ? 'تاریخ آغاز بازه باید قبل از تاریخ پایان یا برابر آن باشد.' : records.error} retry={invalidRange ? undefined : records.reload}/>
  {!invalidRange && (records.loading ? <Loading/> : records.error ? null : records.data.items.length ? <div className="pm-archive-records">{records.data.items.map(entry => <ArchiveCard key={entry.id} entry={entry} open={() => setSelected(entry.id)}/>)}</div> : <Empty title={filtered ? 'برگه‌ای با این مشخصات پیدا نشد' : 'هنوز برگه‌ای برای بایگانی ارسال نشده است'} detail={filtered ? 'جستجو یا بازهٔ تاریخ را تغییر دهید.' : 'پس از تکمیل و ارسال برگهٔ PM، نسخهٔ آن به‌صورت خودکار در این بخش ثبت می‌شود؛ برگه‌های منتظر تأیید هم با وضعیت واقعی خود نمایش داده می‌شوند.'} icon="imports"/>)}
  {!invalidRange && <PageControls page={effectivePage} total={records.data.total} change={setPage} busy={records.loading}/>}
  {selected && <ArchiveDetail key={selected} id={selected} close={close} select={setSelected}/>}
 </div>;
}
function ArchiveDate({ label, value, change }: { label: string; value: string; change: (value: string) => void }) {
 return <div className="field"><label className="checkbox"><input type="checkbox" checked={!!value} onChange={event => change(event.target.checked ? todayIso() : '')}/>{label}</label><JalaliDate value={value || todayIso()} onChange={change} disabled={!value}/></div>;
}
export function ArchiveCard({ entry, open }: { entry: PmArchiveEntry; open: () => void }) {
 const corrected = entry.evidenceSource === 'supervisor-correction-snapshot';
 return <article className="pm-archive-record"><div className="pm-archive-record-title"><span className="pm-archive-record-icon"><Icon name="audit" size={23}/></span><div><h3>{entry.title}</h3><p><bdi>{entry.number}</bdi> · {corrected ? 'نسخهٔ اصلاح سرپرست' : 'نسخهٔ ارسال'} {num(entry.workflowRevision, 0)} · {entry.hall}{entry.specialty ? ' · ' + entry.specialty : ''}</p></div><span className={'pm-archive-status ' + entry.status}>{archiveStatus(entry.status)}</span></div><dl><div><dt>انجام‌دهنده</dt><dd>{entry.performerName || 'ثبت نشده'}</dd></div><div><dt>تاریخ انجام</dt><dd>{dateFa(entry.workDate)}</dd></div><div><dt>دستگاه</dt><dd>{entry.assetName} <small><bdi>{entry.assetCode}</bdi></small></dd></div><div><dt>{corrected ? 'زمان اصلاح سرپرست' : 'زمان ارسال'}</dt><dd>{dateFa(entry.submittedAt, true)}</dd></div></dl><button type="button" className="button secondary small" onClick={open}>مشاهدهٔ برگه و مسیر تأیید<Icon name="arrow" size={16}/></button></article>;
}
function ArchiveDetail({ id, close, select }: { id: string; close: () => void; select: (id: string) => void }) {
 const record = useResource<PmArchiveDetail | null>('/pm/archive/' + encodeURIComponent(id), null);
 return <Modal title="برگهٔ بایگانی‌شدهٔ PM" close={close} wide><div className="modal-body pm-archive-detail">{record.loading ? <Loading/> : record.error ? <ErrorBox text={record.error} retry={record.reload}/> : record.data && <><div className="pm-archive-detail-tools pm-archive-hide-print"><p>مشاهدهٔ سابقهٔ ثبت‌شده؛ تغییر این برگه از بایگانی امکان‌پذیر نیست.</p><button type="button" className="button secondary small" onClick={() => window.print()}><Icon name="reports" size={16}/>چاپ همین نسخه</button></div><PmArchiveDocument detail={record.data} openRevision={select}/>{record.data.revisions.length > 1 && <details className="pm-archive-revisions pm-archive-hide-print"><summary>نسخه‌های ثبت‌شدهٔ همین برگه ({num(record.data.revisionsTotal ?? record.data.revisions.length, 0)})</summary>{record.data.revisionsHasMore && <p>۵۰ نسخهٔ آخر نمایش داده می‌شود. برای یافتن نسخه‌های قدیمی‌تر، شمارهٔ همین برگه را در فهرست بایگانی جستجو کنید.</p>}{record.data.revisions.map(revision => <button key={revision.id} type="button" className="button ghost small" aria-current={revision.id === id ? 'true' : undefined} disabled={revision.id === id} onClick={() => select(revision.id)}>{revision.evidenceSource === 'supervisor-correction-snapshot' ? 'نسخهٔ اصلاح سرپرست' : 'نسخهٔ ارسال'} {num(revision.workflowRevision, 0)} · {dateFa(revision.submittedAt, true)} · {archiveStatus(revision.status)}</button>)}</details>}</>}</div><div className="modal-footer pm-archive-hide-print"><button type="button" className="button ghost" onClick={close}>بستن برگه</button></div></Modal>;
}
