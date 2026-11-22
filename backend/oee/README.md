# OEE

[Project README](../../README.md) · [Backend](../README.md) · [Database upgrade and backup](../schema/README.md) · [Nginx deployment](../../deploy/README.md)

PAK rc13 requires **core schema 12**. The optional **OEE module uses version 1**.
Verify the core schema and backend startup before activating OEE.

**Already using OEE successfully? No OEE SQL is required for this revision.**
Keep the existing version-1 module, line definitions and daily sheets. The
installation transaction below applies only when all four OEE tables are absent.

This is a manual procedure for the Linux Docker host. It uses `FanipakDB`,
`pak_maintenance`, owner `admin`, runtime `pak`, and this release at
`$HOME/Desktop/PAK-Project/backend` for the newly extracted release's maintenance
source. It loads the existing private configuration; it does not replace the
active backend merely by running a diagnostic. After deployment, the same tools
are available in `$HOME/Desktop/backend`. Run commands yourself; no server
automation is required.

## Diagnose before activating anything

Use the **same private configuration as the running backend**. Do not source the
file as shell code. This command opens PostgreSQL only for read-only checks; it
does not install OEE, change privileges, import PM data or modify any records.

```sh
cd "$HOME/Desktop/PAK-Project/backend"
export PAK_ENV_FILE="$HOME/Desktop/PAK-data/backend.env"
python -B tools/check_installation.py --config
```

After that local check succeeds:

```sh
python -B tools/check_database.py --json
```

The JSON identifies the selected configuration path and exported setting names,
the actual connected database/login, core/runtime validation, OEE table/column
presence, module marker, enabled evidence guards and exact runtime rights. It
does not contain the database password or raw driver exception text. A browser
page version or successful core startup cannot establish that optional OEE is
installed in that same database.

The report lists overall `status` and `oee.status` separately. An earlier core
schema can need an upgrade while the installed OEE module is already `ready` at
version 1.

| `status` | Interpretation and next step | Exit code |
| --- | --- | --- |
| `ready` | Core/runtime checks and OEE metadata checks passed; skip installation. If `activeLineCount` is zero, a planner must define/activate a line before daily entry. | 0 |
| `core-upgrade-required` | An earlier supported core is connected. Apply the documented core upgrades for the inspected marker and retain any existing OEE v1 installation. | 1 |
| `core-invalid` | Core marker or full core contract failed. Use the database guide; do not manufacture a marker or initialize a replacement. | 1 |
| `runtime-privileged` | The selected login has administrative, schema-creation or ownership authority. Correct the selected login/overrides or identified privilege problem first. | 1 |
| `oee-missing` | All four optional tables are absent in the connected database. Follow the guarded owner-run installation below after a verified backup. | 2 |
| `oee-partial` | Some objects, columns, marker or enabled evidence guards are incompatible. Preserve their data and investigate; do not recreate the module. | 3 |
| `oee-permissions` | The objects exist but the limited runtime grants do not match. The schema owner must inspect the exact rights; do not grant ownership or `ALL`. | 4 |

An unreadable configuration or connection also exits 1 with a redacted error.
Complete core validation and metadata inspection use read-only transactions;
repeat the diagnosis after concurrent maintenance or a configuration change.
The human-readable form is `python -B tools/check_database.py`. Send diagnostic
output for review rather than sending the private `backend.env` file.

## 1. Confirm prerequisites

1. For an actual module installation, stop all PAK writers during the maintenance window; see the [database backup procedure](../schema/README.md#2-back-up-the-current-database).
2. Complete its [native backup and restore drill](../schema/README.md#2-back-up-the-current-database).
3. [Upgrade and verify core schema **12**](../schema/README.md#3-apply-only-the-required-migrations). Keep the backup path in the same terminal.
4. Confirm `pak` is the existing limited runtime role and `admin` owns the schema.

```sh
cd "$HOME/Desktop/PAK-Project/backend"
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
docker cp schema/postgresql-oee-v1.sql FanipakDB:/tmp/pak-oee-v1-rc13.sql
```

Run the following whole block. It explicitly checks core 12, a separate limited
runtime role, and absence of all OEE tables before creating anything. All DDL,
marker insertion and grants commit together; any SQL error stops and rolls back.

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance <<'SQL'
BEGIN;
SET LOCAL search_path TO public,pg_catalog;
SELECT pg_advisory_xact_lock(158137761,1);
DO $guard$
BEGIN
 IF (SELECT array_agg(version) FROM public.schema_version) IS DISTINCT FROM ARRAY[12] THEN
  RAISE EXCEPTION 'Core schema 12 is required before enabling OEE in this release';
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
\i /tmp/pak-oee-v1-rc13.sql
GRANT SELECT ON public.oee_module TO pak;
GRANT SELECT,INSERT,UPDATE ON public.oee_lines,public.oee_sheets TO pak;
GRANT SELECT,INSERT ON public.oee_sheet_revisions TO pak;
COMMIT;
SQL
```

Expected: `COMMIT`, with no errors. Existing core rows and passwords remain
unchanged. Do not run the raw creation file twice or run it outside this explicit
transaction. Its historical module version remains 1 across supported core
upgrades; this delivery's activation prerequisites are core 12.

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

Expected core **12**, OEE **1**, two enabled (`O` or `A`) guards, and:

| Table | SELECT | INSERT | UPDATE | DELETE / TRUNCATE |
| --- | --- | --- | --- | --- |
| `oee_module` | true | false | false | false |
| `oee_lines` | true | true | true | false |
| `oee_sheets` | true | true | true | false |
| `oee_sheet_revisions` | true | true | false | false |

The runtime role is the limited PostgreSQL login `pak`, not the website admin
account. Diagnose conflicting configuration or inherited database authority using
the [runtime-role section](../schema/README.md#runtime-database-role). Do not
shell-source the private `.env` or use its runtime password for owner maintenance.

For an existing complete module with **missing** grants, its schema owner can
run only the three `GRANT` lines from step 2, then repeat this verification.
Unexpected extra authority can come from role membership or PUBLIC grants;
investigate those sources rather than issuing `GRANT ALL` or deleting history.
Do not turn `pak` into the table owner. The two immutable evidence guards must
remain enabled.

## 4. Start the application and use OEE

Follow [backend startup](../README.md), then verify the API before opening the
matching rc13 frontend. Sign in as a **planner** to define OEE lines and enter
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
TOML and native backup binaries configured. It requires current core schema 12.
`--local-root` belongs to the historical disposable test launcher; do not use it
for this company database. Choose one activation method, not both.
