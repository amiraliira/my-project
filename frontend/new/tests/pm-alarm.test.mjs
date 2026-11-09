import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const ts = createRequire(import.meta.url)('typescript');
const source = ts.transpileModule(readFileSync(new URL('../src/pmAlarm.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { pmIsOverdue } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const deadline = '2026-10-09T20:30:00.000Z', window = { endUtc: deadline }, end = Date.parse(deadline);

test('returned sheets stay overdue at the exact Tehran week boundary', () => {
 assert.equal(pmIsOverdue({ status: 'returned', pmExecutionState: 'returned', pmWindow: window }, end - 1), false);
 assert.equal(pmIsOverdue({ status: 'returned', pmExecutionState: 'returned', pmWindow: window }, end), true);
 assert.equal(pmIsOverdue({ status: 'draft', pmExecutionState: 'assigned', pmWindow: window }, end + 1), true);
});
test('server flags avoid false alarms from a device with the wrong clock', () => {
 assert.equal(pmIsOverdue({ status: 'draft', pmOverdue: false, pmExecutionState: 'unperformed', pmWindow: window }, end + 86_400_000), false);
 assert.equal(pmIsOverdue({ status: 'returned', pmOverdue: true, pmWindow: window }, end - 86_400_000), true);
});
test('sent, completed, and cancelled work never raises an outstanding execution alarm', () => {
 for (const status of ['submitted', 'supervisor', 'head', 'completed', 'cancelled']) assert.equal(pmIsOverdue({ status, pmOverdue: true, pmExecutionState: 'unperformed', pmWindow: window }, end + 86_400_000), false);
});
test('a legacy date-only deadline lasts until the end of its Tehran day', () => {
 const order = { status: 'draft', dueDate: '2026-10-09' };
 assert.equal(pmIsOverdue(order, end - 1), false);
 assert.equal(pmIsOverdue(order, end), true);
 assert.equal(pmIsOverdue({ status: 'draft' }, end), false);
});
