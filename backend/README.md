# FastAPI manual backend — rc.12

Read [the update guide](../docs/update-rc12-fa.md) before using the existing database. This locally tested rc.12 candidate requires API **7**, PostgreSQL 17+ and core schema **10**. Optional OEE remains module version 1. Company deployment and acceptance have not been performed.

From this folder, in your virtual environment:

```sh
python -m pip install -r requirements.txt
python -m pip check
uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers
```

`app/main.py` creates the application; `app/settings.py` reads your private `.env`; `src/pak_maintenance/application.py` adapts FastAPI; `api.py` binds routes. Keep the real settings, external PM seed and backups. This package never overwrites them.

For schema 8, use `schema/postgresql-upgrade-8-to-9.sql` only during explicit maintenance after a verified backup. `tools/upgrade_core_schema.py` is an alternative owner-config/native-backup entry point. `schema/postgresql-v10.sql` initializes an empty new target; it is not an upgrade. Never run old initialization SQL over a live database.

For an existing schema 9 database, `schema/postgresql-upgrade-9-to-10.sql` explicitly installs the correction archive contract. From schema 8, first complete 8-to-9 and then 9-to-10. `tools/upgrade_review_schema.py` is the owner-config/native-backup maintenance alternative.

New domain modules:

- `parts_xlsx.py`, `parts_import.py`: bounded XLSX parsing, autodetection, exact-code preview and transaction.
- `pm_activity_structure.py`: versioned row/column activity edits preserving originals and issued revisions.
- `pm_pdf.py`: current saved revision as Persian PDF with bundled open-licensed font.
- `pm_responsibility.py`: append-only routing correction for untouched issued drafts.
- `work_review_correction.py`: scoped, atomic supervisor time/parts correction, grading and approval. Actual edits create a review revision and immutable PM correction snapshot; unchanged approval creates neither. Unchanged specialties' prior approvals retain their original actor/time, and pending specialties still require their first approval. Supervisor/head return decisions are rejected; historical returns remain readable.
- `performance_service.py`: authenticated personal performance, with separate head-only personnel scope.
- `work_service.py`, `score_facts.py`: frozen participant grading policies and genuine head-only evidence.
- `core_schema_migration.py`: explicit transactional upgrade, never called by startup.

Tests use generated data. SQLite is only an offline unit-test adapter. Native PostgreSQL tests require an explicitly disposable local QA configuration and reject company targets. Optional test dependencies such as httpx and pypdf are not runtime requirements. Historical Windows release tools do not configure this Linux server.
