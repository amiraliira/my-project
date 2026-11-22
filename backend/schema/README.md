# Database

[Project README](../../README.md) · [Backend](../README.md) · [Optional OEE](../oee/README.md)

The backend **PAK 2.0.0rc13, package revision 2** requires **core schema 12**.
Optional OEE uses module version **1**.

After a verified backup, upgrade core **11 to 12** to enable the new work types.
If the current database is at core **10**, apply **10 to 11** first. A valid core
**12** can proceed directly to startup checks. Revision 2 adds no migration on
top of revision 1. Run upgrades explicitly as the
database owner using section 3. Existing report categories, passwords, issued PM
identities, archives and installed OEE data are preserved.

The application version, PostgreSQL version, core schema and OEE module version
are independent. A single schema marker is a prerequisite, not proof of every
table, evidence guard, permission or optional module contract.

Run these commands yourself in a **Linux terminal on the Docker host**. They use
the reported container `FanipakDB`, database `pak_maintenance`, maintenance role
`admin` and application role `pak`. A Windows PowerShell terminal without Docker
cannot execute them. If your actual names differ, change them before running.

The maintenance role must own the application tables (or inherit their ownership).
The application must connect as the separate limited role `pak`; do not give it
superuser, role creation, database creation, replication, ownership, schema CREATE
or row-security bypass privileges. Updating this release does not change existing
passwords. The optional missing-login procedure sets only a newly created role's
initial password.

## 1. Inspect the current installation

The following inspection is read-only. Run all maintenance SQL from the newly
extracted release, **`$HOME/Desktop/PAK-Project/backend`**, before replacing the
active backend. The old `$HOME/Desktop/backend` may not contain the new migration.
Keep this terminal in the extracted release's backend folder throughout the
inspection, backup, restore drill and migration sections.

```sh
cd "$HOME/Desktop/PAK-Project/backend"
docker ps --filter name=FanipakDB
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/inspect.sql
```

`inspect.sql` only reads data. Expected: exactly one core version row, either
**8**, **9**, **10**, **11** or **12**. The role `pak` should exist with all administrative flags
and `public_schema_create` false. Application table owners should be the
maintenance role, not `pak`. The OEE table list is either empty or all four tables
with module marker 1. A partial OEE installation needs investigation before any
upgrade; do not delete its tables to make the error disappear.

If `schema_version` is absent, has multiple rows, has another number, or SQL
reports missing tables, **stop**. Do not manufacture a marker or run initialization
SQL. An rc10 label in the browser does not prove the database is core schema 8.

## 2. Back up the current database

Before a source update or SQL migration, stop the intended Uvicorn process and
all other PAK writers/schedulers for the maintenance window. Leave PostgreSQL
and Nginx running. Use Ctrl+C for foreground Uvicorn, or identify its exact PID
before stopping a detached process. Do not stop unrelated Python processes.

Keep this terminal open so the paths below stay available. A native PostgreSQL
backup contains the current reports, users and password hashes; a GitHub source
upload does not. Keep this backup private and outside the source checkout.

```sh
umask 077
PAK_SNAPSHOT_DIR="$HOME/Desktop/PAK-data/backups"
PAK_BACKUP_STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$PAK_SNAPSHOT_DIR"
PAK_BACKUP_FILE="$PAK_SNAPSHOT_DIR/pak-before-core12-$PAK_BACKUP_STAMP.dump"
```

Run the dump. Continue only if this command exits successfully:

```sh
docker exec FanipakDB pg_dump -U admin -d pak_maintenance -Fc > "$PAK_BACKUP_FILE"
```

Use **no `-t` / `-it`** for a binary dump. Terminal allocation can damage the
binary stream. A file may exist even when `pg_dump` failed; file existence alone
is not success. Do not reuse an old filename or restore the original seed data.

Check that the archive has content and a readable table of contents:

```sh
test -s "$PAK_BACKUP_FILE"
docker exec -i FanipakDB pg_restore --list < "$PAK_BACKUP_FILE" > "$PAK_BACKUP_FILE.list"
sha256sum "$PAK_BACKUP_FILE" > "$PAK_BACKUP_FILE.sha256"
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/inspect.sql > "$PAK_BACKUP_FILE.before.txt"
```

