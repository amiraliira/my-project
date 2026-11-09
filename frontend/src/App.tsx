import { Component, lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api, ApiError, currentSessionGeneration, post, setCsrf } from './api';
import type { Bootstrap, User } from './types';
import Brand from './Brand';
import CompatibilityPage from './CompatibilityPage';
import LocalTestSetup, { LocalTestPasswordSetup, needsLocalTestPassword } from './LocalTestSetup';
import { candidateRelease, startWithCompatibleBackend } from './compatibility';
import { installHashNavigation } from './hashNavigation';
import type { BackendHealth } from './compatibility';
import { ErrorBox, Icon, Loading, dateFa, errorText, roles, todayIso } from './ui';

const PmLibrary = lazy(() => import('./PmWorkspace'));
const OeeWorkspace = lazy(() => import('./OeeWorkspace'));
const Orders = lazy(() => import('./Orders'));
const TechnicianRegistration = lazy(() => import('./TechnicianRegistration'));
const ManagementHome = lazy(() => import('./ManagementHome'));
const Audit = lazy(() => import('./Insights').then(module => ({ default: module.Audit })));
const ChangePassword = lazy(() => import('./Insights').then(module => ({ default: module.ChangePassword })));
const DashboardPage = lazy(() => import('./Insights').then(module => ({ default: module.DashboardPage })));
const Imports = lazy(() => import('./Insights').then(module => ({ default: module.Imports })));
const Reports = lazy(() => import('./Insights').then(module => ({ default: module.Reports })));
const SettingsPage = lazy(() => import('./Insights').then(module => ({ default: module.SettingsPage })));
const Assets = lazy(() => import('./MasterData').then(module => ({ default: module.Assets })));
const PartsConsumption = lazy(() => import('./MasterData').then(module => ({ default: module.PartsConsumption })));
const Plans = lazy(() => import('./MasterData').then(module => ({ default: module.Plans })));
const Users = lazy(() => import('./MasterData').then(module => ({ default: module.Users })));
class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
 state = { failed: false };
 static getDerivedStateFromError() { return { failed: true }; }
 render() { return this.state.failed ? <ErrorBox text="این صفحه بارگیری نشد. اتصال به شبکهٔ شرکت را بررسی و صفحه را تازه کنید." retry={() => window.location.reload()}/> : this.props.children; }
}

