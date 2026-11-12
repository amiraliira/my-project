# OEE: enable optional module version 1

[Project README](../../README.md) · [Backend](../README.md) · [Database upgrade and backup](../schema/README.md) · [Nginx deployment](../../deploy/README.md)

For this rc12 delivery the required **core schema is 10** and the optional
**OEE module is version 1**. They are separate markers. There is no OEE schema 10
to activate. Fix backend imports and complete the core migration before this
procedure; activating OEE cannot fix a backend that fails to import.

This is a manual procedure for the Linux Docker host. It uses `FanipakDB`,
`pak_maintenance`, owner `admin`, runtime `pak`, and this release at
`$HOME/Desktop/PAK-rc12`. Run commands yourself; no server automation is required.

## 1. Confirm prerequisites

1. Stop all PAK writers as described in the [database README](../schema/README.md#1-identify-and-stop-writers).
2. Complete its [native backup and restore drill](../schema/README.md#2-back-up-the-current-database).
3. Upgrade and verify core schema **10**. Keep the backup path in the same terminal.
4. Confirm `pak` is the existing limited runtime role and `admin` owns the schema.

```sh
cd "$HOME/Desktop/PAK-rc12/backend"
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/inspect.sql
```

Interpret the optional table list:

| Existing state | Action |
| --- | --- |
| None of the four OEE tables exists | Continue to step 2. |
| All four exist and `oee_module` has exactly one version-1 row | OEE is already installed; skip step 2. Verify grants in step 3. |
| Some tables only, another marker, or an error | Stop. Keep the data and inspect the error; do not drop/recreate the module. |

There is no `.env` switch to install OEE. `PAK_SCHEDULING_ENABLED` controls PM
issuance, not OEE, and can stay false. Enabling OEE does not import PDF files,
reset passwords, create daily sheets or start PM scheduling.

## 2. Install only when all four OEE tables are absent

Copy the reviewed SQL into the container so `psql` can include it in the same
guarded transaction. This copies a SQL file; it does not execute it yet.

```sh
docker cp schema/postgresql-oee-v1.sql FanipakDB:/tmp/pak-oee-v1-rc12.sql
```

Run the following whole block. It explicitly checks core 10, a separate limited
runtime role, and absence of all OEE tables before creating anything. All DDL,
marker insertion and grants commit together; any SQL error stops and rolls back.

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance <<'SQL'
BEGIN;
SET LOCAL search_path TO public,pg_catalog;
SELECT pg_advisory_xact_lock(158137761,1);
DO $guard$
BEGIN
 IF (SELECT array_agg(version) FROM public.schema_version) IS DISTINCT FROM ARRAY[10] THEN
  RAISE EXCEPTION 'Core schema 10 is required before enabling OEE in this release';
 END IF;
 IF current_user='pak' OR NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname='pak'
   AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
 ) OR has_schema_privilege('pak','public','CREATE') THEN
  RAISE EXCEPTION 'Use a separate schema owner and an existing limited pak runtime role';
 END IF;
 IF EXISTS (
  SELECT 1 FROM information_schema.tables WHERE table_schema='public'
   AND table_name IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')
 ) THEN
  RAISE EXCEPTION 'OEE tables already exist; inspect them instead of recreating them';
 END IF;
END;
$guard$;
\i /tmp/pak-oee-v1-rc12.sql
GRANT SELECT ON public.oee_module TO pak;
GRANT SELECT,INSERT,UPDATE ON public.oee_lines,public.oee_sheets TO pak;
GRANT SELECT,INSERT ON public.oee_sheet_revisions TO pak;
COMMIT;
SQL
```

Expected: `COMMIT`, with no errors. Existing core rows and passwords remain
unchanged. Do not run the raw creation file twice or run it outside this explicit
transaction. Its historical module version remains 1 across supported core
upgrades; this delivery's activation prerequisites are core 10.

## 3. Verify the module and runtime access

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance <<'SQL'
BEGIN READ ONLY;
SELECT version AS core_schema_version FROM public.schema_version;
SELECT version AS oee_module_version FROM public.oee_module;
SELECT tgname,tgenabled FROM pg_trigger
 WHERE NOT tgisinternal
  AND tgrelid IN ('public.oee_sheets'::regclass,'public.oee_sheet_revisions'::regclass)
  AND tgname IN ('oee_revisions_immutable','oee_sheets_no_delete') ORDER BY tgname;
SELECT table_name,
 has_table_privilege('pak','public.'||table_name,'SELECT') AS can_select,
 has_table_privilege('pak','public.'||table_name,'INSERT') AS can_insert,
 has_table_privilege('pak','public.'||table_name,'UPDATE') AS can_update,
 has_table_privilege('pak','public.'||table_name,'DELETE') AS can_delete,
 has_table_privilege('pak','public.'||table_name,'TRUNCATE') AS can_truncate
FROM information_schema.tables WHERE table_schema='public'
 AND table_name IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')
ORDER BY table_name;
COMMIT;
SQL
```

Expected core **10**, OEE **1**, two enabled (`O` or `A`) guards, and:

| Table | SELECT | INSERT | UPDATE | DELETE / TRUNCATE |
| --- | --- | --- | --- | --- |
| `oee_module` | true | false | false | false |
| `oee_lines` | true | true | true | false |
| `oee_sheets` | true | true | true | false |
| `oee_sheet_revisions` | true | true | false | false |

For an existing complete module with **missing** grants, its schema owner can
run only the three `GRANT` lines from step 2, then repeat this verification.
Unexpected extra authority can come from role membership or PUBLIC grants;
investigate those sources rather than issuing `GRANT ALL` or deleting history.
Do not turn `pak` into the table owner. The two immutable evidence guards must
remain enabled.

## 4. Start the application and use OEE

Follow [backend startup](../README.md), then verify the API before opening the
matching rc12 frontend. Sign in as a **planner** to define OEE lines and enter
daily sheets, or as **head** to view lines, daily results and reports. Other roles
do not gain OEE access merely because the database tables exist.

Define an active line with its applicable equipment mapping, then open the daily
sheet and enter the actual production and stoppage data. Technical stoppage is
the availability loss; CIP remains part of operating time under the agreed
definition. No sample production data is inserted by activation.

## 5. Troubleshooting

- **Cannot import `__version__`:** source package problem; use the backend README.
- **Core schema mismatch:** use the database migration procedure first; do not
  edit a marker to force startup.
- **OEE not installed:** confirm this is the same database configured in the
  active backend `.env`. Check all four tables and restart the backend after
  successful activation.
- **Permission denied / failed limited-role check:** use `admin` for maintenance
  and `pak` for normal FastAPI execution, with the scoped grants above.
- **Tables exist but schema/guards are incomplete:** keep the verified backup
  and error output. This is not an absent module; do not recreate it in place.
- **UI says rc10 or cannot reach API:** resolve matching frontend/backend and
  Nginx `/api` routing in the [deployment README](../../deploy/README.md); SQL will
  not replace a cached UI.

The existing `tools/enable_oee.py --owner-config ... --runtime-user pak` is a
maintenance alternative for operators who already have a protected schema-owner
TOML and native backup binaries configured. It requires current core schema 10.
`--local-root` belongs to the historical disposable test launcher; do not use it
for this company database. Choose one activation method, not both.
