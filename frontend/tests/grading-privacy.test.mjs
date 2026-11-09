import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require = createRequire(import.meta.url), ts = require('typescript');
const { createElement } = require('react'), { renderToStaticMarkup } = require('react-dom/server');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const fileImport = name => JSON.stringify(pathToFileURL(require.resolve(name)).href);
const compile = (name, imports = {}) => {
 let source = ts.transpileModule(readFileSync(new URL('../src/' + name, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText;
 for (const [name, replacement] of Object.entries(imports)) source = source.replaceAll(JSON.stringify(name), JSON.stringify(replacement)).replaceAll("'" + name + "'", JSON.stringify(replacement));
 return source;
};
const scoringUrl = encode(compile('scoring.ts')), permissionsUrl = encode(compile('permissions.ts'));
const scoring = await import(scoringUrl), permissions = await import(permissionsUrl);
const uiUrl = encode(`import {createElement as h} from ${fileImport('react')};
 export const num = value => String(value ?? 0), dateFa = value => value || '—';
 export const Field = ({label,children}) => h('label',null,label,children);
 export const DataTable = ({data,columns}) => h('table',null,h('thead',null,h('tr',null,columns.map(c=>h('th',{key:c.title},c.title)))),h('tbody',null,data.map(p=>h('tr',{key:p.personId},columns.map(c=>h('td',{key:c.title},c.cell(p)))))));
`);
const grades = await import(encode(compile('Grading.tsx', { 'react/jsx-runtime': pathToFileURL(require.resolve('react/jsx-runtime')).href, './ui': uiUrl, './scoring': scoringUrl, './permissions': permissionsUrl })));
const order = {
 status: 'head', decisionSpecialty: 'مکانیک', participants: [
  { personId: 'm1', personName: 'مکانیک اول', specialty: 'مکانیک', minutes: 60, supervisorCoefficient: 2, supervisorScoreSource: 'individual', headCoefficient: 0, headScoreSource: 'individual', finalScore: 0 },
  { personId: 'm2', personName: 'مکانیک دوم', specialty: 'مکانیک', minutes: 30, supervisorCoefficient: 3, supervisorScoreSource: 'individual', headCoefficient: 1.5, headScoreSource: 'individual', finalScore: 2.25 },
  { personId: 'e1', personName: 'برق اول', specialty: 'برق', minutes: 60, supervisorCoefficient: 0.5, supervisorScoreSource: 'individual' },
 ], headCoefficient: null,
};
const context = role => ({ user: { role, specialty: 'مکانیک' }, boot: { people: [] } });

test('head decision requires a distinct explicit grade for the full participant set, including zero', () => {
 const payload = scoring.participantGradePayload(order, { m1: '0', m2: '1.25', e1: '3' }, 'head');
 assert.equal(scoring.participantGradePayload(order, { m1: '0.1234567890123456789012345678', m2: '1', e1: '3' }, 'head').participantScores[0].coefficient, '0.1234567890123456789012345678');
 assert.deepEqual(payload, { participantScores: [{ personId: 'm1', coefficient: '0' }, { personId: 'm2', coefficient: '1.25' }, { personId: 'e1', coefficient: '3' }] });
 for (const values of [{ m1: '0', m2: '1.25' }, { m1: '', m2: '2', e1: '3' }, { m1: '3.01', m2: '2', e1: '3' }, { m1: '-0.1', m2: '2', e1: '3' }]) assert.equal(scoring.participantGradePayload(order, values, 'head'), null);
});

test('supervisor grade payload stays confined to their specialty', () => {
 assert.deepEqual(scoring.participantGradePayload(order, { m1: '0', m2: '0.25', e1: '3' }, 'supervisor'), { grades: [{ personId: 'm1', coefficient: '0' }, { personId: 'm2', coefficient: '0.25' }] });
 assert.equal(scoring.participantGradePayload({ ...order, decisionSpecialty: null }, { m1: '3', m2: '3' }, 'supervisor'), null);
});

test('individual head evidence and canonical final scores override a historical shared value', () => {
 const completed = { ...order, status: 'completed', headCoefficient: 2 };
 assert.equal(scoring.headGrade(completed, 'm1').coefficient, 0);
 assert.equal(scoring.headGrade(completed, 'm2').coefficient, 1.5);
 assert.equal(scoring.participantWorkScore(completed, 'm1'), 0);
 assert.equal(scoring.participantWorkScore(completed, 'm2'), 2.25);
 assert.equal(scoring.headGrade({ ...order, participants: [{ personId: 'old', specialty: 'مکانیک', minutes: 60 }], headCoefficient: 1.75 }, 'old').source, 'legacy-order');
});

test('head inputs show every person, their own supervisor grade, and no shared head input', () => {
 const html = renderToStaticMarkup(createElement(grades.HeadGradeInputs, { order, context: context('head'), values: { m1: '0', m2: '1.25', e1: '3' }, setValues() {}, busy: false }));
 assert.equal((html.match(/type="number"/g) || []).length, 3);
 for (const name of ['مکانیک اول', 'مکانیک دوم', 'برق اول']) assert.match(html, new RegExp('نمره رئیس برای ' + name));
 assert.match(html, /نمرهٔ سرپرست/); assert.match(html, /value="0"/); assert.match(html, /value="1.25"/);
});

test('grade rendering and personnel reports follow the confirmed chief-only privacy rules', () => {
 for (const role of ['planner', 'manager', 'admin', 'technician']) {
  assert.equal(renderToStaticMarkup(createElement(grades.ParticipantGrades, { order, context: context(role) })), '');
  assert.equal(permissions.mayViewPersonnelReports(role), false);
  assert.equal(permissions.isChief(role), false);
 }
 const supervisor = renderToStaticMarkup(createElement(grades.ParticipantGrades, { order, context: context('supervisor') }));
 assert.match(supervisor, /مکانیک اول/); assert.doesNotMatch(supervisor, /برق اول|نمره رئیس|امتیاز فعالیت فرد/);
 assert.equal(permissions.mayViewPersonnelReports('supervisor'), false);
 const chief = renderToStaticMarkup(createElement(grades.ParticipantGrades, { order, context: context('head') }));
 assert.match(chief, /برق اول/); assert.match(chief, /نمره رئیس/); assert.match(chief, /امتیاز فعالیت فرد/);
 assert.equal(permissions.mayViewPersonnelReports('head'), true);
 for (const role of ['planner', 'manager', 'supervisor', 'head']) assert.equal(permissions.mayProposePm(role), true);
 assert.equal(permissions.mayProposePm('admin'), false);
});

test('head-only performers use a frozen per-person range and no synthetic supervisor grade', () => {
 const mixed = { ...order, participants: [
  { personId: 'sm', personName: 'سرپرست اجرایی', specialty: 'مکانیک', minutes: 60, gradingPolicy: 'head-only-v1', headGradeMaximum: 9, finalScore: 8.5 },
  { personId: 'm1', personName: 'تکنسین', specialty: 'مکانیک', minutes: 60, gradingPolicy: 'two-stage-v1', headGradeMaximum: 3, supervisorCoefficient: 2, supervisorScoreSource: 'individual' },
 ] };
 assert.deepEqual(scoring.participantGradePayload(mixed, { m1: '2' }, 'supervisor'), { grades: [{ personId: 'm1', coefficient: '2' }] });
 assert.deepEqual(scoring.participantGradePayload(mixed, { sm: '9', m1: '3' }, 'head'), { participantScores: [{ personId: 'sm', coefficient: '9' }, { personId: 'm1', coefficient: '3' }] });
 assert.equal(scoring.participantGradePayload(mixed, { sm: '9.01', m1: '3' }, 'head'), null);
 assert.equal(scoring.participantGradePayload(mixed, { sm: '9', m1: '3.01' }, 'head'), null);
 assert.equal(scoring.participantGradePayload(mixed, { sm: '0', m1: '0' }, 'head').participantScores[0].coefficient, '0');
 assert.equal(scoring.participantWorkScore({ ...mixed, status: 'completed' }, 'sm'), 8.5);
 assert.equal(scoring.supervisorGrade(mixed, 'sm'), null);
 const html = renderToStaticMarkup(createElement(grades.HeadGradeInputs, { order: mixed, context: context('head'), values: { sm: '8.5', m1: '3' }, setValues() {}, busy: false }));
 assert.match(html, /max="9"/); assert.match(html, /max="3"/);
 assert.match(html, /کار سرپرست · ارزیابی مستقیم رئیس/);
 const supervisor = renderToStaticMarkup(createElement(grades.SupervisorGradeInputs, { order: mixed, context: context('supervisor'), values: { m1: '2' }, setValues() {}, busy: false }));
 assert.doesNotMatch(supervisor, /نمره سرپرست برای سرپرست اجرایی/);
 assert.match(supervisor, /نمره سرپرست برای تکنسین/);
});
