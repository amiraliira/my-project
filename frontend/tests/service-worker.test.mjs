import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

function worker() {
 const listeners = new Map(), fetched = [], cached = [];
 const cache = { put: async (key, response) => cached.push([key, response]), addAll: async () => {} };
 runInNewContext(readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8'), {
  URL, Response,
  self: { location: { origin: 'https://pak.internal' }, addEventListener: (name, handler) => listeners.set(name, handler), skipWaiting() {}, clients: { claim() {} } },
  caches: { open: async () => cache, match: async () => undefined },
  fetch: async request => { fetched.push(request.url); return new Response('static'); },
 });
 return { listener: listeners.get('fetch'), fetched, cached };
}

test('offline worker leaves API responses, mutations and other origins uncached', () => {
 const instance = worker();
 for (const [url, method] of [['https://pak.internal/api/health', 'GET'], ['https://pak.internal/api/orders?page=1', 'GET'], ['https://pak.internal/assets/main.js', 'POST'], ['https://other.invalid/assets/main.js', 'GET']]) {
  instance.listener({ request: { url, method, mode: 'cors' }, respondWith() { assert.fail('sensitive request must stay outside static cache'); } });
 }
 assert.equal(instance.fetched.length, 0);
 assert.equal(instance.cached.length, 0);
});

test('offline worker caches only successful same-origin static files', async () => {
 const instance = worker();
 let response;
 instance.listener({ request: { url: 'https://pak.internal/assets/main-hash.js', method: 'GET', mode: 'cors' }, respondWith(value) { response = value; } });
 assert.equal(await (await response).text(), 'static');
 assert.deepEqual(instance.fetched, ['https://pak.internal/assets/main-hash.js']);
 assert.equal(instance.cached.length, 1);
 assert.equal(instance.cached[0][0].url, 'https://pak.internal/assets/main-hash.js');
});
