"""Explicit loopback Nginx handoff; no sockets, server or database required."""
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from pak_maintenance.api import Api
from pak_maintenance.http_security import InternalOnlyMiddleware, RequestPolicy, TrustedReverseProxyMiddleware
from pak_maintenance.network_policy import NetworkPolicyError


@asynccontextmanager
async def _immediate_timeout(_duration):
    # No awaitable in these in-memory receive/send fixtures needs a scheduler.
    # Timeout behavior belongs to the unchanged direct-boundary test suite.
    yield


def _drive_without_io(coroutine):
    """Drive an ASGI exchange containing only immediately completed awaits.

    Avoid creating a socketpair/event-loop just to test forwarding metadata;
    this also works in restricted Windows test environments. Unexpected real
    asynchronous work fails instead of being silently ignored.
    """
    with patch('pak_maintenance.http_security.asyncio.timeout', _immediate_timeout):
        try:
            yielded=coroutine.send(None)
        except StopIteration as done:
            return done.value
        else:
            coroutine.close()
            raise AssertionError(f'Unexpected external awaitable: {type(yielded).__name__}')


class ReverseProxyTests(unittest.TestCase):
    def request(self, *, peer='127.0.0.1', forwarded='192.168.12.23',
                      host='192.168.12.92', proto='https', headers=None,
                      method='GET', path='/api/auth/me', public_url='https://192.168.12.92',
                      development=False, allow_insecure_http=False, replace_headers=None):
        reached=[]
        async def app(scope,receive,send):
            reached.append(scope)
            body=await receive()
            await send({'type':'http.response.start','status':200,
                        'headers':[(k.encode(),v.encode()) for k,v in Api.cookie('synthetic-token',scope['scheme']=='https')]})
            await send({'type':'http.response.body','body':b'ok'})
        policy_proxy=TrustedReverseProxyMiddleware(app,public_url=public_url)
        inner=InternalOnlyMiddleware(app,RequestPolicy(policy_proxy.authorities,development=development,allow_insecure_http=allow_insecure_http))
        middleware=TrustedReverseProxyMiddleware(inner,public_url=public_url)
        default=[(b'host',host.encode()),(b'x-forwarded-for',forwarded.encode()),(b'x-forwarded-proto',proto.encode())]
        pairs=(default if replace_headers is None else replace_headers)+(headers or [])
        scope={'type':'http','client':(peer,51000),'server':('127.0.0.1',5187),
               'scheme':'http','method':method,'path':path,'headers':pairs}
        original=dict(scope)
        sent=[]
        async def receive():
            return {'type':'http.request','body':b''}
        async def send(message):
            sent.append(message)
        _drive_without_io(middleware(scope,receive,send))
        return sent,reached,scope,original

    def test_valid_local_handoff_restores_public_scheme_and_client(self):
        sent,reached,scope,original=self.request()
        self.assertEqual(sent[0]['status'],200)
        self.assertEqual(reached[0]['client'],('192.168.12.23',0))
        self.assertEqual(reached[0]['scheme'],'https')
        self.assertEqual(reached[0]['server'],('192.168.12.92',443))
        self.assertEqual(scope,original)
        headers=dict(sent[0]['headers'])
        self.assertIn(b'; Secure',headers[b'set-cookie'])
        self.assertEqual(headers[b'cache-control'],b'no-store')
        self.assertIn(b'strict-transport-security',headers)

    def test_only_selected_loopback_peers_can_assert_forwarding_headers(self):
        for peer in ('192.168.12.23','10.0.0.2','8.8.8.8','::ffff:127.0.0.1','127.0.0.2'):
            with self.subTest(peer=peer):
                sent,reached,_,_=self.request(peer=peer,forwarded='127.0.0.1')
                self.assertEqual(sent[0]['status'],403)
                self.assertFalse(reached)
        sent,reached,_,_=self.request(peer='::1')
        self.assertEqual(sent[0]['status'],200)
        self.assertTrue(reached)

    def test_public_or_decorated_forwarded_clients_are_rejected(self):
        for address in ('8.8.8.8','169.254.1.1','::ffff:192.168.12.23','192.168.12.23, 127.0.0.1',
                        ' 192.168.12.23','192.168.12.23:1234','localhost',''):
            with self.subTest(address=address):
                sent,reached,_,_=self.request(forwarded=address)
                self.assertEqual(sent[0]['status'],400)
                self.assertFalse(reached)

    def test_internal_ipv6_forwarded_client_is_supported(self):
        sent,reached,_,_=self.request(forwarded='fd00::23')
        self.assertEqual(sent[0]['status'],200)
        self.assertEqual(reached[0]['client'],('fd00::23',0))

    def test_default_public_port_is_equivalent_but_other_hosts_are_not(self):
        for host in ('192.168.12.92','192.168.12.92:443'):
            sent,_,_,_=self.request(host=host)
            self.assertEqual(sent[0]['status'],200)
        for host in ('127.0.0.1:5187','192.168.12.92:80','192.168.12.93','evil.example'):
            sent,reached,_,_=self.request(host=host)
            self.assertEqual(sent[0]['status'],400)
            self.assertFalse(reached)

    def test_missing_duplicate_and_conflicting_proxy_headers_fail_closed(self):
        required=[(b'host',b'192.168.12.92'),(b'x-forwarded-for',b'192.168.12.23'),(b'x-forwarded-proto',b'https')]
        for index in range(3):
            for pairs in (required[:index]+required[index+1:],required+[required[index]]):
                with self.subTest(pairs=pairs):
                    sent,reached,_,_=self.request(replace_headers=pairs)
                    self.assertEqual(sent[0]['status'],400)
                    self.assertFalse(reached)
        for proto in ('http','https,http','HTTPS',''):
            sent,reached,_,_=self.request(proto=proto)
            self.assertEqual(sent[0]['status'],400)
            self.assertFalse(reached)

    def test_same_origin_mutation_policy_is_preserved(self):
        sent,reached,_,_=self.request(method='POST',path='/api/orders')
        self.assertEqual(sent[0]['status'],403)
        self.assertFalse(reached)
        sent,reached,_,_=self.request(method='POST',path='/api/orders',headers=[
            (b'x-app-request',b'1'),(b'origin',b'https://192.168.12.92')])
        self.assertEqual(sent[0]['status'],200)
        self.assertTrue(reached)
        sent,reached,_,_=self.request(method='POST',path='/api/orders',headers=[
            (b'x-app-request',b'1'),(b'origin',b'https://192.168.12.93')])
        self.assertEqual(sent[0]['status'],403)
        self.assertFalse(reached)

    def test_foreign_metadata_is_not_reinterpreted_downstream(self):
        sent,reached,_,_=self.request(headers=[(b'forwarded',b'for=8.8.8.8;proto=http'),
            (b'x-forwarded-host',b'evil.example'),(b'x-forwarded-port',b'80'),(b'x-real-ip',b'8.8.8.8')])
        self.assertEqual(sent[0]['status'],200)
        keys={k.lower() for k,v in reached[0]['headers']}
        self.assertFalse(any(k.startswith(b'x-forwarded-') for k in keys))
        self.assertNotIn(b'forwarded',keys)
        self.assertNotIn(b'x-real-ip',keys)

    def test_proxy_does_not_make_public_plain_http_production_safe(self):
        sent,reached,_,_=self.request(public_url='http://192.168.12.92',proto='http')
        self.assertEqual(sent[0]['status'],403)
        self.assertFalse(reached)
        sent,reached,_,_=self.request(public_url='http://192.168.12.92',proto='http',development=True)
        self.assertEqual(sent[0]['status'],200)
        self.assertNotIn(b'; Secure',dict(sent[0]['headers'])[b'set-cookie'])

    def test_error_responses_are_bounded_private_and_head_has_no_body(self):
        sent,reached,_,_=self.request(peer='192.168.12.23',method='HEAD')
        self.assertEqual(sent[0]['status'],403)
        self.assertFalse(reached)
        self.assertEqual(sent[1]['body'],b'')
        headers=dict(sent[0]['headers'])
        self.assertEqual(headers[b'cache-control'],b'no-store')
        self.assertEqual(headers[b'x-frame-options'],b'DENY')
        sent,reached,_,_=self.request(headers=[(b'x-test',b'a'*17000)])
        self.assertEqual(sent[0]['status'],431)
        self.assertFalse(reached)

    def test_control_character_proxy_header_is_rejected(self):
        sent,reached,_,_=self.request(replace_headers=[(b'host',b'192.168.12.92'),
            (b'x-forwarded-for',b'192.168.12.23\r\nX-Test: yes'),(b'x-forwarded-proto',b'https')])
        self.assertEqual(sent[0]['status'],400)
        self.assertFalse(reached)

    def test_lifespan_passes_through_and_websocket_remains_disabled(self):
        reached=[]
        async def app(scope,receive,send):
            reached.append(scope['type'])
        async def receive():
            return {'type':'lifespan.startup'}
        sent=[]
        async def send(message):
            sent.append(message)
        middleware=TrustedReverseProxyMiddleware(app,public_url='https://192.168.12.92')
        _drive_without_io(middleware({'type':'lifespan'},receive,send))
        self.assertEqual(reached,['lifespan'])
        _drive_without_io(middleware({'type':'websocket'},receive,send))
        self.assertEqual(sent,[{'type':'websocket.close','code':1008}])

    def test_configuration_reuses_numeric_internal_public_origin_policy(self):
        for url in ('https://example.com','https://8.8.8.8','http://0.0.0.0:5187','https://192.168.12.92/api'):
            with self.subTest(url=url),self.assertRaises(NetworkPolicyError):
                TrustedReverseProxyMiddleware(None,public_url=url)

    def test_explicit_http_opt_in_works_without_development_or_secure_cookies(self):
        sent,reached,_,_=self.request(public_url='http://192.168.12.92',proto='http',
            development=False,allow_insecure_http=True,method='POST',path='/api/orders',
            headers=[(b'x-app-request',b'1'),(b'origin',b'http://192.168.12.92')])
        self.assertEqual(sent[0]['status'],200)
        self.assertEqual(reached[0]['scheme'],'http')
        self.assertEqual(reached[0]['client'],('192.168.12.23',0))
        headers=dict(sent[0]['headers'])
        self.assertNotIn(b'; Secure',headers[b'set-cookie'])
        self.assertNotIn(b'strict-transport-security',headers)
        self.assertIn(b'; HttpOnly',headers[b'set-cookie'])
        self.assertIn(b'; SameSite=Strict',headers[b'set-cookie'])
        self.assertEqual(headers[b'cache-control'],b'no-store')

    def test_http_opt_in_keeps_proxy_origin_and_mutation_boundaries(self):
        defaults=dict(public_url='http://192.168.12.92',proto='http',development=False,
                      allow_insecure_http=True,method='POST',path='/api/orders')
        valid_headers=[(b'x-app-request',b'1'),(b'origin',b'http://192.168.12.92')]
        cases=[(dict(headers=[]),403),
               (dict(headers=[(b'x-app-request',b'1'),(b'origin',b'http://192.168.12.93')]),403),
               (dict(headers=valid_headers+[(b'sec-fetch-site',b'cross-site')]),403),
               (dict(headers=valid_headers,peer='192.168.12.23'),403),
               (dict(headers=valid_headers,forwarded='8.8.8.8'),400),
               (dict(headers=valid_headers,host='192.168.12.93'),400),
               (dict(headers=valid_headers+[(b'x-forwarded-proto',b'http')]),400)]
        for updates,status in cases:
            with self.subTest(updates=updates):
                sent,reached,_,_=self.request(**dict(defaults,**updates))
                self.assertEqual(sent[0]['status'],status)
                self.assertFalse(reached)

    def test_http_opt_in_does_not_downgrade_an_https_public_origin(self):
        sent,reached,_,_=self.request(allow_insecure_http=True)
        self.assertEqual(sent[0]['status'],200)
        self.assertEqual(reached[0]['scheme'],'https')
        headers=dict(sent[0]['headers'])
        self.assertIn(b'; Secure',headers[b'set-cookie'])
        self.assertIn(b'strict-transport-security',headers)
