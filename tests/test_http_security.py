import asyncio
import json
import unittest

from pak_maintenance.http_security import InternalOnlyMiddleware, RequestPolicy


class HttpSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, *, peer="127.0.0.1", scheme="http", method="GET",
                      headers=None, path="/api/health", chunks=None, development=True):
        called = []
        async def app(scope, receive, send):
            called.append(True)
            message = await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        messages = chunks or [{"type": "http.request", "body": b""}]
        iterator = iter(messages)
        async def receive():
            return next(iterator, {"type": "http.disconnect"})
        result = []
        async def send(message):
            result.append(message)
        scope = {"type": "http", "client": (peer, 1234), "path": path, "scheme": scheme,
                 "method": method, "headers": [(b"host", b"127.0.0.1:6081")] + (headers or [])}
        middleware = InternalOnlyMiddleware(app, RequestPolicy(frozenset({"127.0.0.1:6081"}), development=development))
        await middleware(scope, receive, send)
        return result, called

    async def test_public_peers_cannot_enter_even_with_forged_proxy_headers(self):
        for peer in ("8.8.8.8", "2001:4860:4860::8888", "169.254.1.1", "::ffff:127.0.0.1"):
            result, called = await self.request(peer=peer, headers=[(b"x-forwarded-for", b"127.0.0.1")])
            self.assertEqual(result[0]["status"], 403)
            self.assertFalse(called)

    async def test_internal_peer_gets_security_headers(self):
        result, called = await self.request(peer="172.16.223.12")
        self.assertEqual(result[0]["status"], 200)
        self.assertTrue(called)
        headers = dict(result[0]["headers"])
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertIn(b"connect-src 'self'", headers[b"content-security-policy"])
        self.assertEqual(headers[b"x-frame-options"], b"DENY")

    async def test_duplicate_host_or_origin_rejected(self):
        for headers in ([(b"host", b"evil.example")], [(b"origin", b"http://127.0.0.1:6081"), (b"origin", b"http://evil.example")]):
            result, called = await self.request(headers=headers)
            self.assertEqual(result[0]["status"], 400)
            self.assertFalse(called)

    async def test_external_origin_and_cross_site_are_rejected(self):
        for headers in ([(b"origin", b"https://evil.example")], [(b"sec-fetch-site", b"cross-site")]):
            result, called = await self.request(headers=headers)
            self.assertEqual(result[0]["status"], 403)
            self.assertFalse(called)

    async def test_mutation_needs_same_origin_security_header(self):
        result, called = await self.request(method="POST", path="/api/orders")
        self.assertEqual(result[0]["status"], 403)
        self.assertFalse(called)
        result, called = await self.request(method="POST", path="/api/orders", headers=[(b"x-app-request", b"1"), (b"origin", b"http://127.0.0.1:6081")])
        self.assertEqual(result[0]["status"], 200)
        self.assertTrue(called)

    async def test_oversize_chunked_body_rejected_before_domain_mutation(self):
        result, called = await self.request(method="POST", path="/api/orders", headers=[(b"x-app-request", b"1")], chunks=[
            {"type": "http.request", "body": b"a" * 600000, "more_body": True},
            {"type": "http.request", "body": b"b" * 600000, "more_body": False}])
        self.assertEqual(result[0]["status"], 413)
        self.assertFalse(called)

    async def test_production_rejects_plain_http_and_requires_https(self):
        result, called = await self.request(path="/api/auth/me", development=False)
        self.assertEqual(result[0]["status"], 403)
        self.assertFalse(called)
        result, called = await self.request(path="/api/auth/me", development=False, scheme="https")
        self.assertEqual(result[0]["status"], 200)
        self.assertIn(b"strict-transport-security", dict(result[0]["headers"]))

    async def test_browser_cross_origin_and_cdn_access_blocked_by_policy(self):
        result, _ = await self.request()
        csp = dict(result[0]["headers"])[b"content-security-policy"]
        for value in (b"script-src 'self'", b"connect-src 'self'", b"font-src 'self'", b"frame-ancestors 'none'"):
            self.assertIn(value, csp)
        self.assertNotIn(b"https:", csp)

    async def test_ambiguous_and_inconsistent_lengths_fail_before_mutation(self):
        for headers in ([(b"content-length", b"1")],
                        [(b"content-length", b"0"), (b"transfer-encoding", b"chunked")],
                        [(b"content-length", b"+1")]):
            result, called = await self.request(headers=headers)
            self.assertEqual(result[0]["status"], 400)
            self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
