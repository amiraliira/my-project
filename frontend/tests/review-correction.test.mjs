import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require=createRequire(import.meta.url),ts=require('typescript');
const {createElement:h}=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const encode=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const resolve=name=>pathToFileURL(require.resolve(name)).href;
function compile(name,imports={}) {
 let source=ts.transpileModule(readFileSync(new URL('../src/'+name,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 for(const [name,value] of Object.entries(imports))source=source.replaceAll(JSON.stringify(name),JSON.stringify(value)).replaceAll("'"+name+"'",JSON.stringify(value));
 return source;
}
const hooksUrl=encode(`let values=[],i=0;export const reset=()=>{values=[];i=0;},begin=()=>{i=0;};export function useState(initial){const n=i++;if(!(n in values))values[n]=typeof initial==='function'?initial():initial;return [values[n],value=>{values[n]=typeof value==='function'?value(values[n]):value;}];}`);
const hooks=await import(hooksUrl);
const apiUrl=encode(`let calls=[],failure='';export const reset=value=>{calls=[];failure=value||'';},requests=()=>calls;export async function post(path,body){calls.push({path,body});if(failure)throw Error(failure);return {version:8};}export const api=()=>{},put=()=>{};`);
const api=await import(apiUrl);
const uiUrl=encode(`import{createElement as h}from ${JSON.stringify(resolve('react'))};let order=null;export const setOrder=value=>{order=value;};
export const Field=({label,children})=>h('label',null,label,children),ErrorBox=({text})=>text?h('div',{role:'alert'},text):null,Modal=({title,children})=>h('div',{role:'dialog','aria-label':title},children),Icon=()=>null;
export const duration=(a,b)=>{if(!a||!b)return 0;const x=a.split(':').map(Number),y=b.split(':').map(Number);return(y[0]*60+y[1]-x[0]*60-x[1]+1440)%1440;},num=value=>String(value??0),errorText=error=>error.message;
export const useResource=()=>({data:order,loading:false,error:'',reload:()=>{}}),useDebounce=value=>value,dateFa=value=>value||'',statuses={},workTypes={},stringify=value=>JSON.stringify(value),todayIso=()=> '2026-11-03',requestId=()=> 'test-key';
export const Badge=()=>null,DataTable=()=>null,Empty=()=>null,Loading=()=>null,PageHeader=()=>null,SearchInput=()=>null;
`);
const catalogUrl=encode('export const CatalogSelect=()=>null,PageControls=()=>null;');
const time24Url=encode(`import{createElement as h}from ${JSON.stringify(resolve('react'))};export default function Time24({value,onChange,label,disabled,required}){return h('input',{type:'text',value,'aria-label':label,disabled,required,onChange:event=>onChange(event.target.value)});}`);
const quantityUrl=encode(compile('quantities.ts'));
const scoringUrl=encode(compile('scoring.ts'));
const gradingUrl=encode(compile('Grading.tsx',{'react/jsx-runtime':resolve('react/jsx-runtime'),'./ui':uiUrl,'./scoring':scoringUrl,'./permissions':encode(compile('permissions.ts'))}));
const imports={'react':hooksUrl,'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':apiUrl,'./Catalog':catalogUrl,'./Time24':time24Url,'./quantities':quantityUrl,'./ui':uiUrl,'./scoring':scoringUrl,'./Grading':gradingUrl};
const moduleUrl=encode(compile('ReviewCorrection.tsx',imports));
const correction=await import(moduleUrl);
const user={id:'supervisor',role:'supervisor',specialty:'مکانیک',name:'سرپرست'};
const order={id:'order-1',number:'EM-1',version:7,status:'supervisor',title:'تعمیر پمپ',assetCode:'A1',assetName:'پمپ',workDate:'2026-10-10',startTime:'08:00',endTime:'09:00',durationMinutes:60,canReviewCorrect:true,canDecide:true,decisionStage:'supervisor',decisionSpecialty:'مکانیک',participants:[{personId:'m1',personName:'مکانیک',specialty:'مکانیک',minutes:60},{personId:'e1',personName:'برقکار',specialty:'برق',minutes:30}],parts:[{partId:'old',code:'P1',name:'قطعه قبلی',unit:'عدد',quantity:1}],approvals:[]};
function nodes(value){if(Array.isArray(value))return value.flatMap(nodes);return value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];}
function harness(record=order,actor=user){hooks.reset();let saved=0;const props={order:record,context:{user:actor,notify:()=>{}},close:()=>{},saved:()=>{saved++;}};const render=()=>{hooks.begin();return correction.default(props);};const find=(type,predicate=()=>true)=>nodes(render()).find(n=>n.type===type&&predicate(n));return{render,find,saved:()=>saved};}
const reasonInput=view=>view.find('textarea');
const button=(view,text)=>view.find('button',n=>n.props.children===text);
const gradeInput=view=>nodes(view.render()).find(node=>typeof node.props?.setValues==='function'&&node.props?.values);
const minutesInput=view=>view.find('input',n=>n.props.type==='number');

test('payload corrects own minutes and full part roster with exact six-place quantity, never other specialties',()=>{
 const draft=correction.initialCorrection(order);draft.reason='اصلاح مصرف واقعی';draft.grades={m1:'0'};draft.minutes.m1='45';draft.minutes.e1='999';draft.parts=[{partId:'new',quantity:'0.000001',name:'قطعه جدید'}];
 const result=correction.correctionPayload(order,user,draft);
 assert.deepEqual(result,{version:7,reason:'اصلاح مصرف واقعی',startTime:'08:00',endTime:'09:00',participants:[{personId:'m1',minutes:45}],parts:[{partId:'new',quantity:'0.000001'}],grades:[{personId:'m1',coefficient:'0'}]});
 assert.throws(()=>correction.correctionPayload(order,{...user,role:'head'},draft),/مرحله/);
 assert.throws(()=>correction.correctionPayload({...order,canReviewCorrect:false},user,draft),/مرحله/);
});

test('validation rejects invalid edits and grades while allowing approval without edits',()=>{
 const changed=()=>({...correction.initialCorrection(order),reason:'علت اصلاح',grades:{m1:'2'},minutes:{m1:'45',e1:'30'}});
 for(const amount of ['0','-1','0.0000001','NaN','1000000001']){const draft=changed();draft.parts=[{partId:'old',quantity:amount}];assert.throws(()=>correction.correctionPayload(order,user,draft),/مقدار/);}
 for(const amount of ['','0','61','1.5']){const draft=changed();draft.minutes.m1=amount;assert.throws(()=>correction.correctionPayload(order,user,draft),/زمان هر نفر/);}
 assert.throws(()=>correction.correctionPayload(order,user,{...changed(),reason:' ' }),/علت/);
 assert.throws(()=>correction.correctionPayload(order,user,{...changed(),endTime:'08:00'}),/یکسان/);
 assert.throws(()=>correction.correctionPayload(order,user,{...changed(),minutes:{m1:'10',e1:'30'},endTime:'08:15'}),/تخصص دیگر/);
 const duplicate=changed();duplicate.parts.push({...duplicate.parts[0]});assert.throws(()=>correction.correctionPayload(order,user,duplicate),/بدون تکرار/);
 const approval=correction.correctionPayload(order,user,{...correction.initialCorrection(order),grades:{m1:'0'}});assert.equal(approval.reason,'');assert.deepEqual(approval.grades,[{personId:'m1',coefficient:'0'}]);
 assert.throws(()=>correction.correctionPayload(order,user,{...changed(),grades:{}}),/نمره/);
 assert.throws(()=>correction.correctionPayload(order,user,{...changed(),grades:{m1:'3.1'}}),/نمره/);
 const pm={...order,pmWeeklyPlanId:'weekly',participants:[order.participants[0]]};assert.throws(()=>correction.correctionPayload(pm,user,changed()),/برگهٔ PM/);
});

test('review shows before and after for removed/added parts and edited minutes; empty roster explicitly removes all parts',()=>{
 const draft=correction.initialCorrection(order);draft.minutes.m1='40';draft.parts=[{partId:'new',name:'قطعه جدید',quantity:'2'}];draft.reason='مصرف واقعی';draft.grades={m1:'2'};
 const changes=correction.correctionChanges(order,draft);
 assert.equal(changes.length,3);assert.ok(changes.some(c=>c.after==='حذف از مصرف این گزارش'));assert.ok(changes.some(c=>c.before==='در مصرف گزارش نبود'));
 draft.parts=[];assert.deepEqual(correction.correctionPayload(order,user,draft).parts,[]);
});

test('editing after review invalidates save; confirmed post sends the reviewed correction and preserves draft on conflict',async()=>{
 api.reset('گزارش هم‌زمان تغییر کرده است');const view=harness();gradeInput(view).props.setValues({m1:'2'});
 assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,true);
 minutesInput(view).props.onChange({target:{value:'45'}});reasonInput(view).props.onChange({target:{value:'زمان واقعی اصلاح شد'}});
 button(view,'بررسی و تأیید').props.onClick();assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,false);assert.match(renderToStaticMarkup(view.render()),/آمادهٔ ثبت و تأیید/);
 reasonInput(view).props.onChange({target:{value:'زمان و مصرف واقعی اصلاح شد'}});assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,true);
 button(view,'بررسی و تأیید').props.onClick();await button(view,'ثبت اصلاحات و تأیید').props.onClick();
 const request=api.requests()[0];assert.equal(request.path,'/orders/order-1/review-correction');assert.equal(request.body.participants[0].minutes,45);assert.deepEqual(request.body.grades,[{personId:'m1',coefficient:'2'}]);assert.equal(request.body.reason,'زمان و مصرف واقعی اصلاح شد');
 assert.equal(view.saved(),0);assert.equal(minutesInput(view).props.value,'45');assert.match(renderToStaticMarkup(view.render()),/اطلاعات واردشده حفظ شده است/);
 api.reset();await button(view,'ثبت اصلاحات و تأیید').props.onClick();assert.equal(view.saved(),1);
});

