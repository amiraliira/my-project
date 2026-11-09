# Source layout and maintenance map — rc10

The handoff separates editable backend and frontend projects. Existing backend modules remain under the `pak_maintenance` package; they have not been renamed into an untested framework merely to change the folder appearance.

```text
backend/
  src/pak_maintenance/       Python application and domain services
  schema/                   Reviewed database contracts and migrations
  tests/                    Backend unit, integration and release tests
  tools/                    Explicit database/release maintenance tools
  pyproject.toml            Python package metadata and CLI entrypoint
  requirements.txt          Exact runtime dependency version pins
  locks/requirements-windows-cp314.lock            Historical Windows artifact-hash lock
frontend/
  src/                      Editable React/TypeScript components and CSS
  public/                   Local public assets and third-party notices
  tests/                    Frontend tests
  dist/                     Compiled, deployable frontend
  package.json              Frontend dependencies and build commands
  pnpm-lock.yaml            Resolved frontend dependency graph
deploy/linux/               Linux configuration template
tools/                      Source-root operational launchers
docs/                       Architecture, deployment and review notes
```

Do not edit `frontend/dist` to make UI changes. Edit `frontend/src` or `frontend/public`, run the frontend checks and rebuild. Runtime data, PM originals, database dumps, certificates and passwords are supplied separately and must not be committed to the source tree.

## Request and data flow

```text
Browser: React frontend
          |
          | same-origin HTTPS /api requests
          v
application.py: FastAPI/Starlette adapter and lifecycle
          |
          v
api.py: authenticated routes and request/response contract
          |
          v
Domain services: report / PM / archive / OEE / reporting
          |
          v
database.py: parameter binding, transactions and schema verification
          |
          v
PostgreSQL: constraints, immutable evidence guards and stored facts
```

`application.py` creates the actual FastAPI application using an explicit `AppConfig`. `api.py` deliberately keeps the existing route contract independent of FastAPI. Frontend validation helps the operator; backend authorization, validation and database transactions remain authoritative.

## Module responsibilities

The files below are in `backend/src/pak_maintenance`.

| Area | Main modules | Review focus |
|---|---|---|
| Startup and configuration | `__main__.py`, `cli.py`, `configuration.py` | Explicit configuration, HTTPS files, one selected internal listener |
| HTTP boundary and sessions | `application.py`, `api.py`, `http_security.py`, `security.py`, `network_policy.py` | Authentication, request size/origin policy, numeric internal endpoints, safe errors |
| Work reports and approvals | `work_service.py`, `common.py`, `quantity.py` | Participant time, exact quantities, revision checks, supervisor/head decisions |
| Individual performance | `score_facts.py`, `performance_service.py` | Separate participant grades and persisted completed-score evidence |
| PM forms and scheduling | `pm_service.py`, `pm_registry.py`, `pm_setup.py`, `pm_calendar.py` | Template/plan versions, Persian week rules, explicit activation and unique issuance |
| PM audit archive | `pm_archive.py`, `permissions.py` | Immutable submission evidence and planner-only archive visibility |
| Production lines and OEE | `oee_service.py`, `oee_metrics.py`, `oee_migration.py` | Shift counts, technical stops, operating time and centrally defined formulas |
| Catalogs, consumption and exports | `master_data.py`, `consumption_service.py`, `reporting_service.py`, `exports.py` | Scoped queries, cumulative approved consumption, bounded/pageable reads and streaming exports |
| Persistence and backups | `database.py`, `backup.py`, `postgres_backup.py` | Explicit PostgreSQL connection, transaction rollback/retry, least privilege, verified native dumps |
| Personal acceptance launcher | `local_test.py`, `local_postgres.py` | Optional local test instance; not the normal Linux production entrypoint |

`backend/schema` records the database contract. Operational startup checks the existing schema and does not silently create or migrate it. Historical SQLite schemas and offline import support remain for migration/unit fixtures; the hosted application rejects SQLite in every mode.

## Permission and workflow boundaries

- Technicians submit work and receive assigned PM work through the technician interface.
- Each supervisor grades the participants in that supervisor's specialty. The head records the final participant-specific grades after the required supervisor decisions.
- Personnel performance reports are restricted to the head. The planner manages operational PM information without access to personnel grading reports.
- The dedicated PM audit archive belongs to the planner role (`planner`, the PM/NET specialist).
- PM form or schedule proposals use an approval workflow; source or UI changes must preserve the server's role and revision checks.

The authoritative permission helpers are in `permissions.py`; route and service checks must be reviewed together. Hiding a frontend button alone does not establish authorization.

## Change and verification rules

Keep changes in the responsible module and its behavior tests. Preserve API compatibility, request identifiers and record-version checks. Do not move approval evidence, time calculation or scoring into frontend-only code. Use the existing central quantity, calendar and OEE calculations instead of duplicating formulas in screens or reports.

The source contains backend behavior tests, explicit disposable PostgreSQL integration fixtures, HTTP/TLS tests and release-tool tests. Frontend commands in `frontend/package.json` run the Node test suite and TypeScript/Vite build. Historical evidence does not prove that relocated build helpers or a new Linux environment have passed; record checks on the actual delivered layout and target environment.

`backend/tools/package_portable.py` and the managed `.cmd` release flow are Windows-specific tooling. Several QA helpers assume the old Windows layout and `.exe` binaries. Consult `linux-deployment.md` for the actual source-based Linux startup path and its remaining acceptance requirements.
