import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = createRequire(import.meta.url), ts = require('typescript');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const reactUrl = pathToFileURL(require.resolve('react')).href;
const uiUrl = encode('let n=0;export const requestId=()=>"test-request-key-"+(++n);');
const source = ts.transpileModule(readFileSync(new URL('../src/pmGovernance.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText.replace("'react'", JSON.stringify(reactUrl)).replace("'./ui'", JSON.stringify(uiUrl));
const { isPendingPmChange, usePmRequestKey } = await import(encode(source));
const { createElement } = require('react'), { renderToStaticMarkup } = require('react-dom/server');

test('PM proposal response is distinct from a published template', () => {
 assert.equal(isPendingPmChange({ pendingApproval: true, changeRequest: { id: 'pending' } }), true);
 for (const result of [null, {}, { pendingApproval: false }, { pendingApproval: 'true' }, { id: 'published', planId: 'plan' }]) assert.equal(isPendingPmChange(result), false);
});

test('a retried proposal preserves its key while a changed payload receives a new key', () => {
 let values;
 function Probe() {
  const payload = usePmRequestKey();
  values = [payload({ version: 2, reason: 'اصلاح فعالیت', changes: [{ cellId: 'a', text: 'متن اول' }] }), payload({ version: 2, reason: 'اصلاح فعالیت', changes: [{ cellId: 'a', text: 'متن اول' }] }), payload({ version: 2, reason: 'اصلاح فعالیت', changes: [{ cellId: 'a', text: 'متن بعدی' }] })];
  return null;
 }
 renderToStaticMarkup(createElement(Probe));
 assert.equal(values[0].requestKey, values[1].requestKey);
 assert.notEqual(values[1].requestKey, values[2].requestKey);
 assert.deepEqual(values[2].changes, [{ cellId: 'a', text: 'متن بعدی' }]);
});
