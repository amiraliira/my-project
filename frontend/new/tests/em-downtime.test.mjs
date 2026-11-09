import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require=createRequire(import.meta.url),ts=require('typescript');
const {createElement:h}=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const encode=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const resolve=name=>pathToFileURL(require.resolve(name)).href;
const hooksUrl=encode(`let values=[],i=0,effects=[];export const reset=()=>{values=[];i=0;effects=[];},begin=()=>{i=0;effects=[];},flushEffects=()=>{const pending=effects;effects=[];for(const effect of pending)effect();};export const useEffect=effect=>{effects.push(effect);};export function useState(initial){const n=i++;if(!(n in values))values[n]=typeof initial==='function'?initial():initial;return[values[n],value=>{values[n]=typeof value==='function'?value(values[n]):value;}];}export const lazy=()=>()=>null,Suspense=({children})=>children;`);
const hooks=await import(hooksUrl);
const uiUrl=encode(`import{createElement as h}from ${JSON.stringify(resolve('react'))};let summary,orders,calls=[],flags={};export const configure=(s,o,f={})=>{summary=s;orders=o;calls=[];flags=f;},requests=()=>calls;export function useResource(path,initial,interval,enabled=true){calls.push({path,interval,enabled});return{data:path.includes('/orders?')?(orders||initial):(summary||initial),loading:false,refreshing:false,loadedPath:path,error:'',reload:()=>{},...flags};}export const num=value=>String(value??0),dateFa=value=>value,todayIso=()=> '2026-11-07';export const Field=({label,children})=>h('label',null,label,children),ErrorBox=({text})=>text?h('div',{role:'alert'},text):null,Icon=()=>null,Loading=()=>null,Badge=({status})=>h('span',null,status),Empty=({title})=>h('p',null,title),Modal=({title,children})=>h('section',{'role':'dialog','aria-label':title},children);`);
const ui=await import(uiUrl);
const catalogUrl=encode(`import{createElement as h}from ${JSON.stringify(resolve('react'))};export function PageControls({page,total,pageSize}){return h('span',{'data-pagination-page':page,'data-pagination-total':total,'data-pagination-size':pageSize});}`);
const dateUrl=encode(`import{createElement as h}from ${JSON.stringify(resolve('react'))};export function JalaliDate({value}){return h('span',{'data-date':value});}`);
let source=ts.transpileModule(readFileSync(new URL('../src/EmDowntime.tsx',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText.replace("import './em-downtime.css';",'');
for(const [name,url]of Object.entries({'react':hooksUrl,'react/jsx-runtime':resolve('react/jsx-runtime'),'./Catalog':catalogUrl,'./JalaliDate':dateUrl,'./ui':uiUrl}))source=source.replaceAll(JSON.stringify(name),JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
const mod=await import(encode(source));
const totals={downtimeMinutes:25,repairMinutes:60,personMinutes:90,unknownDowntimeCount:0};
const group={...totals,hall:'سالن الف',line:'خط ۱',unclassified:false,activityCount:2};
const summary={from:'2026-10-09',to:'2026-11-07',metric:'reported-em-downtime-v1',status:'completed',totalLines:14,totalOrders:26,totals,items:[group],page:1,pageSize:10,total:14,totalPages:2};
const context={user:{id:'H',role:'head',name:'رئیس'},notify:()=>{},refresh:async()=>{}};
function nodes(value){if(Array.isArray(value))return value.flatMap(nodes);return value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];}
function view(props={context}){hooks.reset();const render=()=>{hooks.begin();return mod.default(props);};return{render,find:(type,predicate=()=>true)=>nodes(render()).find(n=>n.type===type&&predicate(n)),all:()=>nodes(render())};}

test('EM report defaults to thirty inclusive days and validates exact bounded ISO dates',()=>{
 assert.deepEqual(mod.defaultEmDowntimePeriod('2026-11-07'),{from:'2026-10-09',to:'2026-11-07'});
 assert.equal(mod.emDowntimePeriodError('2026-01-01','2027-01-01'),'');
 assert.notEqual(mod.emDowntimePeriodError('2026-01-01','2027-01-02'),'');
 for(const pair of [['2026-02-30','2026-03-01'],['2026-11-08','2026-11-07'],['','2026-11-07']])assert.notEqual(mod.emDowntimePeriodError(...pair),'');
 assert.equal(mod.emDowntimePeriodError('2024-02-29','2025-02-28'),'');
});

test('drilldown query retains the exact hall/line pair, explicit empty buckets, dates and complete server pagination',()=>{
 const period={from:'2026-10-01',to:'2026-11-07'},selected={hall:'سالن & الف',line:'خط=۱ + ۲'};
 const url=new URL(mod.emDowntimePath(period,3,selected),'http://local.invalid');
 assert.equal(url.pathname,'/reports/em-downtime/orders');
 assert.equal(url.searchParams.get('hall'),selected.hall);assert.equal(url.searchParams.get('line'),selected.line);
 assert.equal(url.searchParams.get('from'),period.from);assert.equal(url.searchParams.get('to'),period.to);
 assert.equal(url.searchParams.get('page'),'3');assert.equal(url.searchParams.get('pageSize'),'25');
 const empty=new URL(mod.emDowntimePath(period,1,{hall:'',line:''}),'http://local.invalid');
 assert.ok(empty.searchParams.has('hall'));assert.ok(empty.searchParams.has('line'));assert.equal(empty.searchParams.get('line'),'');
 const main=new URL(mod.emDowntimePath(period,2),'http://local.invalid');assert.equal(main.searchParams.get('page'),'2');assert.equal(main.searchParams.get('pageSize'),'10');assert.equal(main.searchParams.has('hall'),false);
});

test('a recorded zero is known downtime but missing, malformed or out-of-range durations remain unknown',()=>{
 for(const value of [0,1,1440])assert.equal(mod.knownEmDowntime(value),true);
 for(const value of [undefined,null,'0',-1,1441,1.5,NaN,Infinity])assert.equal(mod.knownEmDowntime(value),false);
});

test('the authoritative reported duration overrides a normalized legacy value, including explicit unknown',()=>{
 assert.equal(mod.reportedEmDowntime({downtimeMinutes:0,reportedDowntimeMinutes:null}),null);
 assert.equal(mod.reportedEmDowntime({downtimeMinutes:7,reportedDowntimeMinutes:0}),0);
 assert.equal(mod.reportedEmDowntime({downtimeMinutes:7,reportedDowntimeMinutes:12}),12);
 assert.equal(mod.reportedEmDowntime({downtimeMinutes:7}),7);
 assert.equal(mod.reportedEmDowntime({downtimeMinutes:7,reportedDowntimeMinutes:'12'}),null);
});

test('role scope avoids rendering or enabling the chart for unauthorized roles',()=>{
 for(const role of ['head','manager','planner','supervisor','technician'])assert.equal(mod.mayViewEmDowntime(role),true);
 for(const role of ['admin','unknown']){
  ui.configure(summary);const tree=view({context:{...context,user:{...context.user,role}}}).render();
  assert.equal(tree,null);assert.equal(ui.requests().at(-1).enabled,false);
 }
});

test('chart click keeps the selected identity and dates; all groups use server pagination',()=>{
 ui.configure(summary);const chart=view();
 const row=chart.find('button',n=>n.props.className==='em-downtime-bar');row.props.onClick();
 const selected=chart.all().find(n=>n.type?.name==='LineActivities');
 assert.deepEqual(selected.props.group,group);assert.deepEqual(selected.props.period,{from:'2026-10-09',to:'2026-11-07'});
 const controls=chart.all().find(n=>n.type?.name==='PageControls');assert.equal(controls.props.total,14);assert.equal(controls.props.pageSize,10);controls.props.change(2);
 chart.render();assert.equal(new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams.get('page'),'2');
 assert.equal(chart.all().some(n=>n.type?.name==='LineActivities'),false);
});

test('draft filter changes do not change the active query; invalid ranges preserve it and apply resets paging',()=>{
 ui.configure(summary);const chart=view();
 chart.all().find(n=>n.type?.name==='PageControls').props.change(2);
 let dates=chart.all().filter(n=>n.type?.name==='JalaliDate');dates[0].props.onChange('2026-11-08');
 chart.render();assert.equal(new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams.get('from'),'2026-10-09');
 chart.find('form').props.onSubmit({preventDefault(){}});chart.render();assert.equal(new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams.get('page'),'2');
 dates=chart.all().filter(n=>n.type?.name==='JalaliDate');dates[0].props.onChange('2026-11-01');chart.find('form').props.onSubmit({preventDefault(){}});
 chart.render();const query=new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams;
 assert.equal(query.get('from'),'2026-11-01');assert.equal(query.get('to'),'2026-11-07');assert.equal(query.get('page'),'1');
});

test('line pagination clamps a refreshed out-of-range page only from its own complete response',()=>{
 ui.configure(summary);const chart=view();chart.all().find(n=>n.type?.name==='PageControls').props.change(3);
 const currentPage=()=>new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams.get('page');
 for(const [response,flags] of [
  [{...summary,page:2,totalPages:1},{}],
  [{...summary,page:3,totalPages:1},{loading:true}],
  [{...summary,page:3,totalPages:1},{error:'synthetic refresh failure'}],
  [{...summary,page:3,totalPages:1},{loadedPath:'/reports/em-downtime?stale-period'}],
 ]){
  ui.configure(response,undefined,flags);chart.render();hooks.flushEffects();chart.render();assert.equal(currentPage(),'3');
 }
 ui.configure({...summary,page:3,totalPages:1,total:2,items:[]});chart.render();hooks.flushEffects();chart.render();assert.equal(currentPage(),'1');
 ui.configure({...summary,page:1,totalPages:0,total:0,items:[]});chart.render();hooks.flushEffects();chart.render();hooks.flushEffects();chart.render();assert.equal(currentPage(),'1');
});

test('activity pagination also recovers when refresh leaves fewer pages without crossing stale responses',()=>{
 ui.configure(summary);const chart=view();chart.find('button',n=>n.props.className==='em-downtime-bar').props.onClick();const child=chart.all().find(n=>n.type?.name==='LineActivities');
 hooks.reset();const render=()=>{hooks.begin();return child.type(child.props);};
 ui.configure(summary,{orders:[],totals,page:1,pageSize:25,total:70,totalPages:3});
 nodes(render()).find(n=>n.type?.name==='PageControls').props.change(3);
 ui.configure(summary,{orders:[],totals,page:2,pageSize:25,total:2,totalPages:1});render();hooks.flushEffects();render();
 assert.equal(new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams.get('page'),'3');
 ui.configure(summary,{orders:[],totals,page:3,pageSize:25,total:2,totalPages:1});render();hooks.flushEffects();render();
 const query=new URL(ui.requests().at(-1).path,'http://local.invalid').searchParams;
 assert.equal(query.get('page'),'1');assert.equal(query.get('hall'),group.hall);assert.equal(query.get('line'),group.line);
});

test('unloaded chart and activity totals stay unknown until an authoritative response arrives',()=>{
 ui.configure(summary);const chart=view();chart.find('button',n=>n.props.className==='em-downtime-bar').props.onClick();const child=chart.all().find(n=>n.type?.name==='LineActivities');
 ui.configure(summary,undefined,{loadedPath:'',loading:true});
 const initial=renderToStaticMarkup(chart.render());
 assert.match(initial,/<strong>—<small>دقیقه<\/small><\/strong>/);
 assert.match(initial,/<strong>—<small>فعالیت<\/small><\/strong>/);
 assert.match(initial,/<strong>—<small>گروه<\/small><\/strong>/);
 hooks.reset();hooks.begin();const activities=renderToStaticMarkup(child.type(child.props));
 assert.match(activities,/<strong>—<small>دقیقه<\/small><\/strong>/);
 assert.match(activities,/<strong>—<small>نفر‌دقیقه<\/small><\/strong>/);
});

test('unknown-only totals and line values show unknown rather than fabricated zero',()=>{
 ui.configure({...summary,totalOrders:2,totals:{...totals,downtimeMinutes:0,unknownDowntimeCount:2},items:[{...group,downtimeMinutes:0,unknownDowntimeCount:2}]});
 const chart=view(),html=renderToStaticMarkup(chart.render());
 assert.match(html,/مدت توقف 2 گزارش مشخص نشده است/);assert.match(html,/نامشخص/);assert.match(html,/>—</);
 const row=chart.find('button',n=>n.props.className==='em-downtime-bar');assert.match(row.props['aria-label'],/مدت توقف نامشخص/);
});

test('activity rows expose description, people, time and consumed parts while ignoring personnel grades',()=>{
 const record={id:'O1',title:'بازدید آب‌بند',number:'EM-1',assetCode:'A1',assetName:'پمپ',status:'completed',workDate:'2026-11-05',startTime:'17:00',endTime:'18:00',description:'تعویض آب‌بند و کنترل عملکرد',downtimeMinutes:25,durationMinutes:60,participants:[{personId:'T',personName:'مجری آزمون',minutes:60,finalScore:'SENSITIVE-SCORE'}],parts:[{partId:'P',code:'00021',name:'اورینگ',quantity:2,unit:'عدد'}],score:'PRIVATE-GRADE'};
 ui.configure(summary,{orders:[record],total:26,page:1,pageSize:25,totalPages:2,totals});
 const chart=view();chart.find('button',n=>n.props.className==='em-downtime-bar').props.onClick();const child=chart.all().find(n=>n.type?.name==='LineActivities');
 hooks.reset();hooks.begin();const detail=child.type(child.props),html=renderToStaticMarkup(detail);
 for(const value of [record.description,'مجری آزمون','اورینگ','00021','مشاهده گزارش','زمان افراد','زمان تعمیر'])assert.ok(html.includes(value),value);
 assert.doesNotMatch(html,/SENSITIVE-SCORE|PRIVATE-GRADE/);
 assert.match(html,/data-pagination-total="26"/);assert.match(html,/data-pagination-size="25"/);
 ui.configure(summary,{orders:[{...record,reportedDowntimeMinutes:null}],total:1,page:1,pageSize:25,totalPages:1,totals:{...totals,unknownDowntimeCount:1}});
 hooks.reset();hooks.begin();const unknown=renderToStaticMarkup(child.type(child.props));
 assert.match(unknown,/توقف: <b>مدت نامشخص<\/b>/);
});
