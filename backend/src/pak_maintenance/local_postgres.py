"""An explicitly owned, loopback-only PostgreSQL 17+ presentation instance.

This is not the company server installer. It never discovers a server, downloads
software, alters a service/firewall, stops another cluster, or falls back to a
file database. The normal server launcher uses IT's explicit configuration.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time

from .database import Database, DatabaseConfig
from .network_policy import validate_file_path, validate_port

MINIMUM_POSTGRESQL = 17
DEFAULT_DATABASE_PORT = 55487
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}", re.ASCII)
_UNSAFE_TLS_ENV = frozenset({"OPENSSL_CONF", "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES", "SSL_CERT_FILE", "SSL_CERT_DIR"})
# Keep handles for deliberately retained healthy PostgreSQL children until this
# launcher exits. No PID search, forced kill, or OS-wide process cleanup.
_RETAINED_CHILDREN = []


class LocalPostgresError(RuntimeError):
    """A safe local startup failed; exception text never contains passwords."""
    def __init__(self, message, *, native_permission_denied=False):
        super().__init__(message)
        self.native_permission_denied = native_permission_denied


def _path(value):
    path = Path(validate_file_path(str(value)))
    if str(path).startswith("\\\\"):
        raise ValueError("Local PostgreSQL requires a local disk")
    return path.resolve()


def _private_bytes(path, data):
    """Exclusive creation; Windows secrets are already user-bound DPAPI blobs."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_state(path, state):
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".partial")
    try:
        _private_bytes(temporary, json.dumps(state, ensure_ascii=True, indent=2).encode("ascii"))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _dpapi(data, *, decrypt=False):
    """Bind internal credentials to this Windows user's login, with no UI."""
    class Blob(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    storage = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(storage, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = ctypes.c_int
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = ctypes.c_int
    if decrypt:
        result = crypt.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        result = crypt.CryptProtectData(ctypes.byref(incoming), "PAK local PostgreSQL", None, None, None, 1, ctypes.byref(outgoing))
    if not result:
        raise LocalPostgresError("The local database credentials could not be protected for this Windows user")
    try:
        return ctypes.string_at(outgoing.data, outgoing.length)
    finally:
        kernel.LocalFree(outgoing.data)


def _seal(credentials):
    data = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
    if os.name == "nt":
        return {"protection": "windows-user-dpapi", "value": base64.b64encode(_dpapi(data)).decode("ascii")}
    # The development/test Unix host uses the exclusive 0600 state file.
    return {"protection": "owner-mode-0600", "value": base64.b64encode(data).decode("ascii")}


def _unseal(value):
    try:
        data = base64.b64decode(value["value"], validate=True)
        if os.name == "nt" and value["protection"] == "windows-user-dpapi":
            data = _dpapi(data, decrypt=True)
        elif os.name != "nt" and value["protection"] == "owner-mode-0600":
            pass
        else:
            raise ValueError("Wrong credential protection")
        result = json.loads(data)
        if set(result) != {"bootstrap", "owner", "application"} or any(not isinstance(v, str) or len(v) < 32 for v in result.values()):
            raise ValueError("Invalid credentials")
        return result
    except (ValueError, KeyError, TypeError):
        raise LocalPostgresError("The local database credentials are invalid or belong to another OS account") from None


def _clean_environment(password=None):
    # libpq service/proxy/host variables must not redirect local commands.
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    environment["PGCONNECT_TIMEOUT"] = "5"
    environment["PGSSLMODE"] = "disable"
    environment["PGAPPNAME"] = "pak-local-bootstrap"
    if password is not None:
        environment["PGPASSWORD"] = password
    return environment


def _reject_inherited_configuration():
    # Admission must precede even local cluster creation and native process
    # checks. A libpq service/TLS profile must not influence bootstrap before
    # the application's ordinary Database boundary has been reached.
    if any(key.upper().startswith("PG") or key.upper() in _UNSAFE_TLS_ENV for key in os.environ):
        raise LocalPostgresError("Inherited PostgreSQL or TLS profile settings are prohibited; use only this copy's explicit local configuration")


def _private_principals():
    # Numeric SID avoids localized account names and subprocess parsing. This
    # touches only our newly created private data directory, never parent ACLs.
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    token = ctypes.c_void_p()
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise LocalPostgresError("The local database directory could not be protected")
    text = ctypes.c_wchar_p()
    restricted = []
    try:
        length = ctypes.c_ulong()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        storage = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, storage, length, ctypes.byref(length)):
            raise LocalPostgresError("The local database directory could not be protected")
        sid = ctypes.cast(storage, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise LocalPostgresError("The local database directory could not be protected")
        identity = text.value
        # A managed desktop may use a restricted token. Its exact logon SID is
        # private to this login and satisfies the additional restricted-token
        # ACL check without granting Everyone, Users, or application packages.
        restricted_length = ctypes.c_ulong()
        advapi.GetTokenInformation(token, 11, None, 0, ctypes.byref(restricted_length))
        if restricted_length.value:
            class SidAttribute(ctypes.Structure):
                _fields_ = [("sid", ctypes.c_void_p), ("attributes", ctypes.c_ulong)]
            class Groups(ctypes.Structure):
                _fields_ = [("count", ctypes.c_ulong), ("groups", SidAttribute * 1)]
            groups_storage = ctypes.create_string_buffer(restricted_length.value)
            if not advapi.GetTokenInformation(token, 11, groups_storage, restricted_length, ctypes.byref(restricted_length)):
                raise LocalPostgresError("The restricted desktop token could not be inspected")
            groups = ctypes.cast(groups_storage, ctypes.POINTER(Groups)).contents
            entries = ctypes.cast(ctypes.addressof(groups_storage) + Groups.groups.offset, ctypes.POINTER(SidAttribute))
            for index in range(groups.count):
                logon_sid = ctypes.c_wchar_p()
                if not advapi.ConvertSidToStringSidW(entries[index].sid, ctypes.byref(logon_sid)):
                    raise LocalPostgresError("The restricted desktop token could not be inspected")
                try:
                    if re.fullmatch(r"S-1-5-5-[0-9]+-[0-9]+", logon_sid.value):
                        restricted.append(logon_sid.value)
                finally:
                    kernel.LocalFree(logon_sid)
    finally:
        if text:
            kernel.LocalFree(text)
        kernel.CloseHandle(token)
    return [identity, "S-1-5-18", *restricted]


def _create_private_directory(directory):
    """Protect a NEW owned folder at creation; never changes an existing ACL."""
    directory = _path(directory)
    if directory.exists():
        raise FileExistsError("A private data directory must be newly created")
    directory.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if os.name != "nt":
        directory.mkdir(mode=0o700)
        return
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("descriptor", ctypes.c_void_p), ("inherit", ctypes.c_int)]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    kernel.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(SecurityAttributes)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    sddl = "D:P" + "".join(f"(A;OICI;FA;;;{sid})" for sid in _private_principals())
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise LocalPostgresError("The private local database directory descriptor could not be created")
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, 0)
        if not kernel.CreateDirectoryW(str(directory), ctypes.byref(attributes)):
            raise LocalPostgresError("The private local database directory could not be created")
    finally:
        kernel.LocalFree(descriptor)


