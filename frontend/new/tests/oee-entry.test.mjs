import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),ts=require('typescript');
const compiled=ts.transpileModule(readFileSync(new URL('../src/oeeEntry.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {entryNumber,entryClock,durationClock,firstJalaliMonthDay}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
test('count entry preserves unknown versus zero and accepts Persian/Arabic numerals',()=>{
 assert.equal(entryNumber(''),null);assert.equal(entryNumber('۰'),0);assert.equal(entryNumber('۱۲٬۳۴۵'),12345);assert.equal(entryNumber('١٢٣'),123);assert.equal(entryNumber('۱۲٫۵'),12.5);
 assert.equal(Number.isNaN(entryNumber('۱۲kg')),true);assert.equal(Number.isNaN(entryNumber('-2')),true);
});
test('monthly report starts on Jalali month boundary, including leap Nowruz',()=>{
 assert.equal(firstJalaliMonthDay('2026-10-03'),'2026-09-23');
 assert.equal(firstJalaliMonthDay('2024-03-21'),'2024-03-20');
 assert.equal(firstJalaliMonthDay('2025-03-20'),'2025-02-19');
});
test('fast hour entry accepts four digits and Persian clocks without inventing missing input',()=>{
 assert.equal(entryClock('۰۸۳۰'),'08:30');assert.equal(entryClock('830'),'08:30');
 assert.equal(entryClock('8:30'),'08:30');assert.equal(entryClock('٢٣:٤٥'),'23:45');
 assert.equal(entryClock(''),'');assert.equal(entryClock('25:70'),'25:70');
});
test('thousands separators cannot silently change malformed counts',()=>{
 assert.equal(entryNumber('۶,۰۰۰'),6000);assert.equal(Number.isNaN(entryNumber('۱٬۲')),true);
 assert.equal(Number.isNaN(entryNumber('1,00')),true);assert.equal(Number.isNaN(entryNumber('9'.repeat(400))),true);
});
test('runtime is displayed as hours and minutes rather than ambiguous decimal hours',()=>{
 assert.equal(durationClock(390),'06:30');assert.equal(durationClock(361),'06:01');assert.equal(durationClock(690),'11:30');assert.equal(durationClock(0),'00:00');
});
