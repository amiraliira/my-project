"""Explicit read-only PostgreSQL/core/OEE diagnosis using the active app settings.

No migrations, grants, initialization, seed import or password changes are run.
Unknown connection/driver errors are redacted rather than printed verbatim.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).absolute().parents[1]
OEE_COLUMNS = {
    'oee_module': {'version'},
    'oee_lines': {'id','code','name','asset_id','version','active','body'},
    'oee_sheets': {'id','line_id','work_date','shift','version','state','start_minute',
                   'end_minute','body','facts','payload_hash','actor_id','updated_at'},
    'oee_sheet_revisions': {'sheet_id','version','body','actor_id','created_at'},
}
OEE_GUARDS = {('oee_sheets','oee_sheets_no_delete'),
              ('oee_sheet_revisions','oee_revisions_immutable')}
OEE_RIGHTS = {
    'oee_module': (True,False,False,False,False,False),
    'oee_lines': (True,True,True,False,False,False),
    'oee_sheets': (True,True,True,False,False,False),
    'oee_sheet_revisions': (True,True,False,False,False,False),
}
RIGHT_KEYS = ('select','insert','update','delete','truncate','trigger')
EXIT_CODES = {'ready':0,'oee-missing':2,'oee-partial':3,'oee-permissions':4}
NEXT_STEPS = {
    'ready': 'Database and OEE checks passed. Use a planner account to configure lines and enter daily sheets; head can view results.',
    'core-upgrade-required': 'The connected database uses an earlier supported core schema. Follow the explicit upgrade in backend/schema/README.md after a verified backup. Existing OEE version-1 data is preserved; do not reinstall it.',
    'core-invalid': 'Use backend/schema/README.md to inspect the actual core contract. Do not change a marker or initialize a replacement database.',
    'runtime-privileged': 'Use the limited runtime login from the selected private file. Inspect overrides/role ownership; do not weaken the startup guard.',
    'oee-missing': 'Core is ready but OEE is absent in THIS database. Follow backend/oee/README.md after a verified backup; activation is an explicit owner action.',
    'oee-partial': 'Existing OEE objects are incomplete or incompatible. Preserve their data and inspect the reported mismatch; do not recreate them.',
    'oee-permissions': 'OEE objects exist but runtime rights differ from the documented limited grants. Have the schema owner inspect the exact rights.',
}


def _module_status(oee):
    """Keep optional-module findings visible even when the core needs upgrade."""
    if not oee['presentTables']:
        return 'oee-missing'
    if oee['missingTables'] or oee['wrongObjectKinds'] or oee['missingColumns'] or oee['missingEvidenceGuards']:
        return 'oee-partial'
    if not oee['runtimeRights'].get('oee_module',{}).get('select'):
        return 'oee-permissions'
    if oee['versionMarkers'] != [1]:
        return 'oee-partial'
    if not oee['rightsMatch']:
        return 'oee-permissions'
    return 'ready'


def diagnose(database):
    """Return only bounded metadata; every database transaction is read-only."""
    if database.config.provider != 'postgresql':
        raise ValueError('This diagnostic requires the configured PostgreSQL provider')
    result = {'readOnly':True, 'host':database.config.host, 'port':database.config.port,
        'database':database.config.database, 'login':database.config.user,
        'requiredCoreSchema':database.schema_version, 'requiredOeeVersion':1, 'coreVerified':False}
    with database.transaction(write=False) as connection:
        identity = connection.one('SELECT current_database() AS database, current_user AS login')
        result.update(database=identity['database'],login=identity['login'])
        role = connection.one('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                              'FROM pg_roles WHERE rolname=current_user')
        result['runtimeRole'] = {key:bool(role[key]) for key in
            ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')} if role else None
        result['publicSchemaCreate'] = bool(connection.scalar("SELECT has_schema_privilege(current_user,'public','CREATE')"))
        result['ownedPublicTables'] = connection.scalar("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND pg_has_role(current_user,c.relowner,'MEMBER')")
        # Catalog presence must not depend on the runtime role's table grants.
        objects = connection.query("SELECT c.relname AS name,c.relkind AS kind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND c.relname IN ('schema_version','oee_module','oee_lines','oee_sheets','oee_sheet_revisions')")
        objects = {row['name']:row['kind'] for row in objects}
        core_readable = (objects.get('schema_version') in ('r','p') and
            connection.scalar("SELECT has_table_privilege(current_user,'public.schema_version','SELECT')"))
        result['coreMarkers'] = [row['version'] for row in connection.query('SELECT version FROM public.schema_version')] if core_readable else None
        present = sorted(set(objects) & set(OEE_COLUMNS))
        oee = {'presentTables':present, 'missingTables':sorted(set(OEE_COLUMNS)-set(present)),
               'wrongObjectKinds':[name for name in present if objects[name] not in ('r','p')],
               'versionMarkers':None, 'missingColumns':{}, 'enabledEvidenceGuards':[],
               'missingEvidenceGuards':[], 'runtimeRights':{}, 'rightsMatch':False}
        result['oee'] = oee
        if present:
            columns = defaultdict(set)
            for row in connection.query("SELECT c.relname AS table_name,a.attname AS column_name FROM pg_attribute a "
                "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped "
                "AND c.relname IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')"):
                columns[row['table_name']].add(row['column_name'])
            oee['missingColumns'] = {name:sorted(OEE_COLUMNS[name]-columns[name]) for name in present
                                     if OEE_COLUMNS[name]-columns[name]}
            for row in connection.query("SELECT c.relname AS table_name,"
                "has_table_privilege(current_user,c.oid,'SELECT') AS can_select,"
                "has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,"
                "has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,"
                "has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,"
                "has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,"
                "has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') "
                "AND c.relname IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')"):
                oee['runtimeRights'][row['table_name']] = {key:row['can_'+key] is True for key in RIGHT_KEYS}
            oee['rightsMatch'] = (set(oee['runtimeRights']) == set(OEE_RIGHTS) and all(
                tuple(oee['runtimeRights'][name][key] for key in RIGHT_KEYS) == expected
                for name,expected in OEE_RIGHTS.items()))
            marker_readable = (objects.get('oee_module') in ('r','p') and
                'oee_module' not in oee['missingColumns'] and
                oee['runtimeRights'].get('oee_module',{}).get('select'))
            if marker_readable:
                oee['versionMarkers'] = [row['version'] for row in connection.query('SELECT version FROM public.oee_module')]
            guards = connection.query("SELECT c.relname AS table_name,t.tgname AS name FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled IN ('O','A') "
                "AND c.relname IN ('oee_sheets','oee_sheet_revisions') "
                "AND t.tgname IN ('oee_revisions_immutable','oee_sheets_no_delete')")
            enabled = {(row['table_name'],row['name']) for row in guards}
            oee['enabledEvidenceGuards'] = sorted(name for table,name in enabled & OEE_GUARDS)
            oee['missingEvidenceGuards'] = sorted(name for table,name in OEE_GUARDS-enabled)
            if (not (oee['missingTables'] or oee['wrongObjectKinds'] or oee['missingColumns'] or
                     oee['missingEvidenceGuards']) and oee['rightsMatch'] and
                    oee['versionMarkers'] == [1]):
                oee['activeLineCount'] = connection.scalar('SELECT COUNT(*) FROM public.oee_lines WHERE active=1')
    result['postgresqlVersion'] = database.server_version
    # Full core validation runs in its own read-only transaction. A failed SQL
    # statement must not poison the metadata transaction above.
    from pak_maintenance.database import SchemaMismatchError
    try:
        database.verify_schema()
        result['coreVerified'] = True
    except SchemaMismatchError:
        pass
    privileged = (result['runtimeRole'] is None or any(result['runtimeRole'].values()) or
                  result['publicSchemaCreate'] or result['ownedPublicTables'] != 0)
    oee['status'] = _module_status(oee)
    if result['coreMarkers'] != [result['requiredCoreSchema']]:
        markers = result['coreMarkers']
        status = ('core-upgrade-required' if isinstance(markers,list) and len(markers)==1 and
                  type(markers[0]) is int and markers[0] in (8,9,10,11) and
                  markers[0] < result['requiredCoreSchema'] else 'core-invalid')
    elif privileged:
        status = 'runtime-privileged'
    elif not result['coreVerified']:
        status = 'core-invalid'
    else:
        status = oee['status']
    result.update(status=status,nextStep=NEXT_STEPS[status])
    return result


def exit_code(report):
    return EXIT_CODES.get(report['status'],1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',action='store_true',help='Print the complete safe metadata report as JSON')
    arguments = parser.parse_args(argv)
    sys.path.insert(0,str(BACKEND))
    try:
        import app  # Select this delivered package, without importing app.main.
        from app.settings import configuration_source, load_config
        from pak_maintenance.database import Database
        source = configuration_source()
        config = load_config()
    except (ImportError,ValueError,OSError):
        print('[ERROR] Configuration could not be loaded. Run tools/check_installation.py --config first; private values were not printed.',file=sys.stderr)
        return 1
    try:
        report = diagnose(Database(config.database))
    except Exception:
        print('[ERROR] Database diagnosis could not complete. Check connectivity, credentials and read permissions; driver details were redacted. No repair was attempted.',file=sys.stderr)
        return 1
    report['configurationFile'] = source.path
    report['processOverrides'] = list(source.environment_settings)
    if arguments.json:
        print(json.dumps(report,sort_keys=True,indent=2))
    else:
        print('[INFO] Configuration file: '+source.path)
        print('[INFO] Process overrides (names only): '+(', '.join(source.environment_settings) or 'none'))
        print(f"[INFO] PostgreSQL: {report['host']}:{report['port']} / {report['database']} / login {report['login']}")
        print('[INFO] PostgreSQL version: '+report['postgresqlVersion'])
        print('[INFO] Core markers: '+json.dumps(report['coreMarkers']))
        print('[INFO] Full core/runtime verification: '+('passed' if report['coreVerified'] else 'failed'))
        print('[INFO] OEE tables: '+(', '.join(report['oee']['presentTables']) or 'none'))
        print('[INFO] OEE marker: '+json.dumps(report['oee']['versionMarkers']))
        print('[INFO] OEE status: '+report['oee']['status'])
        print('[INFO] Status: '+report['status'])
        if report['status']=='ready' and report['oee'].get('activeLineCount')==0:
            print('[INFO] OEE is installed but has no active lines. A planner must define/activate a line before daily entry.')
        print('[NEXT] '+report['nextStep'])
        print('[INFO] Read-only diagnosis completed; no data, roles or schema were changed. Use --json for guard and privilege details.')
    return exit_code(report)


if __name__=='__main__':
    raise SystemExit(main())
