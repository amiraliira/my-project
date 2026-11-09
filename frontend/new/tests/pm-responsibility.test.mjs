import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const require=createRequire(import.meta.url),ts=require('typescript');
const {createElement:h}=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const encode=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const resolve=name=>pathToFileURL(require.resolve(name)).href;
const ui=encode(`import {createElement as h} from ${JSON.stringify(resolve('react'))}; export const ErrorBox=({text})=>text?h('div',{role:'alert'},text):null, Field=({label,children})=>h('label',null,label,children), Loading=()=>h('span',null,'loading'), Modal=({children})=>h('div',null,children), dateFa=value=>value||'—', errorText=error=>error.message, useResource=()=>({loading:false,data:null,error:'',reload:()=>{}});`);
const catalog=encode(`import {createElement as h} from ${JSON.stringify(resolve('react'))}; export const CatalogSelect=({value,filters})=>h('div',{'data-picker-value':value,'data-picker-role':filters.role,'data-picker-specialty':filters.specialty,'data-picker-active':filters.active});`);
const api=encode(`export class ApiError extends Error {} export const post=()=>{throw new Error('Unexpected network call in static rendering');};`);
let source=ts.transpileModule(readFileSync(new URL('../src/PmResponsibility.tsx',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
for(const [name,url] of Object.entries({'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./api':api,'./Catalog':catalog,'./ui':ui}))source=source.replaceAll(JSON.stringify(name),JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
const {ResponsibilityEditor,mayTransferPmResponsibility}=await import(encode(source));
const detail={orderId:'issued-order',version:7,number:'PMW-1405-08',title:'بازدید تجهیزات',specialty:'تأسیسات',originalSupervisorId:'issued-owner',originalSupervisorName:'مسئول هنگام صدور',supervisorId:'current-owner',supervisorName:'مسئول فعلی',eligible:true,history:[{id:'transfer-1',fromSupervisorId:'issued-owner',fromSupervisorName:'مسئول هنگام صدور',toSupervisorId:'current-owner',toSupervisorName:'مسئول فعلی',actorName:'رئیس آزمون',reason:'اصلاح کارتابل <script>unsafe</script>',createdAt:'2026-11-02T08:00:00Z',orderVersion:6}]};
const render=(changes={},role='head')=>renderToStaticMarkup(h(ResponsibilityEditor,{detail:{...detail,...changes},context:{user:{role},notify:()=>{}},close:()=>{},saved:()=>{},reload:()=>{},onBusy:()=>{}}));

test('only a head or manager may transfer issued PM responsibility',()=>{
 for(const role of ['head','manager'])assert.equal(mayTransferPmResponsibility(role),true);
 for(const role of ['admin','planner','supervisor','technician','','unknown'])assert.equal(mayTransferPmResponsibility(role),false);
});

test('transfer review keeps issued and current identities distinct and requires an actual same-specialty supervisor choice',()=>{
 const html=render();
 for(const text of ['مسئول هنگام صدور','مسئول فعلی','رئیس آزمون','تخصیص قبلی مجری پاک می‌شود','برنامهٔ بازدید را جداگانه اصلاح کنید'])assert.ok(html.includes(text),text);
 assert.match(html,/data-picker-value=""/);
 assert.match(html,/data-picker-role="supervisor"/);
 assert.match(html,/data-picker-specialty="تأسیسات"/);
 assert.match(html,/data-picker-active="true"/);
 assert.match(html,/<button class="button primary" disabled="">ثبت انتقال مسئول/);
 assert.match(html,/&lt;script&gt;unsafe&lt;\/script&gt;/);
 assert.doesNotMatch(html,/<script>/);
});

test('started or submitted reports expose the reason without transfer controls',()=>{
 const html=render({eligible:false,blockedReason:'ثبت اجرای برگه شروع شده است.'});
 assert.ok(html.includes('ثبت اجرای برگه شروع شده است.'));
 assert.ok(html.includes('سابقهٔ اصلاح مسئول'));
 assert.doesNotMatch(html,/data-picker-role|<textarea|ثبت انتقال مسئول/);
});

test('manager gets the transfer editor but other roles cannot submit from a rendered editor',()=>{
 assert.match(render({},'manager'),/ثبت انتقال مسئول/);
 for(const role of ['admin','planner','supervisor','technician'])assert.doesNotMatch(render({},role),/ثبت انتقال مسئول|بررسی انتقال/);
});
