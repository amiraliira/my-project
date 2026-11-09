import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = createRequire(import.meta.url), ts = require('typescript');
const { createElement: h } = require('react'), { renderToStaticMarkup } = require('react-dom/server');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const resolve = name => pathToFileURL(require.resolve(name)).href;
function compile(name, imports = {}) {
 let code = ts.transpileModule(readFileSync(new URL('../src/' + name, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText;
 for (const [name, replacement] of Object.entries(imports)) code = code.replaceAll(JSON.stringify(name), JSON.stringify(replacement)).replaceAll("'" + name + "'", JSON.stringify(replacement));
 return code;
}
const archiveUrl = encode(compile('pmArchiveModel.ts')), permissionsUrl = encode(compile('permissions.ts'));
const archive = await import(archiveUrl), permissions = await import(permissionsUrl);
const uiUrl = encode(`export const num = value => String(value ?? 0), dateFa = value => value || '—';`);
const sheetUrl = encode(`import {createElement as h} from ${JSON.stringify(resolve('react'))}; export const PmSheetView=({sheet,results,onResults})=>h('div',{'data-snapshot-version':sheet.version,'data-editable':Boolean(onResults)},sheet.title,' ',results.result);`);
const { default: Document } = await import(encode(compile('PmArchiveDocument.tsx', { 'react/jsx-runtime': resolve('react/jsx-runtime'), './ui': uiUrl, './pmArchiveModel': archiveUrl, './PmLibrary': sheetUrl })));
const entry = { id: 'archive-1', orderId: 'order-1', number: 'PM-1405-007', title: 'بازدید ماهانهٔ پمپ', status: 'supervisor', currentStatus: 'supervisor', workflowRevision: 3, templateVersion: 2, planVersion: 5, hall: 'فرایند', assetCode: 'PM-106', assetName: 'پمپ فرایند', performerId: 'person-1', performerName: 'تکنسین آزمون', specialty: 'مکانیک', workDate: '2026-10-01', submittedAt: '2026-10-01T08:30:00Z', submittedByName: 'ثبت‌کنندهٔ آزمون', approvedAt: null };
const detail = { entry, sheet: { title: 'قالب صادرشدهٔ قدیمی', version: 2 }, results: { result: 'نتیجهٔ ثبت‌شدهٔ قدیمی <script>alert(1)</script>' }, execution: { description: 'بازدید و بررسی آب‌بند', workDate: '2026-10-01', startTime: '08:00', endTime: '09:00', durationMinutes: 60, participants: [{ personId: 'person-1', personName: 'تکنسین آزمون', specialty: 'مکانیک', minutes: 60 }], parts: [] }, approvals: [], revisions: [] };

test('only the confirmed PM expert role can open archive UI', () => {
 assert.equal(permissions.mayViewPmArchive('planner'), true);
 for (const role of ['technician', 'supervisor', 'head', 'manager', 'admin', '', 'unknown']) assert.equal(permissions.mayViewPmArchive(role), false);
});

test('archive query is bounded and encodes identity/date filters without downloading full history', () => {
 const filters = { ...archive.emptyArchiveFilters(), q: ' پمپ & سالن ', hall: 'فرایند', personId: 'person-1', from: '2026-10-01', to: '2026-10-31', status: 'completed' };
 const path = archive.archivePath(filters, 4), url = new URL('http://localhost' + path);
 assert.equal(url.pathname, '/pm/archive');
 assert.equal(url.searchParams.get('page'), '4'); assert.equal(url.searchParams.get('pageSize'), '25');
 assert.equal(url.searchParams.get('q'), 'پمپ & سالن'); assert.equal(url.searchParams.get('hall'), 'فرایند');
 assert.equal(url.searchParams.get('personId'), 'person-1'); assert.equal(url.searchParams.get('from'), '2026-10-01');
 assert.equal(url.searchParams.has('assetId'), false);
 assert.equal(new URL('http://localhost' + archive.archivePath(archive.emptyArchiveFilters(), 0)).searchParams.get('page'), '1');
});

test('submitted snapshot is readonly and clearly has no final approval', () => {
 const html = renderToStaticMarkup(h(Document, { detail }));
 for (const text of ['تکنسین آزمون', '2026-10-01', '08:00', '09:00', '60', 'قالب صادرشدهٔ قدیمی', 'نتیجهٔ ثبت‌شدهٔ قدیمی', 'نسخهٔ ارسال 3', 'در انتظار تأیید سرپرست', 'این نسخه تأیید نهایی ندارد', 'archive-1']) assert.ok(html.includes(text), text);
 assert.match(html, /data-snapshot-version="2"/); assert.match(html, /data-editable="false"/);
 assert.doesNotMatch(html, /<script>|<input|<textarea|<form/);
 assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
});

test('returned historical revision stays distinct from currently completed work', () => {
 const html = renderToStaticMarkup(h(Document, { detail: { ...detail, entry: { ...entry, status: 'superseded', currentStatus: 'completed' }, approvals: [{ id: 'a1', actorName: 'سرپرست آزمون', stage: 'supervisor', decision: 'return', specialty: 'مکانیک', createdAt: '2026-10-02T08:30:00Z', reason: 'اصلاح نتیجهٔ بازدید' }] } }));
 for (const text of ['نسخهٔ قبلی برگه', 'این نسخه، نسخهٔ قبلی است', 'وضعیت فعلی کار: تأیید نهایی', 'سرپرست آزمون', 'برگشت برای اصلاح', 'اصلاح نتیجهٔ بازدید']) assert.ok(html.includes(text), text);
 assert.ok(html.includes('این نسخه تأیید نهایی ندارد'));
});

test('final approval is shown only for completed snapshot and operational timeline never renders grades', () => {
 const hidden = 'SENSITIVE-GRADE-DO-NOT-RENDER';
 const html = renderToStaticMarkup(h(Document, { detail: { ...detail, entry: { ...entry, status: 'completed', approvedAt: '2026-10-03T11:00:00Z', finalScore: hidden }, execution: { ...detail.execution, participants: detail.execution.participants.map(p => ({ ...p, headCoefficient: hidden, supervisorCoefficient: hidden, finalScore: hidden })) }, approvals: [{ id: 'a1', actorName: 'رئیس آزمون', stage: 'head', decision: 'approve', createdAt: '2026-10-03T11:00:00Z', coefficient: hidden }] } }));
 assert.match(html, /2026-10-03T11:00:00Z/); assert.match(html, /رئیس آزمون/);
 assert.doesNotMatch(html, /SENSITIVE-GRADE-DO-NOT-RENDER|این نسخه تأیید نهایی ندارد|نمره|امتیاز/);
});

test('legacy reconstructed records disclose that identities were captured at migration', () => {
 const html = renderToStaticMarkup(h(Document, { detail: { ...detail, identityCapturedAt: '2026-10-19T08:00:00Z', entry: { ...entry, evidenceSource: 'legacy-reconstructed' } } }));
 assert.match(html, /بازسازی شده/); assert.match(html, /زمان ارتقا/); assert.match(html, /2026-10-19T08:00:00Z/);
 assert.doesNotMatch(html, /این برگه، نسخهٔ ثبت‌شده در زمان ارسال است/);
});

test('supervisor correction identifies the actor and reason while retaining the technician as performer', () => {
 const corrected = { ...detail, entry: { ...entry, workflowRevision: 4, submittedByName: 'سرپرست اصلاح‌کنندهٔ آزمون', evidenceSource: 'supervisor-correction-snapshot' }, correction: { baseArchiveId: 'archive-original', baseRevision: 3, reason: 'اصلاح ساعت پایان <script>unsafe</script>', coefficient: 'HIDDEN-CORRECTION-GRADE' } };
 const html = renderToStaticMarkup(h(Document, { detail: corrected }));
 for (const text of ['نسخهٔ اصلاح سرپرست 4', 'زمان ثبت اصلاح و سرپرست اصلاح‌کننده', 'سرپرست اصلاح‌کنندهٔ آزمون', 'دلیل اصلاح سرپرست', 'اصلاح ساعت پایان', 'نسخهٔ پیش از این اصلاح: 3', 'archive-original', 'گزارش ارسالی انجام‌دهنده']) assert.ok(html.includes(text), text);
 assert.match(html, /<dt>انجام‌دهنده<\/dt><dd>تکنسین آزمون/);
 assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
 assert.doesNotMatch(html, /HIDDEN-CORRECTION-GRADE|<script>|<input|<textarea|<form|این برگه، نسخهٔ ثبت‌شده در زمان ارسال است/);
 const original = renderToStaticMarkup(h(Document, { detail }));
 assert.match(original, /نسخهٔ ارسال 3/);
 assert.match(original, /ثبت‌کنندهٔ آزمون/);
 assert.doesNotMatch(original, /دلیل اصلاح سرپرست|سرپرست اصلاح‌کنندهٔ آزمون|archive-original/);
});

test('correction opens its preserved base snapshot without changing the selected document', () => {
 const corrected = { ...detail, entry: { ...entry, evidenceSource: 'supervisor-correction-snapshot' }, correction: { baseArchiveId: 'archive-before-correction', baseRevision: 2, reason: 'اصلاح نتیجه' } };
 let opened = '';
 const tree = Document({ detail: corrected, openRevision: id => { opened = id; } });
 const find = node => !node || typeof node !== 'object' ? undefined : node.type === 'button' ? node : [node.props?.children].flat(Infinity).map(find).find(Boolean);
 const button = find(tree);
 assert.ok(button);
 assert.equal(button.props.children, 'مشاهدهٔ نسخهٔ پیش از این اصلاح');
 assert.match(button.props.className, /pm-archive-hide-print/);
 button.props.onClick();
 assert.equal(opened, 'archive-before-correction');
 assert.equal(corrected.entry.id, 'archive-1');
 assert.equal(corrected.entry.performerName, 'تکنسین آزمون');
});

test('archive responsibility comes only from names frozen in the submission snapshot', () => {
 const responsibility = { originalSupervisorId: 'issued-supervisor', originalSupervisorName: 'سرپرست اصلی ثبت‌شده', effectiveSupervisorId: 'execution-supervisor', effectiveSupervisorName: 'سرپرست اجرای ثبت‌شده', transferIds: ['transfer-at-submission'] };
 const html = renderToStaticMarkup(h(Document, { detail: { ...detail, responsibility, entry: { ...entry, supervisorName: 'CURRENT-SUPERVISOR-DO-NOT-RENDER' } } }));
 for (const text of ['سرپرست هنگام صدور', 'سرپرست مسئول هنگام اجرا', responsibility.originalSupervisorName, responsibility.effectiveSupervisorName]) assert.ok(html.includes(text), text);
 assert.doesNotMatch(html, /CURRENT-SUPERVISOR-DO-NOT-RENDER|<input|<textarea|<form/);
 const legacy = renderToStaticMarkup(h(Document, { detail: { ...detail, supervisorName: 'GUESSED-SUPERVISOR-DO-NOT-RENDER' } }));
 assert.doesNotMatch(legacy, /سرپرست هنگام صدور|سرپرست مسئول هنگام اجرا|GUESSED-SUPERVISOR-DO-NOT-RENDER/);
 const unnamed = renderToStaticMarkup(h(Document, { detail: { ...detail, responsibility: { ...responsibility, originalSupervisorName: '', effectiveSupervisorName: '' }, entry: { ...entry, supervisorName: 'FALLBACK-DO-NOT-RENDER' } } }));
 assert.equal(unnamed.match(/در این نسخه ثبت نشده/g)?.length, 2);
 assert.doesNotMatch(unnamed, /FALLBACK-DO-NOT-RENDER/);
});
