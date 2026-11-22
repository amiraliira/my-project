import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const require=createRequire(import.meta.url),ts=require('typescript');
const {createElement:h}=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const encode=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const resolve=name=>pathToFileURL(require.resolve(name)).href;
const people=[
 {id:'utility',name:'سرپرست خدمات',code:'S1',role:'supervisor',specialty:'تاسیسات',active:true},
 {id:'energy',name:'سرپرست انرژی آزمون',code:'S2',role:'supervisor',specialty:'انرژی',active:true},
 {id:'inactive',name:'سرپرست غیرفعال',code:'S3',role:'supervisor',specialty:'انرژی',active:false},
 {id:'technician',name:'تکنسین آزمون',code:'T1',role:'technician',specialty:'انرژی',active:true},
];
const catalog=encode(`import {createElement as h} from ${JSON.stringify(resolve('react'))};
 const people=${JSON.stringify(people)};
 export const CatalogSelect=({value,filters,disabled})=>h('select',{value,disabled,'data-owner-picker':true},
  people.filter(p=>Object.entries(filters).every(([key,v])=>String(p[key])===v))
  .map(p=>h('option',{key:p.id,value:p.id},p.name+' · '+p.specialty)));`);
const ui=encode(`import {createElement as h} from ${JSON.stringify(resolve('react'))};export const Field=({label,hint,children})=>h('label',null,label,h('small',null,hint),children);`);
let source=ts.transpileModule(readFileSync(new URL('../src/PmSupervisorSelect.tsx',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText;
for(const[name,url]of Object.entries({'react':resolve('react'),'react/jsx-runtime':resolve('react/jsx-runtime'),'./Catalog':catalog,'./ui':ui}))source=source.replaceAll(JSON.stringify(name),JSON.stringify(url)).replaceAll("'"+name+"'",JSON.stringify(url));
const {default:Select,pmSupervisorFilters,pmSupervisorLabel}=await import(encode(source));

test('responsible-supervisor choices include active energy and utilities teams without changing form classification',()=>{
 const html=renderToStaticMarkup(h(Select,{value:'',onChange:()=>{}}));
 assert.match(html,/سرپرست خدمات/);assert.match(html,/سرپرست انرژی آزمون/);
 assert.doesNotMatch(html,/سرپرست غیرفعال|تکنسین آزمون/);
 assert.match(html,/تخصص برگه تغییر نمی‌کند/);
 assert.deepEqual(pmSupervisorFilters,{role:'supervisor',active:'true'});
});

test('responsible owner label makes the selected execution team explicit',()=>{
 assert.equal(pmSupervisorLabel(people[1]),'سرپرست انرژی آزمون · انرژی · S2');
});

test('read-only callers cannot change responsible ownership through the picker',()=>{
 const html=renderToStaticMarkup(h(Select,{value:'energy',disabled:true,onChange:()=>{throw Error('Readonly selection changed');}}));
 assert.match(html,/<select[^>]*disabled=""/);
 assert.match(html,/<option value="energy" selected=""/);
});
