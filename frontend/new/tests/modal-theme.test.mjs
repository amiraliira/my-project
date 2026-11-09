import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
const require = createRequire(import.meta.url), ts = require('typescript');
const postcss = require(require.resolve('postcss', { paths: [dirname(require.resolve('vite/package.json'))] }));
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const fileImport = name => JSON.stringify(pathToFileURL(require.resolve(name)).href);
const portalUrl = encode('export function createPortal(children) { return children; }');
const apiUrl = encode('export const currentSessionGeneration=()=>0; export async function api() { throw Error("Modal render must not fetch data"); }');
const compiled = ts.transpileModule(readFileSync(new URL('../src/ui.tsx', import.meta.url), 'utf8'), {
 compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
}).outputText.replaceAll('"react/jsx-runtime"', fileImport('react/jsx-runtime')).replaceAll("'react'", fileImport('react'))
 .replace("'react-dom'", JSON.stringify(portalUrl)).replace("'./api'", JSON.stringify(apiUrl));
const { Modal } = await import(encode(compiled));
const { createElement } = require('react'), { renderToStaticMarkup } = require('react-dom/server');

function declaration(selector, property) {
 let value;
 for (const filename of ['styles.css', 'branding.css', 'enterprise.css']) {
  const tree = postcss.parse(readFileSync(new URL('../src/' + filename, import.meta.url), 'utf8'));
  tree.walkRules(rule => { if (rule.selectors.includes(selector)) rule.walkDecls(property, item => { value = item.value; }); });
 }
 if(value?.startsWith('var('))value=declaration(':root',value.slice(4,-1));
 if(/^#[0-9a-f]{3}$/.test(value))value='#'+[...value.slice(1)].map(c=>c+c).join('');
 return value;
}
function luminance(hex) {
 const values = hex.slice(1).match(/../g).map(channel => parseInt(channel, 16) / 255).map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
 return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
}

test('dialog portal retains the shared page theme outside the application root', () => {
 const previous = globalThis.document;
 globalThis.document = { body: {} };
 try {
  const html = renderToStaticMarkup(createElement(Modal, { title: 'برگهٔ PM', close() {}, wide: true }, createElement('p', null, 'شرح فعالیت')));
  assert.match(html, /class="modal-backdrop management-shell"/);
  assert.match(html, /role="dialog"/);
  assert.match(html, /شرح فعالیت/);
 } finally { globalThis.document = previous; }
});

test('light dialog theme provides readable text and translucent backdrop', () => {
 const background = declaration('body.pak-enterprise .management-shell .modal', 'background');
 const foreground = declaration('body.pak-enterprise .management-shell .modal', 'color');
 assert.match(background, /^#[0-9a-f]{6}$/);
 assert.match(foreground, /^#[0-9a-f]{6}$/);
 const ratio = (Math.max(luminance(background), luminance(foreground)) + 0.05) / (Math.min(luminance(background), luminance(foreground)) + 0.05);
 assert.ok(ratio >= 4.5, 'Dialog text must keep ordinary-text contrast');
 assert.match(declaration('body.pak-enterprise .modal-backdrop.management-shell', 'background'), /^#[0-9a-f]{8}$/);
 assert.equal(declaration('body.pak-enterprise .modal-backdrop.management-shell', 'color-scheme'), 'light');
});

test('technician empty-state heading and explanation remain readable on light cards', () => {
 for (const selector of ['body.pak-enterprise .registration-shell :is(.registration-hint,.registration-connection,.registration-subtitle,.muted,.field>small,.empty h3,.empty p)']) {
  const color = declaration(selector, 'color');
  assert.match(color, /^#[0-9a-f]{6}$/);
  const ratio = (luminance('#ffffff') + 0.05) / (luminance(color) + 0.05);
  assert.ok(ratio >= 4.5, selector + ' must retain ordinary-text contrast');
 }
});