A successful `--list` checks archive readability, **not full restorability**.
Before modifying a live database, perform a restore drill into a new isolated
database. The following creates a unique rehearsal database; it does not overwrite
`pak_maintenance`. `admin` must have permission to create this rehearsal target.

```sh
PAK_RESTORE_CHECK="pak_restore_check_$(date +%Y%m%d_%H%M%S)"
docker exec FanipakDB createdb -U admin -T template0 "$PAK_RESTORE_CHECK"
docker exec -i FanipakDB pg_restore -U admin --exit-on-error --no-owner --no-privileges -d "$PAK_RESTORE_CHECK" < "$PAK_BACKUP_FILE"
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d "$PAK_RESTORE_CHECK" < schema/inspect.sql > "$PAK_BACKUP_FILE.restored.txt"
```

Expected: restore exits zero; inspection of the restored database shows the same
core/OEE markers, ten recorded table counts, account count and password-hash
fingerprint as `before.txt`. Database name and ownership/privileges can differ in
this rehearsal because `--no-owner --no-privileges` was intentional. Do not point
the application at this rehearsal database. Keep it until the update is accepted;
this guide does not automatically delete it.

Also preserve the active backend `.env`, PM seed folder containing both
`pm-import.json` and `pm-originals/`, and any other configured external data.
The PostgreSQL dump does not contain these files. See [backend setup](../README.md).

## 3. Apply only the required migrations

Keep all application writers stopped and run each migration as the database
owner `admin`. Use the same database you inspected and backed up in sections 1
and 2. The SQL examples assume the working runtime role is `pak`.

| Actual core marker | Action |
| --- | --- |
| **10** | Run 10-to-11, verify marker 11, then run 11-to-12. |
| **11** | Run 11-to-12 and verify marker 12. |
| **12** | Skip the SQL and verify the complete runtime contract. |
| 8 or 9 | Use the older-version appendix to reach 10 first. |
| Anything else, absent or multiple | Stop and inspect the database before proceeding. |

### Core 10 to 11

Run this only when the inspected marker is **10**. It installs the PM guards
that let the chief select an execution team independently of the form's
technical classification.

```sh
cd "$HOME/Desktop/PAK-Project/backend"
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/postgresql-upgrade-10-to-11.sql
docker exec FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance -c 'SELECT version FROM public.schema_version;'
```

Continue when the migration exits successfully and the query returns exactly one
row with version **11**.

### Core 11 to 12

Run this when the inspected marker is **11**, including after a successful
10-to-11 migration. It validates the expanded work type constraint and updates
the marker in one transaction. Existing orders keep their original category and
history. The application can record CM and Assigned Work once the upgrade and
matching backend are running.

```sh
cd "$HOME/Desktop/PAK-Project/backend"
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/postgresql-upgrade-11-to-12.sql
docker exec FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance -c 'SELECT version FROM public.schema_version;'
```

Continue when the migration exits successfully and the query returns exactly one
row with version **12**.

If the installation uses a different existing limited runtime role, use the
corresponding `tools/upgrade_pm_routing.py` or `tools/upgrade_work_categories.py`
with `--owner-config` and `--runtime-user`, a protected owner configuration, and
the native backup tools. For ordinary Uvicorn startup, use the limited runtime
login.

Use only these versioned upgrade scripts on a populated database. If a script
fails, keep the error and inspect the marker and PostgreSQL state before retrying.

