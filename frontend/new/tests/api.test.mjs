import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url), ts = require('typescript');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const compile = file => ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const compatibilityUrl = encode(compile('compatibility.ts'));
const apiUrl = encode(compile('api.ts').replace("'./compatibility'", JSON.stringify(compatibilityUrl)));
const client = await import(apiUrl), compatibility = await import(compatibilityUrl);
const quantity = await import(encode(compile('quantities.ts')));
const events = []; globalThis.window = { dispatchEvent: event => events.push(event.type) };
const health = { status: 'ok', apiVersion: 7, databaseProvider: 'postgresql', databaseVersionNum: 170011, schemaVersion: 10, capabilities: [...compatibility.requiredCapabilities] };

test('new client blocks old list/catalog backend before protected workflows', () => {
 assert.equal(compatibility.compatibleBackend(health), true);
 assert.equal(compatibility.compatibleBackend({ ...health, schemaVersion: 6 }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, schemaVersion: 8 }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, schemaVersion: 9 }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, apiVersion: 3 }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, apiVersion: 5 }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, apiVersion: 6 }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, capabilities: health.capabilities.filter(value => value !== 'approved-parts-consumption') }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, capabilities: health.capabilities.filter(value => value !== 'internal-network-only') }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, capabilities: health.capabilities.filter(value => value !== 'paged-catalogs') }), false);
 for (const name of ['individual-head-grading', 'chief-personnel-privacy', 'pm-change-approval']) assert.equal(compatibility.compatibleBackend({ ...health, capabilities: health.capabilities.filter(value => value !== name) }), false);
 for (const name of ['supervisor-self-work-head-only', 'pm-activity-structure', 'pm-working-pdf', 'parts-xlsx-import', 'pm-responsibility-transfer', 'own-performance', 'supervisor-review-correction', 'supervisor-correction-approve']) {
  assert.ok(compatibility.requiredCapabilities.includes(name));
  assert.equal(compatibility.compatibleBackend({ ...health, capabilities: health.capabilities.filter(value => value !== name) }), false);
 }
});
test('all live clients require an explicit PostgreSQL server version 17 or newer', () => {
 for (const databaseProvider of ['sqlite', undefined, '']) assert.equal(compatibility.compatibleBackend({ ...health, databaseProvider }), false);
 for (const databaseVersionNum of [undefined, 160999, 170000.5, Infinity, NaN]) assert.equal(compatibility.compatibleBackend({ ...health, databaseVersionNum }), false);
 assert.equal(compatibility.compatibleBackend({ ...health, databaseVersionNum: 170000 }), true);
 assert.equal(compatibility.compatibleBackend({ ...health, databaseVersionNum: 180006 }), true);
 assert.equal(compatibility.compatibleBackend({ ...health, capabilities: health.capabilities.filter(value => value !== 'postgresql17-native-backups') }), false);
});

