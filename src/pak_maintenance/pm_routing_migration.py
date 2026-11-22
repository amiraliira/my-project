"""Explicit guard-only core 10 -> 11 maintenance; startup never invokes it."""
from pathlib import Path
import sqlite3

from .database import Database, SchemaMismatchError, _REQUIRED_COLUMNS
from .core_schema_migration import _role_name, _verify_authority, _verify_complete_review_storage


def _preflight(database,c,runtime_user):
    markers=c.query('SELECT version FROM schema_version')
    if markers not in ([{'version':10}],[{'version':11}]):
        raise SchemaMismatchError('Exactly one verified schema-10 or already-upgraded schema-11 marker is required')
    version=markers[0]['version']
    _verify_complete_review_storage(c)
    database._verify_schema_connection(c,runtime_role=False,expected_version=version)
    _verify_authority(c,runtime_user,set(_REQUIRED_COLUMNS)|{'schema_version'})
    return dict(schemaVersion=version,alreadyApplied=version==11,
                optionalOeePresent=bool(c.scalar("SELECT to_regclass('public.oee_module') IS NOT NULL")))


def preflight_postgresql_v10(database,*,runtime_user):
    if not isinstance(database,Database) or database.is_sqlite:
        raise ValueError('The routing upgrade requires an explicit PostgreSQL owner connection')
    runtime_user=_role_name(runtime_user)
    with database.transaction(write=False) as c:
        return _preflight(database,c,runtime_user)


def upgrade_postgresql_v10(database,*,runtime_user,schema_directory=None):
    """Publish reviewed guards/indexes and marker together after a verified backup."""
    if not isinstance(database,Database) or database.is_sqlite:
        raise ValueError('The routing upgrade requires an explicit PostgreSQL owner connection')
    runtime_user=_role_name(runtime_user)
    directory=Path(schema_directory) if schema_directory else Path(__file__).absolute().parents[2]/'schema'
    script=(directory/'postgresql-upgrade-10-to-11.sql').read_text(encoding='utf-8-sig')
    with database.transaction() as c:
        c.execute('SELECT pg_advisory_xact_lock(7421989)')
        before=_preflight(database,c,runtime_user)
        if not before['alreadyApplied']:
            c.execute("SELECT set_config('pak.runtime_user',:role,true)",{'role':runtime_user})
            c.raw.execute(script)
        after=_preflight(database,c,runtime_user)
        if after['schemaVersion']!=11:
            raise SchemaMismatchError('Routing upgrade did not publish the verified schema-11 contract')
        return dict(schemaVersion=11,alreadyApplied=before['alreadyApplied'],
                    optionalOeePreserved=before['optionalOeePresent'],existingOperationalDataChanged=False,
                    historicalArchivesRewritten=False,issuedIdentityChanged=False)


def upgrade_sqlite_v10(database,*,schema_directory=None):
    """Offline fixture adapter only, never the hosted application's provider."""
    if not isinstance(database,Database) or not database.is_sqlite:
        raise ValueError('SQLite upgrade is only for the explicit offline fixture adapter')
    directory=Path(schema_directory) if schema_directory else Path(__file__).absolute().parents[2]/'schema'
    script=(directory/'sqlite-pm-routing-v11.sql').read_text(encoding='utf-8-sig')
    with database.transaction() as c:
        versions=c.query('SELECT version FROM schema_version')
        if versions==[{'version':11}]:
            database._verify_schema_connection(c,expected_version=11)
            return dict(schemaVersion=11,alreadyApplied=True,existingOperationalDataChanged=False)
        database._verify_schema_connection(c,expected_version=10)
        statement=''
        for line in script.splitlines(keepends=True):
            statement+=line
            if sqlite3.complete_statement(statement):
                c.raw.execute(statement)
                statement=''
        if statement.strip():
            raise ValueError('Incomplete SQLite fixture migration statement')
        c.execute('UPDATE schema_version SET version=11 WHERE version=10')
        database._verify_schema_connection(c,expected_version=11)
        return dict(schemaVersion=11,alreadyApplied=False,existingOperationalDataChanged=False)
