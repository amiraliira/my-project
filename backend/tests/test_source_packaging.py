"""Source packages must preserve current metadata and omit private runtime data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

BACKEND = Path(__file__).absolute().parents[1]
TOOLS = BACKEND.parent / 'tools'
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location('public_source_packaging', TOOLS / 'package_source.py')
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class PublicSourcePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='source-package-', dir=BACKEND / 'tests')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / 'project'
        self.root.mkdir()

    def write(self, name, data='public source'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return path

    def fixture(self):
        release = {'applicationVersion':'2.0.0-rc.12','packageRevision':4,'updateDate':'2026-11-14',
                   'apiVersion':7,'coreSchemaVersion':10,'optionalOeeSchemaVersion':1,
                   'archiveRoot':'PAK-Project','startupMigrates':False}
        self.write('RELEASE.json', json.dumps(release))
        self.write('backend/pyproject.toml', '[project]\nversion="2.0.0rc12"\n')
        self.write('frontend/package.json', '{"version":"2.0.0-rc.12"}')
        self.write('backend/schema/release-policy.json', '{"coreSchema":10,"optionalOeeSchema":1}')
        for name in ('README.md','backend/.env.example','backend/app/__init__.py',
            'backend/app/main.py','backend/src/pak_maintenance/__init__.py',
            'backend/src/pak_maintenance/settings.py','tools/verify_source.py',
            'tools/source_inventory.py','frontend/dist/index.html'):
            self.write(name)

    def test_dotfile_template_is_public_but_private_env_and_data_are_excluded(self):
        self.write('backend/.env.example', 'PAK_DB_PASSWORD=CHANGE_ME_PASSWORD')
        self.write('.gitignore')
        for name in ('backend.env','backend/backend.env','backend/.env','frontend/.env.local',
            'backend/passwords.env','credentials.json','owner-production.toml','seed/pm-import.json',
            'Backups/private.json','SEED/private.json','PAK-data/private.json','Logs/trace.json',
            'pm-originals/private.docx','backups/today.dump','node_modules/pkg/index.js',
            'test-data/acceptance-data.json','postgres/bin/README.md','backend/uvicorn.log'):
            self.write(name, 'PRIVATE_SYNTHETIC_CONTENT')
        inventory = {n for n,p in packaging.source_files(self.root)}
        self.assertEqual(inventory, {'backend/.env.example','.gitignore'})

    def test_current_release_runtime_and_client_contract_is_accepted(self):
        release = packaging.release_metadata(BACKEND.parent)
        self.assertEqual(release['coreSchemaVersion'], 12)
        self.assertIn('work-categories-v1', release['requiredCapabilities'])
        self.assertIn('activity-reporting-v1', release['requiredCapabilities'])
        self.assertIn('pm-responsibility-cross-team', release['requiredCapabilities'])

    def test_archive_uses_current_release_and_does_not_mutate_source_manifest(self):
        self.fixture()
        self.write('source-manifest.json', 'old source manifest must remain untouched')
        self.write('backend/backend.env', 'PRIVATE_SYNTHETIC_CONTENT')
        output = self.home / 'current.zip'
        report = packaging.package(self.root, output)
        self.assertEqual(report['packageRevision'], 4)
        self.assertEqual(report['applicationVersion'], '2.0.0-rc.12')
        with zipfile.ZipFile(output) as archive:
            self.assertIn('PAK-Project/backend/.env.example', archive.namelist())
            self.assertNotIn('PAK-Project/backend/backend.env', archive.namelist())
            manifest = json.loads(archive.read('PAK-Project/source-manifest.json'))
            self.assertEqual(manifest['handoffRevision'], 4)
            self.assertEqual(manifest['coreSchemaVersion'], 10)
            self.assertEqual(archive.testzip(), None)
        self.assertEqual((self.root/'source-manifest.json').read_text(), 'old source manifest must remain untouched')

    def test_version_disagreement_cannot_be_published(self):
        self.fixture()
        self.write('frontend/package.json', '{"version":"2.0.0-rc.10"}')
        with self.assertRaisesRegex(ValueError, 'versions do not match'):
            packaging.package(self.root, self.home/'invalid.zip')
        self.assertFalse((self.home/'invalid.zip').exists())

    def test_existing_destination_is_not_overwritten(self):
        self.fixture()
        destination = self.home/'existing.zip'
        destination.write_bytes(b'KEEP_EXISTING_BACKUP')
        with self.assertRaises(ValueError):
            packaging.package(self.root, destination)
        self.assertEqual(destination.read_bytes(), b'KEEP_EXISTING_BACKUP')

    def test_archive_cannot_be_created_inside_the_source_tree(self):
        self.fixture()
        with self.assertRaises(ValueError):
            packaging.package(self.root, self.root/'self.zip')
        self.assertFalse((self.root/'self.zip').exists())

    def test_missing_package_initialization_cannot_be_published(self):
        self.fixture()
        (self.root/'backend/src/pak_maintenance/__init__.py').unlink()
        with self.assertRaisesRegex(ValueError, 'Required release files missing'):
            packaging.package(self.root, self.home/'incomplete.zip')


if __name__ == '__main__':
    unittest.main()
