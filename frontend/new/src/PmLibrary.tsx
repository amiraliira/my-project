import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError, download, post, put } from './api';
import { CatalogSelect, ServerTable, emptyPage } from './Catalog';
import { JalaliDate } from './JalaliDate';
import type { PageProps } from './Orders';
import type { PageResult } from './types';
import { Empty, ErrorBox, Field, Icon, Loading, Modal, PageHeader, SearchInput, dateFa, errorText, num, todayIso, useDebounce, useResource } from './ui';
import './pm.css';

export interface PmTemplateSummary {
 id: string; title: string; specialty: string; hall: string; period: string; version: number; issues: string[]; mappingStatus: string; calendarEligible?: boolean;
}
export interface PmCellParagraph { text: string; textDirection?: 'rtl' | 'ltr'; alignment?: 'left' | 'center' | 'right' | 'both' | 'justify'; paragraphBidiDefined?: boolean; }
export interface PmCell { id: string; text: string; colSpan: number; editable: boolean; activity: boolean; layoutRow?:number; layoutColumn?:number; rowSpan?:number; originalVerticalMerge?: 'restart' | 'continue' | null; originalMergedRange?: string | null; originalCellText?: string; originalCellParagraphs?: PmCellParagraph[]; sourceRef?: { row?: number; cell?: number; column?: number }; }
export interface PmTable { id: string; rows: { id: string; cells: PmCell[]; sourceRow?: number }[]; originalMergedRanges?: string[]; direction?: 'ltr' | 'rtl'; layoutVersion?:number; }
export interface PmBodyBlock { id: string; type: 'paragraph' | 'table'; text?: string; tableId?: string; sourceBodyIndex: number; alignment?: 'left' | 'center' | 'right' | 'both' | 'justify'; textDirection?: 'rtl' | 'ltr'; empty?: boolean; }
export interface PmSheet { tables: PmTable[]; originalParagraphs?: string[]; bodyBlocks?: PmBodyBlock[]; }
export interface PmSourceRef { path?: string; sheet?: string; cell?: string; }
export interface PmCalendarEvidence {
 sourceRow?: number | null; sourceSheet?: string | null;
 candidateSlots?: { sourceRow: number; sheet: string; status: string; slots: (PmSlot & { sourceCell?: string })[] }[];
 candidateCalendarSources?: { sheet: string; row: number; mappingStatus: string; blockers: string[]; sourceRefs: PmSourceRef[] }[];
 scheduleSourceConflicts?: { mainRowId: string; departmentRowId: string; sameWeeklyPositions: boolean; mainOnlySlots: [number, number][]; departmentOnlySlots: [number, number][]; sourceRefs: PmSourceRef[] }[];
}
export interface PmTemplate extends PmTemplateSummary, PmSheet, PmCalendarEvidence { source: { kind?: string; path?: string; sha256?: string; formCode?: string }; isCustom?: boolean; hasOriginal?: boolean; lifecycleState?: string; planId?: string; }
export interface PmSlot { month: number; week: number; }
export interface PmPlan extends PmCalendarEvidence {
 id: string; version: number; title: string; templateId: string; templateTitle: string; specialty: string; hall: string;
 assetId: string | null; supervisorId: string | null; startDate: string; slots: PmSlot[]; active: boolean; mappingConfirmed: boolean; issues: string[]; calendarEligible?: boolean;
}

