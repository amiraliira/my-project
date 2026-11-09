import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url), ts = require('typescript');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const resolve = name => pathToFileURL(require.resolve(name)).href;
// A small deterministic React lifecycle: actual hook code still owns all
// request, cache, visibility, cancellation and session decisions under test.
const hooksUrl = encode(`
 let slots=[],cursor=0,pending=new Map(),dirty=false;
 const equal=(a,b)=>Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index]));
 export const reset=()=>{slots=[];cursor=0;pending=new Map();dirty=false;};
 export const begin=()=>{cursor=0;dirty=false;};
 export const changed=()=>dirty;
 export function useState(initial){
  const index=cursor++;if(!slots[index])slots[index]={value:typeof initial==='function'?initial():initial};
  return[slots[index].value,next=>{const value=typeof next==='function'?next(slots[index].value):next;if(!Object.is(value,slots[index].value)){slots[index].value=value;dirty=true;}}];
 }
 export function useRef(initial){const index=cursor++;return slots[index]||(slots[index]={current:initial});}
 export function useEffect(effect,deps){const index=cursor++;if(!slots[index]||!equal(slots[index].deps,deps))pending.set(index,{effect,deps});}
 export function useMemo(make,deps){const index=cursor++;if(!slots[index]||!equal(slots[index].deps,deps))slots[index]={deps,value:make()};return slots[index].value;}
 export const useCallback=(callback,deps)=>useMemo(()=>callback,deps);
 export function commit(){const effects=[...pending];pending.clear();for(const[index,next]of effects){slots[index]?.cleanup?.();slots[index]={deps:next.deps,cleanup:next.effect()};}}
 export function cleanup(){for(const slot of slots)slot?.cleanup?.();pending.clear();}
`);
const apiUrl = encode(`
 let generation=0,calls=[];
 export const reset=()=>{generation=0;calls=[];};
 export const currentSessionGeneration=()=>generation;
 export const nextSession=()=>++generation;
 export const requests=()=>calls;
 // Deliberately permit a response after abort, to prove the hook itself
 // rejects late responses even when the transport ignores cancellation.
 export const api=(path,options={})=>new Promise((resolve,reject)=>calls.push({path,signal:options.signal,resolve,reject}));
`);
const hooks = await import(hooksUrl), api = await import(apiUrl);
let source = ts.transpileModule(readFileSync(new URL('../src/ui.tsx', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText;
for (const [name, url] of Object.entries({ react: hooksUrl, 'react/jsx-runtime': resolve('react/jsx-runtime'), 'react-dom': encode('export const createPortal=children=>children;'), './api': apiUrl })) source = source.replaceAll(JSON.stringify(name), JSON.stringify(url)).replaceAll("'" + name + "'", JSON.stringify(url));
const { useResource } = await import(encode(source));

class Target {
 listeners = new Map();
 addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(callback); }
 removeEventListener(type, callback) { this.listeners.get(type)?.delete(callback); }
 emit(type) { for (const listener of [...(this.listeners.get(type) || [])]) listener({ type }); }
 count() { return [...this.listeners.values()].reduce((sum, listeners) => sum + listeners.size, 0); }
}

function environment() {
 const original = new Map(['window', 'document', 'navigator'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
 const originalNow = Object.getOwnPropertyDescriptor(Date, 'now');
 const win = new Target(), doc = Object.assign(new Target(), { visibilityState: 'visible' }), nav = { onLine: true };
 const timers = new Map(); let id = 0, now = 10000;
 win.setInterval = (callback, delay) => { timers.set(++id, { callback, delay }); return id; };
 win.clearInterval = timer => timers.delete(timer);
 for (const [name, value] of Object.entries({ window: win, document: doc, navigator: nav })) Object.defineProperty(globalThis, name, { configurable: true, value });
 Object.defineProperty(Date, 'now', { configurable: true, value: () => now });
 return {
  win, doc, nav, timers,
  advance(milliseconds = 1000) { now += milliseconds; },
  tick() { for (const { callback } of [...timers.values()]) callback(); },
  restore() {
   for (const [name, descriptor] of original) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; }
   Object.defineProperty(Date, 'now', originalNow);
  },
 };
}

function harness(t, options = {}) {
 const env = environment(); hooks.reset(); api.reset();
 const props = { path: '/records?a=1', initial: [], pollMs: 30000, enabled: true, ...options };
 let latest, mounted = true;
 function render() {
  assert.equal(mounted, true, 'cannot render an unmounted hook');
  let iterations = 0;
  do {
   hooks.begin(); latest = useResource(props.path, props.initial, props.pollMs, props.enabled); hooks.commit();
   assert.ok(++iterations < 25, 'hook entered a render/effect loop');
  } while (hooks.changed());
  return latest;
 }
 const view = {
  env, props, render, get value() { return latest; },
  async flush() { await new Promise(resolve => setImmediate(resolve)); return render(); },
  unmount() { if (mounted) { hooks.cleanup(); mounted = false; } },
 };
 t.after(() => { view.unmount(); env.restore(); }); render(); return view;
}

test('resource keeps loaded data during same-key refresh and rerenders do not restart requests', async t => {
 const view = harness(t);
 assert.equal(view.value.loading, true); assert.equal(view.value.refreshing, false); assert.deepEqual(view.value.data, []);
 assert.equal(api.requests().length, 1);
 api.requests()[0].resolve([{ id: 'first' }]); await view.flush();
 assert.equal(view.value.loading, false); assert.equal(view.value.loadedPath, '/records?a=1'); assert.deepEqual(view.value.data, [{ id: 'first' }]);
 for (let index = 0; index < 8; index++) { view.props.initial = []; view.render(); }
 assert.equal(api.requests().length, 1, 'new initial-value identities must not create a fetch loop');
 view.value.reload(); view.render();
 assert.equal(view.value.loading, false); assert.equal(view.value.refreshing, true); assert.deepEqual(view.value.data, [{ id: 'first' }]);
 api.requests()[1].resolve([{ id: 'second' }]); await view.flush();
 assert.equal(view.value.refreshing, false); assert.deepEqual(view.value.data, [{ id: 'second' }]);
});

test('many manual reloads coalesce into one request and at most one trailing request', async t => {
 const view = harness(t);
 for (let index = 0; index < 20; index++) view.value.reload();
 assert.equal(api.requests().length, 1);
 api.requests()[0].resolve(['first']); await view.flush();
 assert.equal(api.requests().length, 2); assert.deepEqual(view.value.data, ['first']); assert.equal(view.value.refreshing, true);
 api.requests()[1].resolve(['latest']); await view.flush();
 assert.equal(api.requests().length, 2); assert.deepEqual(view.value.data, ['latest']); assert.equal(view.value.refreshing, false);
});

test('query changes abort the old request, hide old cached data and ignore its late response', async t => {
 const view = harness(t);
 api.requests()[0].resolve(['old query']); await view.flush();
 view.value.reload(); view.render(); const stale = api.requests()[1];
 view.props.path = '/records?a=2'; view.render();
 assert.equal(stale.signal.aborted, true); assert.deepEqual(view.value.data, []); assert.equal(view.value.loading, true); assert.equal(view.value.loadedPath, '');
 assert.equal(api.requests().length, 3);
 api.requests()[2].resolve(['new query']); await view.flush();
 stale.resolve(['late private old query']); await view.flush();
 assert.deepEqual(view.value.data, ['new query']); assert.equal(view.value.loadedPath, '/records?a=2'); assert.equal(api.requests().length, 3);
});

test('background polling skips hidden or offline pages and removes timers and listeners on unmount', async t => {
 const view = harness(t);
 api.requests()[0].resolve(['loaded']); await view.flush();
 assert.equal(view.env.timers.size, 1); assert.ok(view.env.win.count() > 0); assert.ok(view.env.doc.count() > 0);
 view.env.advance(30000); view.env.doc.visibilityState = 'hidden'; view.env.tick(); view.env.win.emit('focus');
 assert.equal(api.requests().length, 1);
 view.env.doc.visibilityState = 'visible'; view.env.nav.onLine = false; view.env.tick(); view.env.win.emit('online');
 assert.equal(api.requests().length, 1);
 view.env.nav.onLine = true; view.env.doc.emit('visibilitychange'); view.env.win.emit('focus'); view.env.tick();
 assert.equal(api.requests().length, 2, 'visibility/focus/timer burst must share the in-flight request');
 const pending = api.requests()[1]; view.unmount();
 assert.equal(pending.signal.aborted, true); assert.equal(view.env.timers.size, 0); assert.equal(view.env.win.count(), 0); assert.equal(view.env.doc.count(), 0);
 pending.resolve(['late after unmount']); await new Promise(resolve => setImmediate(resolve));
 assert.equal(api.requests().length, 2);
});

test('a new session cannot display cached data or late responses from the previous generation', async t => {
 const view = harness(t);
 api.requests()[0].resolve(['previous user']); await view.flush();
 view.value.reload(); view.render(); const previousRequest = api.requests()[1];
 api.nextSession(); view.render();
 assert.equal(previousRequest.signal.aborted, true); assert.equal(view.value.loading, true); assert.deepEqual(view.value.data, []);
 previousRequest.resolve(['previous user late']); await view.flush(); assert.deepEqual(view.value.data, []);
 api.requests()[2].resolve(['current user']); await view.flush(); assert.deepEqual(view.value.data, ['current user']);
});

test('forbidden refresh clears cached data and session invalidation prevents further polling', async t => {
 const view = harness(t);
 api.requests()[0].resolve(['previously permitted']); await view.flush();
 view.value.reload(); view.render();
 api.requests()[1].reject(Object.assign(new Error('دسترسی لغو شد'), { status: 403 })); await view.flush();
 assert.deepEqual(view.value.data, []); assert.equal(view.value.loadedPath, ''); assert.equal(view.value.loading, false); assert.equal(view.value.error, 'دسترسی لغو شد');
 view.value.reload(); view.render(); const invalidated = api.requests()[2];
 view.env.win.emit('pak:unauthorized'); view.render();
 assert.equal(invalidated.signal.aborted, true); assert.deepEqual(view.value.data, []);
 view.env.advance(30000); view.env.tick(); view.env.win.emit('focus');
 assert.equal(api.requests().length, 3);
 invalidated.resolve(['forbidden late data']); await view.flush(); assert.deepEqual(view.value.data, []);
});

test('disabled resources make no requests and disabling cancels their active work', async t => {
 const view = harness(t, { enabled: false });
 assert.equal(api.requests().length, 0); assert.equal(view.value.loading, false); assert.equal(view.env.timers.size, 0);
 view.props.enabled = true; view.render(); assert.equal(api.requests().length, 1);
 const pending = api.requests()[0]; view.props.enabled = false; view.render();
 assert.equal(pending.signal.aborted, true); assert.equal(view.value.loading, false); assert.equal(view.value.refreshing, false); assert.deepEqual(view.value.data, []);
 assert.equal(view.env.timers.size, 0); assert.equal(view.env.win.count(), 0); assert.equal(view.env.doc.count(), 0);
 pending.resolve(['late disabled value']); await view.flush(); assert.deepEqual(view.value.data, []);
});
