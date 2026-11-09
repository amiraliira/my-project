import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url), ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');
const encode = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const resolve = name => pathToFileURL(require.resolve(name)).href;
function compile(name, imports = {}) {
 let source = ts.transpileModule(readFileSync(new URL('../src/' + name, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText;
 source = source.replace(/^import ['"].*\.css['"];?\s*$/gm, '');
 for (const [name, value] of Object.entries(imports)) source = source.replaceAll(JSON.stringify(name), JSON.stringify(value)).replaceAll("'" + name + "'", JSON.stringify(value));
 return source;
}
const helpersUrl = encode(compile('clock24.ts'));
const { normalizeTime24, time24Digits, time24DialPoint, time24DialValue } = await import(helpersUrl);
const hooksUrl = encode(`let values=[],i=0;export const reset=()=>{values=[];i=0;},begin=()=>{i=0;};export function useState(initial){const n=i++;if(!(n in values))values[n]=typeof initial==='function'?initial():initial;return[values[n],value=>{values[n]=typeof value==='function'?value(values[n]):value;}];}export function useRef(initial){const n=i++;return values[n]||(values[n]={current:initial});}export const useEffect=()=>{},useId=()=> 'clock-test';`);
const hooks = await import(hooksUrl);
const uiUrl = encode(`import{createElement as h}from ${JSON.stringify(resolve('react'))};export const Modal=({title,children,close})=>h('div',{role:'dialog','aria-label':title},children);`);
const { default: Time24 } = await import(encode(compile('Time24.tsx', { react: hooksUrl, 'react/jsx-runtime': resolve('react/jsx-runtime'), './ui': uiUrl, './clock24': helpersUrl })));
function nodes(value) { if (Array.isArray(value)) return value.flatMap(nodes); return value && typeof value === 'object' ? [value, ...nodes(value.props?.children)] : []; }
function harness(options = {}) {
 hooks.reset(); const calls = [];
 const props = { value: '08:00', label: 'شروع کار', onChange: value => { props.value = value; calls.push(value); }, ...options };
 const render = () => { hooks.begin(); return Time24(props); };
 const find = (type, predicate = () => true) => nodes(render()).find(node => node.type === type && predicate(node));
 return { props, calls, render, find };
}
const opener = view => view.find('button', node => node.props['aria-haspopup'] === 'dialog');
const main = view => view.find('input', node => node.props['aria-label'] === 'شروع کار');
const select = view => view.find('button', node => node.props.children === 'انتخاب');
const hourInput = view => view.find('input', node => node.props['aria-label'] === 'ساعت از صفر تا ۲۳');
const minuteInput = view => view.find('input', node => node.props['aria-label'] === 'دقیقه از صفر تا ۵۹');

test('24-hour normalization accepts Persian/Arabic digits and compact numeric keyboard input', () => {
 for (const [raw, expected] of [['00:00', '00:00'], ['12:00', '12:00'], ['23:59', '23:59'], ['۱۷:۳۱', '17:31'], ['١٧:٣١', '17:31'], ['1731', '17:31'], ['۹۰۵', '09:05'], ['9:5', '09:05'], [' 07:30 ', '07:30']]) assert.equal(normalizeTime24(raw), expected, raw);
 assert.equal(time24Digits('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩'), '01234567890123456789');
 for (const raw of ['', '12', '24:00', '23:60', '-1:30', '12:000', '23:59:00', '7 PM', '12345', 'Infinity']) assert.equal(normalizeTime24(raw), null, raw);
});

test('clock face distinguishes midnight from noon and the second hour ring', () => {
 assert.equal(time24DialValue(150, 27, 300, 'hour'), 0);
 assert.equal(time24DialValue(150, 69, 300, 'hour'), 12);
 assert.equal(time24DialValue(211.5, 43.5, 300, 'hour'), 13);
 assert.equal(time24DialValue(190.5, 79.85, 300, 'hour'), 1);
 assert.equal(time24DialValue(150, 273, 300, 'hour'), 18);
 assert.equal(time24DialValue(150, 231, 300, 'hour'), 6);
 for (const value of [0, 1, 12, 13, 17, 23]) { const point = time24DialPoint(value, 'hour'); assert.equal(time24DialValue(point.left * 3, point.top * 3, 300, 'hour'), value); }
 for (const value of [0, 1, 17, 30, 45, 59]) { const point = time24DialPoint(value, 'minute'); assert.equal(time24DialValue(point.left * 3, point.top * 3, 300, 'minute'), value); }
});

test('a partial or invalid new draft immediately clears the previous payload time', () => {
 const view = harness({ value: '14:20', required: true });
 main(view).props.onChange({ target: { value: '25:15' } });
 assert.equal(view.calls.at(-1), '');
 assert.equal(view.props.value, '');
 assert.equal(main(view).props.value, '25:15');
 assert.equal(main(view).props['aria-invalid'], true);
 assert.equal(main(view).props.required, true);
 main(view).props.onChange({ target: { value: '۱۷۳۱' } });
 assert.equal(view.props.value, '17:31');
 main(view).props.onBlur();
 assert.equal(main(view).props.value, '17:31');
 main(view).props.onChange({ target: { value: '17:' } });
 assert.equal(view.props.value, '');
});

test('the 24-hour dial chooses hour 17 and exact typed minute 31 without locale meridiem controls', () => {
 const view = harness(); opener(view).props.onClick();
 const hours = nodes(view.render()).filter(node => node.type === 'button' && String(node.props['aria-label']).startsWith('ساعت '));
 assert.equal(hours.length, 24);
 view.find('button', node => node.props['aria-label'] === 'ساعت ۱۷').props.onClick();
 assert.equal(hourInput(view).props.value, '17');
 assert.equal(minuteInput(view).props.className, 'selected');
 minuteInput(view).props.onChange({ target: { value: '۳۱' } });
 select(view).props.onClick();
 assert.equal(view.props.value, '17:31');
 assert.equal(opener(view).props['aria-expanded'], false);
 const markup = renderToStaticMarkup(view.render());
 assert.doesNotMatch(markup, /type="time"|\bAM\b|\bPM\b/);
 assert.match(markup, /17:31/);
});

test('invalid picker fields cannot commit; Enter confirms a valid midnight value', () => {
 const view = harness(); opener(view).props.onClick();
 hourInput(view).props.onChange({ target: { value: '۲۴' } });
 assert.equal(select(view).props.disabled, true);
 select(view).props.onClick();
 assert.equal(view.calls.length, 0);
 hourInput(view).props.onChange({ target: { value: '۰۰' } });
 minuteInput(view).props.onChange({ target: { value: '۰۰' } });
 let prevented = false;
 view.find('div', node => node.props.className === 'modal-body time24-picker').props.onKeyDown({ key: 'Enter', preventDefault() { prevented = true; } });
 assert.equal(prevented, true);
 assert.equal(view.props.value, '00:00');
});

test('cancel and disabled controls do not mutate the report value', () => {
 const view = harness(); opener(view).props.onClick();
 view.find('button', node => node.props['aria-label'] === 'ساعت ۲۳').props.onClick();
 view.find('button', node => node.props.children === 'انصراف').props.onClick();
 assert.equal(view.props.value, '08:00'); assert.equal(view.calls.length, 0);
 const disabled = harness({ disabled: true });
 assert.equal(main(disabled).props.disabled, true); assert.equal(opener(disabled).props.disabled, true);
 opener(disabled).props.onClick(); main(disabled).props.onChange({ target: { value: '23:59' } });
 assert.equal(opener(disabled).props['aria-expanded'], false); assert.equal(disabled.calls.length, 0);
});