test('part deletion and adding a catalog part invalidate preview and affect the complete consumption list',()=>{
 const view=harness();gradeInput(view).props.setValues({m1:'2'});reasonInput(view).props.onChange({target:{value:'اصلاح قطعه'}});
 view.find('button',n=>String(n.props['aria-label']).startsWith('حذف قطعه')).props.onClick();
 button(view,'بررسی و تأیید').props.onClick();assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,false);
 const picker=()=>nodes(view.render()).find(n=>n.props?.kind==='parts');picker().props.onChange('new');picker().props.onSelected({id:'new',code:'P2',name:'قطعه جدید',unit:'عدد'});
 button(view,'افزودن قطعه').props.onClick();assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,true);
 button(view,'بررسی و تأیید').props.onClick();assert.match(renderToStaticMarkup(view.render()),/قطعه جدید/);
 assert.doesNotMatch(renderToStaticMarkup(view.render()),/aria-label="حذف قطعه قطعه قبلی"/);
});

const ui=await import(uiUrl);
const ordersUrl=encode(compile('Orders.tsx',{'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':apiUrl,'./Catalog':catalogUrl,'./Time24':time24Url,'./ui':uiUrl,'./quantities':quantityUrl,'./ReviewCorrection':moduleUrl,'./PmWork':encode('export const PmExecution=()=>null,PmOrderSheet=()=>null;'),'./Workflow':encode('export const WorkflowProgress=()=>null;'),'./Grading':encode('export const HeadGradeInputs=()=>null,ParticipantGrades=()=>null,SupervisorGradeInputs=()=>null;'),'./scoring':encode('export const participantGradePayload=()=>null;'),'./permissions':encode('export const mayViewGrades=()=>false;')}));
const {OrderDetail,initialReportParticipants,recordedStopText}=await import(ordersUrl);

