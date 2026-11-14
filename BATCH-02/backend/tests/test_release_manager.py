"""Filesystem failure/rollback drills for the offline managed installation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import release_manager as rm


class ReleaseManagerTests(unittest.TestCase):
    def setUp(self):
        self.home = ROOT / '.build' / ('release-test-' + uuid4().hex)
        self.home.mkdir()
        self.addCleanup(shutil.rmtree, self.home)
        self.install = self.home / 'installed'
        self.source = self.home / 'source'
        self.source.mkdir()
        required = ('runtime/python.exe', 'runtime/python314.dll', 'frontend/index.html',
            'src/pak_maintenance/application.py', 'src/pak_maintenance/configuration.py',
            'src/pak_maintenance/database.py', 'tools/verify_package.py',
            'tools/release_manager.py', 'Start-Server.cmd', 'server.example.toml')
        for name in required:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic', encoding='utf-8')
        (self.source / 'release-policy.json').write_text(json.dumps(rm.COMPATIBILITY))
        self.manifest('2.0.0-rc.9')

    def manifest(self, version, **changes):
        files = {p.relative_to(self.source).as_posix():dict(bytes=p.stat().st_size, sha256=rm.digest(p))
                 for p in self.source.rglob('*') if p.is_file() and p.name != 'package-manifest.json'}
        value = dict(version=version, files=files, databaseProvider='postgresql', minimumPostgreSQLMajor=17,
                     applicationAllowsSQLite=False, containsDatabaseServer=False, containsCompanyData=False,
                     localAcceptanceOnly=False, **changes)
        (self.source / 'package-manifest.json').write_text(json.dumps(value))

    def active(self):
        release = rm.prepare(self.install, self.source)
        rm.bootstrap(self.install, release)
        (self.install / 'shared/server.toml').write_text('private-settings')
        rm.atomic_json(self.install / 'current.json',dict(protocol=1,current=release.name,
            previous=None,controller=release.name))
        self.manifest('2.0.0-rc.10')

    def test_tampered_candidate_is_rejected_before_install(self):
        (self.source / 'frontend/index.html').write_text('modified')
        with self.assertRaises(ValueError): rm.prepare(self.install, self.source)
        self.assertFalse((self.install / 'current.json').exists())

    def test_unknown_files_and_credentials_never_enter_release(self):
        (self.source / 'server.toml').write_text('secret')
        release = rm.prepare(self.install, self.source)
        self.assertFalse((release / 'server.toml').exists())
        rm.package_info(release)

    def test_first_install_waits_for_explicit_configuration(self):
        result = rm.apply(self.install, self.source)
        self.assertEqual(result['status'], 'configuration-required')
        self.assertFalse((self.install / 'current.json').exists())
        self.assertTrue((self.install / 'Start-Pak.cmd').exists())

    def test_same_version_cannot_be_reused_with_new_files(self):
        rm.prepare(self.install,self.source)
        (self.source / 'frontend/index.html').write_text('other')
        self.manifest('2.0.0-rc.9')
        with self.assertRaises(ValueError): rm.prepare(self.install,self.source)

    def test_backup_failure_keeps_previous_selection_and_data(self):
        self.active()
        old = (self.install / 'current.json').read_bytes()
        with patch.object(rm.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','')):
            with self.assertRaises(ValueError): rm.apply(self.install,self.source)
        self.assertEqual((self.install / 'current.json').read_bytes(),old)
        self.assertEqual((self.install / 'shared/server.toml').read_text(),'private-settings')

    def test_successful_upgrade_records_previous_without_rewriting_shared(self):
        self.active()
        with patch.object(rm.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"history":"snapshot-1"}','')):
            result = rm.apply(self.install,self.source)
        state = rm.selected(self.install)
        self.assertEqual((state['current'],state['previous']),('2.0.0-rc.10','2.0.0-rc.9'))
        self.assertFalse(result['databaseMigration'])
        self.assertEqual((self.install / 'shared/server.toml').read_text(),'private-settings')
        self.assertIn(b'rc.9', (self.install / 'Start-Pak.cmd').read_bytes())

    def test_same_or_older_release_requires_separate_rollback(self):
        self.active()
        self.manifest('2.0.0-rc.8')
        with self.assertRaises(ValueError): rm.apply(self.install,self.source)

    def test_rollback_failure_preserves_selection(self):
        self.active()
        state = rm.selected(self.install)
        state['previous']='2.0.0-rc.9'
        rm.atomic_json(self.install/'current.json',state)
        before=(self.install/'current.json').read_bytes()
        with patch.object(rm,'backup_before_switch',side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError): rm.rollback(self.install)
        self.assertEqual((self.install/'current.json').read_bytes(),before)

    def test_rollback_never_restores_or_resets_database(self):
        self.active()
        with patch.object(rm.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"history":"s"}','')):
            rm.apply(self.install,self.source)
        with patch.object(rm,'backup_before_switch',return_value='rollback-snapshot'):
            result=rm.rollback(self.install)
        self.assertEqual(rm.selected(self.install)['current'],'2.0.0-rc.9')
        self.assertFalse(result['databaseRestored'])
        self.assertIsNone(rm.selected(self.install)['previous'])

    def test_interrupted_atomic_pointer_write_keeps_old_json(self):
        self.install.mkdir()
        path=self.install/'current.json'
        rm.atomic_json(path,dict(current='old'))
        with patch.object(rm.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError): rm.atomic_json(path,dict(current='new'))
        self.assertEqual(rm.read_json(path),dict(current='old'))

    def test_operating_application_lock_rejects_another_operation(self):
        with rm.installation_lock(self.install):
            with self.assertRaises(OSError):
                with rm.installation_lock(self.install): pass
        with rm.installation_lock(self.install): pass

    def test_version_paths_and_sorting(self):
        self.assertLess(rm.version_key('2.0.0-rc.9'),rm.version_key('2.0.0-rc.10'))
        self.assertLess(rm.version_key('2.0.0-rc.10'),rm.version_key('2.0.0'))
        for value in ('../evil','C:/evil',None,'2.0.0-rc.9/x'):
            with self.assertRaises(ValueError): rm.version_key(value)

    def test_schema_changing_release_is_not_automatically_applied(self):
        (self.source/'release-policy.json').write_text(json.dumps(dict(rm.COMPATIBILITY,coreSchema=9)))
        self.manifest('2.0.0-rc.9')
        with self.assertRaises(ValueError): rm.package_info(self.source)

    def test_reparse_or_symlink_root_is_rejected(self):
        # The sandbox cannot create a Windows junction; inject the OS discovery
        # result and check the refusal. No actual junction creation is claimed.
        with patch.object(Path,'is_junction',return_value=True):
            with self.assertRaises(ValueError): rm.local(self.home/'child')

    def test_duplicate_metadata_is_rejected(self):
        path=self.home/'bad.json';path.write_text('{"protocol":1,"protocol":2}')
        with self.assertRaises(ValueError): rm.read_json(path)

    def test_disk_space_failure_does_not_change_pointer(self):
        self.active()
        before=(self.install/'current.json').read_bytes()
        with patch.object(rm.shutil,'disk_usage',return_value=type('Disk',(),{'free':1})()):
            with self.assertRaises(ValueError): rm.prepare(self.install,self.source)
        self.assertEqual((self.install/'current.json').read_bytes(),before)

    def test_truncated_launcher_is_repaired_without_switching_after_backup_failure(self):
        self.active()
        old=(self.install/'current.json').read_bytes()
        (self.install/'Start-Pak.cmd').write_bytes(b'partial')
        with patch.object(rm.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','')):
            with self.assertRaises(ValueError): rm.apply(self.install,self.source)
        self.assertEqual((self.install/'current.json').read_bytes(),old)
        self.assertIn(b'releases\\2.0.0-rc.9\\runtime', (self.install/'Start-Pak.cmd').read_bytes())

    def test_interrupted_launcher_replacement_preserves_old_complete_launcher(self):
        release=rm.prepare(self.install,self.source)
        rm.bootstrap(self.install,release)
        launcher=self.install/'Start-Pak.cmd'
        launcher.write_bytes(b'old-complete-launcher')
        with patch.object(rm.os,'replace',side_effect=OSError('power failure')):
            with self.assertRaises(OSError): rm.bootstrap(self.install,release)
        self.assertEqual(launcher.read_bytes(),b'old-complete-launcher')

    def test_backup_directory_is_pruned_before_walking(self):
        directory=self.home/'shared'
        (directory/'backups/deep').mkdir(parents=True)
        (directory/'backups/deep/large.dump').write_bytes(b'not read')
        (directory/'seed').mkdir()
        (directory/'seed/original.pdf').write_bytes(b'original')
        visited=[]
        original_walk=rm.os.walk
        def watch(*args,**kwargs):
            for item in original_walk(*args,**kwargs):
                visited.append(Path(item[0]))
                yield item
        with patch.object(rm.os,'walk',side_effect=watch):
            files=list(rm.shared_files(directory))
        self.assertEqual(files,[directory/'seed/original.pdf'])
        self.assertNotIn(directory/'backups',visited)
        self.assertNotIn(directory/'backups/deep',visited)

    def test_cannot_silently_omit_unreadable_shared_files(self):
        directory=self.home/'shared';directory.mkdir()
        def broken(*args,**kwargs):
            kwargs['onerror'](PermissionError('unreadable originals'))
            yield
        with patch.object(rm.os,'walk',side_effect=broken):
            with self.assertRaises(PermissionError): list(rm.shared_files(directory))

    def test_unsafe_controller_metadata_is_rejected(self):
        self.install.mkdir()
        rm.atomic_json(self.install/'current.json',dict(protocol=1,current='2.0.0-rc.9',controller='../escape',previous=None))
        with self.assertRaises(ValueError): rm.selected(self.install)

    @unittest.skipUnless(sys.platform == 'win32','Windows case-insensitive path semantics')
    def test_backup_pruning_respects_windows_case_insensitive_paths(self):
        directory=self.home/'shared'
        (directory/'Backups/deep').mkdir(parents=True)
        (directory/'Backups/deep/old.dump').write_bytes(b'not read')
        (directory/'server.toml').write_text('settings')
        self.assertEqual(list(rm.shared_files(directory)),[directory/'server.toml'])
