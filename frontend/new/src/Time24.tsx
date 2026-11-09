import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Modal } from './ui';
import { clockNumber, normalizeTime24, time24DialPoint, time24DialValue, time24Part } from './clock24';
import './time24.css';

interface Time24Props {
 value: string;
 onChange: (value: string) => void;
 label?: string;
 id?: string;
 name?: string;
 required?: boolean;
 disabled?: boolean;
 className?: string;
}

/** A text field and clock picker with one canonical, locale-independent value. */
export default function Time24({ value, onChange, label = 'ساعت', id, name, required = false, disabled = false, className = '' }: Time24Props) {
 const generatedId = useId(), inputId = id || `time24-${generatedId}`;
 const [draft, setDraft] = useState(value), [open, setOpen] = useState(false), [mode, setMode] = useState<'hour' | 'minute'>('hour');
 const [hourText, setHourText] = useState('00'), [minuteText, setMinuteText] = useState('00');
 const input = useRef<HTMLInputElement>(null), hourInput = useRef<HTMLInputElement>(null), minuteInput = useRef<HTMLInputElement>(null), lastEmitted = useRef(value);
 const hour = time24Part(hourText, 23), minute = time24Part(minuteText, 59);
 const invalid = !!draft.trim() && normalizeTime24(draft) === null;
 function unavailable() { return disabled || !!input.current?.matches(':disabled'); }
 function emit(next: string) { lastEmitted.current = next; onChange(next); }
 function edit(raw: string) {
  if (unavailable()) return;
  setDraft(raw);
  // Never leave an earlier valid value in a payload while a new draft is invalid.
  emit(normalizeTime24(raw) || '');
 }
 useEffect(() => {
  if (value !== lastEmitted.current) { lastEmitted.current = value; setDraft(value); }
 }, [value]);
 useEffect(() => {
  input.current?.setCustomValidity(invalid ? 'ساعت را از ۰۰:۰۰ تا ۲۳:۵۹ وارد کنید.' : '');
 }, [invalid]);
 useEffect(() => {
  if (!open) return;
  if (unavailable()) { setOpen(false); return; }
  hourInput.current?.focus();
  const observer = new MutationObserver(() => { if (unavailable()) setOpen(false); });
  let parent: HTMLElement | null = input.current?.parentElement || null;
  while (parent) { if (parent instanceof HTMLFieldSetElement) observer.observe(parent, { attributes: true, attributeFilter: ['disabled'] }); parent = parent.parentElement; }
  return () => observer.disconnect();
 }, [open, disabled]);
 function show() {
  if (unavailable()) return;
  const initial = normalizeTime24(draft) || '00:00';
  setHourText(initial.slice(0, 2)); setMinuteText(initial.slice(3)); setMode('hour'); setOpen(true);
 }
 function choose(value: number) {
  if (unavailable()) return;
  if (mode === 'hour') { setHourText(String(value).padStart(2, '0')); setMode('minute'); minuteInput.current?.focus(); }
  else setMinuteText(String(value).padStart(2, '0'));
 }
 function confirm() {
  if (unavailable() || hour === null || minute === null) return;
  const next = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
  setDraft(next); emit(next); setOpen(false);
 }
 function confirmKey(event: KeyboardEvent) { if (event.key === 'Enter') { event.preventDefault(); confirm(); } }
 const selected = mode === 'hour' ? hour : minute;
 const point = time24DialPoint(selected ?? 0, mode);
 const faceValues = mode === 'hour' ? Array.from({ length: 24 }, (_, index) => index) : Array.from({ length: 12 }, (_, index) => index * 5);
 return <div className={'time24-control ' + className}>
  <div className={'time24-input-row' + (invalid ? ' time24-invalid' : '')}>
   <input ref={input} id={inputId} name={name} type="text" inputMode="numeric" autoComplete="off" dir="ltr" maxLength={12} placeholder="--:--" aria-label={label} aria-invalid={invalid || undefined} aria-describedby={invalid ? inputId + '-error' : undefined} value={draft} required={required} disabled={disabled} onChange={event => edit(event.target.value)} onBlur={() => { const normalized = normalizeTime24(draft); if (normalized) setDraft(normalized); }}/>
   <span className="time24-format" aria-hidden="true">۲۴</span>
   <button type="button" className="time24-open" disabled={disabled} aria-label={'انتخاب ' + label} aria-haspopup="dialog" aria-expanded={open} onClick={show}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg></button>
  </div>
  {invalid && <small className="time24-error" id={inputId + '-error'}>ساعت نامعتبر است.</small>}
  {open && !disabled && <Modal title={'انتخاب ' + label} close={() => setOpen(false)}>
   <div className="modal-body time24-picker" onKeyDown={confirmKey}>
    <div className="time24-heading"><span>ساعت ۲۴ ساعته</span><span>{mode === 'hour' ? 'انتخاب ساعت' : 'انتخاب دقیقه'}</span></div>
    <div className="time24-digits" dir="ltr">
     <label><span>ساعت</span><input ref={hourInput} type="text" inputMode="numeric" maxLength={2} autoComplete="off" value={hourText} onFocus={() => setMode('hour')} onChange={event => setHourText(event.target.value)} aria-label="ساعت از صفر تا ۲۳" aria-invalid={hour === null || undefined} className={mode === 'hour' ? 'selected' : ''}/></label>
     <span className="time24-separator" aria-hidden="true">:</span>
     <label><span>دقیقه</span><input ref={minuteInput} type="text" inputMode="numeric" maxLength={2} autoComplete="off" value={minuteText} onFocus={() => setMode('minute')} onChange={event => setMinuteText(event.target.value)} aria-label="دقیقه از صفر تا ۵۹" aria-invalid={minute === null || undefined} className={mode === 'minute' ? 'selected' : ''}/></label>
    </div>
    <div className={'time24-face ' + mode} aria-label={mode === 'hour' ? 'صفحهٔ انتخاب ساعت ۲۴ ساعته' : 'صفحهٔ انتخاب دقیقه'} onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); const bounds = event.currentTarget.getBoundingClientRect(); choose(time24DialValue(event.clientX - bounds.left, event.clientY - bounds.top, bounds.width, mode)); }}>
     <span className="time24-hand" aria-hidden="true" style={{ width: `${Math.hypot(point.left - 50, point.top - 50)}%`, transform: `rotate(${(mode === 'hour' ? (selected ?? 0) % 12 * 30 : (selected ?? 0) * 6) - 90}deg)` }}/><span className="time24-pin" aria-hidden="true"/>
     {faceValues.map(value => { const location = time24DialPoint(value, mode); return <button key={value} type="button" className={'time24-face-number ' + (mode === 'hour' && value > 0 && value <= 12 ? 'inner' : 'outer')} style={{ left: location.left + '%', top: location.top + '%' }} aria-label={(mode === 'hour' ? 'ساعت ' : 'دقیقه ') + clockNumber(value, true)} aria-pressed={selected === value} onPointerDown={event => event.stopPropagation()} onClick={() => choose(value)}>{clockNumber(value, mode === 'minute' || value === 0)}</button>; })}
    </div>
    {(hour === null || minute === null) && <p className="time24-picker-error" role="alert">ساعت ۰۰ تا ۲۳ و دقیقه ۰۰ تا ۵۹ است.</p>}
   </div>
   <div className="modal-footer"><button type="button" className="button ghost" onClick={() => setOpen(false)}>انصراف</button><button type="button" className="button primary" disabled={hour === null || minute === null || disabled} onClick={confirm}>انتخاب</button></div>
  </Modal>}
 </div>;
}