interface DisplayCell { cell: PmCell; colSpan: number; rowSpan: number; }
/** Preserve literal codes/dates inside Persian text without letting bidi reverse their groups. */
export function PmSourceText({ text }: { text: string }): ReactNode {
 const parts: ReactNode[] = []; let start = 0;
 for (const match of text.matchAll(/[0-9۰-۹٠-٩]+(?:[ \t]*[-/][ \t]*[0-9۰-۹٠-٩]+)+/g)) {
  const index = match.index!;
  if (index > start) parts.push(text.slice(start, index));
  parts.push(<bdi key={index} dir="ltr">{match[0]}</bdi>); start = index + match[0].length;
 }
 if (start < text.length) parts.push(text.slice(start));
 return parts.length ? parts : text;
}
function excelColumn(value: string) { return [...value.toUpperCase()].reduce((number, letter) => number * 26 + letter.charCodeAt(0) - 64, 0); }
function mergedRange(value: string) {
 const match = /^\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)$/i.exec(value);
 return match ? { column: excelColumn(match[1]), row: Number(match[2]), endColumn: excelColumn(match[3]), endRow: Number(match[4]) } : null;
}
/** Physical cells stay in source order; covered merge continuations produce no extra table cell. */
export function pmDisplayRows(table: PmTable): DisplayCell[][] {
 if(table.layoutVersion===1)return table.rows.map(row=>row.cells.map(cell=>({cell,colSpan:cell.colSpan||1,rowSpan:cell.rowSpan||1})));
 const positioned = table.rows.map(row => { let column = 1; return row.cells.map(cell => { const current = { cell, column: cell.sourceRef?.column ?? column }; column += cell.colSpan || 1; return current; }); });
 const ranges = (table.originalMergedRanges || []).map(mergedRange).filter((range): range is NonNullable<typeof range> => range !== null);
 const verticalCovered = new Set<string>();
 return positioned.map((row, index) => row.flatMap(entry => {
  const { cell, column } = entry, sourceRow = cell.sourceRef?.row ?? table.rows[index].sourceRow ?? index + 1;
  const range = ranges.find(item => sourceRow >= item.row && sourceRow <= item.endRow && column >= item.column && column <= item.endColumn) || (cell.originalMergedRange ? mergedRange(cell.originalMergedRange) : null);
  if (range) {
   if (sourceRow !== range.row || column !== range.column) return [];
   return [{ cell, colSpan: range.endColumn - range.column + 1, rowSpan: range.endRow - range.row + 1 }];
  }
  if (verticalCovered.has(cell.id)) return [];
  let rowSpan = 1;
  if (cell.originalVerticalMerge === 'restart') {
   for (let next = index + 1; next < positioned.length; next++) {
    const continuation = positioned[next].find(item => item.column === column && item.cell.colSpan === cell.colSpan);
    if (!continuation || continuation.cell.originalVerticalMerge !== 'continue') break;
    verticalCovered.add(continuation.cell.id); rowSpan++;
   }
  }
  return [{ cell, colSpan: cell.colSpan || 1, rowSpan }];
 }));
}
export function SheetTables({ sheet, renderCell }: { sheet: PmSheet; renderCell: (cell: PmCell) => ReactNode }) {
 const tableView = (table: PmTable, index: number, blockId?: string) => <section className="pm-table-section" key={blockId || table.id} data-source-block-id={blockId}><h4>جدول {num(index + 1, 0)} برگه</h4><div className="pm-table-scroll" tabIndex={0} aria-label={'جدول ' + num(index + 1, 0) + ' برگه؛ قابل پیمایش'}><table className="pm-source-table" dir={table.direction || 'ltr'}><tbody>{pmDisplayRows(table).map((row, rowIndex) => <tr key={table.rows[rowIndex].id}>{row.map(({ cell, colSpan, rowSpan }) => <td key={cell.id} dir="rtl" data-cell-id={cell.id} colSpan={colSpan} rowSpan={rowSpan} className={cell.activity ? 'pm-activity-cell' : undefined}>{renderCell(cell)}</td>)}</tr>)}</tbody></table></div></section>;
 return <div className="pm-source-document" dir="rtl">{sheet.bodyBlocks?.length ? sheet.bodyBlocks.map(block => {
  if (block.type === 'paragraph') return block.text ? <p key={block.id} className="pm-source-paragraph" data-source-block-id={block.id} dir={block.textDirection || 'auto'} style={{ textAlign: block.alignment === 'both' || block.alignment === 'justify' ? 'justify' : block.alignment }}><PmSourceText text={block.text}/></p> : null;
  const index = sheet.tables.findIndex(table => table.id === block.tableId);
  return index >= 0 ? tableView(sheet.tables[index], index, block.id) : <p key={block.id} className="pm-message warning">جدول این بخش از اصل برگه در دسترس نیست؛ اصل فایل را بررسی کنید.</p>;
 }) : <>{!!sheet.originalParagraphs?.length && <div className="pm-source-paragraphs">{sheet.originalParagraphs.map((text, index) => <p key={index} dir="auto"><PmSourceText text={text}/></p>)}</div>}{sheet.tables.map((table, index) => tableView(table, index))}</>}</div>;
}
export function SourceCellText({ cell }: { cell: PmCell }) {
 const paragraphs = cell.originalCellParagraphs;
 const originalUnchanged = cell.originalCellText !== undefined ? cell.text === cell.originalCellText : paragraphs?.map(paragraph => paragraph.text).join('') === cell.text;
 if (!paragraphs?.length || !originalUnchanged) return <span dir="auto"><PmSourceText text={cell.text}/></span>;
 return <div className="pm-cell-paragraphs">{paragraphs.map((paragraph, index) => <p key={index} dir={paragraph.paragraphBidiDefined ? paragraph.textDirection || 'auto' : 'auto'} style={{ textAlign: paragraph.alignment === 'both' || paragraph.alignment === 'justify' ? 'justify' : paragraph.alignment }}>{paragraph.text ? <PmSourceText text={paragraph.text}/> : <br/>}</p>)}</div>;
}
/** Reused by the execution page: only source result cells become editable. */
export function PmSheetView({ sheet, results, onResults }: { sheet: PmSheet; results: Record<string, string>; onResults?: (results: Record<string, string>) => void }) {
 const resultNumbers=new Map(sheet.tables.flatMap(t=>t.rows.flatMap(r=>r.cells.filter(c=>c.editable))).map((c,i)=>[c.id,i+1]));
 return <div className="pm-detail pm-paper"><SheetTables sheet={sheet} renderCell={cell => cell.editable ? onResults ? <textarea className="pm-result-input" dir="auto" aria-label={cell.sourceRef?.row?'نتیجهٔ بازدید ردیف '+num(cell.sourceRef.row,0)+' ستون '+num(cell.sourceRef.column??cell.sourceRef.cell??1,0):'نتیجهٔ بازدید '+num(resultNumbers.get(cell.id)||1,0)} value={results[cell.id] ?? ''} maxLength={2000} onChange={event => onResults({ ...results, [cell.id]: event.target.value })}/> : results[cell.id] !== undefined ? <span dir="auto"><PmSourceText text={results[cell.id]}/></span> : <SourceCellText cell={cell}/> : <SourceCellText cell={cell}/>}/></div>;
}

