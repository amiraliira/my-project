# PAK Maintenance rc.12 — manual Linux update

Start with [the Persian update guide](docs/update-rc12-fa.md). This manual update supports rc.10/schema 8 and rc.11/schema 9 to **2.0.0-rc.12 / API 7 / core schema 10**. Complete editable source and the rebuilt frontend are included. The live database, real passwords, PM seed and installed dependencies are separate. This is a locally tested candidate for operator installation; company deployment and acceptance have not been performed.

- `backend/app/main.py`: normal `app.main:app` entry point.
- `backend/app/settings.py`: reads your private `.env`.
- `backend/src/pak_maintenance/`: FastAPI and business source, including the PDF font.
- `backend/schema/`: explicit initialization and owner-run upgrade SQL.
- `backend/tests/`: generated acceptance and regression fixtures.
- `frontend/src/`: editable React/TypeScript source.
- `frontend/dist/`: rebuilt static UI for the existing Nginx.
- `deploy/nginx/`: proxy examples with bounded XLSX uploads.
- `docs/update-rc12-fa.md`: update, use, verification and rollback instructions.
- `docs/rc12-ui-fa.md`: unified interface, supervisor reporting, 24-hour clock and stable refresh behavior.
- `docs/em-downtime-fa.md`: r2 recorded EM downtime, line drilldown and report-entry instructions.
- `tools/verify_source.py` and `source-manifest.json`: package checksums.

Run Uvicorn yourself from `backend/`, with your virtual environment active:

```sh
uvicorn app.main:app --host 127.0.0.1 --port 5187 --workers 1 --no-proxy-headers
```

Keep the existing `.env` and `~/Desktop/PAK-data/` outside release packaging. Stop every writer and verify a current native backup before applying the explicit 8-to-9 and then 9-to-10 upgrades, as required by the current schema. Startup never creates, restores or migrates the database. Deploy frontend and backend together; API-6 and older clients are deliberately incompatible.

Features: audited supervisor corrections of participation time and the complete consumed-parts list, with grading and approval in the same form and transaction; already-approved unchanged specialties retain their original approval identity and time, while pending specialties still require their first approval. Supervisor and head return-for-correction actions are removed; historical returns remain readable. Each account sees only its own final performance in its inbox; the head retains personnel reports. Other features include real head-only grades 0–9 for supervisor work; audited correction of untouched issued PM responsibility; activity additions/deletions inside imported PM forms; current saved PM revision as Persian PDF; automatic XLSX parts import with rial prices and atomic reviewed apply.

Revision r2 adds recorded EM downtime beside repair times in work-report entry and a clickable line chart in management and work-report lists. The chart sums valid recorded downtime for completed EM reports with production stopped, grouped by the equipment's exact current hall/line; it retains unknown durations separately and preserves normal report access. Drilldown shows activities, people and consumed parts, with complete pagination and original report details. This is a sum of reported durations, not an OEE event association or a union of overlapping stoppage intervals. See [the feature guide](docs/em-downtime-fa.md).

The product remains **API 7 / core schema 10**, with no new SQL for r2. Deploy the matching r2 frontend and backend together: both declare `em-downtime-drilldown`. The new delivery is `PAK-Update-rc12-2026-11-07-r2.zip`; earlier sealed rc.12 and rc.11 artifacts remain unchanged. Baseline review counts remain historical evidence; r2 adds separate `docs/em-verification.json` evidence rather than relabeling old checks.

Older Windows tools and prior handoff documents are historical source references, not this manual Linux update procedure. Final review evidence and remaining limitations are in `docs/release-review-verification.json` and `docs/rc12-release-review-fa.md`; feature-specific evidence is retained in the other rc.12 verification documents. The earlier sealed rc.11 delivery remains unchanged. No company server was contacted or changed. Local validation does not establish company deployment acceptance.