test('new supervisor and technician reports begin with their own participation, while assignments and existing rosters stay distinct',()=>{
 const supervisor={id:'sm',name:'سرپرست مکانیک',role:'supervisor',specialty:'مکانیک'};
 assert.deepEqual(initialReportParticipants(supervisor),[{personId:'sm',personName:'سرپرست مکانیک',specialty:'مکانیک',minutes:0}]);
 assert.deepEqual(initialReportParticipants({...supervisor,id:'m1',role:'technician'}),[{personId:'m1',personName:'سرپرست مکانیک',specialty:'مکانیک',minutes:0}]);
 assert.deepEqual(initialReportParticipants(supervisor,undefined,true),[]);
 for(const role of ['head','manager','planner','admin'])assert.deepEqual(initialReportParticipants({...supervisor,role}),[]);
 assert.equal(initialReportParticipants(supervisor,order),order.participants);
 assert.equal(initialReportParticipants(supervisor,order,true),order.participants);
});

test('mixed supervisor own work is head-only from zero to nine and same-team technician grades remain separate',async()=>{
 const scoring=await import(scoringUrl);
 const self={personId:'sm',personName:'سرپرست مکانیک',specialty:'مکانیک',minutes:60,gradingPolicy:'head-only-v1'};
 const frozen={...order,decisionSpecialty:'مکانیک',participants:[self,...order.participants]};
 assert.deepEqual(scoring.participantGradePayload(frozen,{sm:'3',m1:'2',e1:'3'},'supervisor'),{grades:[{personId:'m1',coefficient:'2'}]});
 assert.equal(scoring.participantGradePayload({...frozen,participants:[self]},{sm:'3'},'supervisor'),null);
 assert.equal(scoring.headGradeMaximum(self),9);
 assert.deepEqual(scoring.participantGradePayload(frozen,{sm:'9',m1:'3',e1:'0'},'head'),{participantScores:[{personId:'sm',coefficient:'9'},{personId:'m1',coefficient:'3'},{personId:'e1',coefficient:'0'}]});
 assert.equal(scoring.participantGradePayload(frozen,{sm:'9.1',m1:'3',e1:'0'},'head'),null);
 assert.equal(scoring.participantGradePayload(frozen,{sm:'9',m1:'4',e1:'0'},'head'),null);
});
test('actual order detail exposes the correction button only for server-authorized supervisors',()=>{
 for(const [role,flag,expected]of [['supervisor',true,true],['supervisor',false,false],['head',true,false],['technician',true,false]]){
  ui.setOrder({...order,canReviewCorrect:flag});const html=renderToStaticMarkup(h(OrderDetail,{id:order.id,context:{user:{...user,role},boot:{people:[]},notify:()=>{},refresh:async()=>{}},close:()=>{},edit:()=>{},changed:()=>{}}));
  assert.equal(html.includes('اصلاح زمان و قطعات'),expected,role+':'+flag);assert.doesNotMatch(html,/برگشت برای اصلاح/);
 }
});