const monthNames = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
const weekNames = ['اول', 'دوم', 'سوم', 'چهارم'];
const canManage = (role: string) => role === 'head' || role === 'manager';
const canRead = (role: string) => canManage(role) || role === 'admin';
function slotLabel(slot: PmSlot) { return `${monthNames[slot.month - 1] || num(slot.month, 0)} · هفتهٔ ${weekNames[slot.week - 1] || num(slot.week, 0)}`; }
function validSlot(slot: unknown): slot is PmSlot { return !!slot && typeof slot === 'object' && 'month' in slot && 'week' in slot && Number.isInteger(slot.month) && Number.isInteger(slot.week) && Number(slot.month) >= 1 && Number(slot.month) <= 12 && Number(slot.week) >= 1 && Number(slot.week) <= 4; }
function sourceText(value: unknown, fallback = 'تعیین نشده') { return typeof value === 'string' && value.trim() ? value : fallback; }
function sourceRowLabel(value: unknown) { return typeof value === 'number' && Number.isInteger(value) && value > 0 ? num(value, 0) : 'تعیین نشده'; }
function MappingBadge({ status, issues = [] }: { status: string; issues?: string[] }) {
 const labels: Record<string, string> = { verified: 'تطبیق بررسی‌شده', confirmed: 'تطبیق تأییدشده', resolved: 'تطبیق بررسی‌شده', pending: 'در انتظار بررسی', unresolved: 'نیازمند تعیین تکلیف', ambiguous: 'تطبیق مبهم', 'review-required': 'نیازمند بررسی', imported: 'واردشده برای بررسی', unverified: 'تطبیق بررسی‌نشده' };
 const ready = ['verified', 'confirmed', 'resolved'].includes(status) && !issues.length;
 return <span className={'pm-status ' + (ready ? 'ready' : issues.length ? 'blocked' : 'pending')}>{labels[status] || 'در انتظار بررسی'}</span>;
}
export function IssueList({ issues }: { issues: string[] }) {
 return issues.length ? <div className="pm-message warning"><b>موارد نیازمند بررسی</b><ul>{issues.map((issue, i) => <li key={i}>{issueLabel(issue)}</li>)}</ul></div> : null;
}