## 4. Verify preservation before restarting

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/inspect.sql > "$PAK_BACKUP_FILE.after.txt"
```

Compare `before.txt` and `after.txt` while writers are stopped. Expect core marker
**12**, with the same recorded table counts, account count, password-hash fingerprint
and installed OEE marker on an upgrade from core 10 or 11. The 10-to-11 step
updates PM guards and the 11-to-12 step expands the order type constraint.
Earlier upgrades can also create their versioned evidence structures. Backend
startup verifies the complete runtime contract and permissions.

Then follow [backend startup](../README.md). Keep the old checkout and backup.
Optional OEE activation has its own [single procedure](../oee/README.md); it is
not another core-schema migration and PM scheduling can remain disabled.

## 5. Failure and rollback

- **Import/configuration failure before startup:** use the backend runbook. SQL
  does not repair a missing source package or wrong private configuration path.
- **Owner/role/contract failure:** retain the full SQL error and inspect the actual
  roles and source marker. Do not drop evidence or weaken guards.
- **Migration failed:** inspect the actual marker before retrying. Each upgrade
  is atomic. If 10-to-11 succeeded and 11-to-12 failed, the marker remains 11.
- **Source rollback after a committed migration:** use the previous backend with
  its matching database schema. Restore the
  verified pre-upgrade dump into a separate database, test it with the matching
  previous backend/configuration, then plan an explicit switch. Keep the only
  current database intact.
- **New work accepted after the update:** an old backup does not contain it.
  Preserve all current data and plan reconciliation before any rollback. Keep
  writers stopped throughout initial acceptance to avoid this situation.

No command in this README is executed by unpacking the ZIP or starting Uvicorn.

## Runtime database role

This section is diagnostic and optional maintenance. A healthy existing `pak`
role needs no changes for rc13. The website admin account and PostgreSQL
maintenance login `admin` are different accounts. FastAPI must use the limited
PostgreSQL login `pak`, while owner-run maintenance uses `admin`.

### Confirm the selected configuration

For this release's preflight, use the extracted backend with the existing private
configuration. After deployment, the same command is available in the active
backend folder:

```sh
cd "$HOME/Desktop/PAK-Project/backend"
export PAK_ENV_FILE="$HOME/Desktop/PAK-data/backend.env"
python -B tools/check_installation.py --config
printf 'Shell PAK_DB_USER: %s\n' "${PAK_DB_USER-<unset>}"
```

Review the reported configuration source and overridden setting names. Process
environment values take precedence over file values. If the private file says
`pak` but this shell still exports `PAK_DB_USER=admin`, correct that conflict
before changing database permissions. To intentionally use the private file's
DB profile, remove stale DB overrides from this shell and check again:

```sh
unset PAK_DB_USER PAK_DB_PASSWORD PAK_DB_HOST PAK_DB_PORT PAK_DB_NAME
unset PAK_DB_SSLMODE PAK_DB_SSLROOTCERT PAK_DB_ALLOW_LOOPBACK_PLAINTEXT PAK_DB_ALLOW_LAN_PLAINTEXT PAK_DB_CONNECT_TIMEOUT
python -B tools/check_installation.py --config
```

Keep `PAK_ENV_FILE` selected. **Do not run `source`, `. "$PAK_ENV_FILE"` or
`set -a` on this file.** The Python reader treats password punctuation literally;
a shell reader would interpret it differently. Do not print the password or
forward a full `.env`. Local configuration checks do not connect to PostgreSQL.

Use the existing maintenance authentication for the container. The commands here
connect as `admin`; if a password is requested it is the administrator's password,
not `PAK_DB_PASSWORD` from the limited runtime profile. Do not pass the runtime
password as `PGPASSWORD` to an administrator command.

Read the role state independently:

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance <<'SQL'
BEGIN READ ONLY;
SELECT version FROM public.schema_version;
SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
       has_schema_privilege(rolname,'public','CREATE') AS public_schema_create
FROM pg_roles WHERE rolname IN ('admin','pak') ORDER BY rolname;
SELECT c.relname AS application_table,pg_get_userbyid(c.relowner) AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_roles runtime ON runtime.rolname='pak'
WHERE n.nspname='public' AND c.relkind IN ('r','p')
  AND pg_has_role(runtime.oid,c.relowner,'MEMBER') ORDER BY c.relname;
COMMIT;
SQL
```

Expected after the update: one schema row `12`; a `pak` row with all administrative flags and
`public_schema_create` false; no application tables in the last result. The
maintenance role may legitimately have administrative authority. Reaching the
runtime-role guard means earlier checks passed, but later guard/permission checks
still need to pass. Successful complete backend startup is the final runtime gate.

