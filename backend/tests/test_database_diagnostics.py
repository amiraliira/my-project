"""Read-only operator diagnosis distinguishes OEE setup from core and login errors."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import re
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).absolute().parents[1]
SPEC = spec_from_file_location('database_diagnostic_tool',BACKEND/'tools/check_database.py')
tool = module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
from pak_maintenance.database import SchemaMismatchError


class MetadataConnection:
    def __init__(self):
        self.statements = []
        self.objects = {'schema_version':'r',**{n:'r' for n in tool.OEE_COLUMNS}}
        self.columns = deepcopy(tool.OEE_COLUMNS)
        self.guards = set(tool.OEE_GUARDS)
        self.rights = deepcopy(tool.OEE_RIGHTS)
        self.core_markers = [12]
        self.oee_markers = [1]
        self.role = dict(rolsuper=False,rolcreatedb=False,rolcreaterole=False,rolreplication=False,rolbypassrls=False)
        self.schema_create = False
        self.owned = 0
        self.active_lines = 2

    def record(self, sql):
        self.statements.append(sql)
        if not sql.lstrip().startswith('SELECT '):
            raise AssertionError('Diagnostic attempted a non-SELECT statement')

    def one(self, sql):
        self.record(sql)
        if sql.startswith('SELECT current_database()'):
            return dict(database='synthetic_test',login='synthetic_runtime')
        if 'FROM pg_roles' in sql:
            return self.role
        raise AssertionError(sql)

    def scalar(self, sql):
        self.record(sql)
        if "has_schema_privilege" in sql:
            return self.schema_create
        if "public.schema_version" in sql:
            return True
        if 'pg_class' in sql:
            return self.owned
        if 'public.oee_lines' in sql:
            return self.active_lines
        raise AssertionError(sql)

    def query(self, sql):
        self.record(sql)
        if sql.startswith('SELECT c.relname AS name'):
            return [dict(name=n,kind=k) for n,k in self.objects.items()]
        if sql.startswith('SELECT version FROM public.schema_version'):
            return [dict(version=n) for n in self.core_markers]
        if sql.startswith('SELECT version FROM public.oee_module'):
            return [dict(version=n) for n in self.oee_markers]
        if 'FROM pg_attribute' in sql:
            return [dict(table_name=n,column_name=c) for n in self.objects if n in self.columns for c in self.columns[n]]
        if 'has_table_privilege' in sql:
            return [dict(table_name=n,**{'can_'+key:value for key,value in zip(tool.RIGHT_KEYS,self.rights[n])})
                    for n in self.objects if n in self.rights]
        if 'FROM pg_trigger' in sql:
            return [dict(table_name=t,name=n) for t,n in self.guards if t in self.objects]
        raise AssertionError(sql)


class MetadataDatabase:
    def __init__(self):
        self.config = SimpleNamespace(provider='postgresql',host='127.0.0.1',port=5432,
                                      database='synthetic_test',user='synthetic_runtime',
                                      password='SyntheticMustNeverBePrinted')
        self.server_version = '18.6'
        self.schema_version = 12
        self.connection = MetadataConnection()
        self.core_error = None
        self.read_only_calls = 0
        self.core_verifications = 0

    @contextmanager
    def transaction(self, *, write):
        if write is not False:
            raise AssertionError('A diagnostic opened a write transaction')
        self.read_only_calls += 1
        yield self.connection

    def verify_schema(self):
        self.core_verifications += 1
        if self.core_error:
            raise self.core_error


class DatabaseDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.db = MetadataDatabase()

    def test_ready_checks_guards_rights_core_and_read_only_access(self):
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'ready')
        self.assertEqual(tool.exit_code(report),0)
        self.assertEqual(self.db.read_only_calls,1)
        self.assertEqual(self.db.core_verifications,1)
        self.assertTrue(report['coreVerified'])
        self.assertEqual(report['oee']['activeLineCount'],2)
        self.assertNotIn(self.db.config.password,str(report))

    def test_absent_module_is_not_reported_as_a_core_failure(self):
        self.db.connection.objects = {'schema_version':'r'}
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'oee-missing')
        self.assertTrue(report['coreVerified'])
        self.assertEqual(tool.exit_code(report),2)

    def test_partial_module_does_not_become_an_install_request(self):
        del self.db.connection.objects['oee_sheet_revisions']
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'oee-partial')
        self.assertEqual(report['oee']['missingTables'],['oee_sheet_revisions'])

    def test_wrong_module_marker_is_partial(self):
        self.db.connection.oee_markers = [2]
        self.assertEqual(tool.diagnose(self.db)['status'],'oee-partial')

    def test_absent_read_grant_is_a_permission_issue_not_absent_installation(self):
        self.db.connection.rights['oee_module'] = (False,False,False,False,False,False)
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'oee-permissions')
        self.assertIsNone(report['oee']['versionMarkers'])
        self.assertEqual(len(report['oee']['presentTables']),4)

    def test_unexpected_delete_authority_is_not_accepted(self):
        self.db.connection.rights['oee_sheets'] = (True,True,True,True,False,False)
        self.assertEqual(tool.diagnose(self.db)['status'],'oee-permissions')

    def test_disabled_or_wrong_table_evidence_guard_is_not_accepted(self):
        self.db.connection.guards.remove(('oee_sheets','oee_sheets_no_delete'))
        self.db.connection.guards.add(('unrelated','oee_sheets_no_delete'))
        self.assertEqual(tool.diagnose(self.db)['status'],'oee-partial')

    def test_missing_required_column_is_partial(self):
        self.db.connection.columns['oee_sheets'].remove('facts')
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'oee-partial')
        self.assertEqual(report['oee']['missingColumns'],{'oee_sheets':['facts']})

    def test_core_version_mismatch_has_priority_over_optional_module(self):
        self.db.connection.core_markers = [12]
        self.db.core_error = SchemaMismatchError('Synthetic schema error')
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'core-invalid')
        self.assertEqual(tool.exit_code(report),1)

    def test_privileged_login_is_explicit_even_when_marker_is_correct(self):
        self.db.connection.role['rolsuper'] = True
        self.db.core_error = SchemaMismatchError('Synthetic role error')
        self.assertEqual(tool.diagnose(self.db)['status'],'runtime-privileged')

    def test_core_full_verification_is_not_replaced_by_a_marker(self):
        self.db.core_error = SchemaMismatchError('Synthetic missing core evidence')
        report = tool.diagnose(self.db)
        self.assertEqual(report['coreMarkers'],[12])
        self.assertEqual(report['status'],'core-invalid')

    def test_previous_core10_reports_upgrade_without_claiming_oee_is_disabled(self):
        self.db.connection.core_markers = [10]
        self.db.core_error = SchemaMismatchError('Synthetic previous core version')
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'core-upgrade-required')
        self.assertEqual(report['requiredCoreSchema'],12)
        self.assertEqual(report['coreMarkers'],[10])
        self.assertEqual(report['oee']['versionMarkers'],[1])
        self.assertEqual(report['oee']['status'],'ready')
        self.assertEqual(len(report['oee']['presentTables']),4)
        self.assertEqual(tool.exit_code(report),1)

    def test_installed_module_without_active_lines_is_ready_not_missing(self):
        self.db.connection.active_lines = 0
        report = tool.diagnose(self.db)
        self.assertEqual(report['status'],'ready')
        self.assertEqual(report['oee']['activeLineCount'],0)

    def test_cli_redacts_arbitrary_driver_error_instead_of_echoing_credentials(self):
        if str(BACKEND) not in sys.path:
            sys.path.insert(0,str(BACKEND))
        import app
        source = SimpleNamespace(path=str(BACKEND/'synthetic.env'),environment_settings=())
        secret = 'SyntheticExceptionSecretNeverPrint'
        stdout,stderr = StringIO(),StringIO()
        with patch('app.settings.configuration_source',return_value=source), \
             patch('app.settings.load_config',return_value=SimpleNamespace(database=self.db.config)), \
             patch('pak_maintenance.database.Database',return_value=self.db), \
             patch.object(tool,'diagnose',side_effect=RuntimeError(secret)), \
             redirect_stdout(stdout),redirect_stderr(stderr):
            self.assertEqual(tool.main([]),1)
        self.assertNotIn(secret,stdout.getvalue()+stderr.getvalue())
        self.assertIn('redacted',stderr.getvalue())


class NativeDatabaseDiagnosticsTests(unittest.TestCase):
    """Real metadata queries on exclusively owned disposable TLS PostgreSQL."""

    def setUp(self):
        from pg_test_support import fresh_database
        from pak_maintenance.database import Database
        self.runtime_config, self.owner = fresh_database(self, prefix='oeediag')
        self.runtime = Database(self.runtime_config)

    def snapshot(self):
        with self.owner.transaction(write=False) as connection:
            tables = [row['table_name'] for row in connection.query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND table_type='BASE TABLE' ORDER BY table_name")]
            return {name:connection.query('SELECT to_jsonb(t)::text AS row FROM '+name+
                    ' t ORDER BY to_jsonb(t)::text') for name in tables}

    def diagnosis_does_not_change_data(self, database=None):
        before = self.snapshot()
        report = tool.diagnose(database or self.runtime)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(self.runtime_config.password, str(report))
        return report

    def enable(self):
        from pak_maintenance.oee_migration import enable_oee
        return enable_oee(self.owner, runtime_user=self.runtime_config.user)

    def test_native_missing_optional_module_is_not_a_core_failure(self):
        report = self.diagnosis_does_not_change_data()
        self.assertEqual(report['status'], 'oee-missing')
        self.assertTrue(report['coreVerified'])
        self.assertEqual(report['coreMarkers'], [12])
        self.assertEqual(report['oee']['presentTables'], [])

    def test_native_valid_module_is_ready_without_manufacturing_lines(self):
        enabled = self.enable()
        self.assertEqual(enabled['coreSchemaVersion'], 12)
        report = self.diagnosis_does_not_change_data()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['oee']['status'], 'ready')
        self.assertEqual(report['oee']['versionMarkers'], [1])
        self.assertEqual(report['oee']['activeLineCount'], 0)
        self.assertTrue(report['oee']['rightsMatch'])

    def test_native_partial_installation_is_not_reported_as_absent(self):
        role = self.runtime_config.user
        with self.owner.transaction() as connection:
            connection.execute('CREATE TABLE public.oee_module(version INTEGER PRIMARY KEY CHECK(version=1))')
            connection.execute('INSERT INTO public.oee_module(version) VALUES(1)')
            connection.execute(f'GRANT SELECT ON public.oee_module TO "{role}"')
        report = self.diagnosis_does_not_change_data()
        self.assertTrue(report['coreVerified'])
        self.assertEqual(report['status'], 'oee-partial')
        self.assertEqual(report['oee']['versionMarkers'], [1])
        self.assertEqual(len(report['oee']['missingTables']), 3)

    def test_native_unexpected_delete_grant_is_reported_without_repair(self):
        self.enable()
        with self.owner.transaction() as connection:
            connection.execute(f'GRANT DELETE ON public.oee_lines TO "{self.runtime_config.user}"')
        report = self.diagnosis_does_not_change_data()
        self.assertEqual(report['status'], 'oee-permissions')
        self.assertTrue(report['oee']['runtimeRights']['oee_lines']['delete'])
        self.assertFalse(report['oee']['rightsMatch'])

    def test_native_owner_login_is_not_treated_as_runtime_ready(self):
        self.enable()
        report = self.diagnosis_does_not_change_data(self.owner)
        self.assertEqual(report['status'], 'runtime-privileged')
        self.assertGreater(report['ownedPublicTables'], 0)
        self.assertFalse(report['coreVerified'])

    def test_native_previous_core10_keeps_installed_oee_facts_visible(self):
        from pg_test_support import fresh_database, LEGACY_SCRIPTS
        from pak_maintenance.database import Database
        config, owner = fresh_database(self, prefix='oeeprev', initialize=False)
        with owner.transaction() as connection:
            for name in LEGACY_SCRIPTS:
                connection.raw.execute((BACKEND/'schema'/name).read_text(encoding='utf-8-sig'))
            connection.raw.execute((BACKEND/'schema/postgresql-oee-v1.sql').read_text(encoding='utf-8-sig'))
            connection.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
            connection.execute(f'GRANT USAGE ON SCHEMA public TO "{config.user}"')
            connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{config.user}"')
            connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{config.user}"')
            # OEE has narrower rights than the core tables.
            connection.execute(f'REVOKE ALL ON public.oee_module,public.oee_lines,public.oee_sheets,public.oee_sheet_revisions FROM "{config.user}"')
            connection.execute(f'GRANT SELECT ON public.oee_module TO "{config.user}"')
            connection.execute(f'GRANT SELECT,INSERT,UPDATE ON public.oee_lines,public.oee_sheets TO "{config.user}"')
            connection.execute(f'GRANT SELECT,INSERT ON public.oee_sheet_revisions TO "{config.user}"')
        with owner.transaction(write=False) as connection:
            before = connection.query('SELECT version FROM public.schema_version')
        report = tool.diagnose(Database(config))
        self.assertEqual(report['status'], 'core-upgrade-required')
        self.assertEqual(report['coreMarkers'], [10])
        self.assertEqual(report['requiredCoreSchema'], 12)
        self.assertEqual(report['oee']['status'], 'ready')
        self.assertEqual(report['oee']['versionMarkers'], [1])
        with owner.transaction(write=False) as connection:
            self.assertEqual(connection.query('SELECT version FROM public.schema_version'), before)

    def test_native_documented_core_grants_reestablish_the_limited_contract(self):
        role = self.runtime_config.user
        self.assertRegex(role, r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
        readme = (BACKEND/'schema/README.md').read_text(encoding='utf-8-sig')
        provision = next(block for block in re.findall(r'```sql\n(.*?)\n```', readme, re.S)
                         if 'CREATE ROLE pak LOGIN' in block)
        # Password and role creation are interactive operator actions, not part
        # of this grant verification. The fresh QA runtime identity already exists.
        grants = provision[provision.index('GRANT CONNECT'):].split('\\q', 1)[0]
        grants = grants.replace('pak_maintenance', '"'+self.runtime_config.database+'"')
        grants = re.sub(r'\bpak\b', '"'+role+'"', grants)
        before = self.snapshot()
        with self.owner.transaction() as connection:
            connection.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"')
            connection.raw.execute(grants)
        self.runtime.verify_schema()
        self.assertEqual(self.snapshot(), before)


if __name__=='__main__':
    unittest.main()