function issueLabel(issue: string) {
 const labels: Record<string, string> = {
  'chief-explicit-resolution-required-before-scheduling': 'پیش از زمان‌بندی، رئیس باید تطبیق و موارد نامشخص این برگه را صریح تعیین تکلیف کند.',
  'source-filename-inactive-status-unconfirmed': 'در نام فایل، تجهیز یا برگه غیرفعال معرفی شده است؛ وضعیت فعلی باید بررسی شود.',
  'no-actual-activities': 'فعالیت واقعی در این برگه ثبت نشده است؛ فعالیت یا فهرست تجهیزات باید تعیین شود.',
  'hour-and-calendar-dual-trigger-not-linked-to-annual-inspection-calendar': 'برگه هم شرط ساعت کارکرد دستگاه و هم دورهٔ زمانی دارد؛ مبنای صدور باید جداگانه تعیین شود.',
  'folder-header-hall-conflict': 'سالنِ نام پوشه با سالنِ سربرگ برگه متفاوت است.',
  'folder-body-period-conflict': 'دورهٔ نام پوشه با دورهٔ نوشته‌شده در متن برگه متفاوت است.',
  'main-and-department-calendar-week-positions-conflict': 'موعد شیت «کل سالنها» با شیت همان سالن متفاوت است؛ منبع معتبر باید تعیین شود.',
  'filename-contains-unconfirmed-period-change': 'تغییر دوره فقط در نام فایل نوشته شده است؛ هنوز تصمیم رسمی دربارهٔ دوره ثبت نشده است.',
  'control-ledger-not-an-executable-equipment-job': 'این برگه برای کنترل تحویل و برگشت اسناد است و فعالیت اجرایی یک تجهیز نیست.',
  'mixed-specialty-workbook-content': 'در فایل، محتوای تخصص دیگری هم وجود دارد؛ حدود برگهٔ این واحد باید بررسی شود.',
  'excluded-secondary-sheet-has-additional-equipment-requiring-chief-review': 'شیت قدیمی یا فرعی، تجهیزات اضافه دارد؛ ورود آن‌ها به برنامهٔ فعلی به بررسی رئیس نیاز دارد.',
  'main-group-row-has-no-explicit-hall-or-equipment-code': 'ردیف گروهیِ تقویم، سالن یا کد تجهیز را صریح مشخص نکرده است.',
  'multiple-source-forms-for-same-calendar-row-and-specialty': 'چند برگهٔ همین تخصص به یک ردیف تقویم مربوط شده‌اند؛ برگهٔ درست باید تعیین شود.',
  'calendar-code-spelling-normalized-candidate-only': 'نگارش کد در تقویم و برگه یکسان نیست؛ تطبیق پیشنهادی هنوز باید بررسی شود.',
  'reversed-code-collides-with-different-filename-equipment-identity': 'دو ترتیب کد، به تجهیزات متفاوت اشاره می‌کنند؛ هویت تجهیز باید بررسی شود.'
 };
 return labels[issue] || (/[\u0600-\u06ff]/.test(issue) ? issue : 'تطبیق این بخش از منبع به بررسی و تعیین تکلیف رئیس نیاز دارد.');
}
function SourceRefs({ refs }: { refs: PmSourceRef[] }) {
 const available = Array.isArray(refs) ? refs.filter(ref => ref && typeof ref === 'object' && [ref.path, ref.sheet, ref.cell].some(value => typeof value === 'string' && value.trim())) : [];
 return available.length ? <div className="pm-evidence-refs">{available.map((ref, index) => <span key={index}>{sourceText(ref.path, '') ? <span dir="auto">{sourceText(ref.path, '')} · </span> : null}{sourceText(ref.sheet, '') ? `شیت «${sourceText(ref.sheet, '')}»` : ''}{sourceText(ref.cell, '') ? <> · خانهٔ <code dir="ltr">{sourceText(ref.cell, '')}</code></> : null}</span>)}</div> : null;
}
export function CalendarEvidence({ source }: { source: PmCalendarEvidence }) {
 const candidates = Array.isArray(source.candidateSlots) ? source.candidateSlots.filter(candidate => candidate && typeof candidate === 'object') : [], calendars = Array.isArray(source.candidateCalendarSources) ? source.candidateCalendarSources.filter(calendar => calendar && typeof calendar === 'object') : [], conflicts = Array.isArray(source.scheduleSourceConflicts) ? source.scheduleSourceConflicts.filter(conflict => conflict && typeof conflict === 'object') : [];
 const conflictSlots = (values: [number, number][]) => {
  if (!Array.isArray(values)) return 'در منبع تعیین نشده؛ نیازمند بررسی';
  if (!values.length) return 'اختلافی ندارد';
  const slots = values.filter(value => Array.isArray(value) && value.length === 2 && validSlot({ month: value[0], week: value[1] }));
  return [...slots.map(([month, week]) => slotLabel({ month, week })), ...(slots.length !== values.length ? ['بخشی از موعد منبع نامشخص است؛ نیازمند بررسی'] : [])].join('، ');
 };
 if (!candidates.length && !calendars.length && !conflicts.length && !source.sourceRow) return null;
 return <details className="pm-calendar-evidence"><summary>موعد پیشنهادی منبع و اختلاف‌های تقویم</summary><p className="pm-section-note">این موعدها برای بررسی از منابع خوانده شده‌اند. انتخاب برنامهٔ نهایی باید با تعیین منبع معتبر و ثبت علت انجام شود.</p>{!!source.sourceRow && <p className="pm-section-note">مرجع فعلی: شیت «{sourceText(source.sourceSheet)}»، ردیف {sourceRowLabel(source.sourceRow)}</p>}
  {candidates.map((candidate, index) => {
   const sourceSlots = Array.isArray(candidate.slots) ? candidate.slots : [], slots = sourceSlots.filter((slot): boolean => validSlot(slot));
   return <article key={index} className="pm-evidence-card"><h4>شیت «{sourceText(candidate.sheet)}» · ردیف {sourceRowLabel(candidate.sourceRow)}</h4><div className="pm-badges"><MappingBadge status={sourceText(candidate.status, 'unresolved')}/></div>{slots.length > 0 && <ul>{slots.map((slot, slotIndex) => <li key={slotIndex}>{slotLabel(slot)}{sourceText(slot.sourceCell, '') ? <> · خانهٔ <code dir="ltr">{sourceText(slot.sourceCell, '')}</code></> : null}</li>)}</ul>}{(!slots.length || slots.length !== sourceSlots.length) && <p className="pm-section-note">موعد کامل و معتبر در منبع مشخص نشده است؛ رئیس باید آن را بررسی و تعیین کند.</p>}</article>;
  })}
  {calendars.map((calendar, index) => {
   const blockers = Array.isArray(calendar.blockers) ? calendar.blockers.filter(blocker => typeof blocker === 'string') : [];
   return <article key={index} className="pm-evidence-card"><h4>شیت «{sourceText(calendar.sheet)}» · ردیف {sourceRowLabel(calendar.row)}</h4><div className="pm-badges"><MappingBadge status={sourceText(calendar.mappingStatus, 'unresolved')} issues={blockers}/></div>{blockers.length ? <ul>{blockers.map((blocker, blockerIndex) => <li key={blockerIndex}>{issueLabel(blocker)}</li>)}</ul> : <p className="pm-section-note">{Array.isArray(calendar.blockers) ? 'اختلافی برای این تطبیق گزارش نشده است.' : 'وضعیت اختلاف‌های این تطبیق در منبع مشخص نشده است؛ نیازمند بررسی است.'}</p>}<SourceRefs refs={calendar.sourceRefs}/></article>;
  })}
  {conflicts.map((conflict, index) => <article key={index} className="pm-evidence-card warning"><h4>تفاوت «کل سالنها» و شیت سالن</h4><p>نوبت‌های فقط در «کل سالنها»: {conflictSlots(conflict.mainOnlySlots)}</p><p>نوبت‌های فقط در شیت سالن: {conflictSlots(conflict.departmentOnlySlots)}</p><SourceRefs refs={conflict.sourceRefs}/></article>)}
 </details>;
}

