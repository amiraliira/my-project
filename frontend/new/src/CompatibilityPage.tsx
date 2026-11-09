import Brand from './Brand';
import type { BackendHealth } from './compatibility';
import { clientVersion } from './compatibility';
import { Icon, Loading } from './ui';

export default function CompatibilityPage({ state, health, error, retry }: { state: 'checking' | 'incompatible' | 'unreachable'; health: BackendHealth | null; error: string; retry: () => void }) {
 return <main className="compatibility-page"><section className="compatibility-card">
  <div className="compatibility-brand"><Brand compact/></div>
  {state === 'checking' ? <><h1>در حال بررسی نسخه برنامه…</h1><p>پیش از ورود، سازگاری صفحه و برنامه در حال اجرا بررسی می‌شود.</p><Loading/></> : <>
   <div className="compatibility-icon"><Icon name="warning" size={27}/></div>
   <h1>{state === 'incompatible' ? 'نسخه صفحه و برنامه با هم سازگار نیستند' : 'ارتباط با برنامه برقرار نشد'}</h1>
   <p>{state === 'incompatible' ? 'صفحه را تازه کنید. اگر پیام باقی ماند، از مسئول سامانه بخواهید صفحه و برنامهٔ سرور را از یک بستهٔ یکسان اجرا کند.' : error || 'اتصال به شبکهٔ شرکت و اجرای برنامه روی سرور را بررسی کنید. سپس دوباره تلاش کنید.'}</p>
   <dl className="compatibility-versions"><div><dt>نسخه این صفحه</dt><dd dir="ltr">{clientVersion}</dd></div><div><dt>نسخه برنامه در حال اجرا</dt><dd dir="ltr">{health?.version || 'نامشخص'}</dd></div></dl>
   <button className="button primary" onClick={retry}><Icon name="refresh" size={17}/>بررسی دوباره</button>
   <small className="compatibility-footer">برای تازه‌سازی کامل صفحه در رایانه از <span dir="ltr">Ctrl + F5</span> استفاده کنید.</small>
  </>}
 </section></main>;
}
