# Frontend

[Start](../README.md) · [Deployment](../deploy/README.md) · [Backend](../backend/README.md)

The React/TypeScript client **2.0.0-rc.13** pairs with API **8**, core schema **12** and capabilities `work-categories-v1` and `activity-reporting-v1`. Publish the matching `dist/` after backend health checks pass. See [Activity reports](../docs/features.md#activity-reports) for chart filters and drilldown.

## Files

| Path | Purpose |
| --- | --- |
| `src/`, `public/` | Editable application and public assets |
| `package.json`, `pnpm-lock.yaml` | Build commands and dependency graph |
| `tsconfig.json`, `vite.config.mjs`, `index.html` | Build configuration |
| `tests/` | Regression tests |
| `dist/` | Generated website copied to the Nginx document root |
| `node_modules/`, `*.tsbuildinfo` | Installed dependencies and disposable build cache |

Edit source, then build; do not edit compiled `dist/` files to implement features. Keep private configuration, PM documents and database dumps outside all public folders.

## Build when needed

Commands assume the extracted delivery is `~/Desktop/PAK-Project`. Use a supported Node installation matching the delivered Vite requirement: `^20.19.0` or `>=22.12.0`.

```sh
cd "$HOME/Desktop/PAK-Project/frontend"
node --version
pnpm --version
pnpm install --frozen-lockfile
pnpm run test
pnpm run build
```

Use pnpm 10 with the supplied lock file for a repeatable install. If the machine uses npm instead, use `npm install`, `npm run test`, then `npm run build` in a separate working folder. npm does not read `pnpm-lock.yaml`; do not mix package managers in one install or use `npm ci` without a matching npm lock file.

Expected: tests pass and TypeScript/Vite exit successfully. A failed build remains failed even if an older `dist/` exists. Publish only after the matching backend is healthy, using the [deployment guide](../deploy/README.md).

## Troubleshooting

| Failure | Check |
| --- | --- |
| Cannot find `./ui` | `src/ui.tsx` must exist with that exact case. It is a project file, not an npm dependency. |
| Cannot find `./passwordPolicy` | Restore exact-case `src/passwordPolicy.ts` from the same complete release. |
| Missing workflow or JSX environment types | Verify `src/Workflow.tsx` and `src/vite-env.d.ts` are present. |
| Cascading `unknown` or nullable row errors | Fix the first missing import before changing types. Do not remove `tsc`, disable strict checking or add broad `any` casts. |
| Browser shows an older page version | Check the actual Nginx document root and copied build. Reload with Ctrl+F5. If needed, unregister an old service worker for this site in browser developer tools and reload. |
| `/api/health` returns HTML | Correct Nginx API routing; the backend response must remain JSON. |
| Compatibility screen says backend unknown | Check backend startup and the Nginx route before changing database state. |

From this directory, confirm the critical filenames:

```sh
ls -l src/ui.tsx src/passwordPolicy.ts src/Workflow.tsx src/vite-env.d.ts
```

Capture a complete failed build, including its first error:

```sh
npm run build > frontend-build.log 2>&1
```

Browsers use same-origin `/api`; they do not connect to PostgreSQL or directly to the server's loopback address. Clearing browser site storage can sign a user out, but does not change PostgreSQL records.
