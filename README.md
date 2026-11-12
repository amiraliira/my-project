# PAK Maintenance

Manual Linux release **2.0.0-rc.12, package revision 3**. Start here. Each area below has one operational README. Older handoff guides are references, not additional installation procedures.

## Release contract

| Component | Required version |
| --- | --- |
| Backend | `2.0.0rc12` |
| Frontend | `2.0.0-rc.12` |
| API | `7`, including capability `em-downtime-drilldown` |
| PostgreSQL | 17 or newer; the reported Docker container uses 18 |
| Core database schema | **10** |
| Optional OEE module | **1**, enabled separately after core schema 10 |
| Runtime database role | Existing limited login `pak` |
| Reported database / container | `pak_maintenance` / `FanipakDB`; verify actual names |

Revision 3 adds source validation and consolidates operations documentation. It adds no migration beyond revision 2. Application, API, core schema and OEE module are separate version numbers. A browser screenshot does not identify the database schema.

## Existing installation: follow this order

1. Preserve the old backend, active private configuration, current database, PM documents and current UI. Extract the complete ZIP beside them, not over them. The archive root is `PAK-rc12/`; commands assume `$HOME/Desktop/PAK-rc12`.
2. Verify the delivery before editing it:

   ```sh
   cd "$HOME/Desktop/PAK-rc12"
   python3 -B tools/verify_source.py
   ```

   Expect JSON with `"successful": true` and `"handoffRevision": 3`. On missing files/checksum errors, re-extract the complete matching package. Do not create empty initialization files.
3. [Preserve the database and inspect the actual schema](backend/schema/README.md). Stop all writers, make and verify a native backup and restore drill. Apply only **8 -> 9 -> 10**, **9 -> 10**, or no migration for valid schema 10. Never initialize or replace the populated database.
4. [Prepare the backend](backend/README.md). Reuse the correct configuration and PM seed; check source, dependencies and settings before manually starting Uvicorn. Startup does not migrate or seed a database.
5. [Enable optional OEE](backend/oee/README.md), if required. It is separate from the core upgrade and PM scheduling. Existing valid module 1 must not be recreated.
6. [Build the matching frontend](frontend/README.md), then [publish through the existing Nginx](deploy/README.md). Replace the active UI only after the matching API is healthy. Keep the old UI for rollback.
7. Check `/api/health` through Nginx, then an existing login, report, PM document, supervisor report and EM chart. Verify OEE separately if enabled. Resume normal writes after acceptance.

## One README per area

| Area | Instructions |
| --- | --- |
| Backend source, import recovery, environment, manual Uvicorn, detached run and logs | [Backend](backend/README.md) |
| Backup, restore drill, schema inspection, explicit upgrades and rollback | [Database](backend/schema/README.md) |
| Optional module installation, grants and verification | [OEE](backend/oee/README.md) |
| Build, missing imports and client compatibility | [Frontend](frontend/README.md) |
| Existing site, API proxy, uploads and deployment checks | [Nginx](deploy/README.md) |
| Live database vs seed documents vs compiled UI | [Runtime data and PM paths](docs/runtime-data/README.md) |
| Features and historical verification evidence | [Documentation index](docs/README.md) |

## Data and source boundaries

| Item | Update rule |
| --- | --- |
| Existing PostgreSQL database: users, password hashes, reports, parts, plans, PM revisions, optional OEE | Preserve; explicitly upgrade after a verified backup |
| Active private `.env` | Preserve; review against template, never overwrite with placeholders |
| `$HOME/Desktop/PAK-data/seed/` | Keep existing `pm-import.json` and `pm-originals/` together and private |
| Backup archives | Preserve outside source/releases |
| `frontend/dist/` | Generated website; replace with one matching build |
| `backend/src/pak_maintenance/` | Business source; use one complete release, not mixed files |

This delivery contains no live database, real credentials, replacement seed dataset or installed dependencies. It does not configure a server automatically. Local results and limits are recorded in [operations verification](docs/operations-verification.json); the actual Linux deployment still needs operator validation. Do not upload `.env`, dumps, PM documents or `node_modules` to GitHub.
