import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url), ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../src/latestRequest.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { createLatestRequest } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

test('late first selection cannot replace the second even when transport ignores cancellation', async () => {
 const request = createLatestRequest(); let finishFirst, firstSignal;
 const first = request.run(signal => { firstSignal = signal; return new Promise(resolve => { finishFirst = resolve; }); });
 const second = request.run(async () => ({ id: 'second' }));
 assert.equal(firstSignal.aborted, true);
 assert.deepEqual(await second, { current: true, value: { id: 'second' } });
 finishFirst({ id: 'first' });
 assert.deepEqual(await first, { current: false });
});

test('cancelled selection suppresses stale failures while the current failure remains visible', async () => {
 const request = createLatestRequest(); let rejectFirst;
 const pending = request.run(() => new Promise((_resolve, reject) => { rejectFirst = reject; }));
 request.cancel(); rejectFirst(new Error('late transport error'));
 assert.deepEqual(await pending, { current: false });
 await assert.rejects(request.run(async () => { throw new Error('current error'); }), /current error/);
 assert.deepEqual(await request.run(async () => 'retry'), { current: true, value: 'retry' });
});
