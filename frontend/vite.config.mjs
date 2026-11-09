import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import ts from 'typescript';
import { minify } from 'terser';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
const require = createRequire(import.meta.url);
const scheduler = require.resolve('scheduler', { paths: [dirname(require.resolve('react-dom/package.json'))] });
const inProcess = process.env.PAK_BUILD_IN_PROCESS === '1';
// Restricted build hosts can compile and minify in Node. Normal developer
// builds use Vite's standard React/esbuild path; both produce the same app.
const restrictedBuild = {
 name: 'restricted-host-typescript', enforce: 'pre',
 transform(code, id) {
  code = code.replaceAll('import.meta.env.PROD', 'true').replaceAll('import.meta.env.DEV', 'false')
   .replaceAll('process.env.NODE_ENV', '"production"');
  if (/\.tsx?$/.test(id) && !id.includes('node_modules')) code = ts.transpileModule(code, {
   compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
    jsx: ts.JsxEmit.ReactJSX, sourceMap: false } }).outputText;
  return { code, map: null };
 },
 async renderChunk(code) {
  const result = await minify(code, { module: true, ecma: 2022, compress: { passes: 2 }, mangle: true,
   format: { comments: /^!|@license|@preserve/ } });
  return { code: result.code, map: null };
 },
};
const compressedAssets = {
 name: 'precompressed-public-assets', apply: 'build',
 writeBundle(outputOptions, bundle) {
  const outputDirectory = outputOptions.dir ?? dirname(outputOptions.file);
  for (const file of Object.keys(bundle)) {
   if (!/\.(js|css)$/.test(file)) continue;
   const path = join(outputDirectory, file);
   writeFileSync(path + '.gz', gzipSync(readFileSync(path), { level: 9 }));
  }
 },
};

export default defineConfig({
 plugins: [inProcess ? restrictedBuild : react(), tailwindcss(), compressedAssets],
 esbuild: inProcess ? false : undefined,
 resolve: { preserveSymlinks: true, alias: { scheduler } },
 server: { host: '127.0.0.1', port: 5173, proxy: { '/api': 'http://127.0.0.1:5187' } },
 build: { outDir: 'dist', sourcemap: false, target: 'es2022', minify: inProcess ? false : 'esbuild', cssMinify: 'lightningcss' },
});