test('changing a grade after preview requires reviewing the approval again',()=>{
 api.reset();const view=harness();gradeInput(view).props.setValues({m1:'2'});
 button(view,'بررسی و تأیید').props.onClick();assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,false);
 gradeInput(view).props.setValues(previous=>({...previous,m1:'0'}));assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,true);
 button(view,'بررسی و تأیید').props.onClick();assert.equal(button(view,'ثبت اصلاحات و تأیید').props.disabled,false);
 assert.match(renderToStaticMarkup(view.render()),/نمرهٔ مکانیک: 0/);
});

test('carried specialty approvals clearly preserve their original evidence and identify the correcting supervisor',()=>{
 ui.setOrder({...order,approvals:[{id:'carried',stage:'supervisor',specialty:'برق',decision:'approve',actorName:'سرپرست اصلی برق',createdAt:'2026-10-10',carriedFromApprovalId:'original',carriedFromRevision:2,carriedByName:'اصلاح‌کننده مکانیک',carriedAt:'2026-11-04'}]});
 const html=renderToStaticMarkup(h(OrderDetail,{id:order.id,context:{user,boot:{people:[]},notify:()=>{},refresh:async()=>{}},close:()=>{},edit:()=>{},changed:()=>{}}));
 assert.match(html,/سرپرست اصلی برق/);assert.match(html,/تأیید ثبت‌شدهٔ نسخهٔ 2/);assert.match(html,/اصلاح‌کننده مکانیک/);assert.doesNotMatch(html,/برگشت برای اصلاح/);
});

