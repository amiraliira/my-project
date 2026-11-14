"""Security boundary tests using only local metadata; no hosts are contacted."""

from __future__ import annotations

import dataclasses
import ipaddress
import os
import shutil
import socket
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from pak_maintenance.network_policy import (
    InternalNetworkConfig,
    ListenerEndpoint,
    NetworkPolicyError,
    validate_database_host,
    validate_file_path,
    validate_internal_address,
    validate_listener_url,
    validate_port,
)


@contextmanager
def _local_test_directory():
    # Python's default mode=0700 temp directories are inaccessible to this
    # Windows sandbox token. Inherit the workspace ACL instead; delete only
    # this fixture's verified absolute child, never arbitrary temp paths.
    root = Path(__file__).resolve().parent / ".network-policy-test-data"
    root.mkdir(mode=0o755, exist_ok=True)
    fixture = root / uuid.uuid4().hex
    fixture.mkdir(mode=0o755)
    try:
        yield str(fixture)
    finally:
        if fixture.resolve().parent != root.resolve() or fixture.is_symlink():
            raise RuntimeError("Refusing to clean a test fixture outside its owned directory.")
        shutil.rmtree(fixture)
        try:
            root.rmdir()
        except OSError:
            pass


class InternalAddressTests(unittest.TestCase):
    def test_explicit_internal_address_ranges(self) -> None:
        for host in (
            "10.0.0.1", "10.254.254.254", "172.16.0.1", "172.31.254.254",
            "192.168.0.1", "192.168.254.254", "127.0.0.1", "127.2.3.4",
            "::1", "fc00::1", "fd00::a", "FDFE:0000:0000:0000:0000:0000:0000:0001",
        ):
            with self.subTest(host=host):
                self.assertEqual(validate_internal_address(host), ipaddress.ip_address(host))

    def test_public_and_other_special_use_addresses_fail_closed(self) -> None:
        for host in (
            "8.8.8.8", "1.1.1.1", "0.0.0.0", "255.255.255.255", "224.0.0.1",
            "172.15.255.254", "172.32.0.1", "192.167.0.1", "192.169.0.1",
            "169.254.1.1", "100.64.0.1", "192.0.0.1", "192.0.2.1", "198.18.0.1",
            "198.51.100.1", "203.0.113.1", "240.0.0.1", "::", "fe80::1",
            "ff02::1", "fec0::1", "2001:db8::1", "2001:4860:4860::8888",
            "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:8.8.8.8",
        ):
            with self.subTest(host=host), self.assertRaises(NetworkPolicyError):
                validate_internal_address(host)

    def test_private_unicast_is_not_guessed_from_last_octet(self) -> None:
        # 10.1.0.255 is a valid unicast address on 10.1.0.0/23. Without the
        # configured subnet mask, .255 must not be mislabeled as broadcast.
        for host in ("10.1.0.255", "172.17.0.255", "192.168.0.255"):
            with self.subTest(host=host):
                self.assertEqual(validate_internal_address(host), ipaddress.ip_address(host))
        with self.assertRaises(NetworkPolicyError):
            validate_internal_address("255.255.255.255")

    def test_ambiguous_address_parsing_is_refused(self) -> None:
        for host in (
            "localhost", "LOCALHOST", "localhost.", "server.company.local", "10.0.0.1.example.com",
            "127.1", "2130706433", "0x7f000001", "0177.0.0.1", "127.000.0.1", "127.0.0.01",
            "127.0.0.1 ", " 127.0.0.1", "127.0.0.1\n", "127.0.0.1\x00", "۱۲۷.۰.۰.۱",
            "[::1]", "fd00::1%eth0", "fd00::1%0", "fd00::1%25eth0", "127.0.0.1:5432",
            "10.0.0.1,8.8.8.8", "10.0.0.1/8", "10.0.0.1@8.8.8.8", "", None, 123,
        ):
            with self.subTest(host=host), self.assertRaises(NetworkPolicyError):
                validate_internal_address(host)  # type: ignore[arg-type]

    def test_database_host_is_one_numeric_address_with_separate_port(self) -> None:
        self.assertEqual(validate_database_host("FD00:0000:0000:0000:0000:0000:0000:0001"), "fd00::1")
        for host in ("10.0.0.1,10.0.0.2", "[fd00::1]:5432", "host=10.0.0.1", "postgresql://10.0.0.1/db", "/run/postgresql"):
            with self.subTest(host=host), self.assertRaises(NetworkPolicyError):
                validate_database_host(host)

    def test_ports_do_not_accept_coercion_or_booleans(self) -> None:
        for port in (1, 5081, 5432, 65535):
            self.assertEqual(validate_port(port), port)
        for port in (0, -1, 65536, True, False, 5432.0, "5432", None):
            with self.subTest(port=port), self.assertRaises(NetworkPolicyError):
                validate_port(port)  # type: ignore[arg-type]


