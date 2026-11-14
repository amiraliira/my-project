"""Local bootstrap rejects inherited connection profiles before side effects."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pak_maintenance import local_postgres


class LocalBootstrapBoundaryTests(unittest.TestCase):
    def instance(self):
        # No directory, secret, cluster or connection is created by this fixture.
        root = Path(__file__).resolve().parents[1] / ".build/bootstrap-boundary-not-created"
        return local_postgres.LocalPostgres(root / "postgres", root / "owned", root / "payload.dump",
            payload_sha256="a" * 64)

    def test_inherited_profiles_rejected_before_native_checks_or_cluster_mutation(self):
        keys = ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGPASSFILE", "OPENSSL_CONF",
                "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES", "SSL_CERT_FILE", "SSL_CERT_DIR")
        for key in keys:
            with self.subTest(key=key), patch.dict(os.environ, {key: "synthetic-profile-value"}, clear=True):
                local = self.instance()
                with patch.object(local, "_check_binaries") as binaries, \
                     patch.object(local, "_load_or_create_state") as state, \
                     patch.object(local, "_initialize_cluster") as initialize, \
                     patch.object(local, "_start") as start, \
                     patch.object(local, "_create_roles_and_database") as create, \
                     patch.object(local, "_restore_and_grant") as restore:
                    with self.assertRaisesRegex(local_postgres.LocalPostgresError, "Inherited") as raised:
                        local.start()
                    self.assertNotIn("synthetic-profile-value", str(raised.exception))
                    for operation in (binaries, state, initialize, start, create, restore):
                        operation.assert_not_called()
                    self.assertIsNone(local.state)
                    self.assertIsNone(local.process)
                    self.assertEqual(os.environ[key], "synthetic-profile-value")

    def test_direct_bootstrap_connection_also_rejects_an_inherited_profile(self):
        connect = Mock()
        with patch.dict(os.environ, {"PGSERVICE": "synthetic-never-opened"}, clear=True), \
             patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
            with self.assertRaisesRegex(local_postgres.LocalPostgresError, "Inherited"):
                self.instance()._connection()
        connect.assert_not_called()

    def test_bootstrap_connection_pins_local_address_scram_and_no_implicit_tls_or_gss(self):
        local = self.instance()
        local.state = {"bootstrapRole": "synthetic_bootstrap", "ownerRole": "synthetic_owner",
                       "applicationRole": "synthetic_app"}
        local.credentials = {"bootstrap": "synthetic-bootstrap-password",
                             "owner": "synthetic-owner-password", "application": "synthetic-app-password"}
        connect = Mock(return_value=object())
        with patch.dict(os.environ, {"PAK_UNRELATED_SETTING": "untouched"}, clear=True), \
             patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
            local._connection(role="owner", database="synthetic_target", autocommit=True)
            self.assertEqual(dict(os.environ), {"PAK_UNRELATED_SETTING": "untouched"})
        connect.assert_called_once()
        options = connect.call_args.kwargs
        self.assertEqual((options["host"], options["hostaddr"], options["port"]),
                         ("127.0.0.1", "127.0.0.1", local.port))
        self.assertEqual((options["user"], options["dbname"], options["password"]),
                         ("synthetic_owner", "synthetic_target", "synthetic-owner-password"))
        self.assertEqual(options["require_auth"], "scram-sha-256")
        for key in ("gssencmode", "sslcertmode", "channel_binding", "sslmode"):
            self.assertEqual(options[key], "disable")
        self.assertEqual(options["client_encoding"], "UTF8")
        self.assertTrue(options["autocommit"])
        self.assertIn("search_path=public", options["options"])
        self.assertIn("standard_conforming_strings=on", options["options"])
        self.assertIn("statement_timeout=15000", options["options"])
        self.assertIn("lock_timeout=5000", options["options"])


if __name__ == "__main__":
    unittest.main()
