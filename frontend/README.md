# Frontend: build, verify and publish

[Project checklist](../README.md) · [Backend](../backend/README.md) · [Database](../backend/schema/README.md) · [Nginx](../deploy/README.md)

This directory contains the React/TypeScript client **2.0.0-rc.12**. Use the complete matching release: API **7**, core schema **10**, and the `em-downtime-drilldown` capability. The backend validates its database at startup. A successful frontend build does not migrate or validate that database.

The commands below assume the extracted release is at `$HOME/Desktop/PAK-rc12`. Adjust that one directory if you chose another location. Run commands as your normal account, not with `sudo`.

## 1. Identify source and output

| Path | Purpose | Keep in GitHub? |
| --- | --- | --- |
| `src/` | Editable application source | Yes |
| `public/` | Static source files | Yes |
| `package.json`, `pnpm-lock.yaml` | Dependencies and build commands | Yes |
| `tsconfig.json`, `vite.config.mjs`, `index.html` | Build configuration and entry page | Yes |
| `tests/` | Source regression tests | Yes |
| `dist/` | Generated files served by Nginx | Normally no; delivered release ZIPs may include them |
| `node_modules/`, `*.tsbuildinfo` | Installed dependencies and build cache | No |
| `.env` | Private local settings, if present | No |

Do not place PostgreSQL dumps, PM documents or backend settings in `public/` or `dist/`.

## 2. Install build dependencies

From a terminal on the build machine:

```sh
cd "$HOME/Desktop/PAK-rc12/frontend"
node --version
npm --version
```

The delivered Vite package requires Node `^20.19.0` or `>=22.12.0`. Use a supported Node installation meeting that requirement. Building does not require Python, PostgreSQL or a running backend.

The release includes a pnpm lock file. For the repeatable locked install, use pnpm 10:

```sh
pnpm --version
pnpm install --frozen-lockfile
pnpm run build
```

If pnpm is unavailable, npm is an alternative:

```sh
npm install
npm run build
```

`npm install` does not use `pnpm-lock.yaml`; it may create `package-lock.json`. Use one package manager consistently for a given working folder. Do not run `npm ci` unless that folder contains an intentionally maintained, matching `package-lock.json`.

**Expected result:** the command exits successfully and Vite reports a completed build. `dist/index.html` and `dist/assets/` exist. A TypeScript error is a failed build even if an older `dist/` directory is still present.

## 3. Resolve the reported missing-module errors

These are project files, with these exact names:

```text
src/ui.tsx
src/passwordPolicy.ts
```

Linux filenames are case-sensitive. `UI.tsx`, `Ui.tsx` and `passwordpolicy.ts` do not satisfy these imports. Run from `frontend/`:

```sh
pwd
node -p "require('./package.json').version"
ls -l src/ui.tsx src/passwordPolicy.ts
```

Expected package version: `2.0.0-rc.12`; both files must be readable. If either file is absent, restore the full `frontend/` from the same complete release into a fresh folder and repeat step 2. Do not combine helper files from rc10 with an rc12 client.

Missing `ui.tsx` can also cause cascading `unknown`, nullable-row and implicit-type errors in `Insights.tsx`, because the table and resource types are unavailable. Restore the source first. Do not add `any`, disable `strict`, remove `tsc`, or install an npm package named `ui` to bypass these failures.

If the files exist and errors remain, capture the whole build output, including its first error:

```sh
npm run build > frontend-build.log 2>&1
cat frontend-build.log
```

Keep the log outside GitHub if it contains private local paths or other sensitive details.

## 4. Publish only after the backend is ready

Keep the generated build in this release folder until the [database update](../backend/schema/README.md) and [backend startup checks](../backend/README.md) pass. Publish the frontend and backend from the same release during the maintenance window; see [Nginx and frontend publishing](../deploy/README.md).

Browsers call `/api` on the website's own origin. Do not change client URLs to PostgreSQL port 5432 or Uvicorn's loopback address. For this installation, Nginx serves `http://192.168.12.92` and forwards `/api` to `127.0.0.1:5187`.

## 5. Check the browser after publishing

Open the site and reload with `Ctrl+F5`. Then inspect:

```sh
curl -i http://192.168.12.92/api/health
```

Expected: HTTP 200 with a JSON health object reporting API 7, schema 10 and the matching capability list. If you receive HTML, fix Nginx's API routing. If you receive a gateway error, fix backend startup first.

A compatibility screen showing **page version rc10** means that browser loaded an rc10 client. It does not establish the database schema version. Check the served `index.html`, the chosen Nginx `root`, and the release being copied before blaming the database. If a previously installed service worker still serves old content, use the browser's Developer Tools → Application → Service Workers to unregister it for this site and reload. Clearing this site's browser storage signs the user out; it does not change PostgreSQL data.

## Optional development commands

```sh
pnpm run test
pnpm run dev
```

The development server binds loopback and proxies `/api` to `127.0.0.1:5187`. Production uses the built files and Nginx; neither the development nor preview server is the production deployment.
