"""Real ASGI boundary for update downloads/uploads, without sockets or a DB."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import importlib.util
import unittest
from unittest.mock import Mock

from pak_maintenance.application import create_app
from pak_maintenance.common import User
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pak_maintenance.pm_pdf import render_template_pdf

AVAILABLE = all(importlib.util.find_spec(name) for name in ('fastapi', 'httpx', 'reportlab', 'arabic_reshaper', 'bidi'))
if AVAILABLE:
    import httpx


def form():
    return dict(id='local-template', version=2, title='بازدید پمپ', hall='سالن آزمایشی',
                specialty='مکانیک', period='ماهانه', source={}, tables=[dict(id='table', direction='rtl', rows=[
                    dict(id='head', cells=[dict(id='label', text='شرح فعالیت', colSpan=1), dict(id='answer-label', text='نتیجه', colSpan=1)]),
                    dict(id='row', cells=[dict(id='activity', text='کنترل نشتی و صدای پمپ', colSpan=1, activity=True), dict(id='answer', text='', colSpan=1, editable=True)])])])


@unittest.skipUnless(AVAILABLE, 'Install the update test dependencies before ASGI verification')
class UpdateHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix='update-http-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.config = AppConfig(database=DatabaseConfig(provider='postgresql', database='synthetic',
            user='runtime_test', password='Synthetic-test-only', sslmode='disable', allow_loopback_plaintext=True, development=True),
            listen_url='http://127.0.0.1:5916', development=True, serve_frontend=False, frontend_directory=None,
            seed_directory=self.temporary.name, backup_directory=self.temporary.name, scheduling_enabled=False)
        self.services = SimpleNamespace(**{name: Mock() for name in
            ('db', 'auth', 'work', 'pm', 'registry', 'archive', 'setup', 'master', 'parts_import',
             'consumption', 'performance', 'reporting', 'oee', 'backup')})
        self.services.db.config = self.config.database
        self.services.auth.authenticate.return_value = User('head', role='head')
        self.services.parts_import.preview.return_value = dict(canApply=False, rows=[], summary={})
        self.services.parts_import.apply.return_value = dict(inserted=1, updated=0, unchanged=0)
        self.services.pm.template.return_value = form()
        self.app = create_app(self.config, services=self.services)
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,
                                       client=('127.0.0.1', 1234)), base_url=self.config.listen_url)
        self.upload_headers = {'x-app-request': '1', 'origin': self.config.listen_url,
                               'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)
        self.temporary.cleanup()

    async def test_binary_pdf_download_head_and_stale_revision(self):
        response = await self.client.get('/api/pm/templates/local-template/pdf?version=2')
        self.assertEqual(response.status_code, 200, response.text[:100])
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertEqual(response.headers['content-type'], 'application/pdf')
        self.assertIn('attachment; filename="PM-', response.headers['content-disposition'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        head = await self.client.head('/api/pm/templates/local-template/pdf?version=2')
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b'')
        self.assertEqual(int(head.headers['content-length']), len(response.content))
        for query in ('', '?version=1'):
            stale = await self.client.get('/api/pm/templates/local-template/pdf' + query)
            self.assertEqual(stale.status_code, 409)
        self.services.auth.authenticate.return_value = User('technician', role='technician', specialty='مکانیک')
        denied = await self.client.get('/api/pm/templates/local-template/pdf?version=2')
        self.assertEqual(denied.status_code, 403)

    async def test_only_upload_paths_allow_more_than_one_megabyte(self):
        binary = b'P' * (1024 * 1024 + 1)
        response = await self.client.post('/api/parts/import/preview?sheet=Inventory', content=binary, headers=self.upload_headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.services.parts_import.preview.call_args.args[1:], (binary, 'Inventory'))
        for path in ('/api/parts', '/api/orders', '/api/parts/import/unknown'):
            rejected = await self.client.post(path, content=binary, headers=self.upload_headers)
            self.assertEqual(rejected.status_code, 413)
        huge = await self.client.post('/api/parts/import/preview', content=b'P' * (8 * 1024 * 1024 + 1), headers=self.upload_headers)
        self.assertEqual(huge.status_code, 413)

    async def test_upload_authority_same_origin_media_and_fingerprint(self):
        headers = dict(self.upload_headers, **{'x-import-fingerprint': 'checked-preview'})
        applied = await self.client.post('/api/parts/import/apply?sheet=Inventory', content=b'PK', headers=headers)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(self.services.parts_import.apply.call_args.args[1:], (b'PK', 'checked-preview', 'Inventory'))
        for changes, status in (({'content-type': 'text/plain'}, 415), ({'origin': 'http://127.0.0.2:5916'}, 403), ({'x-app-request': '0'}, 403)):
            response = await self.client.post('/api/parts/import/preview', content=b'PK', headers=dict(self.upload_headers, **changes))
            self.assertEqual(response.status_code, status)
        duplicate = await self.client.post('/api/parts/import/apply', content=b'PK',
            headers=list(self.upload_headers.items()) + [('x-import-fingerprint', 'one'), ('x-import-fingerprint', 'two')])
        self.assertEqual(duplicate.status_code, 400)
        self.services.auth.authenticate.return_value = User('supervisor', role='supervisor', specialty='مکانیک')
        denied = await self.client.post('/api/parts/import/preview', content=b'PK', headers=self.upload_headers)
        self.assertEqual(denied.status_code, 403)

    async def test_routing_api_uses_explicit_head_and_manager_authority(self):
        self.services.pm.responsibility.return_value = dict(orderId='local-order', eligible=True)
        self.services.pm.transfer_responsibility.return_value = dict(id='local-order', version=2)
        for role in ('head', 'manager'):
            self.services.auth.authenticate.return_value = User(role, role=role)
            response = await self.client.get('/api/pm/tasks/local-order/responsibility')
            self.assertEqual(response.status_code, 200)
            response = await self.client.post('/api/pm/tasks/local-order/responsibility',
                json=dict(version=1, supervisorId='target', reason='اصلاح مسئول'), headers={'x-app-request': '1'})
            self.assertEqual(response.status_code, 200)
        for role in ('admin', 'planner', 'supervisor', 'technician'):
            self.services.auth.authenticate.return_value = User(role, role=role)
            response = await self.client.get('/api/pm/tasks/local-order/responsibility')
            self.assertEqual(response.status_code, 403)

    async def test_full_activity_preview_returns_only_a_prospective_form(self):
        proposed = dict(form(), version=3)
        self.services.pm.preview_activities.return_value = proposed
        payload = dict(version=2, activities=[dict(id='activity', text='کنترل جدید')])
        response = await self.client.post('/api/pm/templates/local-template/preview-activities',
            json=payload, headers={'x-app-request': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version'], 3)
        self.assertEqual(self.services.pm.preview_activities.call_args.args[1:], ('local-template', payload))
        self.services.pm.edit_template.assert_not_called()


@unittest.skipUnless(AVAILABLE, 'Install PDF runtime dependencies')
class UpdatePdfTests(unittest.TestCase):
    def test_pdf_is_current_revision_and_rendering_does_not_mutate_form(self):
        from pypdf import PdfReader
        current = form(); previous = deepcopy(current)
        binary = render_template_pdf(current)
        document = PdfReader(BytesIO(binary))
        self.assertEqual(current, previous)
        self.assertEqual(document.metadata.title, current['title'])
        self.assertIn('revision 2', document.metadata.subject)
        self.assertEqual(len(document.pages), 1)
