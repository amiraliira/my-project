import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, currentSessionGeneration, upload } from './api';
import { DataTable, ErrorBox, Field, Modal, errorText, num } from './ui';
import './parts-import.css';

type ImportStatus = 'new' | 'updated' | 'unchanged' | 'invalid';
type Change = { field: string; from: string | number | null; to: string | number | null };
type ImportRow = { row: number; code: string; name: string; status: ImportStatus; changes: Change[]; errors: string[] };
type Preview = {
 currency: 'IRR'; sheet: string | null; headerRow: number | null;
 sheets: { name: string; eligible: boolean }[];
 columns: Record<string, { label: string; column: string }>;
 summary: Record<ImportStatus | 'total', number>; rows: ImportRow[];
 fingerprint: string | null; canApply: boolean; warnings: string[];
};
const labels: Record<string, string> = { code: 'کد انبار', name: 'نام قطعه', specification: 'مشخصات فنی', unit: 'واحد', unitCost: 'قیمت واحد (ریال)' };
const statuses: Record<ImportStatus, string> = { new: 'قطعهٔ جدید', updated: 'به‌روزرسانی', unchanged: 'بدون تغییر', invalid: 'نیاز به اصلاح' };
const display = (value: string | number | null) => value == null || value === '' ? '—' : typeof value === 'number' ? num(value, 2) : value;