def _run(executable, arguments, *, password=None, timeout=120, acceptable=(0,)):
    try:
        result = subprocess.run([str(executable), *map(str, arguments)], shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_clean_environment(password), timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise LocalPostgresError(f"The local PostgreSQL {Path(executable).stem} operation could not complete") from None
    if result.returncode not in acceptable:
        # Server/restore errors may echo row data. Never forward them to console.
        permission_denied = (Path(executable).stem == "pg_ctl" and any(fragment in result.stderr.lower()
            for fragment in (b"operation not permitted", b"permission denied", b"access is denied")))
        raise LocalPostgresError(f"The local PostgreSQL {Path(executable).stem} operation failed",
            native_permission_denied=permission_denied)
    return result


class LocalPostgresPathError(LocalPostgresError):
    """The Windows native tools require a usable non-localized install path."""


class LocalPostgres:
    """Create or reopen only the cluster described by our private checkpoint."""
    def __init__(self, binaries, directory, dump_file, *, payload_sha256, port=DEFAULT_DATABASE_PORT):
        self.binaries, self.directory, self.dump_file = map(_path, (binaries, directory, dump_file))
        self.data = self.directory / "data"
        self.state_file = self.directory / "local-state.json"
        self.port = validate_port(port)
        if not isinstance(payload_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", payload_sha256) is None:
            raise ValueError("An explicit verified payload identity is required")
        self.payload_sha256 = payload_sha256
        self.state = None
        self.credentials = None
        self.started_here = False
        self.identity_verified = False
        self.process = None
        suffix = ".exe" if os.name == "nt" else ""
        self.executables = {name: self.binaries / "bin" / (name + suffix)
            for name in ("postgres", "initdb", "pg_ctl", "pg_restore", "pg_dump")}

    def _check_binaries(self):
        if os.name == "nt" and any(not str(path).isascii() for path in (self.binaries,self.directory,self.dump_file)):
            raise LocalPostgresPathError("در ویندوز، برنامه را در پوشه‌ای با نام انگلیسی و مسیر کوتاه، مانند C:\\PAK، استخراج و اجرا کنید.")
        for path in self.executables.values():
            if not path.is_file():
                raise LocalPostgresError("Extract the complete PostgreSQL runtime before starting")
            _path(path)
        output = _run(self.executables["postgres"], ["--version"], timeout=10).stdout.decode("ascii", "replace")
        match = re.search(r"\(PostgreSQL\)\s+(\d+)(?:\.(\d+))?", output)
        if match is None or int(match[1]) < MINIMUM_POSTGRESQL:
            raise LocalPostgresError("The local PostgreSQL runtime must be version 17 or newer")
        self.major = int(match[1])

    def _load_or_create_state(self):
        existed = self.directory.exists()
        if not existed:
            _create_private_directory(self.directory)
        if self.state_file.exists():
            with self.state_file.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise LocalPostgresError("The local PostgreSQL ownership checkpoint is invalid")
            try:
                state = json.loads(raw)
                if (state["format"] != 1 or state["provider"] != "postgresql"
                        or state["payloadSha256"] != self.payload_sha256
                        or _path(state["dataDirectory"]) != self.data
                        or state["majorVersion"] != self.major
                        or re.fullmatch(r"[0-9a-f]{32}", state["instanceId"]) is None
                        or any(_IDENTIFIER.fullmatch(state[key]) is None for key in ("database", "bootstrapRole", "ownerRole", "applicationRole"))
                        or len({state[key] for key in ("bootstrapRole", "ownerRole", "applicationRole")}) != 3
                        or state["phase"] not in ("initialize", "restore", "ready")):
                    raise ValueError("Checkpoint mismatch")
                self.port = validate_port(state["port"])
                self.credentials = _unseal(state["credentials"])
            except (ValueError, KeyError, TypeError):
                raise LocalPostgresError("Existing PostgreSQL data belongs to another package or is incomplete; it was not changed") from None
            self.state = state
            return
        # Unknown files are never adopted, overwritten, cleaned or reinitialized.
        if any(self.directory.iterdir()):
            raise LocalPostgresError("Refusing to initialize an unrecognized PostgreSQL data directory")
        self.credentials = {key: secrets.token_urlsafe(40) for key in ("bootstrap", "owner", "application")}
        self.state = {"format": 1, "provider": "postgresql", "instanceId": secrets.token_hex(16),
            "payloadSha256": self.payload_sha256, "majorVersion": self.major,
            "dataDirectory": str(self.data), "port": self.port, "database": "pak_local",
            "bootstrapRole": "pak_local_bootstrap", "ownerRole": "pak_local_owner",
            "applicationRole": "pak_local_app", "phase": "initialize", "credentials": _seal(self.credentials)}
        _atomic_state(self.state_file, self.state)

    def _connection(self, *, role="bootstrap", database="postgres", autocommit=False):
        _reject_inherited_configuration()
        import psycopg
        return psycopg.connect(host="127.0.0.1", hostaddr="127.0.0.1", port=self.port, dbname=database,
            user=self.state[{"bootstrap": "bootstrapRole", "owner": "ownerRole", "application": "applicationRole"}[role]],
            password=self.credentials[role], sslmode="disable", connect_timeout=5,
            application_name="pak-local-bootstrap", client_encoding="UTF8",
            gssencmode="disable", sslcertmode="disable", require_auth="scram-sha-256",
            channel_binding="disable", ssl_min_protocol_version="TLSv1.2",
            options="-c search_path=public -c standard_conforming_strings=on -c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=30000",
            autocommit=autocommit)

    def _initialize_cluster(self):
        version = self.data / "PG_VERSION"
        if version.exists():
            if version.read_text(encoding="ascii").strip() != str(self.major):
                raise LocalPostgresError("The existing cluster requires its matching PostgreSQL major version")
            if self.state["phase"] == "initialize":
                self._write_cluster_configuration()
            self._verify_configuration_files()
            return
        if self.data.exists() and any(self.data.iterdir()):
            raise LocalPostgresError("An interrupted cluster initialization needs explicit recovery; no files were overwritten")
        if self.state["phase"] != "initialize":
            raise LocalPostgresError("The owned PostgreSQL cluster is missing; no replacement was created")
        password_file = self.directory / ("init-" + secrets.token_hex(8) + ".password")
        try:
            _private_bytes(password_file, (self.credentials["bootstrap"] + "\n").encode("ascii"))
            _run(self.executables["initdb"], ["-D", self.data, "--username", self.state["bootstrapRole"],
                "--pwfile", password_file, "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                "--encoding=UTF8", "--locale=C", "--no-instructions"], timeout=120)
        finally:
            if password_file.exists():
                password_file.unlink()
        self._write_cluster_configuration()

    def _verify_configuration_files(self):
        for name, expected in self.state.get("configurationSha256", {}).items():
            if name not in ("postgresql.conf", "postgresql.auto.conf", "pg_hba.conf") or hashlib.sha256((self.data / name).read_bytes()).hexdigest() != expected:
                raise LocalPostgresError("The owned local PostgreSQL configuration changed; automatic startup was refused")
        if set(self.state.get("configurationSha256", {})) != {"postgresql.conf", "postgresql.auto.conf", "pg_hba.conf"}:
            raise LocalPostgresError("The owned local PostgreSQL configuration checkpoint is missing")

    def _write_cluster_configuration(self):
        # This directory was just created by this launcher. Never edits a server.
        settings = ("listen_addresses = '127.0.0.1'\n" + f"port = {self.port}\n"
            "max_connections = 64\nshared_buffers = '64MB'\n"
            "password_encryption = 'scram-sha-256'\nssl = off\n"
            "fsync = on\nsynchronous_commit = on\nfull_page_writes = on\n"
            "autovacuum = on\nlog_statement = 'none'\nlog_min_error_statement = 'panic'\n"
            "log_parameter_max_length_on_error = 0\nlogging_collector = off\n"
            "unix_socket_directories = ''\n")
        (self.data / "postgresql.conf").write_text(settings, encoding="ascii")
        roles = ",".join(self.state[key] for key in ("bootstrapRole", "ownerRole", "applicationRole"))
        hba = f"host all {roles} 127.0.0.1/32 scram-sha-256\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n"
        (self.data / "pg_hba.conf").write_text(hba, encoding="ascii")
        auto = self.data / "postgresql.auto.conf"
        if not auto.exists():
            auto.write_text("# Owned local PostgreSQL: no automatic overrides.\n", encoding="ascii")
        self.state["configurationSha256"] = {name: hashlib.sha256((self.data / name).read_bytes()).hexdigest()
            for name in ("postgresql.conf", "postgresql.auto.conf", "pg_hba.conf")}
        self.state["phase"] = "restore"
        _atomic_state(self.state_file, self.state)

    def _verify_server_identity(self):
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('server_version_num')::integer,current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port')::integer")
                version, directory, listener, port = cursor.fetchone()
                if version < 170000 or version // 10000 != self.major or _path(directory) != self.data or listener != "127.0.0.1" or port != self.port:
                    raise LocalPostgresError("The running database identity does not match this application's owned local cluster")
        self.identity_verified = True

    def _start(self):
        # Native process queries can be denied to a managed Windows token even
        # when SCRAM access to its owned database is valid. Authenticate first;
        # a port listener alone can never establish ownership.
        try:
            self._verify_server_identity()
            return
        except LocalPostgresError:
            raise
        except Exception:
            pass
        # Fail before pg_ctl if somebody else already owns this exact port.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            if os.name == "nt":
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                reservation.bind(("127.0.0.1", self.port))
            except OSError:
                raise LocalPostgresError("The local PostgreSQL port is in use; no other process was stopped") from None
        # Run this owned child directly. pg_ctl's Windows restricted-token
        # process creation can fail in managed desktop environments; no service
        # install, elevation, arbitrary process termination or alternate server.
        with (self.directory / "postgresql.log").open("ab") as log:
            self.process = subprocess.Popen([str(self.executables["postgres"]), "-D", str(self.data)],
                shell=False, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=_clean_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.started_here = True
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise LocalPostgresError("The owned local PostgreSQL process did not start")
            try:
                self._verify_server_identity()
                return
            except LocalPostgresError:
                raise
            except Exception:
                time.sleep(0.1)
        raise LocalPostgresError("The owned local PostgreSQL process did not become ready")

    def _create_roles_and_database(self):
        from psycopg import sql
        owner, application, database = (self.state[key] for key in ("ownerRole", "applicationRole", "database"))
        with self._connection(autocommit=True) as connection:
            with connection.cursor() as cursor:
                for name, credential in ((owner, "owner"), (application, "application")):
                    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,))
                    if cursor.fetchone() is None:
                        if self.state["phase"] == "ready":
                            raise LocalPostgresError("An existing local database role is missing; no automatic repair was performed")
                        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(name), sql.Literal(self.credentials[credential])))
                cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,))
                if cursor.fetchone() is None:
                    if self.state["phase"] == "ready":
                        raise LocalPostgresError("The existing local database is missing; no replacement was created")
                    cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(database), sql.Identifier(owner)))
                    cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
                    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(application)))
                    cursor.execute(sql.SQL("ALTER DATABASE {} SET pak.local_instance_id TO {}").format(sql.Identifier(database), sql.Literal(self.state["instanceId"])))
        with self._connection(role="owner", database=database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('pak.local_instance_id',true)")
                if cursor.fetchone()[0] != self.state["instanceId"]:
                    raise LocalPostgresError("The local application database ownership checkpoint does not match")

    def _restore_and_grant(self):
        from psycopg import sql
        database, owner, application = (self.state[key] for key in ("database", "ownerRole", "applicationRole"))
        owner_database = Database(self.database_config(role="owner"))
        if self.state["phase"] == "ready":
            # A normal reopen is read-only admission. Never restore data, grant
            # back revoked privileges, or rescan large tables during startup.
            owner_database.verify_schema(runtime_role=False)
            Database(self.database_config()).verify_schema()
            return
        with self._connection(role="owner", database=database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')")
                count = cursor.fetchone()[0]
        if not count:
            self.state["phase"] = "restore"
            _atomic_state(self.state_file, self.state)
            _run(self.executables["pg_restore"], ["--host", "127.0.0.1", "--port", self.port,
                "--username", owner, "--dbname", database, "--no-password", "--no-owner", "--no-privileges",
                "--exit-on-error", "--single-transaction", self.dump_file], password=self.credentials["owner"], timeout=180)
        # A committed restore followed by a power loss is recoverable. An
        # incomplete/foreign schema is rejected by Database.verify_schema.
        owner_database.verify_schema(runtime_role=False)
        with self._connection(role="owner", database=database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
                cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(application)))
                cursor.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(application)))
                cursor.execute(sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(application)))
                cursor.execute(sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {}").format(sql.Identifier(application)))
                # Restored data needs planner statistics before the first user
                # opens a large catalog; runtime credentials cannot own tables.
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
                for (table,) in cursor.fetchall():
                    cursor.execute(sql.SQL("ANALYZE public.{}").format(sql.Identifier(table)))
        Database(self.database_config()).verify_schema()
        if self.state["phase"] != "ready":
            self.state["phase"] = "ready"
            _atomic_state(self.state_file, self.state)

    def database_config(self, *, role="application"):
        if self.state is None or role not in ("application", "owner"):
            raise LocalPostgresError("The local database has not been prepared")
        return DatabaseConfig(provider="postgresql", host="127.0.0.1", port=self.port,
            database=self.state["database"], user=self.state["applicationRole" if role == "application" else "ownerRole"],
            password=self.credentials[role], sslmode="disable", development=True)

    def start(self):
        _reject_inherited_configuration()
        self._check_binaries()
        self._load_or_create_state()
        self._initialize_cluster()
        self._start()
        self._create_roles_and_database()
        self._restore_and_grant()
        return self.database_config()

    def stop(self):
        if self.started_here and self.identity_verified:
            self._verify_server_identity()
            try:
                _run(self.executables["pg_ctl"], ["-D", self.data, "-w", "-t", "40", "stop", "-m", "fast"], timeout=50)
            except LocalPostgresError as error:
                if not error.native_permission_denied:
                    raise
                # Do not replace a denied native control signal with a forced
                # kill. A verified healthy owned cluster can safely remain
                # running, as a normal PostgreSQL service does. The next app
                # launch authenticates its identity and reuses it unchanged.
                self._verify_server_identity()
                self.state["shutdownDeferred"] = True
                self.state["shutdownReason"] = "native-control-unavailable"
                _atomic_state(self.state_file, self.state)
                self.started_here = False
                if self.process is not None:
                    _RETAINED_CHILDREN.append(self.process)
                return
            self.started_here = False
            if self.process is not None:
                self.process.wait(timeout=5)
        elif self.started_here and self.process is not None:
            # An incomplete native startup is retained for explicit recovery;
            # forcing only a postmaster down can strand its worker processes.
            if self.process.poll() is None:
                _RETAINED_CHILDREN.append(self.process)
            self.started_here = False

    @contextmanager
    def running(self):
        try:
            yield self.start()
        finally:
            self.stop()
