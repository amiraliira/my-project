# Database: preserve data and upgrade to core schema 10

[Project README](../../README.md) · [Backend](../README.md) · [Optional OEE](../oee/README.md)

This is the database procedure for **PAK 2.0.0rc12, delivery revision 3**.
The target is **core schema 10**. OEE is a separate optional module, version **1**.
The release number, core schema number and OEE module number are different values.

Run these commands yourself in a **Linux terminal on the Docker host**. They use
the reported container `FanipakDB`, database `pak_maintenance`, maintenance role
`admin` and application role `pak`. A Windows PowerShell terminal without Docker
cannot execute them. If your actual names differ, change them before running.

The maintenance role must own the application tables (or inherit their ownership).
The application must connect as the separate limited role `pak`; do not give it
superuser, role creation, database creation, replication, ownership, schema CREATE
or row-security bypass privileges. Nothing below changes an account password.

## 1. Identify and stop writers

Stop the currently running PAK Uvicorn process and any other PAK instance or PM
scheduler that writes to this database. Keep Nginx and the PostgreSQL container
running. Do not start the new backend yet. If Uvicorn is in the foreground, use
Ctrl+C in its terminal; otherwise identify its exact PID and stop that process.
Do not kill PostgreSQL or unrelated Python processes.

Extract the new release into its own folder, then use:

```sh
cd "$HOME/Desktop/PAK-rc12/backend"
docker ps --filter name=FanipakDB
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/inspect.sql
```

`inspect.sql` only reads data. Expected: exactly one core version row, either
**8**, **9** or **10**. The role `pak` should exist with all administrative flags
and `public_schema_create` false. Application table owners should be the
maintenance role, not `pak`. The OEE table list is either empty or all four tables
with module marker 1. A partial OEE installation needs investigation before any
upgrade; do not delete its tables to make the error disappear.

If `schema_version` is absent, has multiple rows, has another number, or SQL
reports missing tables, **stop**. Do not manufacture a marker or run initialization
SQL. An rc10 label in the browser does not prove the database is core schema 8.

## 2. Back up the current database

Keep this terminal open so the paths below stay available. A native PostgreSQL
backup contains the current reports, users and password hashes; a GitHub source
upload does not. Keep this backup private and outside the source checkout.

```sh
umask 077
PAK_BACKUP_DIR="$HOME/Desktop/PAK-backups"
PAK_BACKUP_STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$PAK_BACKUP_DIR"
PAK_BACKUP_FILE="$PAK_BACKUP_DIR/pak-before-rc12-$PAK_BACKUP_STAMP.dump"
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

All PAK writers must remain stopped. Use the **maintenance owner `admin`**, not
the runtime account `pak`. The SQL files check the existing storage contract,
ownership, limited role and evidence guards before publishing the next marker.
`ON_ERROR_STOP` makes a failed statement exit nonzero. Each migration is atomic;
the two migrations below are two separate transactions, with an inspection between.

| Actual core marker | Action for this release |
| --- | --- |
| 8 | Run 8-to-9; verify 9; then run 9-to-10; verify 10. |
| 9 | Run only 9-to-10; verify 10. |
| 10 | Skip both migrations. Use the matching backend. |
| Anything else / missing / multiple | Stop and investigate; do not edit the marker. |

**Only if the current marker is 8:**

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/postgresql-upgrade-8-to-9.sql
docker exec FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance -c 'SELECT version FROM public.schema_version;'
```

Expected: exactly one row, **9**. If the migration failed, stop; do not proceed
to 9-to-10 or start rc12. The default runtime role inside these scripts is `pak`.
If your real runtime role is different, use the owner-config maintenance helpers
with their explicit `--runtime-user`; do not search/replace SQL role names blindly.

**Only if the current marker is now 9:**

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/postgresql-upgrade-9-to-10.sql
docker exec FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance -c 'SELECT version FROM public.schema_version;'
```

Expected: exactly one row, **10**. These scripts preserve existing users,
password hashes, reports, part records, PM definitions and any complete OEE v1
module. 8-to-9 adds participant grading-policy and PM responsibility-transfer
evidence; 9-to-10 adds the PM correction archive contract. Existing approvals and
archives are not rewritten into fabricated new evidence.

Do not run `postgresql-v10.sql`, `postgresql-v9.sql`, or another initialization
file on the existing database. Do not use `DROP DATABASE`, seed restore, table
truncate, manual marker changes, or `GRANT ALL` to bypass a failed check.

## 4. Verify preservation before restarting

```sh
docker exec -i FanipakDB psql -X -v ON_ERROR_STOP=1 -U admin -d pak_maintenance < schema/inspect.sql > "$PAK_BACKUP_FILE.after.txt"
```

Compare `before.txt` and `after.txt`: core marker should be 10; the recorded
table counts, account count and password-hash fingerprint should match while
writers are stopped. The new schema-9 tables and schema-10 guard are expected
structural changes. These checks supplement the restore drill; a marker alone
does not prove a complete schema. Backend startup performs the stricter runtime
schema and privilege validation.

Then follow [backend startup](../README.md). Keep the old checkout and backup.
Optional OEE activation has its own [single procedure](../oee/README.md); it is
not another core-schema migration and PM scheduling can remain disabled.

## 5. Failure and rollback

- **ImportError before startup:** fix the source package using the backend README;
  SQL does not fix a missing `pak_maintenance/__init__.py`.
- **Owner/privilege/contract failure:** retain the full SQL error and inspect the
  actual owner/runtime roles. Do not drop tables or weaken the runtime role.
- **Failure after 8-to-9:** the committed marker may now be 9. Inspect it before
  resuming. Do not rerun steps based only on an earlier browser version.
- **Rollback:** these migrations have no automatic down-migration. Never restart
  rc10 against core schema 10. Restore the verified pre-update dump into a separate
  target, test it with the matching old backend and preserved configuration, and
  explicitly switch after review. Do not overwrite the only copy of current data.
- **New reports after an update:** restoring the pre-update dump would omit those
  reports. Keep all writers stopped during validation; once new work is accepted,
  plan any rollback and data reconciliation explicitly.

No command in this README is run by application startup or by unpacking the ZIP.