const reportHooksUrl=encode(`let values=[],i=0;export const reset=()=>{values=[];i=0;},begin=()=>{i=0;};export function useState(initial){const n=i++;if(!(n in values))values[n]=typeof initial==='function'?initial():initial;return[values[n],value=>{values[n]=typeof value==='function'?value(values[n]):value;}];}export function useRef(initial){const n=i++;return values[n]||(values[n]={current:initial});}export const useEffect=()=>{},useCallback=value=>value,lazy=()=>()=>null,Suspense=({children})=>children;`);
const reportHooks=await import(reportHooksUrl);
const reportApiUrl=encode(`let mode,draft,sent,calls=[];export const configure=(next,initial,submitted)=>{mode=next;draft=initial;sent=submitted;calls=[];},requests=()=>calls;export async function put(path,body){calls.push({method:'PUT',path,body});return{...draft,version:draft.version+1};}export async function post(path,body){calls.push({method:'POST',path,body});if(path.endsWith('/submit')){if(mode==='success')return sent;throw Error('خطای ارسال');}return draft;}export async function api(path){calls.push({method:'GET',path});if(mode==='recovered')return sent;throw Error('خطای دریافت');}`);
const reportApi=await import(reportApiUrl);
const reportEditorModule=await import(encode(compile('Orders.tsx',{'react':reportHooksUrl,'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':reportApiUrl,'./Catalog':catalogUrl,'./Time24':time24Url,'./ui':uiUrl,'./quantities':quantityUrl,'./ReviewCorrection':moduleUrl,'./PmWork':encode('export const PmExecution=()=>null,PmOrderSheet=()=>null;'),'./Workflow':encode('export const WorkflowProgress=()=>null;'),'./Grading':encode('export const HeadGradeInputs=()=>null,ParticipantGrades=()=>null,SupervisorGradeInputs=()=>null;'),'./scoring':encode('export const participantGradePayload=()=>null;'),'./permissions':encode('export const mayViewGrades=()=>false;')})));
function reportEditor(mode='success',assignmentMode=false,existing=true){
 reportHooks.reset();const actions=[],received=[];
 const draft={...order,type:assignmentMode?'PM':'EM',status:'draft',createdBy:user.id,assetId:'asset',description:'تعمیر و آزمون عملکرد پمپ',assigneeId:assignmentMode?'m1':null};
 const sent={...draft,status:'supervisor',version:9,canDecide:true,decisionSpecialty:'مکانیک'};reportApi.configure(mode,draft,sent);
 const props={existing:existing?draft:undefined,assignmentMode,context:{user,boot:{people:[],assets:[],parts:[]},notify:()=>{},refresh:async()=>{}},saved:()=>actions.push('saved'),close:()=>actions.push('close'),onSubmitted:value=>{actions.push('submitted');received.push(value);}};
 const render=()=>{reportHooks.begin();const child=reportEditorModule.OrderEditor(props);return child.type(child.props);};
 const find=(type,predicate=()=>true)=>nodes(render()).find(node=>node.type===type&&predicate(node));
 return{actions,received,sent,find,render};
}
const reportEvent=()=>({preventDefault(){},currentTarget:{form:{reportValidity:()=>true}}});

test('a new supervisor own-work report opens directly for execution without assignment fields; existing assignments remain editable',()=>{
 const view=reportEditor('success',false,false),own=renderToStaticMarkup(view.render());
 assert.match(own,/ثبت گزارش کار/);
 assert.ok(nodes(view.render()).some(node=>node.props?.fallbackLabel===user.name));
 assert.doesNotMatch(own,/تخصیص به|موعد انجام|انتخاب مجری/);
 const prior=renderToStaticMarkup(reportEditor().render());
 assert.match(prior,/تخصیص به/);
 assert.match(prior,/موعد انجام/);
 assert.match(renderToStaticMarkup(reportEditor('success',true,false).render()),/تخصیص کار PM جدید/);
});

test('ordinary submit and confirmed lost-response recovery hand the authoritative order to the caller exactly once after close',async()=>{
 for(const mode of ['success','recovered']){
  const view=reportEditor(mode),submit=view.find('button',node=>node.props.type==='button'&&node.props.className==='button primary');
  submit.props.onClick(reportEvent());await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(view.received,[view.sent],mode);assert.deepEqual(view.actions.slice(-3),['saved','close','submitted'],mode);
  assert.equal(reportApi.requests().filter(request=>request.path.endsWith('/submit')).length,1,mode);
  assert.equal(reportApi.requests().filter(request=>request.path.endsWith('/decision')).length,0,'submission must not add grading');
 }
});

test('draft, PM assignment and failed submission never request a post-submit review',async()=>{
 for(const assignmentMode of [false,true]){
  const view=reportEditor('success',assignmentMode);await view.find('form').props.onSubmit(reportEvent());
  assert.deepEqual(view.received,[]);assert.equal(view.actions.filter(action=>action==='close').length,1);
  assert.equal(reportApi.requests().some(request=>request.path.endsWith('/submit')),false);
 }
 const failed=reportEditor('failed');failed.find('button',node=>node.props.type==='button'&&node.props.className==='button primary').props.onClick(reportEvent());await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(failed.received,[]);assert.equal(failed.actions.includes('close'),false);
});


test('report detail distinguishes measured zero, unknown stop length, and absent stop status',()=>{
 assert.equal(recordedStopText({productionStopped:true,downtimeMinutes:0}),'0 دقیقه');
 assert.equal(recordedStopText({productionStopped:true,downtimeMinutes:25}),'25 دقیقه');
 for(const value of [undefined,null,'25',true,-1,1441,12.5])assert.equal(recordedStopText({productionStopped:true,downtimeMinutes:value}),'مدت توقف ثبت نشده');
 assert.equal(recordedStopText({productionStopped:false,downtimeMinutes:25}),'بدون توقف ثبت‌شده');
 for(const value of [undefined,null,'true',1])assert.equal(recordedStopText({productionStopped:value,downtimeMinutes:25}),'وضعیت توقف ثبت نشده');
});