export default function PmLibrary(context: PageProps) {
 const [tab, setTab] = useState<'templates' | 'plans'>('templates');
 const [q, setQ] = useState(''), [templatePage, setTemplatePage] = useState(1), [planPage, setPlanPage] = useState(1);
 const [templateId, setTemplateId] = useState(''), [planId, setPlanId] = useState('');
 const [importBusy, setImportBusy] = useState(false), [importError, setImportError] = useState('');
 const search = useDebounce(q), allowed = canRead(context.user.role);
 useEffect(() => { setTemplatePage(1); setPlanPage(1); }, [search]);
 const templates = useResource<PageResult<PmTemplateSummary>>('/pm/templates?' + new URLSearchParams({ page: String(templatePage), pageSize: '25', q: search }), emptyPage(), 0, allowed && tab === 'templates');
 const plans = useResource<PageResult<PmPlan>>('/pm/plans?' + new URLSearchParams({ page: String(planPage), pageSize: '25', q: search }), emptyPage(), 0, allowed && tab === 'plans');
 const closeTemplate = useCallback(() => setTemplateId(''), []), closePlan = useCallback(() => setPlanId(''), []);
 const reload = () => { templates.reload(); plans.reload(); };
 if (!allowed) return <Empty title="دسترسی به کتابخانهٔ PM ندارید" detail="بررسی قالب‌ها و تنظیم برنامه با رئیس یا مدیر فنی است." icon="lock"/>;
 return <div className="pm-library">
  <PageHeader title="برگه‌ها و زمان‌بندی PM" subtitle="مرجع برگه‌های اصلی، بازبینی فعالیت‌ها و برنامهٔ چهار هفتهٔ هر ماه">
   <button className="button secondary" disabled={importBusy} onClick={async () => {
    if (importBusy) return; setImportBusy(true); setImportError('');
    try { await post('/pm/stage-import', {}); reload(); context.notify('برگه‌ها برای بازبینی وارد شدند؛ هیچ دستورکاری صادر نشد.'); }
    catch (error) { setImportError(errorText(error)); } finally { setImportBusy(false); }
   }}><Icon name="imports" size={18}/>{importBusy ? 'در حال ورود برای بازبینی…' : 'ورود برگه‌ها برای بازبینی'}</button>
  </PageHeader>
  <div className="info-banner"><Icon name="plans"/><div><b>بررسی قبل از صدور</b><span>ورود برگه‌ها فقط قالب و برنامهٔ پیشنهادی می‌سازد. تا تعیین تجهیز، سرپرست، هفته‌ها و تأیید رئیس، برنامه فعال نمی‌شود. برق و مکانیک دو برگهٔ مستقل دارند.</span></div></div>
  <ErrorBox text={importError}/>
  <section className="panel pm-panel">
   <div className="tabs"><button type="button" className={tab === 'templates' ? 'active' : ''} onClick={() => { setTab('templates'); setQ(''); }}>قالب برگه‌ها</button><button type="button" className={tab === 'plans' ? 'active' : ''} onClick={() => { setTab('plans'); setQ(''); }}>زمان‌بندی</button></div>
   <div className="pm-toolbar"><SearchInput value={q} onChange={setQ} placeholder={tab === 'templates' ? 'جستجوی برگه، دوره یا سالن…' : 'جستجوی برنامه، برگه یا سالن…'}/><button type="button" className="button ghost small" onClick={reload} disabled={templates.loading && tab === 'templates' || plans.loading && tab === 'plans'}><Icon name="refresh" size={17}/>تازه‌سازی</button><span className="pm-count">هر صفحه حداکثر ۲۵ مورد</span></div>
   {tab === 'templates' ? <><ErrorBox text={templates.error} retry={templates.reload}/>{templates.loading ? <Loading/> : <ServerTable result={templates.data} changePage={setTemplatePage} rowKey={item => item.id} columns={[
    { title: 'برگه', cell: item => <><b className="pm-row-title">{item.title}</b><small>نسخهٔ {num(item.version, 0)}</small></> },
    { title: 'واحد / محل', cell: item => <>{item.specialty}<small>{item.hall || 'محل تعیین نشده'}</small></> },
    { title: 'دورهٔ درج‌شده', cell: item => item.period || 'در منبع مشخص نشده' },
    { title: 'تطبیق', cell: item => <div className="pm-badges"><MappingBadge status={item.mappingStatus} issues={item.issues}/>{item.issues.length > 0 && <small>{num(item.issues.length, 0)} مورد نیازمند بررسی</small>}</div> },
    { title: 'عملیات', cell: item => <button className="button ghost small" onClick={() => setTemplateId(item.id)}>مشاهدهٔ اصل جدول{canManage(context.user.role) ? ' و بازبینی' : ''}</button> }
   ]} empty={<Empty title="برگه‌ای در کتابخانه نیست" detail={search ? 'نام دیگری جستجو کنید.' : 'با ورود برگه‌ها برای بازبینی، قالب‌های اصلی در این فهرست قرار می‌گیرند.'} icon="plans"/>}/>}</> : <><ErrorBox text={plans.error} retry={plans.reload}/>{plans.loading ? <Loading/> : <ServerTable result={plans.data} changePage={setPlanPage} rowKey={item => item.id} columns={[
    { title: 'برنامه / برگه', cell: item => <><b className="pm-row-title">{item.title}</b><small>{item.templateTitle}</small></> },
    { title: 'واحد / محل', cell: item => <>{item.specialty}<small>{item.hall || 'محل تعیین نشده'}</small></> },
    { title: 'شروع برنامه', cell: item => dateFa(item.startDate) },
    { title: 'نوبت‌های ماهانه', cell: item => item.slots.length ? <>{num(item.slots.length, 0)} نوبت<small>{item.slots.slice(0, 2).map(slotLabel).join('، ')}{item.slots.length > 2 ? '…' : ''}</small></> : <span className="negative">هفته تعیین نشده</span> },
    { title: 'وضعیت', cell: item => <div className="pm-badges"><span className={'pm-status ' + (item.active ? 'ready' : 'pending')}>{item.active ? 'فعال' : 'غیرفعال'}</span>{!item.mappingConfirmed && <span className="pm-status blocked">تطبیق تأیید نشده</span>}</div> },
    { title: 'عملیات', cell: item => <button className="button ghost small" onClick={() => setPlanId(item.id)}>{canManage(context.user.role) ? 'بررسی و ویرایش' : 'مشاهدهٔ برنامه'}</button> }
   ]} empty={<Empty title="برنامه‌ای پیدا نشد" detail={search ? 'نام دیگری جستجو کنید.' : 'برنامه‌های پیشنهادی پس از ورود برگه‌ها برای بازبینی نمایش داده می‌شوند.'} icon="plans"/>}/>}</>}
  </section>
  {templateId && <TemplateDialog key={templateId} id={templateId} context={context} close={closeTemplate} saved={reload}/>}
  {planId && <PlanDialog key={planId} id={planId} context={context} close={closePlan} saved={reload}/>}
 </div>;
}

