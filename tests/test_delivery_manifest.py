"""Delivery integrity rejects mixed providers, altered files and unsafe paths."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from test_database import fixture_directory


SPEC = importlib.util.spec_from_file_location("pak_delivered_verifier", Path(__file__).resolve().parents[1] / "tools/verify_package.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class DeliveryManifestTests(unittest.TestCase):
    def make_package(self, directory):
        names = ("runtime/python.exe", "runtime/python314.dll", "frontend/index.html", "src/pak_maintenance/application.py",
                 "src/pak_maintenance/configuration.py", "src/pak_maintenance/database.py", "tools/verify_package.py",
                 "Start-Server.cmd", "server.example.toml")
        files = {}
        for name in names:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            value = ("synthetic delivery fixture: " + name).encode("utf-8")
            path.write_bytes(value)
            files[name] = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        return dict(databaseProvider="postgresql", minimumPostgreSQLMajor=17, applicationAllowsSQLite=False,
                    localAcceptanceOnly=False, containsDatabaseServer=False, containsCompanyData=False, files=files)

    def write(self, directory, manifest):
        (directory / "package-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_verified_server_layout_passes_then_an_altered_binary_fails(self):
        with fixture_directory() as directory:
            manifest = self.make_package(directory)
            self.write(directory, manifest)
            self.assertEqual(VERIFIER.verify(directory), 9)
            original = directory / "runtime/python.exe"
            original.write_bytes(b"x" * original.stat().st_size)
            with self.assertRaisesRegex(ValueError, "hash"):
                VERIFIER.verify(directory)

    def test_sqlite_manifest_and_working_database_are_rejected(self):
        with fixture_directory() as directory:
            manifest = self.make_package(directory)
            manifest["databaseProvider"] = "sqlite"
            self.write(directory, manifest)
            with self.assertRaisesRegex(ValueError, "PostgreSQL"):
                VERIFIER.verify(directory)
            manifest["databaseProvider"] = "postgresql"
            path = directory / "acceptance.db"
            path.write_bytes(b"old file database must not ship")
            manifest["files"][path.name] = dict(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.write(directory, manifest)
            with self.assertRaisesRegex(ValueError, "legacy"):
                VERIFIER.verify(directory)

    def test_duplicate_json_path_or_parent_traversal_is_rejected(self):
        with fixture_directory() as directory:
            manifest = self.make_package(directory)
            for name in ("../outside.py", "src//ambiguous.py", "C:/outside.py", "src\\outside.py"):
                with self.subTest(path=name):
                    candidate = {**manifest, "files": {**manifest["files"], name: {"bytes": 0, "sha256": "0" * 64}}}
                    self.write(directory, candidate)
                    with self.assertRaises(ValueError):
                        VERIFIER.verify(directory)
            self.write(directory, manifest)
            path = directory / "package-manifest.json"
            path.write_text(path.read_text(encoding="utf-8").replace('{', '{"databaseProvider":"sqlite",', 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                VERIFIER.verify(directory)

    def test_server_manifest_cannot_include_private_payload_or_daemon(self):
        with fixture_directory() as directory:
            manifest = self.make_package(directory)
            manifest["containsDatabaseServer"] = True
            self.write(directory, manifest)
            with self.assertRaisesRegex(ValueError, "IT server"):
                VERIFIER.verify(directory)
            manifest["containsDatabaseServer"] = False
            manifest["localAcceptanceOnly"] = True
            self.write(directory, manifest)
            with self.assertRaisesRegex(ValueError, "presentation"):
                VERIFIER.verify(directory)


if __name__ == "__main__":
    unittest.main()
