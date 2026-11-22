"""Real PostgreSQL backup gate for managed releases; disposable TLS QA only."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import socket
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import release_manager as rm
from pg_test_support import fresh_database, native_tool_paths, qa_settings
from pak_maintenance.database import Database


class ReleasePostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()  # Explicit owned synthetic configuration or skip.

    def setUp(self):
        config,self.owner=fresh_database(self,prefix='release')
        self.config=replace(config,development=False)
        self.home=ROOT.parents[2]/'work'/('itqa-'+uuid4().hex[:8])
        self.home.mkdir()
        self.addCleanup(shutil.rmtree,self.home)
        shared=self.home/'shared'
        for name in ('seed/pm-originals','certificates','backups'):
            (shared/name).mkdir(parents=True)
        (shared/'seed/pm-import.json').write_text('{"synthetic":true}')
        tls=qa_settings()[0].parent/'tls'
        for source,target in (('server.pem','server.crt'),('server.key','server.key')):
            shutil.copy2(tls/source,shared/'certificates'/target)
        raw=dict(listen_url='https://127.0.0.1:55991',frontend_directory=str(ROOT.parent/'frontend/dist'),
            seed_directory=str(shared/'seed'),backup_directory=str(shared/'backups'),
            development=False,scheduling_enabled=False,automatic_backup_enabled=False,**native_tool_paths())
        text='\n'.join(f'{k} = {str(v).lower() if isinstance(v,bool) else json.dumps(v)}' for k,v in raw.items())
        text+='\n[database]\n'+'\n'.join(f'{k} = {str(v).lower() if isinstance(v,bool) else json.dumps(v)}'
            for k,v in {name:getattr(self.config,name) for name in ('provider','host','port','database','user','password','sslmode','sslrootcert','development')}.items())
        (shared/'server.toml').write_text(text,encoding='utf-8')

    def test_backup_gate_copies_shared_and_leaves_database_intact(self):
        with self.owner.connect() as c:
            before=c.query('SELECT * FROM schema_version')
        history=rm.backup_before_switch(self.home,ROOT)
        directory=self.home/'history'/history
        report=rm.read_json(directory/'backup.json')
        archive=self.home/'shared/backups'/report['databaseFile']
        self.assertEqual(archive.read_bytes()[:5],b'PGDMP')
        self.assertEqual(rm.digest(archive),report['databaseSha256'])
        self.assertEqual((directory/'shared/server.toml').read_bytes(),(self.home/'shared/server.toml').read_bytes())
        self.assertTrue((directory/'shared/seed/pm-import.json').is_file())
        self.assertFalse((directory/'shared/backups').exists())
        with self.owner.connect() as c:
            self.assertEqual(c.query('SELECT * FROM schema_version'),before)

    def test_another_live_application_session_blocks_switch(self):
        with Database(self.config).connect():
            with self.assertRaises(ValueError): rm.preflight(self.home,ROOT,stopped=True)

    def test_invalid_https_key_blocks_backup_and_activation(self):
        (self.home/'shared/certificates/server.key').write_text('invalid')
        with self.assertRaises(Exception): rm.backup_before_switch(self.home,ROOT)
        self.assertFalse((self.home/'history').exists())

    def test_external_operational_directory_is_rejected(self):
        path=self.home/'shared/server.toml'
        text=path.read_text().replace(json.dumps(str(self.home/'shared/seed')),json.dumps(str(ROOT/'seed')))
        path.write_text(text)
        with self.assertRaises(ValueError): rm.configuration(self.home,ROOT)

    def test_missing_oee_evidence_guard_blocks_switch(self):
        from pak_maintenance.oee_migration import enable_oee
        enable_oee(self.owner,runtime_user=self.config.user)
        rm.preflight(self.home,ROOT,stopped=True)
        with self.owner.transaction() as c:
            c.execute('ALTER TABLE oee_sheet_revisions DISABLE TRIGGER oee_revisions_immutable')
        with self.assertRaises(ValueError): rm.preflight(self.home,ROOT,stopped=True)

    def test_busy_local_endpoint_blocks_switch(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',55991))
            listener.listen(1)
            with self.assertRaises(OSError): rm.preflight(self.home,ROOT,stopped=True)