class ListenerTests(unittest.TestCase):
    def test_internal_numeric_listeners_and_default_ports(self) -> None:
        cases = {
            "http://127.0.0.1:5081": "http://127.0.0.1:5081",
            "https://192.168.1.105:8443/": "https://192.168.1.105:8443",
            "HTTP://10.0.0.1": "http://10.0.0.1:80",
            "https://[fd00::1]": "https://[fd00::1]:443",
            "http://[::1]:5081/": "http://[::1]:5081",
        }
        for supplied, expected in cases.items():
            with self.subTest(supplied=supplied):
                self.assertEqual(validate_listener_url(supplied).url, expected)

    def test_url_parser_confusion_and_extra_components_fail_closed(self) -> None:
        for url in (
            "http://0.0.0.0:5081", "http://*:5081", "http://+:5081", "http://[::]:5081",
            "http://localhost:5081", "https://example.com", "http://8.8.8.8", "ftp://10.0.0.1",
            "http://user:secret@10.0.0.1", "http://10.0.0.1@8.8.8.8", "http://10.0.0.1/path",
            "http://10.0.0.1//", "http://10.0.0.1/.", "http://10.0.0.1/?", "http://10.0.0.1#",
            "http://10.0.0.1?next=https://evil.example", "http://10.0.0.1/%2e%2e",
            "http://10.0.0.1\\@8.8.8.8", "http:\\10.0.0.1", "http://%31%30.0.0.1",
            "http://010.0.0.1", "http://127.1", "http://2130706433", "http://10.0.0.1\t:5081",
            "\nhttp://10.0.0.1:5081", "http://10.0.0.1:5081\n", "http://10.0.0.1:５０８１",
            "http://[fd00::1%25eth0]:5081", "http://[::ffff:10.0.0.1]:5081",
            "http://[127.0.0.1]:5081", "http://::1:5081", "http://10.0.0.1:", "http://10.0.0.1:0",
            "http://10.0.0.1:65536", "http://10.0.0.1:05081", "http://10.0.0.1:+5081",
            "http://10.0.0.1:5081;http://8.8.8.8:5081", "", None,
        ):
            with self.subTest(url=url), self.assertRaises(NetworkPolicyError):
                validate_listener_url(url)  # type: ignore[arg-type]

    def test_direct_construction_cannot_bypass_validation(self) -> None:
        for address in (ipaddress.ip_address("8.8.8.8"), ipaddress.ip_address("::"), "127.0.0.1"):
            with self.subTest(address=address), self.assertRaises(NetworkPolicyError):
                ListenerEndpoint("http", address, 5081)  # type: ignore[arg-type]
        with self.assertRaises(NetworkPolicyError):
            ListenerEndpoint("ftp", ipaddress.ip_address("127.0.0.1"), 5081)  # type: ignore[arg-type]
        with self.assertRaises(NetworkPolicyError):
            ListenerEndpoint("http", ipaddress.ip_address("127.0.0.1"), True)
        endpoint = validate_listener_url("http://127.0.0.1:5081")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            endpoint.port = 80  # type: ignore[misc]


