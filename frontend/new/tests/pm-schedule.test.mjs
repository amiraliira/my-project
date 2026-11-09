import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const ts=createRequire(import.meta.url)('typescript');
const code=ts.transpileModule(readFileSync(new URL('../src/pmSchedule.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {recurrenceSlots,recurrenceLabel,initialRecurrence,formPeriodGroup}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const monthly={mode:'source-period',unit:'month',interval:10,anchorYear:1405,anchorMonth:7,week:2,meterPolicy:'calendar-only'};
test('ten-month preview continues across Persian years from its first due month',()=>{
 assert.deepEqual(recurrenceSlots(monthly,[],1404),[]);
 assert.deepEqual(recurrenceSlots(monthly,[],1405),[{month:7,week:2}]);
 assert.deepEqual(recurrenceSlots(monthly,[],1406),[{month:5,week:2}]);
 assert.deepEqual(recurrenceSlots(monthly,[],1407),[{month:3,week:2}]);
 assert.deepEqual(recurrenceSlots(monthly,[],1408),[{month:1,week:2},{month:11,week:2}]);
 assert.equal(recurrenceLabel(monthly),'هر ۱۰ ماه');
});
test('monthly and annual schedules retain chosen week and first month',()=>{
 const annual={...monthly,interval:12,week:4};
 assert.deepEqual(recurrenceSlots(annual,[],1406),[{month:7,week:4}]);
 const slots=recurrenceSlots({...monthly,interval:1},[],1405);
 assert.equal(slots.length,6);assert.deepEqual(slots[0],{month:7,week:2});assert.deepEqual(slots.at(-1),{month:12,week:2});
 assert.equal(recurrenceSlots({...monthly,interval:1},[],1406).length,12);
});
test('weekly preview follows four complete company weeks per month',()=>{
 const weekly={...monthly,unit:'week',interval:1,week:1};
 assert.equal(recurrenceSlots(weekly,[],1405).length,24);
 for(let year=1406;year<=1605;year++){
  const slots=recurrenceSlots(weekly,[],year);assert.equal(slots.length,48);
  assert.equal(new Set(slots.map(s=>s.month+':'+s.week)).size,48);
 }
 assert.equal(recurrenceSlots({...weekly,interval:2},[],1406).length,24);
});
test('manual calendar preserves original selections and invalid cadence shows no fabricated slots',()=>{
 const original=[{month:1,week:1},{month:12,week:4}];
 assert.strictEqual(recurrenceSlots(undefined,original,1405),original);
 assert.strictEqual(recurrenceSlots({mode:'annual-slots'},original,1405),original);
 for(const bad of [{interval:0},{interval:1.5},{interval:121},{anchorYear:1405.5},{anchorMonth:13},{anchorMonth:1.5},{week:0},{week:1.5},{unit:'day'}])assert.deepEqual(recurrenceSlots({...monthly,...bad},[],1405),[]);
 assert.deepEqual(recurrenceSlots(monthly,[],1405.5),[]);
});
test('initial form period suggestion never replaces source calendar or approved chief cadence',()=>{
 const suggestion={unit:'month',interval:1,anchorYear:1405,anchorMonth:7,week:1};
 const base={slots:[],version:1,mappingConfirmed:false,recurrence:{mode:'annual-slots'},sourcePeriodSuggestion:suggestion,startDate:'2026-09-23'};
 assert.deepEqual(initialRecurrence(base,'2026-10-05'),{mode:'source-period',...suggestion,meterPolicy:'calendar-only'});
 assert.deepEqual(initialRecurrence({...base,slots:[{month:1,week:1},{month:2,week:3}]},'2026-10-05'),{mode:'annual-slots'});
 assert.deepEqual(initialRecurrence({...base,version:2},'2026-10-05'),{mode:'annual-slots'});
 assert.deepEqual(initialRecurrence({...base,mappingConfirmed:true},'2026-10-05'),{mode:'annual-slots'});
 assert.deepEqual(initialRecurrence({...base,recurrence:monthly},'2026-10-05'),monthly);
 assert.deepEqual(initialRecurrence({...base,sourcePeriodSuggestion:null},'2026-10-05'),{mode:'annual-slots'});
 assert.deepEqual(initialRecurrence({...base,sourcePeriodSuggestion:{unit:'day',interval:45}},'2026-10-05'),{mode:'annual-slots'});
});
test('form groups retain distinct day, month and year intervals',()=>{
 const fifteen=formPeriodGroup({periodClass:'day-interval',intervalDays:15}),fortyFive=formPeriodGroup({periodClass:'day-interval',intervalDays:45});
 assert.notEqual(fifteen.key,fortyFive.key);assert.equal(fifteen.label,'هر ۱۵ روز');assert.equal(fortyFive.label,'هر ۴۵ روز');
 const four=formPeriodGroup({periodClass:'other-monthly',intervalMonths:4}),seven=formPeriodGroup({periodClass:'other-monthly',intervalMonths:7});
 assert.notEqual(four.key,seven.key);assert.equal(four.label,'هر ۴ ماه');assert.equal(seven.label,'هر ۷ ماه');
 assert.equal(formPeriodGroup({periodClass:'other-monthly',intervalMonths:18}).label,'هر ۱۸ ماه');
 assert.equal(formPeriodGroup({periodClass:'multi-year',intervalMonths:120}).label,'هر ۱۰ سال');
 assert.equal(formPeriodGroup({periodClass:'monthly',intervalMonths:1,period:'ماهانه / ۴۰۰ ساعت'}).label,'ماهانه');
 assert.equal(formPeriodGroup({periodClass:'weekly'}).label,'هفتگی');
});
