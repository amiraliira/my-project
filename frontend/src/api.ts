import { compatibleBackend } from './compatibility';
import type { BackendHealth } from './compatibility';
let csrf = '';
let sessionGeneration = 0;
export function setCsrf(token: string) { csrf = token || ''; sessionGeneration++; }
export const currentSessionGeneration = () => sessionGeneration;
export class ApiError extends Error { status: number; constructor(message: string, status: number) { super(message); this.name = 'ApiError'; this.status = status; } }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
 if (path === '/inbox' || path.startsWith('/inbox?') || path.startsWith('/orders/') && path.endsWith('/decision') && options.method === 'POST') {
  const health = await api<BackendHealth>('/health', { signal: options.signal, cache: 'no-store' });
  if (!compatibleBackend(health)) {
   window.dispatchEvent(new CustomEvent('pak:backend-incompatible', { detail: health }));
   throw new ApiError('نسخه صفحه و برنامه در حال اجرا سازگار نیستند. برنامه قدیمی را ببندید و نسخه کامل جدید را اجرا کنید.', 426);
  }
 }
 const headers = new Headers(options.headers);
 headers.set('Accept', 'application/json');
 if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
 if (options.method && !['GET', 'HEAD'].includes(options.method)) { headers.set('X-App-Request', '1'); if (csrf) headers.set('X-CSRF-Token', csrf); }
 const generation = sessionGeneration;
 const controller = new AbortController(); let timedOut = false; const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30000); const abort = () => controller.abort(); options.signal?.addEventListener('abort', abort, { once: true }); if (options.signal?.aborted) controller.abort();
 try {
  const response = await fetch('/api' + path, { ...options, signal: controller.signal, headers, credentials: 'same-origin' });
  const text = response.status === 204 || options.method === 'HEAD' ? '' : await response.text();
  let body: any;
  try { body = text ? JSON.parse(text) : undefined; } catch { if (response.ok) throw new ApiError('پاسخ سرور معتبر نیست. صفحه را تازه‌سازی کنید و دوباره تلاش کنید.', response.status); }
  if (!response.ok) {
   const fallback = response.status === 409 ? 'اطلاعات این رکورد تغییر کرده است. تازه‌سازی کنید و تغییرات خود را دوباره اعمال کنید.' : response.status === 401 ? 'نشست شما پایان یافته است. دوباره وارد شوید.' : response.status === 403 ? 'برای این اقدام دسترسی ندارید.' : 'درخواست انجام نشد. دوباره تلاش کنید.';
   if (response.status === 401 && generation === sessionGeneration && !['/auth/me', '/auth/login'].includes(path)) window.dispatchEvent(new Event('pak:unauthorized'));
   throw new ApiError(body?.error || body?.title || fallback, response.status);
  }
  return body as T;
 }
 catch (error) { if (timedOut) throw new ApiError('سرور در ۳۰ ثانیه پاسخ نداد. اتصال را بررسی کنید؛ قبل از ثبت دوباره، وضعیت رکورد را ببینید.', 0); if (error instanceof ApiError || error instanceof DOMException && error.name === 'AbortError') throw error; throw new ApiError('ارتباط با سرور برقرار نشد. اتصال شبکه شرکت را بررسی و دوباره تلاش کنید.', 0); }
 finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
}
export const post = <T,>(path: string, body: unknown, headers?: HeadersInit) => api<T>(path, { method: 'POST', body: JSON.stringify(body), headers });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });
// Raw XLSX bodies keep the same session, CSRF, abort and error handling as JSON.
export const upload = <T,>(path: string, file: Blob, headers: HeadersInit = {}, signal?: AbortSignal) => {
 const uploadHeaders = new Headers(headers);
 uploadHeaders.set('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
 return api<T>(path, { method: 'POST', body: file, headers: uploadHeaders, signal });
};
export async function download(path: string, name: string) {
 // Check access first; let the browser stream the file to disk without a full in-memory Blob.
 await api<void>(path, { method: 'HEAD', cache: 'no-store' });
 const anchor = document.createElement('a'); anchor.href = '/api' + path; anchor.download = name; anchor.hidden = true; document.body.append(anchor); anchor.click(); anchor.remove();
}
