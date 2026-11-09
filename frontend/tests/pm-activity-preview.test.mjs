import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const require=createRequire(import.meta.url),ts=require('typescript');
const {renderToStaticMarkup}=require('react-dom/server');
const encode=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const resolve=name=>pathToFileURL(require.resolve(name)).href;
function compile(name,imports={}) {
 let source=ts.transpileModule(readFileSync(new URL('../src/'+name,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 for(const [name,url] of Object.entries(imports))source=source.replaceAll(JSON.stringify(name),JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
 return source;
}
// Event tests keep hook state across explicit renders. The real sheet renderer
// below renders the endpoint response; this harness never builds sheet geometry.
const hooksUrl=encode(`let slots=[],cursor=0;export const reset=()=>{slots=[];cursor=0;},begin=()=>{cursor=0;};export function useState(initial){const index=cursor++;if(!(index in slots))slots[index]=typeof initial==='function'?initial():initial;return [slots[index],value=>{slots[index]=typeof value==='function'?value(slots[index]):value;}];}export function useRef(initial){const index=cursor++;return slots[index]??(slots[index]={current:initial});}`);
const apiUrl=encode(`export class ApiError extends Error{constructor(message,status){super(message);this.status=status;}}let handler;export const configure=value=>{handler=value;};export const post=(path,payload)=>handler('POST',path,payload),put=(path,payload)=>handler('PUT',path,payload);export const api=()=>{throw Error('Unexpected request');},download=api;`);
const uiUrl=encode(`import {createElement as h} from ${JSON.stringify(resolve('react'))};let n=0;export const requestId=()=> 'activity-test-'+(++n),num=value=>String(value??0),dateFa=value=>value||'',errorText=error=>error.message,todayIso=()=> '2026-11-03';export const ErrorBox=({text})=>text?h('div',{role:'alert'},text):null,Field=({label,children})=>h('label',null,label,children),Icon=()=>null,Empty=()=>null,Loading=()=>null,Modal=()=>null,PageHeader=()=>null,SearchInput=()=>null,useDebounce=value=>value,useResource=()=>{throw Error('Unexpected resource request');};`);
const catalogUrl=encode('export const CatalogSelect=()=>null,ServerTable=()=>null,emptyPage=()=>({items:[]});');
const libraryUrl=encode(compile('PmLibrary.tsx',{'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':apiUrl,'./ui':uiUrl,'./Catalog':catalogUrl,'./JalaliDate':encode('export const JalaliDate=()=>null;')}).replace("import './pm.css';",''));
const governanceUrl=encode(compile('pmGovernance.ts',{'react':hooksUrl,'./ui':uiUrl}));
const {default:Editor}=await import(encode(compile('PmActivityEditor.tsx',{'react':hooksUrl,'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':apiUrl,'./ui':uiUrl,'./PmLibrary':libraryUrl,'./permissions':encode(compile('permissions.ts')),'./pmGovernance':governanceUrl})));
const hooks=await import(hooksUrl),transport=await import(apiUrl);
const cell=(id,text,changes={})=>({id,text,colSpan:1,rowSpan:1,editable:false,activity:false,...changes});
const template={id:'template/a',version:7,title:'برگهٔ روانکاری',specialty:'مکانیک',hall:'فرایند',period:'هفتگی',issues:[],mappingStatus:'verified',source:{},tables:[{id:'t1',layoutVersion:1,direction:'rtl',rows:[
 {id:'r1',cells:[cell('a1','روانکاری یاتاقان',{activity:true}),cell('grease1','NLGI 2'),cell('dose1','20 g'),cell('result1','نتیجهٔ بازدید',{editable:true})]},
 {id:'r2',cells:[cell('a2','بررسی نشتی',{activity:true}),cell('note2','بررسی عینی')]},
]}]};
// Fixed mock transport response, including static cells and result cells. Only
// the backend is responsible for deciding and constructing these rows/IDs.
const prospective={...template,version:8,originalParagraphs:['راهنمای کامل برگه از پاسخ سرور'],tables:[{...template.tables[0],rows:[template.tables[0].rows[0],{id:'server-clone-r1',cells:[cell('server-clone-a1','روانکاری موتور',{activity:true}),cell('server-clone-grease','NLGI 2'),cell('server-clone-dose','20 g'),cell('server-clone-result','نتیجهٔ بازدید',{editable:true})]},template.tables[0].rows[1]]}]};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function nodes(element) {
 if(Array.isArray(element))return element.flatMap(nodes);
 if(!element||typeof element!=='object')return [];
 return [element,...nodes(element.props?.children)];
}
function text(element) {
 if(Array.isArray(element))return element.map(text).join('');
 return element&&typeof element==='object'?text(element.props?.children):element==null?'':String(element);
}
function harness(handler) {
 hooks.reset();transport.configure(handler);
 const busy=[],notifications=[];let saved=0;
 const props={template,context:{user:{role:'head'},notify:value=>notifications.push(value)},cancel:()=>{},saved:()=>{saved++;},onBusy:value=>busy.push(value)};
 const render=()=>{hooks.begin();return Editor(props);};
 const find=(type,predicate=()=>true)=>nodes(render()).find(node=>node.type===type&&predicate(node));
 const button=label=>find('button',node=>text(node.props.children)===label);
 const activity=(id,value)=>{find('textarea',node=>node.props.id===id).props.onChange({target:{value}});};
 const reason=value=>find('textarea',node=>!node.props.id).props.onChange({target:{value}});
 const changed=()=>{activity('activity-a1','روانکاری اصلاح‌شده');reason('اصلاح روش روانکاری');};
 return {render,find,button,activity,reason,changed,busy,notifications,get saved(){return saved;}};
}

test('activity preview requests the backend sheet and renders copied static/result cells before saving the exact reviewed payload',async()=>{
 const calls=[];let finish;
 const view=harness((method,path,payload)=>{calls.push({method,path,payload});return method==='POST'?new Promise(resolve=>{finish=resolve;}):Promise.resolve(prospective);});
 view.button('افزودن فعالیت بعد از این').props.onClick();
 view.activity(view.find('textarea',node=>node.props.id!=='activity-a1'&&node.props.id!=='activity-a2'&&!!node.props.id).props.id,'  روانکاری موتور  ');
 view.reason('  افزودن روانکاری موتور  ');
 assert.equal(view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.disabled,true);
 view.button('پیش‌نمایش تغییرات').props.onClick();
 assert.equal(calls.length,1);
 assert.equal(calls[0].method,'POST');assert.equal(calls[0].path,'/pm/templates/template%2Fa/preview-activities');
 assert.deepEqual(calls[0].payload.activities,[{id:'a1',text:'روانکاری یاتاقان'},{id:undefined,text:'روانکاری موتور',cloneFromId:'a1'},{id:'a2',text:'بررسی نشتی'}]);
 assert.equal(calls[0].payload.version,7);assert.equal(calls[0].payload.reason,'افزودن روانکاری موتور');assert.ok(calls[0].payload.requestKey);
 assert.deepEqual(Object.keys(calls[0].payload).sort(),['activities','reason','requestKey','version']);
 assert.equal(view.find('fieldset').props.disabled,true);
 assert.match(renderToStaticMarkup(view.render()),/در حال دریافت پیش‌نمایش کامل/);
 finish(prospective);await tick();
 const html=renderToStaticMarkup(view.render());
 assert.match(html,/خانه‌های ثابتِ ردیف یا ستونِ الگو هم کپی می‌شوند/);
 assert.match(html,/پیش‌نمایش کامل برگه · نسخهٔ 8/);assert.match(html,/راهنمای کامل برگه از پاسخ سرور/);
 for(const id of ['server-clone-a1','server-clone-grease','server-clone-dose','server-clone-result'])assert.ok(html.includes('data-cell-id="'+id+'"'),id);
 assert.equal(html.match(/NLGI 2/g)?.length,2);assert.equal(html.match(/20 g/g)?.length,2);assert.equal(html.match(/نتیجهٔ بازدید/g)?.length,2);
 assert.match(html,/<table class="pm-source-table" dir="rtl">/);
 assert.equal(view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.disabled,false);
 view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.onClick();await tick();
 assert.equal(calls[1].method,'PUT');assert.equal(calls[1].path,'/pm/templates/template%2Fa');assert.equal(calls[1].payload,calls[0].payload);
 assert.equal(view.saved,1);assert.deepEqual(view.busy,[true,false,true,false]);
});

test('every activity, reason, add, or delete edit invalidates the full preview and blocks publication',async()=>{
 for(const edit of [view=>view.activity('activity-a1','متن پس از بررسی'),view=>view.reason('علت پس از بررسی'),view=>view.button('افزودن فعالیت بعد از این').props.onClick(),view=>view.button('حذف فعالیت').props.onClick()]) {
  const view=harness(()=>Promise.resolve(prospective));view.changed();view.button('پیش‌نمایش تغییرات').props.onClick();await tick();
  assert.equal(view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.disabled,false);
  edit(view);
  assert.equal(view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.disabled,true);
  assert.doesNotMatch(renderToStaticMarkup(view.render()),/پیش‌نمایش کامل برگه · نسخهٔ/);
 }
});

test('preview or save version conflicts preserve draft text and block stale publication',async()=>{
 for(const stage of ['POST','PUT']) {
  const calls=[];const view=harness((method,path,payload)=>{calls.push({method,path,payload});return method===stage?Promise.reject(new transport.ApiError('نسخهٔ جدیدتری ثبت شده است.',409)):Promise.resolve(prospective);});
  view.changed();view.button('پیش‌نمایش تغییرات').props.onClick();await tick();
  if(stage==='PUT'){view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.onClick();await tick();}
  const html=renderToStaticMarkup(view.render());assert.match(html,/نسخهٔ برگه تغییر کرده است/);assert.match(html,/روانکاری اصلاح‌شده/);
  assert.equal(view.find('fieldset').props.disabled,true);assert.equal(view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.disabled,true);
  assert.equal(view.saved,0);assert.equal(view.busy.at(-1),false);
 }
});

test('a late preview response cannot restore review after a subsequent edit',async()=>{
 let finish;const view=harness(()=>new Promise(resolve=>{finish=resolve;}));view.changed();view.button('پیش‌نمایش تغییرات').props.onClick();
 view.reason('اصلاح علت در انتظار پاسخ');finish(prospective);await tick();
 assert.equal(view.button('ثبت نسخهٔ جدید فعالیت‌ها').props.disabled,true);
 assert.doesNotMatch(renderToStaticMarkup(view.render()),/پیش‌نمایش کامل برگه · نسخهٔ/);
});