const navigation = [{ id: 'inbox', title: 'کارتابل و گزارش‌های واحد فنی', icon: 'orders' }, { id: 'dashboard', title: 'نمای کلی', icon: 'dashboard' }, { id: 'orders', title: 'دستورکارها', icon: 'orders' }, { id: 'assets', title: 'تجهیزات و شناسنامه', icon: 'assets' }, { id: 'stock', title: 'قطعات و مصرف', icon: 'stock' }, { id: 'pm-library', title: 'برگه‌ها و تقویم PM', icon: 'plans' }, { id: 'plans', title: 'برنامه پیشگیرانه', icon: 'plans' }, { id: 'reports', title: 'گزارش‌ها', icon: 'reports' }, { id: 'imports', title: 'آرشیو اطلاعات قبلی', icon: 'imports' }, { id: 'audit', title: 'رویدادها و ممیزی', icon: 'audit' }, { id: 'users', title: 'کاربران و دسترسی‌ها', icon: 'users' }, { id: 'settings', title: 'تنظیمات سامانه', icon: 'settings' }];
navigation.push({id:'oee',title:'کارکرد خطوط و OEE',icon:'reports'});
function allowed(id: string, role: string) { if (role === 'technician') return false; if(id==='oee')return ['head','planner'].includes(role); if(id==='pm-library')return ['head','manager','planner','supervisor','admin'].includes(role); if(id==='plans')return role==='head'; return id === 'users' ? ['head','admin'].includes(role) : ['audit', 'imports'].includes(id) ? ['admin', 'head'].includes(role) : true; }
function roleNavigation(role: string) {
 const priority: Record<string, string[]> = {
  admin: ['inbox', 'pm-library', 'users', 'imports', 'audit', 'settings', 'reports', 'orders', 'assets', 'stock', 'plans', 'dashboard'],
  supervisor: ['inbox', 'pm-library', 'orders', 'reports', 'dashboard', 'assets', 'stock', 'settings'],
  planner: ['inbox', 'pm-library', 'oee', 'orders', 'reports', 'dashboard', 'assets', 'stock', 'settings'],
  head: ['inbox', 'pm-library', 'oee', 'orders', 'reports', 'dashboard', 'assets', 'stock', 'users', 'imports', 'audit', 'settings'],
  manager: ['inbox', 'pm-library', 'orders', 'reports', 'dashboard', 'assets', 'stock', 'settings']
 };
 return (priority[role] || []).map(id => navigation.find(item => item.id === id)!).filter(item => allowed(item.id, role) && item.id !== 'dashboard');
}
function ReleaseNotice({ health }: { health: BackendHealth | null }) { return candidateRelease(health) ? <span className="release-notice">نسخه آزمایشی · در حال ارزیابی</span> : null; }
function Login({ loggedIn, initialError, health, prepared }: { loggedIn: (user: User, csrf: string) => void; initialError: string; health: BackendHealth | null; prepared: () => void }) {
 const [code, setCode] = useState(''), [password, setPassword] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(initialError);
 const firstPassword = needsLocalTestPassword(health, window.location.hostname);
 useEffect(() => { setError(initialError); }, [initialError]);
 return <main className="login-page pak-login"><div className="pak-login-layout">
  <section className="login-identity" aria-labelledby="login-company-title">
   <img className="pak-logo" src="/pak-logo.png" width={144} height={144} alt="لوگوی شرکت لبنیات پاک"/>
   <div className="login-identity-text"><h1 id="login-company-title">شرکت لبنیات پاک</h1><p>سیستم نگهداری و تعمیرات</p></div>
  </section>
  <section className="login-form" aria-labelledby="login-heading"><h2 id="login-heading">ورود به حساب کاربری</h2><p className="login-intro">{firstPassword ? 'برای اولین ورود، یک رمز آزمایشی انتخاب کنید.' : 'کد پرسنلی و رمز عبور خود را وارد کنید.'}</p><ReleaseNotice health={health}/>{firstPassword ? <LocalTestPasswordSetup prepared={prepared}/> : <form onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setError(''); try { const result = await post<{ user: User; csrfToken: string }>('/auth/login', { code: code.trim(), password }); setPassword(''); loggedIn(result.user, result.csrfToken); } catch (x) { setError(errorText(x)); } finally { setBusy(false); } }}><ErrorBox text={error}/><LocalTestSetup health={health} hostname={window.location.hostname} value={code} onSelect={setCode} disabled={busy}/><label className="field"><span>کد پرسنلی</span><input autoComplete="username" inputMode="numeric" dir="ltr" required value={code} onChange={e => setCode(e.target.value)} placeholder="کد پرسنلی شما"/></label><label className="field"><span>رمز عبور</span><input type="password" autoComplete="current-password" dir="ltr" required value={password} onChange={e => setPassword(e.target.value)} placeholder="رمز عبور"/></label><button className="button primary login-submit" disabled={busy}>{busy ? 'در حال ورود…' : 'ورود به سامانه'}<Icon name="arrow" size={18}/></button></form>}</section>
 </div></main>;
}