function TemplateDialog({ id, context, close, saved }: { id: string; context: PageProps; close: () => void; saved: () => void }) {
 const resource = useResource<PmTemplate | null>('/pm/templates/' + encodeURIComponent(id), null);
 const [editing, setEditing] = useState(false), [changes, setChanges] = useState<Record<string, string>>({}), [reason, setReason] = useState('');
 const [busy, setBusy] = useState(false), [error, setError] = useState(''), [conflict, setConflict] = useState(false), [downloadBusy, setDownloadBusy] = useState(false);
 const detail = resource.data, canEdit = canManage(context.user.role);
 const cancelEdit = () => { setEditing(false); setChanges({}); setReason(''); setError(''); setConflict(false); };
 const changed = detail ? detail.tables.flatMap(table => table.rows.flatMap(row => row.cells)).filter(cell => cell.activity && changes[cell.id] !== undefined && changes[cell.id] !== cell.text).map(cell => ({ cellId: cell.id, text: changes[cell.id] })) : [];
 const downloadOriginal = async () => { if (downloadBusy) return; setDownloadBusy(true); setError(''); try { await download('/pm/templates/' + encodeURIComponent(id) + '/original', detail?.source.path?.split(/[\\/]/).pop() || 'PM-original'); } catch (e) { setError(errorText(e)); } finally { setDownloadBusy(false); } };
 return <Modal title="اصل برگه و بازبینی فعالیت‌ها" close={close} wide locked={busy || downloadBusy}>
  <form onSubmit={async event => {
   event.preventDefault(); if (busy || !detail || !editing || !canEdit) return;
   if (!changed.length) { setError('فعالیتی تغییر نکرده است.'); return; }
   if (reason.trim().length < 3) { setError('علت اصلاح را حداقل با سه نویسه وارد کنید.'); return; }
   if (changed.some(change => !change.text.trim())) { setError('متن فعالیت نمی‌تواند خالی باشد.'); return; }
   setBusy(true); setError(''); setConflict(false);
   try { await put('/pm/templates/' + encodeURIComponent(id), { version: detail.version, reason: reason.trim(), changes: changed }); cancelEdit(); resource.reload(); saved(); context.notify('نسخهٔ جدید فعالیت‌ها ذخیره شد؛ برگه‌های قبلی حفظ شدند.'); }
   catch (e) { setError(errorText(e)); setConflict(e instanceof ApiError && e.status === 409); } finally { setBusy(false); }
  }}>
   <div className="modal-body pm-detail pm-paper"><ErrorBox text={resource.error} retry={resource.reload}/><ErrorBox text={error}/>{resource.loading ? <Loading/> : detail && <>
    <div className="pm-detail-head"><div><h3>{detail.title}</h3><div className="pm-badges"><span className="pm-status">{detail.specialty}</span><span className="pm-status">نسخهٔ {num(detail.version, 0)}</span><MappingBadge status={detail.mappingStatus} issues={detail.issues}/></div></div><button type="button" className="button ghost small" disabled={busy || downloadBusy} onClick={downloadOriginal}><Icon name="download" size={17}/>{downloadBusy ? 'دریافت اصل فایل…' : 'دریافت اصل فایل'}</button></div>
    <div className="pm-meta"><div><small>محل</small><b>{detail.hall || 'در منبع تعیین نشده'}</b></div><div><small>دورهٔ منبع</small><b>{detail.period || 'در منبع تعیین نشده'}</b></div><div><small>کد فرم</small><b dir="auto">{detail.source.formCode || 'در منبع درج نشده'}</b></div></div>
    <IssueList issues={detail.issues}/><CalendarEvidence source={detail}/>
    <p className="pm-message">اصلاح فعالیت‌ها فقط بر نسخه‌های بعدی اثر دارد. متن و نتایج برگه‌های صادرشدهٔ قبلی تغییر نمی‌کند. ترتیب ردیف‌ها، ستون‌ها و شماره‌های منبع در این نمایش حفظ شده‌اند.</p>
    {conflict && <div className="pm-conflict"><p>نسخهٔ برگه هم‌زمان تغییر کرده است. متن اصلاحی شما در این صفحه حفظ شده؛ برای بازخوانی نسخهٔ جدید، ابتدا اصلاحات خود را نگه دارید.</p><button type="button" className="button secondary small" onClick={() => { cancelEdit(); resource.reload(); }}>کنارگذاشتن اصلاحات و دریافت نسخهٔ جدید</button></div>}
    <SheetTables sheet={detail} renderCell={cell => editing && canEdit && cell.activity ? <><textarea aria-label="متن فعالیت" required value={changes[cell.id] ?? cell.text} onChange={event => setChanges(current => ({ ...current, [cell.id]: event.target.value }))}/>{changes[cell.id] !== undefined && changes[cell.id] !== cell.text && <div className="pm-original"><b>متن پیش از اصلاح</b><br/>{cell.text}</div>}</> : <SourceCellText cell={cell}/>}/>
    {!detail.tables.length && <Empty title="جدول قابل نمایش در این برگه نیست" detail="اصل فایل و موارد نیازمند بررسی را بررسی کنید." icon="plans"/>}
    {editing && <><Field label="علت اصلاح فعالیت‌ها *" hint="علت همراه نسخهٔ جدید ثبت و قابل پیگیری می‌شود."><textarea rows={3} required minLength={3} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)}/></Field><p className="pm-change-summary">{num(changed.length, 0)} فعالیت برای اصلاح انتخاب شده است.</p></>}
    <div className="pm-source-label">منبع: <span dir="auto">{detail.source.path}</span></div><div className="pm-source-label">هش اصل فایل: <code dir="ltr">{detail.source.sha256}</code></div>
   </>}</div>
   <div className="modal-footer"><button type="button" className="button ghost" onClick={editing ? cancelEdit : close} disabled={busy || downloadBusy}>{editing ? 'انصراف از اصلاح' : 'بستن'}</button>{detail && !resource.loading && canEdit && (editing ? <button type="submit" className="button primary" disabled={busy || downloadBusy || !changed.length}>{busy ? 'ذخیرهٔ نسخه…' : 'ذخیرهٔ نسخهٔ جدید'}</button> : <button type="button" className="button primary" disabled={!detail.tables.some(table => table.rows.some(row => row.cells.some(cell => cell.activity)))} onClick={() => setEditing(true)}>اصلاح فعالیت‌ها</button>)}</div>
  </form>
 </Modal>;
}

