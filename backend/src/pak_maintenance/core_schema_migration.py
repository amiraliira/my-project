"""Explicit, owner-run PostgreSQL core schema 8 -> 9 maintenance.

Application startup never imports or invokes this module. Stop every writer and
create a verified native backup before calling the mutation function. Existing
operational rows and optional OEE history are neither reconstructed nor changed.
"""
from pathlib import Path
import re

from .database import Database, SchemaMismatchError, _REQUIRED_COLUMNS
from .migration import MigrationError, TABLES, _verify_schema


def _role_name(runtime_user: str) -> str:
    if not isinstance(runtime_user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", runtime_user, re.ASCII):
        raise ValueError("An explicit simple runtime database role is required")
    return runtime_user


def _verify_authority(c, runtime_user, tables):
    current_user = c.scalar("SELECT current_user")
    runtime = c.one("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role", dict(role=runtime_user))
    if runtime is None or any(runtime.values()) or runtime_user == current_user:
        raise SchemaMismatchError("A separate, existing non-administrative runtime role is required")
    if c.scalar("SELECT has_schema_privilege(:role,'public','CREATE')", dict(role=runtime_user)):
        raise SchemaMismatchError("The runtime role must not be able to create schema objects")
    if c.scalar("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND pg_has_role(:role,c.relowner,'MEMBER')", dict(role=runtime_user)):
        raise SchemaMismatchError("The runtime role must not own or inherit ownership of application tables")
    for table in tables:
        if not c.scalar("SELECT pg_has_role(current_user,c.relowner,'MEMBER') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=:table AND c.relkind IN ('r','p')", dict(table=table)):
            raise SchemaMismatchError("The maintenance connection must own every existing core application table")
    required = set(tables) & set(_REQUIRED_COLUMNS)
    for table in sorted(required):
        rights = c.one("SELECT has_table_privilege(:role,:table,'SELECT') AS can_select,has_table_privilege(:role,:table,'INSERT') AS can_insert,has_table_privilege(:role,:table,'UPDATE') AS can_update,has_table_privilege(:role,:table,'DELETE') AS can_delete,has_table_privilege(:role,:table,'TRUNCATE') AS can_truncate,has_table_privilege(:role,:table,'TRIGGER') AS can_trigger", dict(role=runtime_user, table='public.' + table))
        if (any(rights[key] is not True for key in ('can_select', 'can_insert', 'can_update', 'can_delete'))
                or rights['can_truncate'] is not False or rights['can_trigger'] is not False):
            raise SchemaMismatchError("The existing runtime role needs only SELECT, INSERT, UPDATE and DELETE on core tables; TRUNCATE and TRIGGER are forbidden")


def _preflight(database, c, runtime_user):
    markers = c.query("SELECT version FROM schema_version")
    if markers not in ([{"version": 8}], [{"version": 9}]):
        raise SchemaMismatchError("Only one verified schema-8 or already-upgraded schema-9 marker is accepted")
    version = markers[0]['version']
    if version == 8:
        try:
            _verify_schema(c, version=8, allow_optional_oee=True)
        except MigrationError as error:
            raise SchemaMismatchError(str(error)) from None
        functions = {row['name'] for row in c.query("SELECT p.proname AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'")}
        if not {'pak_round_even', 'pak_decimal', 'pak_participant_score', 'pak_decimal_sum'} <= functions:
            raise SchemaMismatchError('Exact PostgreSQL scoring functions are absent from the schema-8 source')
        constraints = {row['name'] for row in c.query("SELECT conname AS name FROM pg_constraint WHERE connamespace='public'::regnamespace AND convalidated")}
        if not {'ck_order_parts_units', 'ck_movements_units'} <= constraints:
            raise SchemaMismatchError('Validated quantity constraints are required in the schema-8 source')
        tables = {item.name for item in TABLES}
    else:
        database._verify_schema_connection(c, runtime_role=False, expected_version=9)
        tables = set(_REQUIRED_COLUMNS) | {'schema_version'}
    _verify_authority(c, runtime_user, tables)
    return dict(schemaVersion=version, alreadyApplied=version == 9,
                optionalOeePresent=bool(c.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='oee_module'")))


def preflight_postgresql_v8(database: Database, *, runtime_user: str):
    """Read-only owner/runtime/complete-schema checks before native backup."""
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError("The core upgrade requires an explicit PostgreSQL database")
    runtime_user = _role_name(runtime_user)
    with database.transaction(write=False) as c:
        return _preflight(database, c, runtime_user)


def upgrade_postgresql_v8(database: Database, *, runtime_user: str,
                          schema_directory: Path | None = None):
    """Commit all DDL, guards, limited grants and the marker together.

    The entry point performs the verified backup. Direct callers must supply
    that same maintenance prerequisite; this function never starts application
    writers, changes connection settings, or restores/clears existing data.
    """
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError("The core upgrade requires an explicit PostgreSQL database")
    runtime_user = _role_name(runtime_user)
    directory = Path(schema_directory) if schema_directory else Path(__file__).resolve().parents[2] / 'schema'
    script = (directory / 'postgresql-upgrade-8-to-9.sql').read_text(encoding='utf-8-sig')
    with database.transaction() as c:
        c.execute("SELECT pg_advisory_xact_lock(7421989)")
        before = _preflight(database, c, runtime_user)
        if not before['alreadyApplied']:
            c.execute("SELECT set_config('pak.runtime_user',:role,true)", dict(role=runtime_user))
            c.raw.execute(script)
        after = _preflight(database, c, runtime_user)
        if after['schemaVersion'] != 9:
            raise SchemaMismatchError("The explicit upgrade did not publish a verified schema-9 contract")
        return dict(schemaVersion=9, alreadyApplied=before['alreadyApplied'],
                    optionalOeePreserved=before['optionalOeePresent'],
                    existingOperationalDataChanged=False, historicalPoliciesBackfilled=False,
                    issuedPmResponsibilitiesBackfilled=False)


def _verify_complete_review_storage(c):
    """The 9/10 step preserves the complete frozen storage layout and keys."""
    contract = {}
    for spec in TABLES:
        contract[spec.name] = (
            {column: 'numeric' if column in spec.numeric else 'integer' if column in spec.integer else
             'bigint' if column in spec.bigint else 'text' for column in spec.columns}, spec.keys)
    contract['participant_grading_policies'] = (
        {name: 'integer' if name == 'revision' else 'text' for name in
         ('order_id', 'revision', 'person_id', 'specialty', 'role_at_submission', 'policy', 'created_at')},
        ('order_id', 'revision', 'person_id'))
    contract['pm_responsibility_transfers'] = (
        {name: 'integer' if name == 'order_version' else 'text' for name in
         ('id', 'order_id', 'order_version', 'from_supervisor_id', 'to_supervisor_id', 'actor_id', 'reason', 'created_at')}, ('id',))
    optional = {'oee_module', 'oee_lines', 'oee_sheets', 'oee_sheet_revisions'}
    objects = c.query("SELECT table_name,table_type FROM information_schema.tables WHERE table_schema='public'")
    actual = {row['table_name'] for row in objects}
    oee = optional <= actual
    if actual != set(contract) | (optional if oee else set()) or any(row['table_type'] != 'BASE TABLE' for row in objects):
        raise SchemaMismatchError('The explicit review upgrade requires exactly the complete core tables and optional complete OEE module')
    if oee:
        if c.query('SELECT version FROM oee_module') != [{'version': 1}]:
            raise SchemaMismatchError('The optional OEE module marker is not version 1')
        if c.scalar("SELECT COUNT(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled IN ('O','A') AND t.tgname IN ('oee_revisions_immutable','oee_sheets_no_delete')") != 2:
            raise SchemaMismatchError('The optional OEE history guards are missing')
    for table, (columns, keys) in contract.items():
        actual_columns = {row['column_name']: row['data_type'] for row in c.query(
            "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=:table", {'table': table})}
        actual_keys = tuple(row['column_name'] for row in c.query(
            "SELECT k.column_name FROM information_schema.table_constraints t JOIN information_schema.key_column_usage k ON k.constraint_name=t.constraint_name AND k.constraint_schema=t.constraint_schema AND k.table_name=t.table_name WHERE t.table_schema='public' AND t.table_name=:table AND t.constraint_type='PRIMARY KEY' ORDER BY k.ordinal_position", {'table': table}))
        if actual_columns != columns or actual_keys != keys:
            raise SchemaMismatchError('Complete core column/type/primary-key contract differs for ' + table)


def _preflight_v9(database, c, runtime_user):
    markers = c.query("SELECT version FROM schema_version")
    if markers not in ([{"version": 9}], [{"version": 10}]):
        raise SchemaMismatchError("Only one verified schema-9 or already-upgraded schema-10 marker is accepted")
    version = markers[0]['version']
    _verify_complete_review_storage(c)
    database._verify_schema_connection(c, runtime_role=False, expected_version=version)
    _verify_authority(c, runtime_user, set(_REQUIRED_COLUMNS) | {'schema_version'})
    return dict(schemaVersion=version, alreadyApplied=version == 10,
                optionalOeePresent=bool(c.scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='oee_module'")))


def preflight_postgresql_v9(database: Database, *, runtime_user: str):
    """Read-only owner/limited-runtime checks for the explicit 9 -> 10 step."""
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError("The review correction upgrade requires an explicit PostgreSQL database")
    runtime_user = _role_name(runtime_user)
    with database.transaction(write=False) as c:
        return _preflight_v9(database, c, runtime_user)


def upgrade_postgresql_v9(database: Database, *, runtime_user: str,
                          schema_directory: Path | None = None):
    """Publish the correction archive contract atomically, after a native backup.

    Existing reports, archives, scores and optional OEE rows are preserved. The
    separate operator entry point takes the verified backup before this call.
    """
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError("The review correction upgrade requires an explicit PostgreSQL database")
    runtime_user = _role_name(runtime_user)
    directory = Path(schema_directory) if schema_directory else Path(__file__).resolve().parents[2] / 'schema'
    script = (directory / 'postgresql-upgrade-9-to-10.sql').read_text(encoding='utf-8-sig')
    with database.transaction() as c:
        c.execute("SELECT pg_advisory_xact_lock(7421989)")
        before = _preflight_v9(database, c, runtime_user)
        if not before['alreadyApplied']:
            c.execute("SELECT set_config('pak.runtime_user',:role,true)", dict(role=runtime_user))
            c.raw.execute(script)
        after = _preflight_v9(database, c, runtime_user)
        if after['schemaVersion'] != 10:
            raise SchemaMismatchError("The explicit upgrade did not publish the verified schema-10 correction archive contract")
        return dict(schemaVersion=10, alreadyApplied=before['alreadyApplied'],
                    optionalOeePreserved=before['optionalOeePresent'],
                    existingOperationalDataChanged=False, historicalArchivesRewritten=False)
