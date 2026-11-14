"""Windows broker fallback rejects reparse ancestors and missing paths."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

LOCATION = Path(__file__).absolute().parents[2] / "tools" / "verify_source.py"
SPEC = importlib.util.spec_from_file_location("source_verifier_review", LOCATION)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@unittest.skipUnless(verifier.os.name == "nt", "Windows final-path broker fallback")
class SourceVerifierWindowsTests(unittest.TestCase):
    def denied(self):
        error = PermissionError("Synthetic final-path broker denial")
        error.winerror = 5
        return error

    def test_existing_plain_path_survives_broker_denial(self):
        with patch.object(Path, "resolve", side_effect=self.denied()):
            self.assertEqual(verifier.canonical_local_path(LOCATION), LOCATION.absolute())

    def test_missing_target_cannot_be_accepted_by_fallback(self):
        with patch.object(Path, "resolve", side_effect=self.denied()):
            with self.assertRaises(FileNotFoundError):
                verifier.canonical_local_path(LOCATION.parent / "absent-source-verification-target.fixture")

    def test_any_reparse_ancestor_is_rejected(self):
        original = Path.lstat
        for blocked in (LOCATION, LOCATION.parent, LOCATION.parents[2]):
            def metadata(path, *, follow_symlinks=True):
                if path == blocked:
                    return SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)
                return original(path)
            with self.subTest(component=str(blocked)), patch.object(Path, "resolve", side_effect=self.denied()), patch.object(Path, "lstat", metadata):
                with self.assertRaisesRegex(ValueError, "reparse"):
                    verifier.canonical_local_path(LOCATION)

    def test_other_permission_failures_are_not_ignored(self):
        error = PermissionError("Synthetic unrelated permission denial")
        error.winerror = 32
        with patch.object(Path, "resolve", side_effect=error):
            with self.assertRaises(PermissionError):
                verifier.canonical_local_path(LOCATION)