test('authenticated mutation carries CSRF and idempotency headers', async () => {
 client.setCsrf('test-token');
 globalThis.fetch = async (url, options) => { assert.equal(url, '/api/orders'); assert.equal(options.credentials, 'same-origin'); assert.equal(options.headers.get('X-CSRF-Token'), 'test-token'); assert.equal(options.headers.get('X-Idempotency-Key'), 'one-request'); assert.equal(options.headers.get('Content-Type'), 'application/json'); assert.equal(options.body, '{"title":"test"}'); return new Response('{"id":"saved"}'); };
 assert.deepEqual(await client.post('/orders', { title: 'test' }, { 'X-Idempotency-Key': 'one-request' }), { id: 'saved' });
});
test('binary inventory upload keeps XLSX body, same-origin credentials and session protection', async () => {
 client.setCsrf('inventory-session'); const file = new Blob(['synthetic-xlsx-bytes']);
 globalThis.fetch = async (url, options) => {
  assert.equal(url, '/api/parts/import/apply?sheet=Stock'); assert.equal(options.method, 'POST');
  assert.equal(options.body, file); assert.equal(options.credentials, 'same-origin');
  assert.equal(options.headers.get('Content-Type'), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  assert.equal(options.headers.get('X-App-Request'), '1'); assert.equal(options.headers.get('X-CSRF-Token'), 'inventory-session');
  assert.equal(options.headers.get('X-Import-Fingerprint'), 'reviewed-snapshot');
  return new Response('{"inserted":2,"updated":3,"unchanged":1}');
 };
 assert.deepEqual(await client.upload('/parts/import/apply?sheet=Stock', file, { 'X-Import-Fingerprint': 'reviewed-snapshot' }), { inserted: 2, updated: 3, unchanged: 1 });
});
test('late 401 from previous session cannot sign out a later session', async () => {
 events.length = 0; let finish;
 globalThis.fetch = () => new Promise(resolve => { finish = resolve; });
 const pending = client.api('/orders'); client.setCsrf('next-token'); finish(new Response('{"error":"expired"}', { status: 401 }));
 await assert.rejects(pending, error => error.status === 401); assert.deepEqual(events, []);
 globalThis.fetch = async () => new Response('{}', { status: 401 });
 await assert.rejects(client.api('/orders'), error => error.status === 401); assert.deepEqual(events, ['pak:unauthorized']);
});
test('malformed successful body produces a usable error and abort stays cancellable', async () => {
 globalThis.fetch = async () => new Response('<html>wrong route</html>');
 await assert.rejects(client.api('/orders'), error => error instanceof client.ApiError && error.status === 200);
 globalThis.fetch = async (_url, options) => { if (options.signal.aborted) throw new DOMException('cancelled', 'AbortError'); return new Response('{}'); };
 const abort = new AbortController(); abort.abort();
 await assert.rejects(client.api('/orders', { signal: abort.signal }), error => error.name === 'AbortError');
});
test('deadline covers response body, not only response headers', async () => {
 const nativeTimeout = globalThis.setTimeout; globalThis.setTimeout = (callback, delay, ...args) => nativeTimeout(callback, delay === 30000 ? 5 : delay, ...args);
 try {
  globalThis.fetch = async (_url, options) => ({ ok: true, status: 200, text: () => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })) });
  await assert.rejects(client.api('/orders'), error => error instanceof client.ApiError && error.status === 0 && error.message.includes('۳۰'));
 } finally { globalThis.setTimeout = nativeTimeout; }
});
test('large download uses authorized HEAD then browser navigation without a Blob', async () => {
 const calls = []; let clicked = false, removed = false;
 globalThis.fetch = async (url, options) => { calls.push([url, options.method]); return new Response(null, { status: 204 }); };
 const anchor = { click() { clicked = true; }, remove() { removed = true; } }; globalThis.document = { createElement: tag => { assert.equal(tag, 'a'); return anchor; }, body: { append: item => assert.equal(item, anchor) } };
 await client.download('/reports/works.csv', 'works.csv'); assert.deepEqual(calls, [['/api/reports/works.csv', 'HEAD']]); assert.equal(anchor.href, '/api/reports/works.csv'); assert.equal(anchor.download, 'works.csv'); assert.equal(clicked, true); assert.equal(removed, true);
 clicked = false; globalThis.fetch = async () => new Response(null, { status: 403 }); await assert.rejects(client.download('/reports/works.csv', 'works.csv'), error => error.status === 403); assert.equal(clicked, false);
});
test('part additions use six-place fixed units and reject nonrepresentable quantities', () => {
 assert.equal(quantity.addQuantity(0.1, 0.2), 0.3);
 assert.equal(quantity.addQuantity(0.000001, 0.000002), 0.000003);
 assert.equal(quantity.validQuantity(1.234567), true);
 assert.equal(quantity.validQuantity(0.0000001), false);
 assert.equal(quantity.validQuantity(Number.NaN), false);
 assert.equal(quantity.validQuantity(Number.POSITIVE_INFINITY), false);
 assert.equal(quantity.validQuantity(1_000_000_000), true);
 assert.equal(quantity.validQuantity(-0.5, true), true);
 assert.equal(quantity.validQuantity(0, true), false);
 assert.equal(quantity.addQuantity(1_000_000_000, 0.000001), null);
});
