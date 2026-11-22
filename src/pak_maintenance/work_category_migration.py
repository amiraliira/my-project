"""Explicit schema 11 -> 12 owner maintenance; no operational row rewrites."""
from pathlib import Path

from .database import Database, SchemaMismatchError, _REQUIRED_COLUMNS
from .core_schema_migration import _role_name, _verify_authority, _verify_complete_review_storage
from .work_category_contract import verify_work_category_contract


def _preflight(database, connection, runtime_user):
    markers = connection.query('SELECT version FROM schema_version')
    if markers not in ([{'version': 11}], [{'version': 12}]):
        raise SchemaMismatchError('Exactly one verified schema-11 or already-upgraded schema-12 marker is required')
    version = markers[0]['version']
    _verify_complete_review_storage(connection)
    database._verify_schema_connection(connection, runtime_role=False, expected_version=version)
    verify_work_category_contract(connection, version)
    _verify_authority(connection, runtime_user, set(_REQUIRED_COLUMNS) | {'schema_version'})
    return dict(schemaVersion=version, alreadyApplied=version == 12,
                optionalOeePresent=bool(connection.scalar("SELECT to_regclass('public.oee_module') IS NOT NULL")))


def preflight_postgresql_v11(database, *, runtime_user):
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError('The work-category upgrade requires an explicit PostgreSQL owner connection')
    runtime_user = _role_name(runtime_user)
    with database.transaction(write=False) as connection:
        return _preflight(database, connection, runtime_user)


def upgrade_postgresql_v11(database, *, runtime_user, schema_directory=None):
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError('The work-category upgrade requires an explicit PostgreSQL owner connection')
    runtime_user = _role_name(runtime_user)
    directory = Path(schema_directory) if schema_directory else Path(__file__).absolute().parents[2] / 'schema'
    script = (directory / 'postgresql-upgrade-11-to-12.sql').read_text(encoding='utf-8-sig')
    with database.transaction() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(7421989)')
        before = _preflight(database, connection, runtime_user)
        if not before['alreadyApplied']:
            connection.execute("SELECT set_config('pak.runtime_user',:role,true)", {'role': runtime_user})
            connection.raw.execute(script)
        after = _preflight(database, connection, runtime_user)
        if after['schemaVersion'] != 12:
            raise SchemaMismatchError('The work-category upgrade did not publish its verified schema-12 contract')
        return dict(schemaVersion=12, alreadyApplied=before['alreadyApplied'],
                    optionalOeePreserved=before['optionalOeePresent'], existingOperationalDataChanged=False,
                    existingProjectsReclassified=False, historicalArchivesRewritten=False)
