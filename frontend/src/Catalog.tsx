import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api } from './api';
import type { Asset, PageResult, Part, User } from './types';
import { DataTable, ErrorBox, Loading, errorText, num, useDebounce, useResource } from './ui';

type CatalogItem = Asset | Part | User;
type Kind = 'assets' | 'parts' | 'people';
export const emptyPage = <T,>(): PageResult<T> => ({ items: [], page: 1, pageSize: 25, total: 0, totalPages: 0 });
export function PageControls({ page, total, pageSize = 25, change, busy = false }: { page: number; total: number; pageSize?: number; change: (page: number) => void; busy?: boolean }) {
 const pages = Math.ceil(total / pageSize);
 return <div className="pagination"><span>{num(total, 0)} رکورد</span>{pages > 1 && <div><button type="button" className="button ghost small" disabled={busy || page <= 1} onClick={() => change(page - 1)}>قبلی</button><span>{num(page, 0)} از {num(pages, 0)}</span><button type="button" className="button ghost small" disabled={busy || page >= pages} onClick={() => change(page + 1)}>بعدی</button></div>}</div>;
}
export function ServerTable<T>({ result, changePage, busy, ...table }: { result: PageResult<T>; changePage: (page: number) => void; busy?: boolean; columns: { title: string; cell: (row: T) => ReactNode }[]; rowKey: (row: T) => string; onRow?: (row: T) => void; empty?: ReactNode }) {
 return <><DataTable data={result.items} {...table} pagination={false}/><PageControls page={result.page} total={result.total} pageSize={result.pageSize} change={changePage} busy={busy}/></>;
}

/** Search and selected-id hydration are bounded independently; catalogs never enter bootstrap. */
export function CatalogSelect({ kind, value, onChange, onSelected, filters = {}, placeholder = 'جستجو و انتخاب', required = false, disabled = false, fallbackLabel = '' }: { kind: Kind; value: string; onChange: (id: string) => void; onSelected?: (item: CatalogItem) => void; filters?: Record<string, string>; placeholder?: string; required?: boolean; disabled?: boolean; fallbackLabel?: string }) {
 const [open, setOpen] = useState(false), [query, setQuery] = useState(''), [page, setPage] = useState(1), [selected, setSelected] = useState<CatalogItem | null>(null), [selectedError, setSelectedError] = useState('');
 const root = useRef<HTMLDivElement>(null), notifySelected = useRef(onSelected); notifySelected.current = onSelected;
 const search = useDebounce(query, 200), filterQuery = new URLSearchParams(filters).toString();
 const path = '/catalog/' + kind + '?' + new URLSearchParams({ ...filters, q: search, page: String(page), pageSize: '25' });
 const resource = useResource<PageResult<CatalogItem>>(path, emptyPage(), 0, open && !disabled);
 useEffect(() => { setPage(1); }, [search, filterQuery]);
 useEffect(() => { setSelected(null); setSelectedError(''); if (!value) return; const controller = new AbortController(); api<PageResult<CatalogItem>>('/catalog/' + kind + '?ids=' + encodeURIComponent(value), { signal: controller.signal }).then(result => { if (controller.signal.aborted) return; const item = result.items.find(entry => entry.id === value); if (item) { setSelected(item); notifySelected.current?.(item); } else setSelectedError('گزینه انتخاب‌شده پیدا نشد؛ دوباره انتخاب کنید.'); }).catch(error => { if (!controller.signal.aborted) setSelectedError(errorText(error)); }); return () => controller.abort(); }, [kind, value]);
 useEffect(() => { const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); }; document.addEventListener('pointerdown', close); return () => document.removeEventListener('pointerdown', close); }, []);
 useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
 const itemLabel = (item: CatalogItem) => kind === 'people' ? item.name : item.code + ' — ' + item.name;
 const label = selected?.id === value ? itemLabel(selected) : value ? fallbackLabel || 'در حال دریافت انتخاب…' : placeholder;
 return <div className="picker" ref={root} onKeyDown={event => { if (event.key === 'Escape' && open) { event.stopPropagation(); setOpen(false); root.current?.querySelector<HTMLButtonElement>('.picker-selected')?.focus(); } }}>
  <div className="picker-control"><button type="button" className={'picker-selected ' + (!value ? 'muted' : '')} disabled={disabled} onClick={() => setOpen(current => !current)} aria-expanded={open}>{label}<span>⌄</span></button>{value && !disabled && <button type="button" className="picker-clear" aria-label="پاک کردن انتخاب" onClick={() => { onChange(''); setSelected(null); }}>×</button>}</div>
  <input className="validation-input" value={value} onChange={() => {}} required={required} disabled={disabled} tabIndex={-1} aria-label={placeholder}/><ErrorBox text={selectedError}/>
  {open && !disabled && <div className="picker-menu"><input autoFocus value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} placeholder="نام یا کد را بنویسید" aria-label="جستجوی گزینه"/><ErrorBox text={resource.error} retry={resource.reload}/>{resource.loading ? <Loading/> : <div className="picker-list">{resource.data.items.map(item => <button type="button" key={item.id} className={item.id === value ? 'chosen' : ''} onClick={() => { setSelected(item); onChange(item.id); notifySelected.current?.(item); setOpen(false); setQuery(''); setPage(1); }}>{itemLabel(item)}<small>{'hall' in item ? item.hall + ' / ' + (item.line || 'بدون خط') : 'specialty' in item ? item.specialty + ' · ' + item.code : item.specification || item.unit || ''}</small></button>)}{!resource.data.items.length && !resource.error && <div className="muted picker-none">نتیجه‌ای پیدا نشد.</div>}</div>}<PageControls page={page} total={resource.data.total} change={setPage} busy={resource.loading}/></div>}
 </div>;
}
