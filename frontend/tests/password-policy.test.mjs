import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const ts = createRequire(import.meta.url)('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../src/passwordPolicy.ts', import.meta.url), 'utf8'), {
 compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { validNewPassword, passwordMinLength, passwordMaxLength } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

test('new passwords match backend bounds without trimming or imposing new login rules', () => {
 assert.equal(passwordMinLength, 12); assert.equal(passwordMaxLength, 200);
 for (const [password, valid] of [['', false], ['a'.repeat(11), false], ['a'.repeat(12), true], ['a'.repeat(200), true], ['a'.repeat(201), false], [' '.repeat(12), true]]) {
  assert.equal(validNewPassword(password), valid);
 }
});

test('Unicode password bounds count code points like Python instead of UTF-16 units', () => {
 assert.equal('🔒'.repeat(6).length, 12);
 assert.equal(validNewPassword('🔒'.repeat(6)), false);
 assert.equal(validNewPassword('🔒'.repeat(12)), true);
 assert.equal(validNewPassword('🔒'.repeat(200)), true);
 assert.equal(validNewPassword('🔒'.repeat(201)), false);
 assert.equal(validNewPassword('پ'.repeat(12)), true);
});
