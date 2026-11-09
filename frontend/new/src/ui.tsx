import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {createPortal} from 'react-dom';
import { api, currentSessionGeneration } from './api';

export const roles: Record<string, string> = { technician: 'تکنسین', supervisor: 'سرپرست', planner: 'کارشناس نت', head: 'رئیس فنی', manager: 'مدیر فنی', admin: 'مدیر سامانه' };
export const statuses: Record<string, string> = { draft: 'پیش‌نویس', submitted: 'ارسال‌شده', supervisor: 'تأیید سرپرستان', planner: 'تأیید کارشناس نت', head: 'تأیید رئیس یا مدیر فنی', completed: 'تکمیل نهایی', returned: 'برگشت برای اصلاح', cancelled: 'لغوشده' };
export const workTypes: Record<string, string> = { PM: 'پیشگیرانه', EM: 'رفع خرابی', PROJECT: 'پروژه' };
const numberFormats = new Map<number, Intl.NumberFormat>();
const dateFormats = [false, true].map(time => new Intl.DateTimeFormat('fa-IR', { timeZone: 'Asia/Tehran', year: 'numeric', month: 'short', day: 'numeric', ...(time ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' as const } : {}) }));
const isoFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tehran', year: 'numeric', month: '2-digit', day: '2-digit' });
export const num = (value: unknown, digits = 1) => { let format = numberFormats.get(digits); if (!format) { format = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: digits }); if (numberFormats.size >= 32) numberFormats.clear(); numberFormats.set(digits, format); } return format.format(Number(value ?? 0)); };
export function dateFa(value?: string | null, time = false) { if (!value) return '—'; const d = new Date(value.includes('T') ? value : value + 'T12:00:00'); if (Number.isNaN(d.getTime())) return value; return dateFormats[Number(time)].format(d); }
export function todayIso() { const p = isoFormat.formatToParts(new Date()); return ['year', 'month', 'day'].map(k => p.find(x => x.type === k)?.value).join('-'); }
export function duration(start: string, end: string) { if (!start || !end) return 0; const a = start.split(':').map(Number), b = end.split(':').map(Number); let n = b[0] * 60 + b[1] - a[0] * 60 - a[1]; if (n < 0) n += 1440; return n; }
export const errorText = (error: unknown) => error instanceof Error ? error.message : 'خطایی رخ داد.';
export function requestId() { if (typeof crypto.randomUUID === 'function') return crypto.randomUUID(); const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128; const hex = [...bytes].map(x => x.toString(16).padStart(2, '0')).join(''); return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-'); }
export function stringify(value: unknown) { return value == null ? '—' : typeof value === 'string' ? value : JSON.stringify(value); }

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
 const paths: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="4" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="11" width="7" height="10" rx="1.5"/></>,
  orders: <><path d="M9 5H5v16h14V5h-4"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="m8 12 2 2 5-5M8 18h7"/></>,
  assets: <><rect x="3" y="7" width="18" height="12" rx="2"/><path d="M8 7V3h8v4M7 12h3m4 0h3M7 16h10"/></>,
  stock: <><path d="m3 7 9-4 9 4-9 5zM3 7v10l9 4 9-4V7M12 12v9M7.5 5 17 9"/></>,
  plans: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-13 5 3 3 5-5"/></>,
  reports: <><path d="M4 3v18h17M8 16v-4m5 4V7m5 9v-6"/></>,
  users: <><circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 5a5 5 0 0 1 3 5"/></>,
  imports: <><path d="M3 8h18v13H3zM2 3h20v5H2zM9 12h6m-3 0v5m-2-2 2 2 2-2"/></>,
  audit: <><path d="M9 5H4v16h16V5h-5"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M8 12h8m-8 4h8"/></>,
  settings: <><path d="m9 3-1 3-3 1-2 5 2 5 3 1 1 3h6l1-3 3-1 2-5-2-5-3-1-1-3z"/><circle cx="12" cy="12" r="3"/></>,
  plus: <path d="M12 5v14M5 12h14"/>, search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>, close: <path d="m6 6 12 12M6 18 18 6"/>, refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5 8a7 7 0 0 1 12-3l3 3M4 16l3 3a7 7 0 0 0 12-3"/></>, logout: <><path d="M9 3H3v18h6m5-5 4-4-4-4m-6 4h12"/></>, menu: <path d="M3 6h18M3 12h18M3 18h18"/>, check: <path d="m5 12 4 4L19 6"/>, arrow: <path d="m9 5 7 7-7 7"/>, download: <><path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/></>, lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/></>, warning: <><path d="m12 3 10 18H2zM12 9v5m0 3v1"/></>,
 };
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.orders}</svg>;
}
export function useDebounce<T>(value: T, delay = 300) { const [debounced, set] = useState(value); useEffect(() => { const timer = setTimeout(() => set(value), delay); return () => clearTimeout(timer); }, [value, delay]); return debounced; }
export function useResource<T>(path: string, initial: T, pollMs = 0, enabled = true) {
 const generation = currentSessionGeneration(), key = generation + ':' + path;
 const [state, setState] = useState({ key: '', data: initial, loaded: false, pending: true, error: '', updatedAt: 0 });
 const initialValue = useRef(initial); initialValue.current = initial;
 const execute = useRef<(manual: boolean) => void>(() => {});
 useEffect(() => {
  if (!enabled) { execute.current = () => {}; return; }
  let active = true, pending = false, queued = false, lastStarted = 0;
  let controller: AbortController | null = null;
  const current = () => active && generation === currentSessionGeneration();
  const run = async (manual: boolean) => {
   if (!current()) return;
   if (pending) { if (manual) queued = true; return; }
   pending = true; lastStarted = Date.now(); controller = new AbortController();
   const request = controller;
   setState(previous => previous.key === key ? { ...previous, pending: true, error: '' } : { key, data: initialValue.current, loaded: false, pending: true, error: '', updatedAt: 0 });
   try {
    const data = await api<T>(path, { signal: request.signal });
    if (current() && !request.signal.aborted) setState({ key, data, loaded: true, pending: false, error: '', updatedAt: Date.now() });
   } catch (error) {
    if (current() && !request.signal.aborted) {
     const denied = [401, 403].includes((error as { status?: number })?.status || 0);
     setState(previous => ({ ...previous, ...(denied ? { data: initialValue.current, loaded: false } : {}), pending: false, error: errorText(error) }));
    }
   } finally {
    pending = false;
    if (current() && queued) { queued = false; void run(false); }
   }
  };
  execute.current = manual => { void run(manual); };
  const update = () => { if (document.visibilityState === 'visible' && navigator.onLine && !pending && Date.now() - lastStarted >= 1000) void run(false); };
  const invalidate = () => { active = false; controller?.abort(); setState({ key: '', data: initialValue.current, loaded: false, pending: false, error: '', updatedAt: 0 }); };
  void run(false);
  const timer = pollMs ? window.setInterval(update, pollMs) : undefined;
  if (pollMs) { window.addEventListener('focus', update); window.addEventListener('online', update); document.addEventListener('visibilitychange', update); }
  window.addEventListener('pak:unauthorized', invalidate); window.addEventListener('pak:password-changed', invalidate); window.addEventListener('pak:backend-incompatible', invalidate);
  return () => { active = false; queued = false; controller?.abort(); execute.current = () => {}; if (timer !== undefined) window.clearInterval(timer); window.removeEventListener('focus', update); window.removeEventListener('online', update); document.removeEventListener('visibilitychange', update); window.removeEventListener('pak:unauthorized', invalidate); window.removeEventListener('pak:password-changed', invalidate); window.removeEventListener('pak:backend-incompatible', invalidate); };
 }, [path, enabled, pollMs, generation, key]);
 const reload = useCallback(() => execute.current(true), []);
 const setData = useCallback((value: T | ((previous: T) => T)) => setState(previous => ({ ...previous, key, loaded: true, data: typeof value === 'function' ? (value as (previous: T) => T)(previous.key === key ? previous.data : initialValue.current) : value })), [key]);
 const matches = enabled && state.key === key;
 return { data: matches && state.loaded ? state.data : initial, setData, loadedPath: matches && state.loaded ? path : '', updatedAt: matches ? state.updatedAt : 0, loading: enabled && (!matches || (!state.loaded && state.pending)), refreshing: matches && state.loaded && state.pending, error: matches ? state.error : '', reload };
}
export function ErrorBox({ text, retry }: { text: string; retry?: () => void }) { return text ? <div className="alert error" role="alert"><Icon name="warning"/><span>{text}</span>{retry && <button className="text-button" onClick={retry}>تلاش دوباره</button>}</div> : null; }
export function Empty({ title = 'هنوز اطلاعاتی ثبت نشده', detail = 'پس از ثبت اطلاعات، فهرست این بخش نمایش داده می‌شود.', icon = 'orders', action }: { title?: string; detail?: string; icon?: string; action?: ReactNode }) { return <div className="empty"><span className="empty-icon"><Icon name={icon} size={30}/></span><h3>{title}</h3><p>{detail}</p>{action}</div>; }
export function Loading() { return <div className="loading flex items-center justify-center gap-2" role="status"><span className="spinner"/>در حال دریافت اطلاعات…</div>; }
export function Badge({ status, type }: { status?: string; type?: string }) { return <span className={'badge ' + (status || type || '')}>{status ? statuses[status] || status : workTypes[type || ''] || type}</span>; }
export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) { return <div className="page-header flex flex-wrap items-center justify-between gap-5"><div><h1>{title}</h1>{subtitle && <details className="page-help"><summary>راهنما</summary><p>{subtitle}</p></details>}</div><div className="header-actions flex flex-wrap items-center justify-end gap-2">{children}</div></div>; }
export function Field({ label, children, hint, wide = false }: { label: string; children: ReactNode; hint?: string; wide?: boolean }) { return <label className={'field ' + (wide ? 'wide' : '')}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>; }
const modalStack:HTMLDivElement[]=[];
let modalBodyOverflow='';
export function Modal({title,children,close,wide=false,locked=false}:{title:string;children:ReactNode;close:()=>void;wide?:boolean;locked?:boolean}) {
 const ref=useRef<HTMLDivElement>(null),onClose=useRef(close),isLocked=useRef(locked);onClose.current=close;isLocked.current=locked;
 useEffect(()=>{
  const last=document.activeElement as HTMLElement|null,el=ref.current;if(!el)return;
  if(!modalStack.length){modalBodyOverflow=document.body.style.overflow;document.body.style.overflow='hidden';}modalStack.push(el);
  el.querySelector<HTMLElement>('input:not([disabled]),select:not([disabled]),button:not([disabled]),textarea:not([disabled])')?.focus();
  const key=(event:KeyboardEvent)=>{
   if(modalStack.at(-1)!==el)return;
   if(event.key==='Escape'){event.preventDefault();if(!isLocked.current)onClose.current();return;}
   if(event.key==='Tab'){
    const items=[...el.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')].filter(x=>x.offsetParent!==null&&!x.closest('fieldset[disabled]'));
    if(!items.length){event.preventDefault();return;}
    if(event.shiftKey&&document.activeElement===items[0]){event.preventDefault();items.at(-1)?.focus();}
    else if(!event.shiftKey&&document.activeElement===items.at(-1)){event.preventDefault();items[0].focus();}
   }
  };
  document.addEventListener('keydown',key);
  return()=>{document.removeEventListener('keydown',key);const index=modalStack.indexOf(el);if(index>=0)modalStack.splice(index,1);if(!modalStack.length)document.body.style.overflow=modalBodyOverflow;if(last?.isConnected)last.focus();};
 },[]);
 // Portals sit outside the page shell; carry its theme so every dialog stays readable.
 return createPortal(<div className="modal-backdrop management-shell" onMouseDown={e=>{if(e.target===e.currentTarget&&!locked)close();}}><div ref={ref} className={'modal '+(wide?'modal-wide':'')} role="dialog" aria-modal="true" aria-label={title} aria-busy={locked}><div className="modal-head"><h2>{title}</h2><button type="button" className="icon-button" aria-label="بستن" disabled={locked} onClick={close}><Icon name="close"/></button></div><fieldset className="modal-operation" disabled={locked}>{children}</fieldset></div></div>,document.body);
}
export function SearchInput({ value, onChange, placeholder = 'جستجو…' }: { value: string; onChange: (value: string) => void; placeholder?: string }) { return <div className="search"><Icon name="search"/><input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder}/></div>; }
interface Option { id: string; label: string; sub?: string; }
export function SearchSelect({ options, value, onChange, placeholder = 'جستجو و انتخاب', required = false, disabled = false }: { options: Option[]; value: string; onChange: (value: string) => void; placeholder?: string; required?: boolean; disabled?: boolean }) {
 const [query, setQuery] = useState(''), [open, setOpen] = useState(false); const q = useDebounce(query, 120).toLocaleLowerCase('fa'); const root = useRef<HTMLDivElement>(null);
 const selected = useMemo(() => options.find(option => option.id === value), [options, value]);
 const searchable = useMemo(() => options.map(option => ({ option, text: (option.label + ' ' + (option.sub || '')).toLocaleLowerCase('fa') })), [options]);
 const found = useMemo(() => !open ? [] : !q ? options : searchable.filter(entry => entry.text.includes(q)).map(entry => entry.option), [open, q, options, searchable]);
 useEffect(() => { const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); }; document.addEventListener('pointerdown', close); return () => document.removeEventListener('pointerdown', close); }, []);
 useEffect(() => { const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && open) { event.stopPropagation(); setOpen(false); root.current?.querySelector<HTMLButtonElement>('.picker-selected')?.focus(); } }; root.current?.addEventListener('keydown', escape); const element = root.current; return () => element?.removeEventListener('keydown', escape); }, [open]);
 return <div className="picker" ref={root}><div className="picker-control"><button type="button" className={'picker-selected ' + (!selected ? 'muted' : '')} disabled={disabled} onClick={() => setOpen(!open)} aria-expanded={open}>{selected ? selected.label : placeholder}<span>⌄</span></button>{selected && !disabled && <button type="button" className="picker-clear" aria-label="پاک کردن انتخاب" onClick={() => onChange('')}>×</button>}</div><input className="validation-input" value={value} onChange={() => {}} required={required} disabled={disabled} tabIndex={-1} aria-label={placeholder}/>{open && !disabled && <div className="picker-menu"><input autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="نام یا کد را بنویسید" aria-label="جستجوی گزینه"/><div className="picker-list">{found.slice(0, 25).map(option => <button type="button" key={option.id} className={option.id === value ? 'chosen' : ''} onClick={() => { onChange(option.id); setOpen(false); setQuery(''); }}>{option.label}{option.sub && <small>{option.sub}</small>}</button>)}{!found.length && <div className="muted picker-none">نتیجه‌ای پیدا نشد.</div>}</div><div className="picker-count">{num(found.length, 0)} نتیجه؛ نمایش حداکثر ۲۵ مورد</div></div>}</div>;
}
export function DataTable<T>({ data, columns, rowKey, onRow, empty, pagination = true }: { data: T[]; columns: { title: string; cell: (row: T) => ReactNode }[]; rowKey: (row: T) => string; onRow?: (row: T) => void; empty?: ReactNode; pagination?: boolean }) {
 const [page, setPage] = useState(0); const count = 25, pages = Math.ceil(data.length / count), currentPage = Math.min(page, Math.max(0, pages - 1)), shown = data.slice(currentPage * count, (currentPage + 1) * count); useEffect(() => { setPage(value => Math.min(value, Math.max(0, Math.ceil(data.length / count) - 1))); }, [data.length]);
 if (!data.length) return empty || <Empty/>;
 return <><div className="table-wrap max-w-full overflow-x-auto overscroll-x-contain"><table><thead><tr>{columns.map(c => <th key={c.title}>{c.title}</th>)}</tr></thead><tbody>{(pagination ? shown : data).map(row => <tr key={rowKey(row)} className={onRow ? 'clickable' : ''}>{columns.map((c, i) => <td key={c.title}>{i === 0 && onRow ? <button className="row-open" onClick={() => onRow(row)}>{c.cell(row)}</button> : c.cell(row)}</td>)}</tr>)}</tbody></table></div>{pagination && <div className="pagination"><span>{num(data.length, 0)} رکورد</span>{pages > 1 && <div><button className="button ghost small" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>قبلی</button><span>{num(currentPage + 1, 0)} از {num(pages, 0)}</span><button className="button ghost small" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>بعدی</button></div>}</div>}</>;
}