export function PartsImport({ close, saved, notify }: { close: () => void; saved: () => Promise<void>; notify: (text: string) => void }) {
 const [file, setFile] = useState<File | null>(null), [preview, setPreview] = useState<Preview | null>(null);
 const [error, setError] = useState(''), [busy, setBusy] = useState<'preview' | 'apply' | ''>(''), [dragging, setDragging] = useState(false);
 const [filter, setFilter] = useState<ImportStatus | 'changed' | 'all'>('changed');
 const input = useRef<HTMLInputElement>(null), request = useRef<AbortController | null>(null), generation = useRef(0);
 useEffect(() => () => { generation.current++; request.current?.abort(); }, []);

 const inspect = useCallback(async (nextFile: File, sheet = '') => {
  request.current?.abort(); const controller = new AbortController(); request.current = controller;
  const current = ++generation.current, session = currentSessionGeneration();
  setError(''); setPreview(null); setBusy('preview'); setFilter('changed');
  try {
   const result = await upload<Preview>('/parts/import/preview' + (sheet ? '?' + new URLSearchParams({ sheet }) : ''), nextFile, {}, controller.signal);
   if (current === generation.current && session === currentSessionGeneration()) setPreview(result);
  } catch (problem) { if (current === generation.current && !(problem instanceof DOMException && problem.name === 'AbortError')) setError(errorText(problem)); }
  finally { if (current === generation.current) setBusy(''); }
 }, []);

 const choose = (files: FileList | File[]) => {
  if (busy) return;
  setDragging(false); setError(''); setPreview(null);
  if (files.length !== 1) { setFile(null); setError('هر بار فقط یک فایل اکسل انتخاب کنید.'); return; }
  const next = files[0];
  if (!/\.xlsx$/i.test(next.name) || !next.size || next.size > 8 * 1024 * 1024) {
   setFile(null); setError('فایل باید xlsx و حداکثر ۸ مگابایت باشد. فایل xls قدیمی را در Excel با قالب xlsx ذخیره کنید.'); return;
  }
  setFile(next); void inspect(next);
 };
 const shown = useMemo(() => (preview?.rows || []).filter(row => filter === 'all' || filter === 'changed' && row.status !== 'unchanged' || row.status === filter), [preview, filter]);
 const apply = async () => {
  if (!file || !preview?.canApply || !preview.fingerprint || busy) return;
  setError(''); setBusy('apply');
  const session = currentSessionGeneration();
  try {
   const result = await upload<{ inserted: number; updated: number; unchanged: number }>('/parts/import/apply' + (preview.sheet ? '?' + new URLSearchParams({ sheet: preview.sheet }) : ''), file, { 'X-Import-Fingerprint': preview.fingerprint });
   if (session !== currentSessionGeneration()) return;
   await saved().catch(() => {});
   notify(`${num(result.inserted, 0)} قطعه اضافه و ${num(result.updated, 0)} قطعه به‌روز شد.`);
   close();
  } catch (problem) {
   // A timeout may follow a committed write. Never silently retry an import.
   setPreview(previous => previous ? { ...previous, canApply: false, fingerprint: null } : previous);
   setError(errorText(problem) + (problem instanceof ApiError && problem.status === 0 ? ' پیش از تکرار، دوباره پیش‌نمایش بگیرید تا وضعیت ثبت مشخص شود.' : ' دوباره پیش‌نمایش بگیرید.'));
  } finally { setBusy(''); }
 };

 return <Modal title="ورود اکسل انبار" close={close} wide locked={!!busy}>
  <div className="modal-body parts-import">
   <p className="info-note">فایل معمولی انبار را انتخاب کنید؛ ستون‌ها خودکار تشخیص داده می‌شوند. قیمت‌ها به ریال است. سلول خالی، مقدار قبلی را پاک نمی‌کند و قیمت صفر، صفر ثبت می‌شود.</p>
   <div className={'parts-import-drop' + (dragging ? ' dragging' : '')} onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); if (!busy) choose(event.dataTransfer.files); }}>
    <b>اکسل را اینجا بکشید</b><span>یا از دستگاه انتخاب کنید؛ xlsx، حداکثر ۸ مگابایت</span>
    <button type="button" className="button secondary" disabled={!!busy} onClick={() => input.current?.click()}>انتخاب فایل اکسل</button>
    <input ref={input} className="parts-import-input" tabIndex={-1} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" aria-label="انتخاب فایل اکسل انبار" disabled={!!busy} onChange={event => { if (event.target.files?.length) choose(event.target.files); event.target.value = ''; }}/>
   </div>
   {file && <div className="parts-import-file"><b dir="auto">{file.name}</b><button type="button" className="button ghost small" disabled={!!busy} onClick={() => void inspect(file, preview?.sheet || '')}>دریافت دوبارهٔ پیش‌نمایش</button></div>}
   <ErrorBox text={error}/>
   {busy && <p role="status" aria-live="polite">{busy === 'preview' ? 'در حال تشخیص ستون‌ها و بررسی قطعات…' : 'در حال ثبت تغییرات؛ این صفحه را نبندید…'}</p>}
   {preview && <>
    {preview.sheets.filter(sheet => sheet.eligible).length > 1 && <Field label="برگهٔ انبار"><select disabled={!!busy} value={preview.sheet || ''} onChange={event => { if (file && event.target.value) void inspect(file, event.target.value); }}><option value="">برگه را انتخاب کنید</option>{preview.sheets.filter(sheet => sheet.eligible).map(sheet => <option key={sheet.name} value={sheet.name}>{sheet.name}</option>)}</select></Field>}
    {preview.warnings.map(warning => <p className="info-note" key={warning}>{warning}</p>)}
    {preview.sheet && <p className="muted">برگه: {preview.sheet} — ردیف عنوان: {num(preview.headerRow, 0)}</p>}
    {Object.keys(preview.columns).length > 0 && <div className="parts-import-columns" aria-label="ستون‌های تشخیص داده‌شده">{Object.entries(preview.columns).map(([field, column]) => <span key={field}>{labels[field] || field}: <b>{column.label}</b> <small dir="ltr">({column.column})</small></span>)}</div>}
    {!!preview.rows.length && <>
     <div className="parts-import-summary">{(['new', 'updated', 'unchanged', 'invalid'] as ImportStatus[]).map(status => <button type="button" key={status} className={'button ghost' + (filter === status ? ' selected' : '')} disabled={!!busy} onClick={() => setFilter(status)}>{statuses[status]}: <b>{num(preview.summary[status], 0)}</b></button>)}</div>
     <Field label="نمایش ردیف‌ها"><select value={filter} disabled={!!busy} onChange={event => setFilter(event.target.value as typeof filter)}><option value="changed">تغییرها و موارد نیازمند اصلاح</option><option value="all">همهٔ ردیف‌ها</option>{Object.entries(statuses).map(([status, label]) => <option key={status} value={status}>{label}</option>)}</select></Field>
     <DataTable data={shown} rowKey={row => String(row.row)} empty={<p className="muted">ردیفی در این دسته نیست.</p>} columns={[
      { title: 'ردیف / کد انبار', cell: row => <><span>{num(row.row, 0)}</span><small className="mono" dir="ltr">{row.code || '—'}</small></> },
      { title: 'قطعه / وضعیت', cell: row => <><b>{row.name || 'نام مشخص نیست'}</b><small className={row.status === 'invalid' ? 'negative' : 'muted'}>{statuses[row.status]}</small></> },
      { title: 'تغییرات', cell: row => <>{row.changes.filter(change => change.field !== 'code').map(change => <div className="parts-import-change" key={change.field}><b>{labels[change.field] || change.field}: </b><span dir="auto">{display(change.from)} ← {display(change.to)}</span></div>)}{row.status === 'unchanged' && 'بدون تغییر'}</> },
      { title: 'نیاز به بررسی', cell: row => row.errors.length ? row.errors.map(message => <div className="negative" key={message}>{message}</div>) : '—' },
     ]}/>
     {!!preview.summary.invalid && <p className="negative" role="status">ردیف‌های مشخص‌شده را در اکسل اصلاح و فایل را دوباره انتخاب کنید؛ تا آن زمان هیچ ردیفی ثبت نمی‌شود.</p>}
     {!preview.summary.invalid && !preview.summary.new && !preview.summary.updated && <p role="status">اطلاعات فایل با قطعات ثبت‌شده یکسان است؛ تغییری برای ثبت وجود ندارد.</p>}
    </>}
   </>}
   <p className="muted">تطبیق بر اساس کد دقیق انبار است. کد تازه، قطعهٔ تازه می‌سازد. اطلاعات مصرف و تعداد موجودی با این ورود تغییر نمی‌کند.</p>
  </div>
  <div className="modal-footer"><button type="button" className="button ghost" disabled={!!busy} onClick={close}>بستن</button><button type="button" className="button primary" disabled={!!busy || !preview?.canApply} onClick={() => void apply()}>{busy === 'apply' ? 'در حال ثبت…' : 'تأیید و ثبت تغییرات'}</button></div>
 </Modal>;
}
