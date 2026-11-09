# React frontend source

Edit `src/*.tsx`, `src/*.ts` and `src/*.css`. Local public assets are in `public`. `dist` is compiled output and can be regenerated. Never edit it to implement a feature.

This project uses React, TypeScript, Vite and Tailwind CSS. Exact direct dependencies are in `package.json`; resolved dependencies are in `pnpm-lock.yaml`. Use a Node version satisfying the Vite 7 engines requirement, for example a supported Node 22 release at least 22.12. A compatible pnpm must already be installed by IT.

```bash
cd frontend
pnpm install --offline --frozen-lockfile
pnpm test
pnpm run build
```

An offline install needs the correct platform's dependencies already present in the pnpm store. A Windows `node_modules` directory is not a Linux dependency store. On an approved connected build machine, IT can instead populate its store with `pnpm install --frozen-lockfile` and transfer the approved build inputs. No package installation runs as part of the application server.

`PAK_BUILD_IN_PROCESS=1 pnpm run build` selects the existing constrained-host build path. The source handoff includes a compression build-hook repair so an unsuccessful build is not hidden behind a missing `dist/assets` error. Generated gzip assets are checked against their uncompressed originals.

The current local rc.12 candidate requires API 7 and core schema 10; API-6 and older clients are incompatible. `ReviewCorrection.tsx` provides one supervisor form for time/parts edits, grades, preview and approval. Unchanged specialties' existing approvals retain their original actor/time; pending specialties still require their first approval. Live return-for-correction actions are removed from supervisor and head decisions, while historical returns remain readable. No-change approval needs no correction reason and creates no correction revision.

For the manual Linux deployment, Nginx serves `frontend/dist` from `/home/pak/Desktop/UI` and forwards `/api` to Uvicorn on `127.0.0.1:5187`. Browsers use the site's own origin. Vite development also proxies `/api` to port 5187. Building the frontend does not start or reconfigure the company server. This candidate has not been packaged or deployed.