export default function App() {
 const bootstrapRequest = useRef<AbortController | null>(null);
 const hashNavigation = useRef<ReturnType<typeof installHashNavigation> | null>(null);
 const [backendState, setBackendState] = useState<'checking' | 'compatible' | 'incompatible' | 'unreachable'>('checking'), [backendHealth, setBackendHealth] = useState<BackendHealth | null>(null), [backendError, setBackendError] = useState(''), [startupAttempt, setStartupAttempt] = useState(0);
 const [user, setUser] = useState<User | null>(null), [authLoading, setAuthLoading] = useState(true), [authError, setAuthError] = useState(''), [boot, setBoot] = useState<Bootstrap | null>(null), [bootError, setBootError] = useState(''), [bootLoading, setBootLoading] = useState(false), [page, setPage] = useState(window.location.hash.slice(1) || 'inbox'), [, setMenu] = useState(false), [passwordOpen, setPasswordOpen] = useState(false), [accountOpen, setAccountOpen] = useState(false), [online, setOnline] = useState(navigator.onLine), [toast, setToast] = useState(''), [logoutBusy, setLogoutBusy] = useState(false), [shellError, setShellError] = useState('');
 useEffect(() => {
  hashNavigation.current = installHashNavigation(setPage);
  const connection = () => setOnline(navigator.onLine), expired = (event: Event) => { bootstrapRequest.current?.abort(); setUser(null); setBoot(null); setCsrf(''); setPasswordOpen(false); setAccountOpen(false); setAuthError(event.type === 'pak:password-changed' ? 'رمز عبور تغییر کرد. با رمز جدید وارد شوید.' : 'نشست شما پایان یافته است. دوباره وارد شوید.'); };
  const incompatible = (event: Event) => { setBackendHealth((event as CustomEvent<BackendHealth>).detail); setBackendError(''); setBackendState('incompatible'); setAuthLoading(false); };
  window.addEventListener('online', connection); window.addEventListener('offline', connection); window.addEventListener('pak:unauthorized', expired); window.addEventListener('pak:password-changed', expired); window.addEventListener('pak:backend-incompatible', incompatible);
  return () => { hashNavigation.current?.dispose(); hashNavigation.current = null; window.removeEventListener('online', connection); window.removeEventListener('offline', connection); window.removeEventListener('pak:unauthorized', expired); window.removeEventListener('pak:password-changed', expired); window.removeEventListener('pak:backend-incompatible', incompatible); };
 }, []);
 useEffect(() => {
  const controller = new AbortController(); let cancelled = false;
  setBackendState('checking'); setBackendHealth(null); setBackendError(''); setAuthLoading(true); setAuthError(''); setUser(null); setBoot(null); setCsrf('');
  startWithCompatibleBackend(() => api<BackendHealth>('/health', { cache: 'no-store', signal: controller.signal }), async health => {
   if (cancelled) return;
   setBackendHealth(health); setBackendState('compatible');
   try { const result = await api<{ user: User; csrfToken: string }>('/auth/me', { signal: controller.signal }); if (!cancelled) { setCsrf(result.csrfToken); setUser(result.user); } }
   catch (error) { if (!cancelled && !(error instanceof ApiError && error.status === 401)) setAuthError(errorText(error)); }
  }).then(result => { if (!cancelled && !result.compatible) { setBackendHealth(result.health); setBackendState('incompatible'); } }).catch(error => { if (!cancelled) { setBackendState('unreachable'); setBackendError(errorText(error)); } }).finally(() => { if (!cancelled) setAuthLoading(false); });
  return () => { cancelled = true; controller.abort(); };
 }, [startupAttempt]);
 const refresh = useCallback(async () => { bootstrapRequest.current?.abort(); const controller = new AbortController(); bootstrapRequest.current = controller; const generation = currentSessionGeneration(); setBootError(''); try { const result = await api<Bootstrap>('/bootstrap?catalogMode=paged', { signal: controller.signal }); if (!controller.signal.aborted && generation === currentSessionGeneration()) setBoot(result); } catch (e) { if (!controller.signal.aborted && generation === currentSessionGeneration()) setBootError(errorText(e)); throw e; } }, []);
 useEffect(() => { if (!user || backendState !== 'compatible') return; let cancelled = false; setBootLoading(true); refresh().catch(() => {}).finally(() => { if (!cancelled) setBootLoading(false); }); return () => { cancelled = true; bootstrapRequest.current?.abort(); }; }, [user?.id, backendState, refresh]);
 useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(''), 4200); return () => clearTimeout(id); }, [toast]);
 const notify = useCallback((message: string) => setToast(message), []), closePassword = useCallback(() => setPasswordOpen(false), []);
 useEffect(() => {
  if (user?.role !== 'technician') return;
  setMenu(false); setAccountOpen(false);
  if (window.location.hash !== '#registration') {
   hashNavigation.current?.replace('registration');
  }
 }, [user?.role, page]);

 useEffect(() => {
  if (!user || user.role === 'technician') return;
  const valid = navigation.some(item => item.id === page) && allowed(page, user.role);
  if (!valid || !window.location.hash) {
   hashNavigation.current?.replace('inbox');
  }
 }, [user?.role, page]);
 function navigate(id: string) { hashNavigation.current?.navigate(id); setMenu(false); }
 if (backendState !== 'compatible') return <CompatibilityPage state={backendState} health={backendHealth} error={backendError} retry={() => setStartupAttempt(value => value + 1)}/>;
 if (authLoading) return <div className="app-start flex min-h-dvh flex-col items-center justify-center gap-6 bg-pak-background text-pak-text"><Brand/><Loading/></div>;
 if (!user) return <Login health={backendHealth} initialError={authError} prepared={() => setStartupAttempt(current => current + 1)} loggedIn={(u, token) => { setCsrf(token); setUser(u); setAuthError(''); setBoot(null); hashNavigation.current?.replace(u.role === 'technician' ? 'registration' : 'inbox'); }}/>;
 const currentPage = navigation.some(n => n.id === page) && allowed(page, user.role) ? page : 'inbox'; const context = boot ? { user, boot, refresh, notify } : null;
 const render = () => { if (!context) return <div className="panel"><ErrorBox text={bootError} retry={() => { setBootLoading(true); refresh().catch(() => {}).finally(() => setBootLoading(false)); }}/>{bootLoading && <Loading/>}</div>; switch (currentPage) { case 'inbox': return <ManagementHome {...context} navigate={navigate}/>; case 'orders': return <Orders {...context}/>; case 'assets': return <Assets {...context}/>; case 'stock': return <PartsConsumption {...context}/>; case 'pm-library': return <PmLibrary {...context}/>; case 'oee': return <OeeWorkspace {...context}/>; case 'plans': return <Plans {...context}/>; case 'reports': return <Reports {...context}/>; case 'imports': return <Imports {...context}/>; case 'audit': return <Audit/>; case 'users': return <Users {...context}/>; case 'settings': return <SettingsPage {...context}/>; default: return <DashboardPage navigate={navigate} user={user}/>; } };
 if (user.role === 'technician') return <div className="registration-shell">
  <header className="registration-header"><div className="registration-brand"><Brand compact/></div><div className="registration-account"><span>{user.name}</span><button className="button ghost small" onClick={() => setPasswordOpen(true)}><Icon name="lock" size={16}/>تغییر رمز عبور</button><button className="button ghost small" disabled={logoutBusy} onClick={async () => { setLogoutBusy(true); setShellError(''); try { await post('/auth/logout', {}); setUser(null); setBoot(null); setCsrf(''); } catch (e) { setShellError(errorText(e)); } finally { setLogoutBusy(false); } }}><Icon name="logout" size={16}/>{logoutBusy ? 'خروج…' : 'خروج'}</button></div></header>
  <main className="registration-content">{!online && <div className="alert warning"><Icon name="warning"/>ارتباط دستگاه قطع است؛ برای دریافت و ثبت کار به شبکه شرکت متصل شوید.</div>}<ErrorBox text={shellError}/>{boot && bootError && <ErrorBox text={bootError} retry={() => { refresh().catch(() => {}); }}/>}{context ? <PageBoundary><Suspense fallback={<Loading/>}><TechnicianRegistration {...context}/></Suspense></PageBoundary> : <div className="registration-card"><ErrorBox text={bootError} retry={() => { setBootLoading(true); refresh().catch(() => {}).finally(() => setBootLoading(false)); }}/>{bootLoading && <Loading/>}</div>}</main>
  <footer className="registration-footer"><span>شرکت لبنیات پاک · واحد فنی</span><ReleaseNotice health={backendHealth}/></footer>{passwordOpen && <Suspense fallback={<Loading/>}><ChangePassword close={closePassword} notify={notify}/></Suspense>} {toast && <div className="toast" role="status"><Icon name="check"/>{toast}<button className="icon-button" aria-label="بستن پیام" onClick={() => setToast('')}><Icon name="close" size={16}/></button></div>}
 </div>;
 return <div className="management-shell min-h-dvh bg-pak-background text-pak-text">
  <header className="management-header"><button className="management-brand" onClick={() => navigate('inbox')}><Brand compact/></button><div className="management-header-left"><span className="management-header-date">{dateFa(todayIso())}</span><div className="account-wrap"><button className="account-button" onClick={() => setAccountOpen(!accountOpen)} aria-expanded={accountOpen}><span className="avatar small-avatar">{user.name?.slice(0, 1)}</span><span>{user.name}</span><span>⌄</span></button>{accountOpen && <div className="account-menu"><b>{roles[user.role]}{user.specialty ? ' · ' + user.specialty : ''} · {user.code}</b><button onClick={() => { setAccountOpen(false); setPasswordOpen(true); }}><Icon name="lock" size={17}/>تغییر رمز عبور</button><button disabled={logoutBusy} onClick={async () => { setLogoutBusy(true); setShellError(''); try { await post('/auth/logout', {}); setUser(null); setBoot(null); setCsrf(''); setAccountOpen(false); } catch (e) { setShellError(errorText(e)); } finally { setLogoutBusy(false); } }}><Icon name="logout" size={17}/>{logoutBusy ? 'خروج…' : 'خروج از حساب'}</button></div>}</div></div></header>
  <div className="management-layout"><nav className="management-navigation" aria-label="بخش‌های سامانه">{roleNavigation(user.role).map(item => <button key={item.id} className={currentPage === item.id ? 'active' : ''} aria-current={currentPage === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><Icon name={item.icon} size={17}/>{item.id === 'inbox' ? user.role === 'admin' ? 'مدیریت سامانه' : 'نمای مدیریت' : item.title}</button>)}</nav>
  <main className="management-content">{!online && <div className="alert warning"><Icon name="warning"/>ارتباط دستگاه قطع است؛ برای دریافت گزارش تازه و ثبت تصمیم به شبکه شرکت متصل شوید.</div>}<ErrorBox text={shellError}/>{boot && bootError && <ErrorBox text={bootError} retry={() => { refresh().catch(() => {}); }}/>}<PageBoundary key={currentPage}><Suspense fallback={<Loading/>}>{render()}</Suspense></PageBoundary></main></div>
  <footer className="management-footer"><span>شرکت لبنیات پاک · واحد فنی</span><ReleaseNotice health={backendHealth}/><span>{roles[user.role]}{user.specialty ? ' · ' + user.specialty : ''}</span></footer>{passwordOpen && <Suspense fallback={<Loading/>}><ChangePassword close={closePassword} notify={notify}/></Suspense>} {toast && <div className="toast" role="status"><Icon name="check"/>{toast}<button className="icon-button" aria-label="بستن پیام" onClick={() => setToast('')}><Icon name="close" size={16}/></button></div>}
 </div>;
}

