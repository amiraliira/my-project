"""ASGI request boundary: internal peers, trusted Host, same origin, bounded input.

Direct mode trusts no proxy headers. Explicit reverse-proxy mode accepts one
loopback Nginx hop through TrustedReverseProxyMiddleware; caller-supplied
forwarding headers cannot grant trust to a non-loopback peer. This boundary
neither opens sockets nor changes the host.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

from .common import ApiError, dumps
from .network_policy import NetworkPolicyError, validate_internal_address, validate_listener_url

MAX_BODY_BYTES = 1024 * 1024
MAX_FILE_BODY_BYTES = 8 * 1024 * 1024
FILE_UPLOAD_ROUTES = frozenset({"/api/parts/import/preview", "/api/parts/import/apply"})
SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"same-origin",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
    b"content-security-policy": (
        b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
        b"base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    b"x-frame-options": b"DENY",
}


@dataclass(frozen=True, slots=True)
class RequestPolicy:
    authorities: frozenset[str]
    development: bool = False
    max_body_bytes: int = MAX_BODY_BYTES
    allow_insecure_http: bool = False

    def __post_init__(self):
        if type(self.allow_insecure_http) is not bool:
            raise ValueError("HTTP allowance must be an explicit boolean")
        if type(self.development) is not bool:
            raise ValueError("Development mode must be an explicit boolean")
        if type(self.authorities) is not frozenset or not self.authorities:
            raise ValueError("An immutable nonempty set of Host authorities is required")
        for authority in self.authorities:
            if (not isinstance(authority, str) or not authority.isascii() or
                    any(c in authority for c in "/\\@?#\r\n\t ") or
                    authority != authority.lower()):
                raise ValueError("Invalid trusted Host authority")
            # Even direct middleware construction cannot authorize a DNS name,
            # public destination, userinfo or wildcard.
            validate_listener_url("http://" + authority)
        if type(self.max_body_bytes) is not int or not 1 <= self.max_body_bytes <= MAX_BODY_BYTES:
            raise ValueError("Invalid maximum request size")

    def inspect(self, scope: dict) -> dict[bytes, bytes]:
        client = scope.get("client")
        try:
            if not client or not isinstance(client[0], str):
                raise NetworkPolicyError("Missing peer")
            validate_internal_address(client[0])
        except (NetworkPolicyError, TypeError, IndexError):
            raise ApiError(403, "دسترسی به سامانه فقط از شبکهٔ داخلی مجاز است.") from None

        pairs = scope.get("headers", [])
        if len(pairs) > 100 or sum(len(k) + len(v) for k, v in pairs) > 16_384:
            raise ApiError(431, "حجم هدرهای درخواست بیش از حد مجاز است.")
        headers = {}
        unique = {b"host", b"origin", b"x-app-request", b"x-idempotency-key", b"content-length",
                  b"content-type", b"transfer-encoding", b"cookie", b"sec-fetch-site", b"x-import-fingerprint"}
        for key, value in pairs:
            key = key.lower()
            if b"\r" in value or b"\n" in value or b"\x00" in value:
                raise ApiError(400, "هدر درخواست معتبر نیست.")
            if key in unique and key in headers:
                raise ApiError(400, "هدر امنیتی تکراری است.")
            headers[key] = value
        try:
            authority = headers.get(b"host", b"").decode("ascii").lower()
        except UnicodeDecodeError:
            raise ApiError(400, "نشانی درخواست معتبر نیست.") from None
        if authority not in self.authorities:
            raise ApiError(400, "نشانی درخواست مجاز نیست.")

        path = scope.get("path", "")
        api = path == "/api" or path.startswith("/api/")
        if not self.development and not self.allow_insecure_http and scope.get("scheme") != "https" and path != "/api/health":
            # Serving a login screen over HTTP would let a browser transmit its
            # password before the API rejects the unencrypted request.
            raise ApiError(403, "ارتباط امن HTTPS برای استفاده از سامانه لازم است.")
        if api:
            if scope.get("method") not in {"GET", "HEAD", "OPTIONS"} and headers.get(b"x-app-request") != b"1":
                raise ApiError(403, "هدر امنیتی درخواست موجود نیست.")
            origin = headers.get(b"origin")
            if origin:
                try:
                    # A default :80/:443 is equivalent to an omitted port.
                    # Parse only strict numeric internal origins, with no DNS.
                    text = origin.decode("ascii")
                    if text.endswith("/"):
                        raise NetworkPolicyError("An Origin has no trailing path")
                    supplied = validate_listener_url(text)
                    expected = validate_listener_url(f"{scope.get('scheme')}://{authority}")
                    if supplied != expected:
                        raise NetworkPolicyError("Different origin")
                except (UnicodeDecodeError, NetworkPolicyError):
                    raise ApiError(403, "مبدأ درخواست معتبر نیست.") from None
            if headers.get(b"sec-fetch-site") == b"cross-site":
                raise ApiError(403, "مبدأ درخواست معتبر نیست.")
        length = headers.get(b"content-length")
        if length is not None and b"transfer-encoding" in headers:
            raise ApiError(400, "ساختار اندازهٔ درخواست مبهم است.")
        if length is not None:
            if not length or len(length) > 20 or any(c < 48 or c > 57 for c in length):
                raise ApiError(400, "اندازه درخواست معتبر نیست.")
            if int(length) > self.body_limit(scope):
                raise ApiError(413, "حجم درخواست بیش از حد مجاز است.")
        return headers

    def body_limit(self, scope: dict) -> int:
        if scope.get("method") == "POST" and scope.get("path") in FILE_UPLOAD_ROUTES:
            return MAX_FILE_BODY_BYTES
        return self.max_body_bytes


class TrustedReverseProxyMiddleware:
    """Accept one explicitly configured, local Nginx hop.

    Uvicorn must run with --no-proxy-headers: this adapter inspects the actual
    TCP peer before replacing the scope client/scheme. Nginx must overwrite
    X-Forwarded-For with $remote_addr and X-Forwarded-Proto with $scheme.
    Comma-separated forwarding chains are deliberately unsupported.
    """
    def __init__(self, app, *, public_url):
        self.app = app
        self.endpoint = validate_listener_url(public_url)
        authority = urlsplit(self.endpoint.url).netloc.lower()
        authorities = {authority}
        if self.endpoint.port == (443 if self.endpoint.scheme == "https" else 80):
            authorities.add(f"[{self.endpoint.address}]" if self.endpoint.address.version == 6 else str(self.endpoint.address))
        self.authorities = frozenset(authorities)

    def adapt_scope(self, scope):
        client = scope.get("client")
        try:
            if not isinstance(client, (tuple, list)) or not client or not isinstance(client[0], str):
                raise NetworkPolicyError("Missing proxy peer")
            peer = validate_internal_address(client[0])
            if str(peer) not in {"127.0.0.1", "::1"}:
                raise NetworkPolicyError("Proxy peer is not the selected loopback hop")
        except (NetworkPolicyError, TypeError, IndexError):
            raise ApiError(403, "دسترسی مستقیم به بک‌اند مجاز نیست؛ از نشانی وب سامانه استفاده کنید.") from None

        pairs = scope.get("headers", [])
        if len(pairs) > 100 or sum(len(k) + len(v) for k, v in pairs) > 16_384:
            raise ApiError(431, "حجم هدرهای درخواست بیش از حد مجاز است.")
        forwarded = {}
        unique = {b"host", b"x-forwarded-for", b"x-forwarded-proto"}
        for key, value in pairs:
            key = key.lower()
            if b"\r" in value or b"\n" in value or b"\x00" in value:
                raise ApiError(400, "هدر درخواست معتبر نیست.")
            if key in unique:
                if key in forwarded:
                    raise ApiError(400, "هدر پراکسی تکراری است.")
                forwarded[key] = value
        try:
            authority = forwarded.get(b"host", b"").decode("ascii").lower()
            scheme = forwarded.get(b"x-forwarded-proto", b"").decode("ascii")
            address = forwarded.get(b"x-forwarded-for", b"").decode("ascii")
            if authority not in self.authorities or scheme != self.endpoint.scheme:
                raise NetworkPolicyError("The public origin does not match")
            original_client = validate_internal_address(address)
        except (UnicodeDecodeError, NetworkPolicyError):
            raise ApiError(400, "نشانی یا هدرهای پراکسی معتبر نیستند.") from None

        # Discard forwarding metadata once consumed. Downstream middleware must
        # not reinterpret a second chain or caller-supplied Forwarded header.
        headers = [(key, value) for key, value in pairs
                   if key.lower() not in {b"forwarded", b"x-real-ip"}
                   and not key.lower().startswith(b"x-forwarded-")]
        return {**scope, "client": (str(original_client), 0),
                "server": (str(self.endpoint.address), self.endpoint.port),
                "scheme": self.endpoint.scheme, "headers": headers}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        try:
            adapted = self.adapt_scope(scope)
        except ApiError as error:
            body = dumps({"error": error.message}).encode("utf-8")
            headers = {**SECURITY_HEADERS, b"cache-control": b"no-store",
                       b"content-type": b"application/json; charset=utf-8",
                       b"content-length": str(len(body)).encode("ascii")}
            await send({"type": "http.response.start", "status": error.status,
                        "headers": list(headers.items())})
            await send({"type": "http.response.body",
                        "body": b"" if scope.get("method") == "HEAD" else body})
            return
        await self.app(adapted, receive, send)


class InternalOnlyMiddleware:
    def __init__(self, app, policy: RequestPolicy):
        self.app, self.policy = app, policy

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        api = scope.get("path", "") == "/api" or scope.get("path", "").startswith("/api/")
        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                replacement = dict(SECURITY_HEADERS)
                if api:
                    replacement[b"cache-control"] = b"no-store"
                if scope.get("scheme") == "https":
                    replacement[b"strict-transport-security"] = b"max-age=31536000"
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in replacement]
                message = {**message, "headers": headers + list(replacement.items())}
            await send(message)

        async def error_response(error):
            body = dumps({"error": error.message}).encode("utf-8")
            await secured_send({"type": "http.response.start", "status": error.status,
                                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                            (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body})

        try:
            inspected_headers = self.policy.inspect(scope)
            # Buffer only a bounded request, before downstream code can mutate data.
            body = bytearray()
            try:
                async with asyncio.timeout(30):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        if message["type"] != "http.request":
                            raise ApiError(400, "ساختار درخواست معتبر نیست.")
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > self.policy.body_limit(scope):
                            raise ApiError(413, "حجم درخواست بیش از حد مجاز است.")
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                raise ApiError(408, "زمان دریافت درخواست به پایان رسید.") from None
            advertised = inspected_headers.get(b"content-length")
            if advertised is not None and int(advertised) != len(body):
                raise ApiError(400, "اندازهٔ درخواست با محتوای آن یکسان نیست.")
            replayed = False
            async def bounded_receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                # Preserve disconnect signaling for streaming/cancellation.
                return await receive()
            await self.app(scope, bounded_receive, secured_send)
        except ApiError as error:
            if started:
                raise
            await error_response(error)