class FilePathTests(unittest.TestCase):
    def test_unc_numeric_internal_shares_are_normalized_without_access(self) -> None:
        cases = {
            r"\\192.168.1.20\backups\پاک": "\\\\192.168.1.20\\backups\\پاک",
            "//10.0.0.2/backup/weekly/": "\\\\10.0.0.2\\backup\\weekly",
            r"\\127.0.0.1\backup": "\\\\127.0.0.1\\backup",
        }
        # lstat of a UNC share would perform network I/O. Prohibit it even
        # though these shares have not been created and are never contacted.
        with patch.object(Path, "lstat", side_effect=AssertionError("UNC must not be accessed")):
            for supplied, expected in cases.items():
                with self.subTest(path=supplied):
                    self.assertEqual(validate_file_path(supplied), expected)

    def test_unsafe_unc_and_cloud_destinations_are_refused(self) -> None:
        for path in (
            r"\\8.8.8.8\backups", r"\\storage.company.local\backups", r"\\localhost\backups",
            r"\\169.254.1.1\backups", r"\\255.255.255.255\backups", r"\\10.0.0.1", "\\\\10.0.0.1\\",
            r"\\\10.0.0.1\backups", r"\\fd00::1\backups", r"\\[fd00::1]\backups",
            r"\\fd00--1.ipv6-literal.net\backups", r"\\10.0.0.1:445\backups",
            r"\\10.0.0.1\..\backups", r"\\10.0.0.1\.\backups", r"\\10.0.0.1\backups\..\secret",
            r"\\10.0.0.1\backups\folder.\file", r"\\10.0.0.1\backup \file",
            r"\\10.0.0.1\backup\file:secret", r"\\10.0.0.1\backup\NUL.txt",
            "https://10.0.0.1/backups", "https://storage.example/backups", "s3://company/backups",
            "file://10.0.0.1/backups", "\\\\10.0.0.1\\backups\\\x00file",
        ):
            with self.subTest(path=path), self.assertRaises(NetworkPolicyError):
                validate_file_path(path)

    def test_device_namespaces_ads_and_relative_paths_are_refused(self) -> None:
        for path in (
            r"\\?\C:\backups", r"\\?\UNC\10.0.0.1\backups", r"\\.\C:\backups",
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\backups", r"\Device\HarddiskVolume1\backups",
            r"C:backups", r"\backups", "backups", "../backups", r"C:\backups\..\secret",
            r"C:\backups\.\file", r"C:\backups\file:secret", r"C:\backups\NUL", r"C:\backups\CON.txt",
            r"C:\backups\com1.log", r"C:\backups\LPT².log", r"C:\backups\file ", r"C:\backups\folder.",
            r"C:\backups\*", r"C:\backups\?", "C:\\backups\\file\n", "", b"C:\\backups", None,
        ):
            with self.subTest(path=path), self.assertRaises(NetworkPolicyError):
                validate_file_path(path)  # type: ignore[arg-type]

    def test_real_absolute_local_directory_and_future_child_are_allowed(self) -> None:
        with _local_test_directory() as directory:
            root = Path(directory)
            self.assertEqual(Path(validate_file_path(root)), root)
            future = root / "پاک" / "backup.db"
            self.assertEqual(Path(validate_file_path(future)), future)

    def test_existing_symlink_and_descendants_are_refused(self) -> None:
        with _local_test_directory() as directory:
            root = Path(directory)
            target = root / "actual"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Host does not permit test symlinks: {error.__class__.__name__}")
            for path in (link, link / "future" / "backup.db"):
                with self.subTest(path=path), self.assertRaises(NetworkPolicyError):
                    validate_file_path(path)

    @unittest.skipUnless(os.name == "nt", "Windows drive metadata test")
    def test_available_mapped_drives_are_refused_without_contacting_targets(self) -> None:
        import ctypes

        drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        drive_type.argtypes = (ctypes.c_wchar_p,)
        drive_type.restype = ctypes.c_uint
        mapped = [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if drive_type(f"{letter}:\\") == 4]
        if not mapped:
            self.skipTest("No existing mapped drives on this host; no test mapping is created.")
        for path in mapped:
            with self.subTest(path=path), self.assertRaises(NetworkPolicyError):
                validate_file_path(path)


class FrozenConfigurationTests(unittest.TestCase):
    def test_complete_configuration_is_immutable_and_canonical(self) -> None:
        with _local_test_directory() as directory:
            config = InternalNetworkConfig.from_values(
                listen_urls=["http://127.0.0.1:5081", "https://[fd00::1]:8443"],
                database_host="FD00:0:0:0:0:0:0:1",
                database_port=5432,
                backup_directory=directory,
            )
            self.assertEqual(config.database_host, "fd00::1")
            self.assertIsInstance(config.listeners, tuple)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                config.database_host = "8.8.8.8"  # type: ignore[misc]

    def test_missing_or_mutable_or_duplicate_listeners_are_refused(self) -> None:
        with _local_test_directory() as directory:
            for listeners in ((), [], [validate_listener_url("http://127.0.0.1:5081")], ("http://127.0.0.1:5081",)):
                with self.subTest(listeners=listeners), self.assertRaises(NetworkPolicyError):
                    InternalNetworkConfig(listeners, "127.0.0.1", 5432, directory)  # type: ignore[arg-type]
            for urls in ([], "http://127.0.0.1:5081", ["http://127.0.0.1:5081", "http://127.0.0.1:5081/"]):
                with self.subTest(urls=urls), self.assertRaises(NetworkPolicyError):
                    InternalNetworkConfig.from_values(
                        listen_urls=urls, database_host="127.0.0.1", database_port=5432, backup_directory=directory,
                    )

    def test_validation_never_resolves_or_connects(self) -> None:
        with _local_test_directory() as directory:
            with (
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS is prohibited")),
                patch.object(socket, "gethostbyname", side_effect=AssertionError("DNS is prohibited")),
                patch.object(socket, "create_connection", side_effect=AssertionError("Network access is prohibited")),
            ):
                InternalNetworkConfig.from_values(
                    listen_urls=["http://127.0.0.1:5081"], database_host="192.168.1.20",
                    database_port=5432, backup_directory=directory,
                )
                validate_file_path(r"\\192.168.1.20\backups")
                for host in ("localhost", "example.com", "10.0.0.1.example.com"):
                    with self.subTest(host=host), self.assertRaises(NetworkPolicyError):
                        validate_database_host(host)


if __name__ == "__main__":
    unittest.main()
