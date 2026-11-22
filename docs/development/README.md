# Development and delivery

[Start](../../README.md) · [Feature reference](../features.md) · [Backend runbook](../../backend/README.md)

## Architecture boundaries

```text
React client -> same-origin /api -> FastAPI adapter -> authenticated routes
             -> domain services -> transactional persistence -> PostgreSQL
```

| Boundary | Source | Responsibility |
| --- | --- | --- |
| Manual ASGI startup | `backend/app/` | Stable `app.main:app` entry point and compatibility imports |
| Configuration | `backend/src/pak_maintenance/settings.py`, `configuration.py` | Literal file parsing, explicit selection/overrides and validated application settings |
| HTTP adapter | `application.py`, `http_security.py` | Lifecycle, request policy, bounded IO and HTTP adaptation |
| API and authorization | `api.py`, `permissions.py`, `security.py` | Routes, authenticated scope, sessions and server-side permission checks |
| Work and scoring | `work_service.py`, `work_review_correction.py`, `score_facts.py`, `performance_service.py` | Participant-specific facts, corrections, approval and completed scores |
| PM | `pm_service.py`, `pm_*.py` | Versioned forms/plans, scheduling, originals, PDFs and immutable evidence |
| Parts and exports | `parts_import.py`, `parts_xlsx.py`, `consumption_service.py`, `reporting_service.py`, `exports.py` | Reviewed imports, exact quantities, scoped/pageable reports and bounded exports |
| OEE | `oee_metrics.py`, `oee_service.py` | Shared metric calculations and daily sheets |
| Storage | `database.py`, `backup.py`, `postgres_backup.py` | Bound SQL, transaction boundaries, schema validation and native backups |

Python files named in the table are under `backend/src/pak_maintenance/` unless another path is shown. Keep calculations and authorization in their domain boundary; a hidden button is not an access-control check. Prefer existing quantity, calendar and scoring helpers over copied formulas.

The retained `backend/launchers/*.cmd` files and `backend/tools/package_portable.py` belong to historical Windows portable tooling and developer regression coverage. They are not the update or startup procedure for this Linux installation. Operators use the [deployment guide](../../deploy/README.md) and manual Uvicorn [backend runbook](../../backend/README.md).

## Compatibility and data contract

The current contracts are backend `2.0.0rc13`, frontend `2.0.0-rc.13`, API 8, core schema 12 and optional OEE 1. Core 12 adds the `CM` and `GENERAL` work categories through an explicit owner migration. The `/api/reports/activities` and `/api/reports/activities/orders` endpoints share date, category, grouping and access scope with the activity chart. The release advertises `work-categories-v1` and `activity-reporting-v1`.

A schema change needs an explicit migration design, stopped-writer and backup/restore plan, native PostgreSQL tests, preservation checks and rollback/reconciliation instructions. Startup must never become an implicit migration or reset hook. A successful version marker check alone does not establish a valid complete storage contract.

## Iteration process

Work in small, reviewable increments. Record each item with:

1. The operator/user scenario and observable failure or desired outcome.
2. A bounded scope and explicit acceptance examples.
3. Data, permission, API and deployment impact.
4. The responsible code boundary and meaningful verification.
5. Remaining risks and a rollback path.

Implement and review one cohesive behavior before adding unrelated requests. Demonstrate the result against the acceptance examples, collect operator feedback, then prioritize the next item. A framework label or a large rewrite is not evidence of quality.

## Definition of done

- The acceptance scenario works and existing data/authorization behavior is preserved.
- Changed behavior has focused tests, including invalid input and relevant role boundaries.
- Persistence/SQL changes have disposable native PostgreSQL verification; SQLite-only tests are insufficient for PostgreSQL behavior.
- UI changes pass applicable Node tests, TypeScript and production build, with visual interaction checks when layout or navigation changes.
- Documentation has one operational entry point per area, valid links and commands matching the delivered paths.
- The complete deliverable passes its source manifest and starts through the documented entry point.
- Release evidence states what ran, what was reused because bytes were unchanged, skipped checks and environment limits.
- Private settings, live data and original documents are outside source control; operator acceptance of the actual deployment is recorded separately from local QA.

## Test workflow

Use a dedicated development environment with the runtime dependency pins installed. Some HTTP/PDF tests need additional development dependencies; do not add them to runtime requirements merely to satisfy a test host.

For configuration/startup work, from `backend/`:

```sh
python -m unittest discover -s tests -p 'test_manual*.py'
```

Select the behavior tests for the area being changed, then the relevant integration checks. Native tests must use an explicitly disposable local QA configuration and generated data. They must not target the company database.

For frontend work, from `frontend/`:

```sh
pnpm run test
pnpm run build
```

Do not mark a release accepted merely because tests pass on a developer's computer. The deployment guide's operational smoke checks are the separate installation gate.

## Prioritized follow-up backlog

These are follow-up items, not features claimed by this revision:

| Priority | Item | Acceptance evidence |
| --- | --- | --- |
| P1 | Rehearse the documented update on a representative Linux installation | Operator can complete it with the guide; existing records/passwords and PM downloads remain valid |
| P1 | Establish production-sized performance budgets | Reproducible query/page/refresh timings and resource use on representative, appropriately protected data |
| P1 | Add repeatable CI release checks | Incomplete upload, import failure, case collision and manifest mismatch fail before a release is published |
| P2 | Reduce large modules incrementally where ownership is unclear | Behavior/authorization tests remain stable; API and storage contract impact is explicit |
| P2 | Expand role-based workflow and accessibility review | Recorded scenarios for technician, supervisor, planner and head, including keyboard/24-hour entry |

Do not combine these into an unreviewable rewrite. Prioritize with the operator after the current acceptance criteria are met.
