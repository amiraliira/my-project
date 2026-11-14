"""Actual FastAPI/httpx through the proxy boundary; synthetic services, no DB."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pak_maintenance.application import create_app
from pak_maintenance.common import User
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig

AVAILABLE=all(importlib.util.find_spec(x) is not None for x in ('fastapi','httpx'))
if AVAILABLE:
    import httpx


@unittest.skipUnless(AVAILABLE,'Real FastAPI/httpx dependencies unavailable')
class ManualHttpCase(unittest.IsolatedAsyncioTestCase):
    public_url="https://192.168.12.92"
    allow_http=False
    async def asyncSetUp(self):
        self.temp=TemporaryDirectory(prefix="pak-http-", dir=Path(__file__).absolute().parent)
        self.config=AppConfig(database=DatabaseConfig(provider='postgresql',database='synthetic',
            user='runtime_test',password='Synthetic-test-only',sslmode='disable',
            allow_loopback_plaintext=True),listen_url=self.public_url,allow_insecure_http=self.allow_http,
            frontend_directory=None,serve_frontend=False,reverse_proxy=True,
            seed_directory=self.temp.name,backup_directory=self.temp.name,scheduling_enabled=False)
        self.services=SimpleNamespace(**{name:Mock() for name in (
            'db','auth','work','pm','registry','archive','setup','master','consumption',
            'performance','reporting','oee','backup')})
        self.services.db.config=self.config.database
        self.services.db.server_version_num=180006
        self.services.db.server_version='18.6'
        self.services.auth.login.return_value=(User('tech',code='1',name='Synthetic',role='technician'),'synthetic-token')
        self.app=create_app(self.config,services=self.services)
        self.lifecycle=self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,client=('127.0.0.1',12345)),
            base_url='http://192.168.12.92')
        self.forwarded={'x-forwarded-proto':self.public_url.split(':',1)[0],'x-forwarded-for':'192.168.12.34'}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None,None,None)
        self.temp.cleanup()

    async def test_real_framework_api_health_through_nginx_metadata(self):
        response=await self.client.get('/api/health',headers=self.forwarded)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['databaseVersionNum'],180006)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual('strict-transport-security' in response.headers,not self.allow_http)
        self.services.db.verify_schema.assert_called_once_with()

    async def test_login_retains_https_cookie_and_original_client(self):
        response=await self.client.post('/api/auth/login',json={'code':'1','password':'Synthetic-test-only'},
            headers={**self.forwarded,'x-app-request':'1','origin':self.public_url})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual('; Secure' in response.headers['set-cookie'],not self.allow_http)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.services.auth.login.assert_called_once_with('192.168.12.34','1','Synthetic-test-only')

    async def test_cross_origin_mutation_never_reaches_authentication(self):
        response=await self.client.post('/api/auth/login',json={'code':'1','password':'Synthetic-test-only'},
            headers={**self.forwarded,'x-app-request':'1','origin':'https://192.168.12.99'})
        self.assertEqual(response.status_code,403)
        self.services.auth.login.assert_not_called()

    async def test_no_static_frontend_is_mounted(self):
        for path in ('/','/index.html','/assets/client.js'):
            response=await self.client.get(path,headers=self.forwarded)
            self.assertEqual(response.status_code,404)

    async def test_nonloopback_client_cannot_forge_nginx(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,client=('192.168.12.34',12345)),
                base_url='http://192.168.12.92') as client:
            response=await client.get('/api/health',headers=self.forwarded)
        self.assertEqual(response.status_code,403)

    async def test_missing_proxy_metadata_fails_closed(self):
        response=await self.client.get('/api/health')
        self.assertEqual(response.status_code,400)
@unittest.skipUnless(AVAILABLE,'Real FastAPI/httpx dependencies unavailable')
class ManualPlainHttpCase(ManualHttpCase):
    public_url='http://192.168.12.92'
    allow_http=True