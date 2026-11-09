import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = createRequire(import.meta.url), ts = require('typescript');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const reactUrl = pathToFileURL(require.resolve('react')).href;
const jsxUrl = pathToFileURL(require.resolve('react/jsx-runtime')).href;
const apiUrl = encode('export async function api() { throw Error("Unexpected unconfigured account request"); } export async function post() { throw Error("Rendering must not initialize credentials"); }');
const uiUrl = encode('export const roles = {}; export const errorText = error => error.message; export const ErrorBox = () => null; export const Icon = () => null;');
const policyUrl = encode(ts.transpileModule(readFileSync(new URL('../src/passwordPolicy.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText);
const compiled = ts.transpileModule(readFileSync(new URL('../src/LocalTestSetup.tsx', import.meta.url), 'utf8'), {
 compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
}).outputText.replaceAll('"react/jsx-runtime"', JSON.stringify(jsxUrl)).replaceAll("'react'", JSON.stringify(reactUrl))
 .replace("'./api'", JSON.stringify(apiUrl)).replace("'./ui'", JSON.stringify(uiUrl)).replace("'./passwordPolicy'", JSON.stringify(policyUrl)).replace("import './local-test.css';", '');
const { default: LocalTestSetup, LocalTestPasswordSetup, needsLocalTestPassword, isLocalTestMode, readLocalTestAccounts } = await import(encode(compiled));
const { createElement } = require('react'), { renderToStaticMarkup } = require('react-dom/server');
const localHealth = { localTestMode: true };

test('first password form appears only for explicitly uninitialized loopback copy', () => {
 assert.equal(needsLocalTestPassword({ ...localHealth, localTestInitialized: false }, '127.0.0.1'), true);
 for (const [health, hostname] of [[localHealth, '127.0.0.1'], [{ ...localHealth, localTestInitialized: true }, '127.0.0.1'], [{ localTestInitialized: false }, '127.0.0.1'], [{ ...localHealth, localTestInitialized: false }, '192.168.1.20']]) {
  assert.equal(needsLocalTestPassword(health, hostname), false);
 }
});

test('initial password setup is only a form within the original login layout', () => {
 const html = renderToStaticMarkup(createElement(LocalTestPasswordSetup, { prepared() { assert.fail('Render cannot reset credentials'); } }));
 assert.match(html, /^<form/);
 assert.equal((html.match(/type="password"/g) || []).length, 2);
 assert.equal((html.match(/minLength="12"/gi) || []).length, 2);
 assert.match(html, /button primary login-submit/);
 assert.match(html, /تنظیم رمز و ادامه/);
 assert.doesNotMatch(html, /<main|<h1|test-setup\.html|<img/);
 const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
 assert.match(app, /className="login-page pak-login"/);
 assert.match(app, /className="pak-login-layout"/);
 assert.match(app, /className="login-identity"/);
 assert.match(app, /src="\/pak-logo\.png" width=\{144\} height=\{144\}/);
 assert.match(app, /شرکت لبنیات پاک<\/h1><p>سیستم نگهداری و تعمیرات/);
});

test('normal and non-loopback logins never request or expose test accounts', async () => {
 let calls = 0;
 const request = async () => { calls++; return { accounts: [] }; };
 for (const [health, hostname] of [[null, '127.0.0.1'], [{}, 'localhost'], [{ localTestMode: false }, '127.0.0.1'], [{ localTestMode: 'true' }, 'localhost'], [localHealth, '192.168.1.20'], [localHealth, 'external.invalid'], [localHealth, '127.0.0.1.external.invalid']]) {
  assert.equal(isLocalTestMode(health, hostname), false);
  assert.deepEqual(await readLocalTestAccounts(health, hostname, request), []);
  assert.equal(renderToStaticMarkup(createElement(LocalTestSetup, { health, hostname, value: '', onSelect() {} })), '');
 }
 assert.equal(calls, 0);
});

test('explicit local copied test fetches accounts without caching or credential mutation', async () => {
 const signal = new AbortController().signal;
 const accounts = [{ code: '123', name: 'نام آزمایشی', role: 'supervisor', specialty: 'مکانیک' }];
 let calls = 0;
 const result = await readLocalTestAccounts(localHealth, '127.0.0.1', async (path, options) => {
  calls++; assert.equal(path, '/test-setup/accounts'); assert.equal(options.cache, 'no-store'); assert.equal(options.signal, signal); assert.equal(options.method, undefined); assert.equal(options.body, undefined);
  return { accounts };
 }, signal);
 assert.equal(calls, 1); assert.deepEqual(result, accounts);
 const html = renderToStaticMarkup(createElement(LocalTestSetup, { health: localHealth, hostname: '127.0.0.1', value: '', onSelect() {} }));
 assert.match(html, /نسخهٔ مستقل آزمایشی/); assert.match(html, /انتخاب حساب برای تست/); assert.doesNotMatch(html, /type="password"/);
});

test('invalid account payload is refused with a useful Persian error', async () => {
 for (const payload of [null, {}, { accounts: {} }, { accounts: [null] }, { accounts: [{ code: '', name: 'x', role: 'head' }] }]) {
  await assert.rejects(readLocalTestAccounts(localHealth, 'localhost', async () => payload), /فهرست حساب‌های آزمایشی/);
 }
});

test('local test login does not add navigation tabs to the technician workspace', () => {
 const source = readFileSync(new URL('../src/TechnicianRegistration.tsx', import.meta.url), 'utf8');
 const parsed = ts.createSourceFile('TechnicianRegistration.tsx', source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
 const tabs = [];
 function visit(node) {
  if (ts.isJsxOpeningElement(node) && node.tagName.getText(parsed) === 'button') {
   const attributes = node.attributes.properties.filter(ts.isJsxAttribute);
   if (attributes.some(attribute => attribute.name.text === 'role' && attribute.initializer?.text === 'tab')) {
    tabs.push(attributes.find(attribute => attribute.name.text === 'id')?.initializer?.text);
   }
  }
  ts.forEachChild(node, visit);
 }
 visit(parsed);
 assert.deepEqual(tabs, ['technician-tab-registration', 'technician-tab-pm']);
 assert.doesNotMatch(source, /LocalTestSetup|test-setup/);
});