function PlanDialog({ id, context, close, saved }: { id: string; context: PageProps; close: () => void; saved: () => void }) {
 const resource = useResource<PmPlan | null>('/pm/plans/' + encodeURIComponent(id), null);
 return resource.data && !resource.loading ? <PlanEditor key={resource.data.version} detail={resource.data} context={context} close={close} saved={saved} reload={resource.reload}/> : <Modal title="بررسی زمان‌بندی PM" close={close} wide><div className="modal-body"><ErrorBox text={resource.error} retry={resource.reload}/>{resource.loading && <Loading/>}</div><div className="modal-footer"><button type="button" className="button ghost" onClick={close}>بستن</button></div></Modal>;
}

function PlanEditor({ detail, context, close, saved, reload }: { detail: PmPlan; context: PageProps; close: () => void; saved: () => void; reload: () => void }) {
 const [assetId, setAssetId] = useState(detail.assetId || ''), [supervisorId, setSupervisorId] = useState(detail.supervisorId || '');
 const [startDate, setStartDate] = useState(detail.startDate || todayIso()), [slots, setSlots] = useState<PmSlot[]>(detail.slots.map(slot => ({ ...slot })));
 const [active, setActive] = useState(detail.active), [mappingConfirmed, setMappingConfirmed] = useState(detail.mappingConfirmed), [month, setMonth] = useState(detail.slots[0]?.month || 1), [reason, setReason] = useState('');
 const [busy, setBusy] = useState(false), [error, setError] = useState(''), [conflict, setConflict] = useState(false);
 const canEdit = canManage(context.user.role), mayActivate = detail.calendarEligible !== false && mappingConfirmed && !!assetId && !!supervisorId && slots.length > 0 && !!startDate;
 const blockers = [detail.calendarEligible === false ? 'مبنای این برگه برای صدور تقویمی مشخص نشده است؛ برنامهٔ وابسته به ساعت دستگاه باید جداگانه تعیین تکلیف شود.' : '', !assetId ? 'تجهیز این برگه تعیین نشده است.' : '', !supervisorId ? 'سرپرست این تخصص تعیین نشده است.' : '', !slots.length ? 'ماه و هفتهٔ اجرای برنامه تعیین نشده است.' : '', !mappingConfirmed ? 'تطبیق برگه و برنامه هنوز توسط رئیس تأیید نشده است.' : '', !startDate ? 'تاریخ شروع برنامه تعیین نشده است.' : ''].filter(Boolean);
 const toggleSlot = (week: number) => { setSlots(current => current.some(slot => slot.month === month && slot.week === week) ? current.filter(slot => slot.month !== month || slot.week !== week) : [...current, { month, week }].sort((a, b) => a.month - b.month || a.week - b.week)); };
 return <Modal title={canEdit ? 'بررسی و ویرایش زمان‌بندی PM' : 'مشاهدهٔ زمان‌بندی PM'} close={close} wide locked={busy}>
  <form onSubmit={async event => {
   event.preventDefault(); if (busy || !canEdit) return;
   if (reason.trim().length < 3) { setError('علت تغییر یا تأیید برنامه را حداقل با سه نویسه وارد کنید.'); return; }
   if (active && !mayActivate) { setError('پیش از فعال‌سازی، موارد تعیین‌نشده را تکمیل و تطبیق را تأیید کنید.'); return; }
   setBusy(true); setError(''); setConflict(false);
   try { await put('/pm/plans/' + encodeURIComponent(detail.id), { version: detail.version, reason: reason.trim(), assetId: assetId || null, supervisorId: supervisorId || null, startDate, slots, active, mappingConfirmed }); saved(); context.notify('زمان‌بندی PM ذخیره شد؛ برگه‌های قبلی حفظ شدند.'); close(); }
   catch (e) { setError(errorText(e)); setConflict(e instanceof ApiError && e.status === 409); } finally { setBusy(false); }
  }}>
   <div className="modal-body pm-detail"><ErrorBox text={error}/><div className="pm-detail-head"><div><h3>{detail.title}</h3><div className="pm-badges"><span className="pm-status">{detail.specialty}</span><span className="pm-status">نسخهٔ {num(detail.version, 0)}</span><span className={'pm-status ' + (detail.active ? 'ready' : 'pending')}>{detail.active ? 'فعال' : 'غیرفعال'}</span></div></div></div>
    <div className="pm-meta"><div><small>قالب برگه — ثابت</small><b>{detail.templateTitle}</b></div><div><small>تخصص — ثابت</small><b>{detail.specialty}</b></div><div><small>محل درج‌شده</small><b>{detail.hall || 'تعیین نشده'}</b></div></div>
    <IssueList issues={detail.issues}/><CalendarEvidence source={detail}/><p className="pm-message">هر نوبت، یک برگهٔ مستقل برای همین تخصص و یک مجری دارد. این صفحه زمان‌بندی را تنظیم می‌کند؛ تخصیص مجری و ثبت ساعت کار در برگهٔ اجرایی انجام می‌شود. تغییر برنامه و فعالیت‌ها، سوابق صادرشدهٔ قبلی را تغییر نمی‌دهد.</p>
    {conflict && <div className="pm-conflict"><p>برنامه هم‌زمان تغییر کرده است. انتخاب‌ها و علت واردشدهٔ شما حفظ شده؛ پس از نگه‌داشتن تغییرات، نسخهٔ جدید را دریافت کنید.</p><button type="button" className="button secondary small" onClick={reload}>کنارگذاشتن تغییرات و دریافت نسخهٔ جدید</button></div>}
    <fieldset className="modal-operation" disabled={!canEdit || busy}><div className="pm-form-grid">
     <Field label="تجهیز برگه" hint="تطبیق تجهیز با متن و کد اصل برگه را بررسی کنید."><CatalogSelect kind="assets" value={assetId} onChange={value => { setAssetId(value); setMappingConfirmed(false); setActive(false); }} disabled={!canEdit || busy}/></Field>
     <Field label={'سرپرست واحد ' + detail.specialty}><CatalogSelect kind="people" value={supervisorId} onChange={value => { setSupervisorId(value); setMappingConfirmed(false); setActive(false); }} filters={{ role: 'supervisor', specialty: detail.specialty, active: 'true' }} disabled={!canEdit || busy}/></Field>
     <Field label="تاریخ شروع برنامه — شمسی"><JalaliDate value={startDate} onChange={setStartDate}/></Field>
     <Field label="تاریخ انتخاب‌شده"><div className="pm-single-assignee">{dateFa(startDate)}</div></Field>
     <Field label="ماه شمسی برای انتخاب هفته‌ها" wide><select value={month} onChange={event => setMonth(Number(event.target.value))}>{monthNames.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select></Field>
    </div><div className="pm-week-select" role="group" aria-label={'هفته‌های ' + monthNames[month - 1]}>{weekNames.map((name, index) => { const selected = slots.some(slot => slot.month === month && slot.week === index + 1); return <button type="button" key={name} className={selected ? 'selected' : ''} aria-pressed={selected} onClick={() => toggleSlot(index + 1)}><span>هفتهٔ {name}</span><br/><small>{selected ? 'انتخاب شده' : 'انتخاب نشده'}</small></button>; })}</div>
    <p className="pm-section-note">چهار هفتهٔ کامل شنبه تا جمعه از اولین شنبهٔ هر ماه؛ هفتهٔ چهارم می‌تواند به ماه بعد برسد. تأخیر نوبت قبل، تاریخ نوبت بعد را جابه‌جا نمی‌کند.</p>
    <div className="pm-schedule-preview"><b>{num(slots.length, 0)} نوبت انتخاب شده</b><span>{slots.length ? slots.map(slotLabel).join('، ') : 'ماه و هفته‌ای انتخاب نشده است.'}</span></div>
    <label className="checkbox"><input type="checkbox" checked={mappingConfirmed} onChange={event => { setMappingConfirmed(event.target.checked); if (!event.target.checked) setActive(false); }}/><span>تطبیق برگه، تجهیز، تخصص، سرپرست و هفته‌های برنامه را به‌عنوان رئیس تأیید می‌کنم.</span></label>
    <label className="checkbox"><input type="checkbox" checked={active} disabled={!mayActivate && !active || !canEdit || busy} onChange={event => setActive(event.target.checked)}/><span>برنامه فعال باشد</span></label>
    {blockers.length > 0 && <div className="pm-message warning"><b>پیش‌نیازهای فعال‌سازی</b><ul>{blockers.map(blocker => <li key={blocker}>{blocker}</li>)}</ul></div>}
    {canEdit && <Field label="علت تغییر یا تأیید برنامه *" hint="موارد نامفهوم منبع باید صریح تعیین تکلیف شوند؛ تصمیم شما با علت ثبت می‌شود."><textarea rows={3} required minLength={3} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)}/></Field>}
    </fieldset>
   </div><div className="modal-footer"><button type="button" className="button ghost" disabled={busy} onClick={close}>{canEdit ? 'انصراف' : 'بستن'}</button>{canEdit && <button type="submit" className="button primary" disabled={busy}>{busy ? 'ذخیرهٔ برنامه…' : 'ذخیرهٔ زمان‌بندی'}</button>}</div>
  </form>
 </Modal>;
}
