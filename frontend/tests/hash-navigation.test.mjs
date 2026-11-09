import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url),ts=require('typescript');
const source=ts.transpileModule(readFileSync(new URL('../src/hashNavigation.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {installHashNavigation}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

function browser() {
 const previous=globalThis.window,events=new EventTarget(),entries=[{url:'http://pak.invalid/#inbox',state:null}],pages=[];
 let at=0,allow=true,attempts=0;
 const history={
  get state(){return entries[at].state;},
  replaceState(state,_title,url){entries[at]={state,url:new URL(url,entries[at].url).href};},
  pushState(state,_title,url){entries.splice(at+1);entries.push({state,url:new URL(url,entries[at].url).href});at++;},
  go(delta){const next=at+delta;if(next<0||next>=entries.length)return;const old=entries[at].url;at=next;if(old!==entries[at].url)queueMicrotask(()=>events.dispatchEvent(new Event('hashchange')));}
 };
 globalThis.window={history,location:{get href(){return entries[at].url;},get hash(){return new URL(entries[at].url).hash;}},addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events),dispatchEvent:events.dispatchEvent.bind(events)};
 events.addEventListener('pak:before-navigation',event=>{attempts++;if(!allow)event.preventDefault();});
 const router=installHashNavigation(page=>pages.push(page));
 return {router,pages,entries,history,get at(){return at;},get attempts(){return attempts;},allow:value=>{allow=value;},fragment(hash){history.pushState(null,'',hash);events.dispatchEvent(new Event('hashchange'));},async flush(){await new Promise(resolve=>setImmediate(resolve));},close(){router.dispose();globalThis.window=previous;}};
}

test('navigation creates real history entries and role replacements keep the position',()=>{
 const b=browser();try{
  assert.equal(b.router.navigate('oee'),true);assert.equal(b.at,1);assert.deepEqual(b.pages,['oee']);
  assert.equal(b.router.navigate('oee'),false);assert.equal(b.attempts,1);assert.equal(b.entries.length,2);
  b.router.replace('registration');assert.equal(b.entries.length,2);assert.equal(b.at,1);assert.equal(b.pages.at(-1),'registration');
 }finally{b.close();}
});

test('a dismissed explicit navigation leaves the current form and history untouched',()=>{
 const b=browser();try{b.allow(false);assert.equal(b.router.navigate('assets'),false);assert.equal(b.attempts,1);assert.equal(b.entries.length,1);assert.deepEqual(b.pages,[]);}finally{b.close();}
});

test('dismissed Back and Forward preserve entries, current page, and the forward branch',async()=>{
 const b=browser();try{
  b.router.navigate('oee');b.router.navigate('assets');b.history.go(-1);await b.flush();
  assert.equal(b.at,1);assert.equal(b.pages.at(-1),'oee');const urls=b.entries.map(e=>e.url),before=b.pages.length,attempts=b.attempts;
  b.allow(false);b.history.go(-1);await b.flush();assert.equal(b.at,1);assert.equal(b.pages.length,before);assert.equal(b.attempts,attempts+1);assert.deepEqual(b.entries.map(e=>e.url),urls);
  b.history.go(1);await b.flush();assert.equal(b.at,1);assert.equal(b.pages.length,before);assert.equal(b.attempts,attempts+2);assert.deepEqual(b.entries.map(e=>e.url),urls);
  b.allow(true);b.history.go(1);await b.flush();assert.equal(b.at,2);assert.equal(b.pages.at(-1),'assets');
  b.history.go(-1);await b.flush();assert.equal(b.at,1);assert.equal(b.pages.at(-1),'oee');
 }finally{b.close();}
});

test('a manual fragment cannot unmount a guarded form and a disposed listener is removed',async()=>{
 const b=browser();try{
  b.router.navigate('oee');const before=b.pages.length;b.allow(false);b.fragment('#stock');await b.flush();
  assert.equal(b.pages.length,before);assert.equal(new URL(b.entries[b.at].url).hash,'#oee');
  b.router.dispose();const attempts=b.attempts;b.history.go(-1);await b.flush();assert.equal(b.attempts,attempts);
 }finally{b.close();}
});
