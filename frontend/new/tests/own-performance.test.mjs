import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const require=createRequire(import.meta.url),ts=require('typescript');
const {createElement:h}=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const encode=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const resolve=name=>pathToFileURL(require.resolve(name)).href;
function compile(name,imports={}) {
 let source=ts.transpileModule(readFileSync(new URL('../src/'+name,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 for(const [name,url] of Object.entries(imports))source=source.replaceAll(JSON.stringify(name),JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
 return source;
}
const uiUrl=encode(`
 import {createElement as h} from ${JSON.stringify(resolve('react'))};
 let report=null,options={},requests=[],reloads=0;
 export const configure=(value,settings={})=>{report=value;options=settings;requests=[];reloads=0;};
 export const snapshot=()=>({requests,reloads});
 export const useResource=(path,initial,poll=0,enabled=true)=>{
  if(path.startsWith('/performance?')){requests.push({path,poll,enabled});return {data:enabled?report:initial,loadedPath:enabled?(options.loadedPath??path):'',loading:!!options.loading,error:options.error||'',reload:()=>{reloads++;}};}
  return {data:initial,loadedPath:path,loading:false,error:'',reload:()=>{},updatedAt:1};
 };
 export const Field=({label,children})=>h('label',null,label,children),ErrorBox=({text})=>text?h('div',{role:'alert'},text):null;
 export const DataTable=({data,columns,empty})=>data.length?h('table',null,h('thead',null,h('tr',null,columns.map(column=>h('th',{key:column.title},column.title)))),h('tbody',null,data.map((person,index)=>h('tr',{key:person.personId||index},columns.map(column=>h('td',{key:column.title},column.cell(person))))))):empty||null;
 export const Empty=({title,detail})=>h('p',null,title,detail),Loading=()=>h('p',{role:'status'},'در حال دریافت اطلاعات…'),Icon=()=>null,Badge=()=>null,SearchInput=()=>null;
 export const num=value=>String(value??0),dateFa=value=>value||'—',todayIso=()=> '2026-11-03',duration=()=>0,errorText=error=>error.message,requestId=()=> 'test-request',useDebounce=value=>value;
 export const roles={technician:'تکنسین',supervisor:'سرپرست',planner:'کارشناس',head:'رئیس',manager:'مدیر',admin:'مدیر سامانه'},statuses={},workTypes={PM:'PM',EM:'EM',PROJECT:'پروژه'};
`);
const ui=await import(uiUrl);
const performanceImports={'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./ui':uiUrl};
const performanceUrl=encode(compile('Performance.tsx',performanceImports));
const {default:PersonnelPerformance,OwnPerformance}=await import(performanceUrl);
const user=(role,id='viewer')=>({id,role,name:'کاربر آزمون',code:'123',specialty:'مکانیک'});
const detail={orderId:'order-own',number:'EM-100',workDate:'2026-11-02',type:'EM',title:'فعالیت نهایی خودم',personId:'viewer',personName:'کاربر آزمون',specialty:'مکانیک',minutes:60,hours:1,supervisorCoefficient:2,headCoefficient:1.5,score:3,source:'individual'};
const person={personId:'viewer',name:'کاربر آزمون',code:'123',specialty:'مکانیک',active:true,minutes:60,hours:1,activityCount:1,score:3,pm:0,em:1,project:0};
const report={period:{kind:'monthly',year:1405,month:8,startDate:'2026-10-23',endDate:'2026-11-21',label:'آبان ۱۴۰۵'},scope:'own',formula:'frozen-policy',years:[1404,1405],people:[person,{...person,personId:'other',name:'OTHER-PERSON-NOT-FOR-OWN-VIEW'}],totals:{peopleCount:1,minutes:60,hours:1,activityCount:1,score:3,unscoredCount:0},details:[detail],detailPage:1,detailPageSize:20,detailTotal:1};

test('all six roles see a loaded personal summary with details initially collapsed and no personnel selectors or rankings',()=>{
 for(const role of ['technician','supervisor','planner','head','manager','admin']) {
  ui.configure(report);
  const html=renderToStaticMarkup(h(OwnPerformance,{user:user(role)}));
  assert.match(html,/aria-label="عملکرد من"/);assert.match(html,/aria-expanded="false"/);
  for(const label of ['امتیاز نهایی من','ساعت تأییدشده من','فعالیت نهایی من','ریز فعالیت‌ها +'])assert.ok(html.includes(label),role+': '+label);
  assert.doesNotMatch(html,/performance-controls|performance-formula/);
  assert.doesNotMatch(html,/performance-detail-list|فعالیت نهایی خودم/);
  assert.doesNotMatch(html,/OTHER-PERSON-NOT-FOR-OWN-VIEW|رتبه \/ نام|همه نفرات مجاز|نفر در حوزه دسترسی/);
  const request=ui.snapshot().requests.at(-1),url=new URL('http://local'+request.path);
  assert.equal(request.enabled,true);assert.equal(request.poll,30000);assert.equal(url.searchParams.get('scope'),'own');assert.equal(url.searchParams.get('period'),'monthly');assert.equal(url.searchParams.get('detailPageSize'),'20');
  assert.equal(url.searchParams.has('personId'),false);
 }
});

const hooksUrl=encode(`let slots=[],cursor=0;export const reset=()=>{slots=[];cursor=0;},begin=()=>{cursor=0;};export function useState(initial){const index=cursor++;if(!(index in slots))slots[index]=typeof initial==='function'?initial():initial;return [slots[index],value=>{slots[index]=typeof value==='function'?value(slots[index]):value;}];}export function useRef(initial){const index=cursor++;return slots[index]??(slots[index]={current:initial});}export const useMemo=callback=>callback(),useEffect=()=>{};`);
const hooks=await import(hooksUrl);
const interactive=await import(encode(compile('Performance.tsx',{...performanceImports,'react':hooksUrl})));
function nodes(element) {
 if(Array.isArray(element))return element.flatMap(nodes);
 return element&&typeof element==='object'?[element,...nodes(element.props?.children)]:[];
}
function harness(own=true,role='technician') {
 hooks.reset();
 const entry=(own?interactive.OwnPerformance:interactive.default)({user:user(role)});
 const render=()=>{hooks.begin();return entry.type(entry.props);};
 const find=(type,predicate=()=>true)=>nodes(render()).find(node=>node.type===type&&predicate(node));
 return {render,find};
}

test('own report distinguishes valid zero from unscored history and renders the frozen head-only formula without a supervisor factor',()=>{
 ui.configure({...report,totals:{...report.totals,score:0,activityCount:2,unscoredCount:1},details:[{...detail,supervisorCoefficient:null,headCoefficient:0,score:0,source:'head-only'},{...detail,orderId:'old-unscored',title:'سابقهٔ بدون نمره',supervisorCoefficient:null,headCoefficient:null,score:null,source:null}]});
 const view=harness(true,'supervisor');view.find('button',node=>node.props.className==='performance-toggle').props.onClick();
 const html=renderToStaticMarkup(view.render());
 assert.match(html,/<b>0<\/b><span>امتیاز نهایی من/);
 assert.match(html,/نمرهٔ مستقیم رئیس \(۰ تا ۹\) × ساعت مشارکت/);
 assert.match(html,/کار سرپرست: نمره رئیس \(۰ تا ۹\) × ساعت مشارکت/);
 assert.match(html,/سابقه نمره کامل نیست؛ امتیاز محاسبه نشده است/);
 const direct=html.match(/<div class="performance-calculation head-only">[\s\S]*?<\/article>/)?.[0];
 assert.ok(direct);assert.doesNotMatch(direct,/<span>سرپرست<\/span>/);assert.match(direct,/<span>رئیس<\/span><b>0<\/b>/);assert.match(direct,/<span>امتیاز<\/span><b>0<\/b>/);
});

test('monthly, all-period, paging and refresh controls keep own scope and reset detail pages',()=>{
 ui.configure({...report,detailPage:2,detailTotal:45});const view=harness();
 view.find('button',node=>node.props.className==='performance-toggle').props.onClick();
 const next=()=>view.find('button',node=>node.props.children==='بعدی');
 next().props.onClick();view.render();
 let url=new URL('http://local'+ui.snapshot().requests.at(-1).path);assert.equal(url.searchParams.get('detailPage'),'2');
 view.find('select',node=>node.props.value==='monthly').props.onChange({target:{value:'all'}});view.render();
 url=new URL('http://local'+ui.snapshot().requests.at(-1).path);assert.equal(url.searchParams.get('scope'),'own');assert.equal(url.searchParams.get('period'),'all');assert.equal(url.searchParams.get('detailPage'),'1');assert.equal(url.searchParams.has('year'),false);assert.equal(url.searchParams.has('month'),false);assert.equal(url.searchParams.has('personId'),false);
 view.find('select',node=>node.props.value==='all').props.onChange({target:{value:'monthly'}});
 view.find('select',node=>node.props.value===8).props.onChange({target:{value:'7'}});view.render();
 url=new URL('http://local'+ui.snapshot().requests.at(-1).path);assert.equal(url.searchParams.get('month'),'7');assert.equal(url.searchParams.get('detailPage'),'1');
 view.find('button',node=>node.props.className==='button secondary small').props.onClick();assert.equal(ui.snapshot().reloads,1);
});

test('personal summary stays visible while period controls follow the details panel',()=>{
 ui.configure(report);const view=harness();
 const toggle=()=>view.find('button',node=>node.props.className==='performance-toggle');
 assert.equal(toggle().props['aria-expanded'],false);
 toggle().props.onClick();let html=renderToStaticMarkup(view.render());
 assert.match(html,/aria-expanded="true"/);assert.match(html,/ریز فعالیت‌های من/);assert.match(html,/فعالیت نهایی خودم/);
 toggle().props.onClick();html=renderToStaticMarkup(view.render());
 assert.match(html,/aria-expanded="false"/);assert.doesNotMatch(html,/performance-detail-list|فعالیت نهایی خودم/);
 for(const label of ['امتیاز نهایی من','ساعت تأییدشده من','فعالیت نهایی من'])assert.ok(html.includes(label),label);
 assert.doesNotMatch(html,/performance-controls/);
 assert.equal(ui.snapshot().requests.at(-1).enabled,true);assert.equal(ui.snapshot().requests.at(-1).poll,30000);
});

test('personnel report remains head-only and separate from the personal scope',()=>{
 for(const role of ['technician','supervisor','planner','manager','admin']) {
  ui.configure(report);assert.equal(renderToStaticMarkup(h(PersonnelPerformance,{user:user(role)})),'');assert.equal(ui.snapshot().requests.length,0);
 }
 ui.configure({...report,scope:'all'});const view=harness(false,'head');
 view.find('button',node=>node.props.className==='performance-toggle').props.onClick();
 const html=renderToStaticMarkup(view.render());
 assert.match(html,/عملکرد کارکنان فنی|رتبه \/ نام/);assert.match(html,/همه نفرات مجاز/);assert.match(html,/OTHER-PERSON-NOT-FOR-OWN-VIEW/);
 assert.equal(new URL('http://local'+ui.snapshot().requests.at(-1).path).searchParams.get('scope'),'all');
 assert.notEqual(OwnPerformance({user:user('head','first')}).key,OwnPerformance({user:user('head','second')}).key);
 assert.notEqual(OwnPerformance({user:user('head')}).key,PersonnelPerformance({user:user('head')}).key);
 assert.notEqual(OwnPerformance({user:user('head')}).key,OwnPerformance({user:user('supervisor')}).key);
});

test('own panel keeps a visible empty, loading or error state and excludes prior-period response data',()=>{
 ui.configure({...report,people:[],details:[],totals:{peopleCount:0,minutes:0,hours:0,activityCount:0,score:0},detailTotal:0});
 let html=renderToStaticMarkup(h(OwnPerformance,{user:user('technician')}));assert.match(html,/<b>0<\/b><span>امتیاز نهایی من/);
 const view=harness();view.find('button',node=>node.props.className==='performance-toggle').props.onClick();assert.match(renderToStaticMarkup(view.render()),/در این بازه فعالیت تأییدشده‌ای/);
 ui.configure(null,{loading:true});html=renderToStaticMarkup(h(OwnPerformance,{user:user('technician')}));assert.match(html,/عملکرد من/);assert.match(html,/role="status"/);
 ui.configure(null,{error:'خطای آزمون عملکرد'});html=renderToStaticMarkup(h(OwnPerformance,{user:user('supervisor')}));assert.match(html,/role="alert"/);assert.match(html,/خطای آزمون عملکرد/);
 ui.configure({...report,totals:{...report.totals,score:987654321}},{loadedPath:'/performance?previous-period'});html=renderToStaticMarkup(h(OwnPerformance,{user:user('technician')}));assert.doesNotMatch(html,/987654321|فعالیت نهایی خودم/);
});

const catalogUrl=encode('export const CatalogSelect=()=>null,PageControls=()=>null,emptyPage=()=>({items:[],total:0,page:1,totalPages:0});');
const ordersUrl=encode('export const OrderDetail=()=>null,OrderEditor=()=>null;');
const workUrl=encode('export const PmExecution=()=>null,PmTaskList=()=>null,pmStates={};');
const apiUrl=encode('const blocked=()=>{throw Error("Inbox render must not call a backend");};export const api=blocked,post=blocked,put=blocked;');
const inboxImports={'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./ui':uiUrl,'./Performance':performanceUrl,'./Catalog':catalogUrl,'./Orders':ordersUrl,'./PmWork':workUrl,'./Time24':encode('export default()=>null;'),'./api':apiUrl,'./JalaliDate':encode('export const JalaliDate=()=>null;'),'./quantities':encode(compile('quantities.ts')),'./pmAlarm':encode(compile('pmAlarm.ts'))};
const {default:TechnicianInbox}=await import(encode(compile('TechnicianRegistration.tsx',inboxImports).replace("import './technician-pm.css';",'')));
const {default:ManagementInbox}=await import(encode(compile('ManagementHome.tsx',inboxImports)));
test('every inbox mounts personal performance immediately while the personnel report is opened from the head module',()=>{
 for(const role of ['technician','supervisor','planner','head','manager','admin']) {
  ui.configure(report);
  const props={user:user(role),boot:{people:[],assets:[],parts:[],locations:[],specialties:[],assetLines:[]},notify:()=>{},refresh:async()=>{},navigate:()=>{}};
  const html=renderToStaticMarkup(h(role==='technician'?TechnicianInbox:ManagementInbox,props));
  assert.equal(html.match(/aria-label="عملکرد من"/g)?.length,1,role);
  assert.match(html,/امتیاز نهایی من/);assert.match(html,/فعالیت نهایی من/);assert.doesNotMatch(html,/performance-detail-list|فعالیت نهایی خودم/);
  assert.equal(ui.snapshot().requests.filter(request=>request.enabled&&new URL('http://local'+request.path).searchParams.get('scope')==='own').length,1,role);
  assert.equal(html.includes('aria-label="عملکرد کارکنان فنی"'),false);
  if(role==='head')assert.match(html,/عملکرد کارکنان/);
 }
});
