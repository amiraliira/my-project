"""Explicit same-host transport and API-only config; no server/database actions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig, DatabaseConfigurationError


class ManualConfigurationCase(unittest.TestCase):
    def database(self, **changes):
        values=dict(provider='postgresql',database='pak_synthetic',host='127.0.0.1',
            user='runtime_test',password='Synthetic-test-only',sslmode='disable',
            allow_loopback_plaintext=True)
        values.update(changes)
        return DatabaseConfig(**values)

    def test_explicit_plaintext_loopback_does_not_enable_development(self):
        for address in ('127.0.0.1','::1'):
            db=self.database(host=address)
            self.assertFalse(db.development)
            self.assertEqual(db.sslmode,'disable')

    def test_plaintext_exception_never_allows_lan_database(self):
        for address in ('192.168.12.92','10.0.0.2','172.16.0.2'):
            with self.subTest(host=address), self.assertRaises(DatabaseConfigurationError):
                self.database(host=address)

    def test_loopback_exception_requires_explicit_boolean_opt_in(self):
        with self.assertRaises(DatabaseConfigurationError):
            self.database(allow_loopback_plaintext=False)
        for value in ('true',1,None):
            with self.subTest(value=value), self.assertRaises(DatabaseConfigurationError):
                self.database(allow_loopback_plaintext=value)

    def test_direct_and_old_development_configuration_still_supported(self):
        db=self.database(development=True,allow_loopback_plaintext=False)
        self.assertTrue(db.development)
        self.assertFalse(db.allow_loopback_plaintext)

    def test_api_only_proxy_needs_no_frontend_directory(self):
        with TemporaryDirectory(prefix="pak-config-", dir=Path(__file__).absolute().parent) as folder:
            config=AppConfig(database=self.database(),listen_url='https://192.168.12.92',
                frontend_directory=None,serve_frontend=False,reverse_proxy=True,
                seed_directory=folder,backup_directory=folder,scheduling_enabled=False)
            self.assertFalse(config.development)
            self.assertIsNone(config.frontend_directory)
            self.assertIn('192.168.12.92',config.request_policy.authorities)

    def test_static_mode_still_requires_frontend_path(self):
        with TemporaryDirectory(prefix="pak-config-", dir=Path(__file__).absolute().parent) as folder, self.assertRaises((ValueError,TypeError)):
            AppConfig(database=self.database(),listen_url='https://192.168.12.92',
                frontend_directory=None,seed_directory=folder,backup_directory=folder)

    def test_production_public_http_is_not_enabled_by_proxy_or_db_exception(self):
        with TemporaryDirectory(prefix="pak-config-", dir=Path(__file__).absolute().parent) as folder,self.assertRaisesRegex(ValueError,'HTTPS'):
            AppConfig(database=self.database(),listen_url='http://192.168.12.92',
                frontend_directory=None,serve_frontend=False,reverse_proxy=True,
                seed_directory=folder,backup_directory=folder)

    def test_api_only_toml_can_omit_frontend_path(self):
        with TemporaryDirectory(prefix="pak-config-", dir=Path(__file__).absolute().parent) as folder:
            file=Path(folder)/'server.toml'
            file.write_text('listen_url="https://192.168.12.92"\nserve_frontend=false\nreverse_proxy=true\n'
                'seed_directory='+repr(folder.replace('\\','/'))+'\nbackup_directory='+repr(folder.replace('\\','/'))+
                '\n[database]\nprovider="postgresql"\ndatabase="pak_synthetic"\nuser="runtime_test"\n'
                'password="Synthetic-test-only"\nsslmode="disable"\nallow_loopback_plaintext=true\n',encoding='utf-8')
            config=AppConfig.load(str(file))
            self.assertTrue(config.reverse_proxy)
            self.assertIsNone(config.frontend_directory)

    def test_lan_plaintext_test_requires_separate_opt_in(self):
        for host in ('192.168.12.92','10.0.0.2','172.16.0.2','fd00::2'):
            with self.subTest(host=host):
                config=self.database(host=host,allow_loopback_plaintext=False,allow_lan_plaintext=True)
                self.assertFalse(config.development)
                self.assertEqual(config.sslmode,'disable')
                self.assertEqual(config.host,host)

    def test_lan_exception_does_not_admit_public_ips_or_hostnames(self):
        for host in ('8.8.8.8','2001:4860:4860::8888','example.com'):
            with self.subTest(host=host),self.assertRaises(ValueError):
                self.database(host=host,allow_lan_plaintext=True)

    def test_lan_exception_is_boolean_and_does_not_change_loopback_policy(self):
        for value in ('true',1,None):
            with self.subTest(value=value),self.assertRaises(DatabaseConfigurationError):
                self.database(host='192.168.12.92',allow_lan_plaintext=value)
        with self.assertRaises(DatabaseConfigurationError):
            self.database(allow_loopback_plaintext=False,allow_lan_plaintext=True)

    def test_lan_plaintext_cannot_be_combined_with_tls_ca(self):
        with self.assertRaises(DatabaseConfigurationError):
            self.database(host='192.168.12.92',allow_lan_plaintext=True,sslrootcert='/unused/ca.crt')
