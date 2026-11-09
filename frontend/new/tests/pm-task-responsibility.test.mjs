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
 for(const [name,url] of Object.entries(imports))source=source.replaceAll(JSON.stringify(name),JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
 return source;
}
const ui=encode(`
 import {createElement as h} from ${JSON.stringify(resolve('react'))};
 let task;
 export const configureTask=value=>{task=value;};
 export const useResource=()=>({loading:false,error:'',updatedAt:1,reload:()=>{},data:{items:[task],total:1,page:1,totalPages:1,counts:{pending:1,submitted:0,completed:0,overdue:0,total:1}}});
 export const num=value=>String(value??0),dateFa=value=>value||'—',todayIso=()=> '2026-11-03',duration=()=>0,errorText=error=>error.message;
 export const Badge=()=>null,Icon=()=>null,Loading=()=>null,ErrorBox=()=>null;
 export const Field=({children})=>h('label',null,children),Modal=({children})=>h('div',null,children);
`);
const catalog=encode(`export const CatalogSelect=()=>null,PageControls=()=>null,emptyPage=()=>({items:[],total:0,page:1,totalPages:1});`);
const api=encode(`export class ApiError extends Error {} const blocked=()=>{throw new Error('Static task rendering must never call a backend');}; export const api=blocked,download=blocked,post=blocked,put=blocked;`);
const responsibility=encode(compile('PmResponsibility.tsx',{'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':api,'./Catalog':catalog,'./ui':ui}));
const source=compile('PmWork.tsx',{
 'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':api,'./Catalog':catalog,'./ui':ui,'./Time24':encode('export default()=>null;'),
 './Orders':encode('export const OrderDetail=()=>null;'),'./PmLibrary':encode('export const PmSheetView=()=>null;'),
 './JalaliDate':encode('export const JalaliDate=()=>null;'),'./pmAlarm':encode('export const pmIsOverdue=()=>false;'),
 './latestRequest':encode(compile('latestRequest.ts')),'./PmResponsibility':responsibility,
}).replace("import './pm-inbox.css';",'');
const {PmTaskList}=await import(encode(source)),{configureTask}=await import(ui);
const untouched={id:'issued-draft',version:1,title:'برگهٔ صادرشدهٔ دست‌نخورده',type:'PM',status:'draft',participants:[],assigneeId:null,pmExecutionState:'unassigned',pmSpecialty:'مکانیک',pmSupervisorId:'issued-owner',pmEffectiveSupervisorId:'effective-owner',pmCanTransferResponsibility:true};
function render(role,changes={}) {
 configureTask({...untouched,...changes});
 return renderToStaticMarkup(h(PmTaskList,{context:{user:{role,id:'reviewer'},notify:()=>{}}}));
}

test('head and manager see responsibility correction on an eligible untouched issued draft',()=>{
 for(const role of ['head','manager']) {
  const html=render(role);
  assert.match(html,/برگهٔ صادرشدهٔ دست‌نخورده/);
  assert.match(html,/<button class="button secondary small">اصلاح مسئول برگه<\/button>/);
 }
});

test('task-list transfer control honors authoritative eligibility and role restrictions',()=>{
 for(const role of ['head','manager'])for(const status of ['draft','returned','supervisor','completed'])assert.doesNotMatch(render(role,{status,pmCanTransferResponsibility:false}),/اصلاح مسئول برگه/);
 for(const role of ['admin','planner','supervisor','technician','unknown'])assert.doesNotMatch(render(role),/اصلاح مسئول برگه/);
 assert.doesNotMatch(render('head',{pmCanTransferResponsibility:undefined}),/اصلاح مسئول برگه/);
});

test('only the effective supervisor sees assignment after responsibility correction',()=>{
 configureTask({...untouched,pmCanTransferResponsibility:false});
 const forUser=id=>renderToStaticMarkup(h(PmTaskList,{context:{user:{role:'supervisor',id},notify:()=>{}}}));
 assert.match(forUser('effective-owner'),/تخصیص مجری/);
 assert.doesNotMatch(forUser('issued-owner'),/>تخصیص مجری<\/button>/);
});
