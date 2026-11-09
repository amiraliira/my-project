import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { gunzipSync } from 'node:zlib';
import { build } from 'vite';
import config from '../vite.config.mjs';

const compression = config.plugins.find(plugin => plugin.name === 'precompressed-public-assets');

function fixture(t, source) {
 const root = mkdtempSync(join(tmpdir(), 'pak-compression-build-'));
 t.after(() => {
  assert.equal(dirname(root), resolve(tmpdir()));
  assert.ok(basename(root).startsWith('pak-compression-build-'));
  rmSync(root, { recursive: true, force: true });
 });
 writeFileSync(join(root, 'index.html'), '<script type="module" src="/main.js"></script>');
 writeFileSync(join(root, 'main.js'), source);
 writeFileSync(join(root, 'styles.css'), 'body { color: #123456; }');
 return root;
}

function buildFixture(root) {
 return build({
  configFile: false, root, publicDir: false, logLevel: 'silent',
  plugins: [compression], esbuild: false,
  resolve: { preserveSymlinks: true },
  build: {
   outDir: join(root, 'custom-output'), minify: false, cssMinify: false,
   rollupOptions: { output: {
    entryFileNames: 'nested/[name]-[hash].js',
    assetFileNames: 'nested/[name]-[hash][extname]',
   } },
  },
 });
}

test('production gzip files decode to emitted JS and CSS in a custom output directory', async t => {
 const root = fixture(t, 'import "./styles.css"; window.buildFixture = "ready";');
 const result = await buildFixture(root);
 const assets = result.output.filter(item => /\.(js|css)$/.test(item.fileName));
 assert.ok(assets.some(item => item.fileName.endsWith('.js')));
 assert.ok(assets.some(item => item.fileName.endsWith('.css')));
 for (const asset of assets) {
  const path = join(root, 'custom-output', asset.fileName);
  assert.deepEqual(gunzipSync(readFileSync(path + '.gz')), readFileSync(path));
 }
});

test('a failed build preserves its unresolved-import error instead of a compression error', async t => {
 const root = fixture(t, 'import "./missing-module.js";');
 await assert.rejects(buildFixture(root), error => {
  assert.equal(error.code, 'UNRESOLVED_IMPORT');
  assert.doesNotMatch(error.message, /ENOENT|scandir/);
  return true;
 });
});
