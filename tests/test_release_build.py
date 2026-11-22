"""Publishing must reject mixed versions and stale compiled clients."""
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import package_portable as build


class ReleaseBuildTests(unittest.TestCase):
    def setUp(self):
        self.home=ROOT/'.build'/('build-test-'+uuid4().hex)
        self.home.mkdir()
        self.addCleanup(shutil.rmtree,self.home)
        files={'pyproject.toml':'[project]\nversion="2.0.0rc10"',
            'frontend/package.json':json.dumps(dict(version='2.0.0-rc.10')),
            'src/pak_maintenance/__init__.py':'__version__ = "2.0.0rc10"',
            'frontend/src/compatibility.ts':"export const clientVersion = '2.0.0-rc.10';",
            'frontend/dist/index.html':'<script src="/assets/index-synthetic.js"></script>',
            'frontend/dist/assets/index-synthetic.js':'var version="2.0.0-rc.10"; var company="شرکت پاک";'}
        for name,body in files.items():
            path=self.home/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body,encoding='utf-8')
        self.override=patch.object(build,'PROJECT',self.home)
        self.override.start();self.addCleanup(self.override.stop)

    def test_matching_source_and_bundle_can_be_published(self):
        build.validate_release()

    def test_stale_compiled_frontend_is_rejected(self):
        (self.home/'frontend/dist/assets/index-synthetic.js').write_text('version="2.0.0-rc.8"')
        with self.assertRaises(ValueError): build.validate_release()

    def test_different_python_runtime_is_rejected(self):
        (self.home/'src/pak_maintenance/__init__.py').write_text('__version__ = "2.0.0rc8"')
        with self.assertRaises(ValueError): build.validate_release()

    def test_different_frontend_project_is_rejected(self):
        (self.home/'frontend/package.json').write_text('{"version":"2.0.0-rc.8"}')
        with self.assertRaises(ValueError): build.validate_release()

    def final_review(self):
        (self.home/'docs').mkdir()
        proof=dict(version=build.VERSION,readyForItAcceptance=True,
            sourceFiles={'src/pak_maintenance/__init__.py':build.sha256(self.home/'src/pak_maintenance/__init__.py')},
            frontendFiles={'frontend/dist/assets/index-synthetic.js':build.sha256(self.home/'frontend/dist/assets/index-synthetic.js')})
        (self.home/'docs'/f'{build.REVIEW_TAG}-review.json').write_text(json.dumps(proof))

    def test_final_gate_rejects_missing_completed_review(self):
        with self.assertRaises(FileNotFoundError): build.validate_final_review()

    def test_final_gate_rejects_code_changed_after_tests(self):
        self.final_review()
        build.validate_final_review()
        (self.home/'src/pak_maintenance/__init__.py').write_text('changed after testing')
        with self.assertRaises(ValueError): build.validate_final_review()

    def test_final_gate_rejects_compiled_frontend_changed_after_tests(self):
        self.final_review()
        (self.home/'frontend/dist/assets/index-synthetic.js').write_text('other frontend')
        with self.assertRaises(ValueError): build.validate_final_review()