### Correct only the identified mismatch

| Observation | Action |
| --- | --- |
| Runtime selected `admin`, while a valid `pak` login exists | Correct the selected private file or stale process override, then restart the intended API. Do not alter the administrator role. |
| `pak` has administrative flags | A maintenance operator can remove only those flags after checking its actual responsibilities; never weaken the application's check. |
| `pak` can create public-schema objects | Inspect direct, inherited and PUBLIC grants. Revoking a direct grant alone does not remove authority inherited from elsewhere. |
| `pak` owns or inherits ownership of tables | Plan an explicit ownership/membership correction with the maintenance operator. A `GRANT` does not remove ownership. |
| A required table right is absent | Grant the specific required right as the schema owner. Do not grant ownership, `TRUNCATE`, `TRIGGER` or `ALL PRIVILEGES`. |
| `pak` is absent | Provision it once using the following optional section. Do not recreate a working role or reset an existing password. |

### Provision a missing login on an existing verified schema 12

Use only after confirming
`pak` does not exist and the administrator owns the application schema and is
authorized to create a login. Open an
interactive maintenance session so the new password is entered privately:

```sh
docker exec -it FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance
```

Inside that session:

```sql
CREATE ROLE pak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
\password pak
GRANT CONNECT ON DATABASE pak_maintenance TO pak;
GRANT USAGE ON SCHEMA public TO pak;
REVOKE CREATE ON SCHEMA public FROM pak;
GRANT SELECT ON public.schema_version TO pak;
GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE
 public.pm_responsibility_transfers,public.participant_grading_policies,
 public.pm_submission_archive,public.users,public.sessions,public.assets,
 public.orders,public.parts,public.participants,public.order_parts,
 public.approvals,public.participant_scores,public.participant_head_scores,
 public.completed_scores,public.pm_change_requests,public.request_claims,
 public.audit,public.legacy_records,public.part_consumptions,
 public.part_consumption_totals,public.pm_templates,public.pm_template_revisions,
 public.pm_weekly_plans,public.pm_plan_revisions,public.pm_weekly_occurrences,
 public.pm_weekly_assets,public.pm_setup_requests,public.plans,
 public.pm_occurrences,public.stock_movements,public.stock_request_claims TO pak;
GRANT EXECUTE ON FUNCTION public.pak_round_even(numeric,integer),
 public.pak_decimal(numeric),public.pak_participant_score(integer,numeric,numeric),
 public.pak_decimal_add(numeric,numeric),public.pak_decimal_sum(numeric) TO pak;
\q
```

These core-table rights match the existing application contract; immutable-history
triggers still protect evidence. The optional OEE tables use the narrower grants
in the [OEE README](../oee/README.md#3-verify-the-module-and-runtime-access), not the
core CRUD statement. Do not transfer table ownership to `pak` or grant role
membership in the owner. If schema CREATE remains inherited, investigate it rather
than broadly revoking other applications' access. Put the new `pak` password only
in the selected private configuration, then repeat inspection and backend startup.

The explicit function grant includes the decimal aggregate, as supported by the
[PostgreSQL 18 GRANT syntax](https://www.postgresql.org/docs/18/sql-grant.html).

## Older-version appendix: reach core 10 first

This appendix applies to an inspected core 8 or 9 database. Use the same stopped-writer window and
verified native backup; the maintenance owner executes every command.
Keep the working directory at `$HOME/Desktop/PAK-Project/backend`, as in section 1.

Only when the marker is **8**:

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/postgresql-upgrade-8-to-9.sql
docker exec FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance -c 'SELECT version FROM public.schema_version;'
```

Expect exactly one row **9** before continuing. Only when the marker is **9**:

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/postgresql-upgrade-9-to-10.sql
docker exec FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance -c 'SELECT version FROM public.schema_version;'
```

Expect exactly one row **10**. Then follow the 10-to-11 and 11-to-12 steps in section 3.
Each versioned step is a separate guarded transaction. Inspect the committed
marker after a failure; never assume that the earlier browser label identifies
the current database version.
