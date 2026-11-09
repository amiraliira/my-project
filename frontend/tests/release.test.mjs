import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const ts = createRequire(import.meta.url)('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../src/compatibility.ts', import.meta.url), 'utf8'), {
 compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { candidateRelease, startWithCompatibleBackend, requiredCapabilities } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const candidate = { status: 'ok', apiVersion: 7, databaseProvider: 'postgresql', databaseVersionNum: 170011, schemaVersion: 10, capabilities: [...requiredCapabilities], releaseStage: 'candidate', productionAccepted: false };

test('complete candidate remains usable while its production acceptance stays visible', async () => {
 let sessions = 0;
 const result = await startWithCompatibleBackend(async () => candidate, async health => { assert.equal(candidateRelease(health), true); sessions++; });
 assert.equal(result.compatible, true);
 assert.equal(sessions, 1);
 assert.equal(candidateRelease({ ...candidate, releaseStage: 'production', productionAccepted: false }), true);
 assert.equal(candidateRelease({ ...candidate, productionAccepted: undefined }), true);
 assert.equal(candidateRelease({ ...candidate, releaseStage: 'production', productionAccepted: true }), false);
 assert.equal(candidateRelease({ ...candidate, productionAccepted: true }), false);
 assert.equal(candidateRelease({ status: 'ok' }), false);
 assert.equal(candidateRelease(null), false);
});

test('candidate status cannot bypass a missing workflow contract', async () => {
 let sessions = 0;
 const result = await startWithCompatibleBackend(async () => ({ ...candidate, capabilities: candidate.capabilities.filter(value => value !== 'participant-grading') }), async () => { sessions++; });
 assert.equal(result.compatible, false);
 assert.equal(sessions, 0);
});

test('API 6 backend is rejected before any authenticated startup workflow', async () => {
 let sessions = 0;
 const result = await startWithCompatibleBackend(async () => ({ ...candidate, apiVersion: 6 }), async () => { sessions++; });
 assert.equal(result.compatible, false);
 assert.equal(sessions, 0);
});

test('archive interface refuses older database schema or backend without immutable archive capability', async () => {
 for (const health of [{ ...candidate, schemaVersion: 8 }, { ...candidate, capabilities: candidate.capabilities.filter(value => value !== 'pm-submission-archive') }]) {
  let sessions = 0;
  const result = await startWithCompatibleBackend(async () => health, async () => { sessions++; });
  assert.equal(result.compatible, false); assert.equal(sessions, 0);
 }
});
