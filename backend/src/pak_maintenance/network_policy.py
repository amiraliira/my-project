"""Fail-closed, offline validation of internal deployment destinations.

This module never resolves a hostname, probes a host, or opens a network
connection. It deliberately accepts numeric endpoints only. Application-level
checks do not replace an outbound-deny host firewall, TLS, OS permissions, or
change approval: a host administrator can modify the program or its environment.
Existing local symlinks / Windows reparse points are refused, but filesystem
mount changes and path replacement after validation remain OS control boundaries.
"""

from __future__ import annotations

import ctypes
import ipaddress
import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_INTERNAL_V4 = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)
_INTERNAL_V6 = ipaddress.IPv6Network("fc00::/7")
_LISTENER = re.compile(
    r"(?P<scheme>https?)://(?P<host>\[[0-9a-f:]+\]|[0-9.]+)"
    r"(?::(?P<port>[0-9]+))?/?",
    re.IGNORECASE | re.ASCII,
)
_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:\\", re.ASCII)
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')
_RESERVED_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$")
    + tuple(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")
)


class NetworkPolicyError(ValueError):
    """A destination cannot be shown to satisfy the internal-only policy."""


def validate_internal_address(host: str) -> IPAddress:
    """Return an explicitly private / loopback numeric address, without DNS.

    IPv4 shorthand, padded octets, mapped IPv6, scope IDs, special-use ranges,
    and the limited broadcast address are refused. A private address ending
    in .255 can be a valid unicast address on a wider subnet: actual directed
    broadcasts cannot be identified without the configured subnet mask and
    must be blocked by host / network firewall policy.
    """
    if not isinstance(host, str) or not host or not host.isascii() or host != host.strip():
        raise NetworkPolicyError("An internal numeric IP address is required.")
    if any(character in host for character in "%[]/\\,@; \t\r\n"):
        raise NetworkPolicyError("Hostnames, scope IDs, lists, and decorated addresses are prohibited.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise NetworkPolicyError("An internal numeric IP address is required; DNS names are prohibited.") from None

    if isinstance(address, ipaddress.IPv4Address):
        if str(address) != host or not any(address in network for network in _INTERNAL_V4):
            raise NetworkPolicyError("Only canonical RFC1918 or loopback IPv4 addresses are permitted.")
        return address

    if address.ipv4_mapped is not None or not (address == ipaddress.IPv6Address("::1") or address in _INTERNAL_V6):
        raise NetworkPolicyError("Only unscoped ULA or loopback IPv6 addresses are permitted.")
    return address


def validate_database_host(host: str) -> str:
    """Validate exactly one numeric PostgreSQL host, with its port separate."""
    return str(validate_internal_address(host))


def validate_port(port: int) -> int:
    """Validate a real integer port; booleans and string coercion are refused."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise NetworkPolicyError("A port must be an integer between 1 and 65535.")
    return port


@dataclass(frozen=True, slots=True)
class ListenerEndpoint:
    """An immutable listener whose constructor also enforces the policy."""

    scheme: Literal["http", "https"]
    address: IPAddress
    port: int

    def __post_init__(self) -> None:
        if self.scheme not in ("http", "https"):
            raise NetworkPolicyError("Only HTTP and HTTPS listeners are permitted.")
        if not isinstance(self.address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            raise NetworkPolicyError("A parsed numeric listener address is required.")
        validate_internal_address(str(self.address))
        validate_port(self.port)

    @property
    def url(self) -> str:
        host = f"[{self.address}]" if isinstance(self.address, ipaddress.IPv6Address) else str(self.address)
        return f"{self.scheme}://{host}:{self.port}"


def validate_listener_url(url: str) -> ListenerEndpoint:
    """Parse an HTTP(S) root listener; paths, userinfo and wildcards are denied.

    Matching the entire ASCII string prevents permissive URL parsers from
    silently stripping control characters or normalizing ambiguous authorities.
    """
    if not isinstance(url, str) or not url or not url.isascii():
        raise NetworkPolicyError("An ASCII HTTP(S) listener URL is required.")
    match = _LISTENER.fullmatch(url)
    if match is None:
        raise NetworkPolicyError("Listeners require an internal numeric host and no credentials, path, query, or fragment.")
    host = match["host"]
    if host.startswith("["):
        host = host[1:-1]
        if ":" not in host:
            raise NetworkPolicyError("Only IPv6 literals may use URL brackets.")
    address = validate_internal_address(host)
    scheme = match["scheme"].lower()
    port_text = match["port"]
    if port_text is None:
        port = 443 if scheme == "https" else 80
    else:
        if len(port_text) > 5 or (len(port_text) > 1 and port_text.startswith("0")):
            raise NetworkPolicyError("A listener port must use canonical decimal notation.")
        port = validate_port(int(port_text))
    return ListenerEndpoint(scheme=scheme, address=address, port=port)  # type: ignore[arg-type]


def _validate_windows_components(components: Iterable[str]) -> None:
    for component in components:
        if not component or component in (".", "..") or component.endswith((".", " ")):
            raise NetworkPolicyError("Empty, relative, and Windows-normalized path components are prohibited.")
        if any(character in _WINDOWS_FORBIDDEN for character in component):
            raise NetworkPolicyError("Device syntax, alternate data streams, and wildcard paths are prohibited.")
        if component.split(".", 1)[0].rstrip(" ").upper() in _RESERVED_NAMES:
            raise NetworkPolicyError("Windows device names are prohibited for application files.")


def _check_local_path_metadata(path: str, *, windows: bool) -> None:
    """Inspect local metadata only; never call this for a UNC destination."""
    if windows:
        # GetDriveType reads a local drive-letter mapping. Do not resolve its
        # remote target with WNetGetConnection or probe whether it is reachable.
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            drive_type = kernel.GetDriveTypeW
            drive_type.argtypes = (ctypes.c_wchar_p,)
            drive_type.restype = ctypes.c_uint
            kind = drive_type(PureWindowsPath(path).anchor)
        except (AttributeError, OSError):
            raise NetworkPolicyError("The local drive type could not be verified.") from None
        if kind == 4:
            raise NetworkPolicyError("Mapped network drives are prohibited; use an explicitly validated internal UNC share.")
        if kind not in (2, 3, 5, 6):
            raise NetworkPolicyError("The configured local drive is unavailable or cannot be verified.")

    candidate = Path(path)
    for ancestor in (*reversed(candidate.parents), candidate):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            # Descendants cannot exist once an ancestor is missing.
            break
        except OSError:
            raise NetworkPolicyError("The local application path could not be verified.") from None
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise NetworkPolicyError("Symlinks and Windows reparse points are prohibited for local application files.")


def validate_file_path(path: str | os.PathLike[str]) -> str:
    """Validate an absolute local path or numeric internal IPv4 UNC path.

    UNC syntax is checked without accessing the share. IPv6 UNC hostname
    encodings, DNS names, Windows device namespaces, ADS, drive-relative paths,
    mapped drives and existing local reparse points are refused. POSIX absolute
    paths are supported on POSIX hosts. Network mounts / TOCTOU require OS
    controls; this function cannot prove a physical disk's origin forever.
    """
    try:
        text = os.fspath(path)
    except TypeError:
        raise NetworkPolicyError("An absolute file path is required.") from None
    if not isinstance(text, str) or not text or len(text) > 32767:
        raise NetworkPolicyError("A nonempty absolute file path is required.")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in text):
        raise NetworkPolicyError("Control characters are prohibited in application file paths.")

    normalized = text.replace("/", "\\")
    if normalized.startswith(("\\\\?\\", "\\\\.\\")):
        raise NetworkPolicyError("Windows device and extended namespaces are prohibited.")
    if normalized.startswith("\\\\"):
        parts = normalized[2:].split("\\")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise NetworkPolicyError("A UNC destination requires an internal numeric host and a share name.")
        host = validate_internal_address(parts[0])
        if not isinstance(host, ipaddress.IPv4Address):
            raise NetworkPolicyError("UNC destinations require a canonical numeric IPv4 host.")
        components = parts[1:]
        if components[-1] == "":
            components = components[:-1]
        _validate_windows_components(components)
        return "\\\\" + str(host) + "\\" + "\\".join(components)

    if _DRIVE_ABSOLUTE.match(normalized):
        components = normalized[3:].split("\\")
        if components[-1] == "":
            components = components[:-1]
        _validate_windows_components(components)
        if os.name != "nt":
            raise NetworkPolicyError("Windows drive paths require a Windows host.")
        canonical = normalized[0].upper() + normalized[1:]
        _check_local_path_metadata(canonical, windows=True)
        return canonical

    if os.name != "nt" and text.startswith("/") and not text.startswith("//"):
        if "\\" in text or ":" in text or any(part in (".", "..") for part in text.split("/")):
            raise NetworkPolicyError("Ambiguous or relative path components are prohibited.")
        _check_local_path_metadata(text, windows=False)
        return str(Path(text))

    raise NetworkPolicyError("An absolute local path or explicitly internal UNC destination is required.")


@dataclass(frozen=True, slots=True)
class InternalNetworkConfig:
    """Validated startup destinations; retaining this object prevents reload drift."""

    listeners: tuple[ListenerEndpoint, ...]
    database_host: str
    database_port: int
    backup_directory: str

    def __post_init__(self) -> None:
        if type(self.listeners) is not tuple or not self.listeners:
            raise NetworkPolicyError("At least one immutable explicit listener is required.")
        if any(not isinstance(listener, ListenerEndpoint) for listener in self.listeners):
            raise NetworkPolicyError("Every listener must already satisfy the network policy.")
        if len(set(self.listeners)) != len(self.listeners):
            raise NetworkPolicyError("Duplicate listeners are prohibited.")
        object.__setattr__(self, "database_host", validate_database_host(self.database_host))
        validate_port(self.database_port)
        object.__setattr__(self, "backup_directory", validate_file_path(self.backup_directory))

    @classmethod
    def from_values(
        cls,
        *,
        listen_urls: Iterable[str],
        database_host: str,
        database_port: int,
        backup_directory: str | os.PathLike[str],
    ) -> InternalNetworkConfig:
        if isinstance(listen_urls, (str, bytes)):
            raise NetworkPolicyError("Listeners must be a collection of complete URLs.")
        return cls(
            listeners=tuple(validate_listener_url(url) for url in listen_urls),
            database_host=database_host,
            database_port=database_port,
            backup_directory=os.fspath(backup_directory),
        )
