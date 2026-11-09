import { useEffect, useState } from 'react';
import { api, post } from './api';
import type { BackendHealth } from './compatibility';
import { ErrorBox, Icon, errorText, roles } from './ui';
import { passwordMaxLength, passwordMinLength, passwordPolicyError, validNewPassword } from './passwordPolicy';
import './local-test.css';

export interface LocalTestAccount { code: string; name: string; role: string; specialty?: string; }
type AccountResponse = { accounts: LocalTestAccount[] };
type AccountRequest = (path: string, options: RequestInit) => Promise<AccountResponse>;

export function isLocalTestMode(health: BackendHealth | null, hostname: string) {
 return health?.localTestMode === true && ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(hostname);
}

export function needsLocalTestPassword(health: BackendHealth | null, hostname: string) {
 return isLocalTestMode(health, hostname) && health?.localTestInitialized === false;
}

export function LocalTestPasswordSetup({ prepared }: { prepared: () => void }) {
 const [password, setPassword] = useState(''), [repeat, setRepeat] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
 return <form onSubmit={async event => {
  event.preventDefault(); if (busy) return;
  if (!validNewPassword(password)) { setError(passwordPolicyError); return; }
  if (password !== repeat) { setError('تکرار رمز عبور مطابقت ندارد.'); return; }
  setBusy(true); setError('');
  try {
   const result = await post<{ initialized: boolean }>('/test-setup/initialize', { password, passwordRepeat: repeat });
   if (result.initialized !== true) throw new Error('آماده‌سازی ورود انجام نشد؛ دوباره تلاش کنید.');
   setPassword(''); setRepeat(''); prepared();
  } catch (failure) { setError(errorText(failure)); }
  finally { setBusy(false); }
 }}>
  <ErrorBox text={error}/>
  <label className="field"><span>رمز آزمایشی — ۱۲ تا ۲۰۰ نویسه</span><input type="password" autoComplete="new-password" dir="ltr" required minLength={passwordMinLength} maxLength={passwordMaxLength * 2} disabled={busy} value={password} onChange={event => setPassword(event.target.value)} placeholder="رمز عبور"/></label>
  <label className="field"><span>تکرار رمز آزمایشی</span><input type="password" autoComplete="new-password" dir="ltr" required minLength={passwordMinLength} maxLength={passwordMaxLength * 2} disabled={busy} value={repeat} onChange={event => setRepeat(event.target.value)} placeholder="تکرار رمز عبور"/></label>
  <small className="local-test-password-note">این رمز فقط برای حساب‌های همین کپی آزمایشی است.</small>
  <button className="button primary login-submit" disabled={busy}>{busy ? 'در حال آماده‌سازی ورود…' : 'تنظیم رمز و ادامه'}<Icon name="arrow" size={18}/></button>
 </form>;
}

export async function readLocalTestAccounts(health: BackendHealth | null, hostname: string, request: AccountRequest, signal?: AbortSignal) {
 if (!isLocalTestMode(health, hostname)) return [];
 const response = await request('/test-setup/accounts', { cache: 'no-store', signal });
 if (!response || !Array.isArray(response.accounts) || response.accounts.some(account => !account || typeof account.code !== 'string' || !account.code.trim() || typeof account.name !== 'string' || typeof account.role !== 'string')) {
  throw new Error('فهرست حساب‌های آزمایشی دریافت نشد. دوباره تلاش کنید.');
 }
 return response.accounts;
}

export default function LocalTestSetup({ health, hostname, value, onSelect, disabled = false }: {
 health: BackendHealth | null; hostname: string; value: string; onSelect: (code: string) => void; disabled?: boolean;
}) {
 const enabled = isLocalTestMode(health, hostname);
 const [accounts, setAccounts] = useState<LocalTestAccount[]>([]), [loading, setLoading] = useState(enabled), [error, setError] = useState(''), [attempt, setAttempt] = useState(0);
 useEffect(() => {
  if (!enabled) { setAccounts([]); setError(''); setLoading(false); return; }
  const controller = new AbortController();
  setLoading(true); setError('');
  readLocalTestAccounts(health, hostname, (path, options) => api<AccountResponse>(path, options), controller.signal)
   .then(result => { if (!controller.signal.aborted) setAccounts(result); })
   .catch(failure => { if (!controller.signal.aborted) setError(errorText(failure)); })
   .finally(() => { if (!controller.signal.aborted) setLoading(false); });
  return () => controller.abort();
 }, [enabled, attempt]);
 if (!enabled) return null;
 const roleOrder = ['head', 'manager', 'admin', 'supervisor', 'planner', 'technician'];
 const groups = [...new Set([...roleOrder, ...accounts.map(account => account.role)])];
 return <aside className="local-test-accounts" aria-label="انتخاب حساب آزمایشی">
  <b>نسخهٔ مستقل آزمایشی</b>
  <p>اطلاعات نسخهٔ اصلی تغییر نمی‌کند. برای آزمایش هر نقش، حساب آن را انتخاب کنید و رمز انتخاب‌شده در آماده‌سازی را وارد کنید.</p>
  {error && <div className="alert error" role="alert"><span>{error}</span><button type="button" className="text-button" disabled={disabled || loading} onClick={() => setAttempt(current => current + 1)}>تلاش دوباره</button></div>}
  <label className="field"><span>انتخاب حساب برای تست</span>
   <select value={accounts.some(account => account.code === value) ? value : ''} disabled={disabled || loading || !accounts.length} onChange={event => onSelect(event.target.value)}>
    <option value="">{loading ? 'در حال دریافت حساب‌ها…' : 'یک حساب انتخاب کنید'}</option>
    {groups.map(role => { const group = accounts.filter(account => account.role === role); return group.length ? <optgroup key={role} label={roles[role] || role}>{group.map(account => <option key={account.code} value={account.code}>{account.name} · {account.code}{account.specialty ? ' · ' + account.specialty : ''}</option>)}</optgroup> : null; })}
   </select>
  </label>
  {!loading && !error && !accounts.length && <p>حساب فعالی برای انتخاب وجود ندارد. کد پرسنلی را در کادر زیر وارد کنید.</p>}
 </aside>;
}
