"""Bounded, explicit PostgreSQL 17+ custom archives without shell or network discovery.

The only database destination is the already validated numeric application
endpoint. Passwords never enter argv, logs, SQL, or an inherited libpq profile.
An exclusive short-lived passfile is removed on success, failure and timeout.
Archive decoding is verified locally; an actual restore remains a separate QA
operation against an explicitly selected disposable database.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

from .database import DatabaseConfigurationError, DatabaseDependencyError
from .network_policy import validate_file_path


_TOOL_VERSION = re.compile(rb"\b(?:pg_dump|pg_restore) \(PostgreSQL\) (\d+)(?:\.(\d+))?\b")
_UNSAFE_TLS_ENV = {"OPENSSL_CONF", "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES", "SSL_CERT_FILE", "SSL_CERT_DIR"}


def executable_path(value):
    if not isinstance(value, str):
        raise ValueError("An explicit PostgreSQL backup executable is required")
    path = Path(validate_file_path(value))
    if str(path).startswith("\\\\") or not path.is_file():
        raise ValueError("PostgreSQL backup tools must be existing local executable files")
    return path


def _environment(executable):
    if any(key.upper().startswith("PG") or key.upper() in _UNSAFE_TLS_ENV for key in os.environ):
        raise DatabaseConfigurationError("Inherited libpq or TLS settings are prohibited for PostgreSQL backup tools")
    # Do not inherit arbitrary executable search paths, service files, PG*,
    # Python hooks, proxy settings, TLS configuration or tracing configuration.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    environment["PATH"] = str(executable.parent) + os.pathsep + str(system)
    return environment


def _version(executable, environment, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("PostgreSQL backup deadline exceeded")
    try:
        result = subprocess.run([str(executable), "--version"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=environment, cwd=executable.parent, timeout=min(10, remaining), check=False, shell=False,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise DatabaseDependencyError("The explicit PostgreSQL backup executable could not be verified") from None
    match = _TOOL_VERSION.search(result.stdout[:1024]) if result.returncode == 0 else None
    if match is None or int(match[1]) < 17:
        raise DatabaseDependencyError("PostgreSQL backup tools version 17 or newer are required")
    return int(match[1])


def _conninfo(config):
    values = {
        "host": config.host, "hostaddr": config.host, "port": str(config.port),
        "dbname": config.database, "user": config.user, "sslmode": config.sslmode,
        "connect_timeout": str(config.connect_timeout), "application_name": "PakMaintenance-Backup",
        "client_encoding": "UTF8", "gssencmode": "disable", "sslcertmode": "disable",
        "require_auth": "scram-sha-256", "channel_binding": "require" if config.sslmode == "verify-full" else "disable",
        "ssl_min_protocol_version": "TLSv1.2",
        "options": "-c search_path=public -c standard_conforming_strings=on -c lock_timeout=5000",
    }
    if config.sslrootcert is not None:
        values["sslrootcert"] = validate_file_path(config.sslrootcert)
        values["sslcrl"] = validate_file_path(config.sslrootcert + ".crl")
    def quote(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return " ".join(key + "=" + quote(value) for key, value in values.items())


@contextmanager
def _passfile(directory, config):
    if any(character in config.password for character in "\r\n"):
        raise ValueError("PostgreSQL backup passwords must not contain line breaks")
    private = Path(validate_file_path(str(directory / (".pak-pgpass-" + uuid4().hex))))
    if private.resolve().parent != directory.resolve():
        raise ValueError("Backup credential path is outside the private directory")
    def escape(value):
        return str(value).replace("\\", "\\\\").replace(":", "\\:")
    path = private / "pgpass"
    created = False
    try:
        # Protect only this newly created credential directory. Existing backup
        # directories and IT-managed ACLs are never modified.
        from .local_postgres import _create_private_directory
        _create_private_directory(private)
        created = True
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(":".join(escape(value) for value in (config.host, config.port, config.database, config.user, config.password)) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        if created:
            Path(validate_file_path(str(path))).unlink(missing_ok=True)
            Path(validate_file_path(str(private))).rmdir()


def _run(arguments, environment, deadline, cancelled):
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=environment, cwd=Path(arguments[0]).parent, shell=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        while True:
            if cancelled() or time.monotonic() >= deadline:
                raise TimeoutError("PostgreSQL backup cancelled or deadline exceeded")
            try:
                result = process.wait(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
                if result:
                    raise DatabaseDependencyError("The PostgreSQL backup or archive verification tool failed; no archive was published")
                return
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def create_archive(database, destination, *, dump_executable, restore_executable, deadline, cancelled=lambda: False):
    if database.config.provider != "postgresql":
        raise ValueError("PostgreSQL archive requires a PostgreSQL connection")
    dump = executable_path(dump_executable)
    restore = executable_path(restore_executable)
    dump_env, restore_env = _environment(dump), _environment(restore)
    dump_major, restore_major = _version(dump, dump_env, deadline), _version(restore, restore_env, deadline)
    with database.connect() as connection:
        server_major = connection.raw.info.server_version // 10000
    if dump_major < server_major or restore_major < dump_major:
        raise DatabaseDependencyError("PostgreSQL backup tools must support the server version and the resulting archive")
    destination = Path(validate_file_path(str(destination)))
    with _passfile(destination.parent, database.config) as passfile:
        dump_env["PGPASSFILE"] = str(passfile)
        _run([str(dump), "--no-password", "--format=custom", "--compress=6", "--no-owner", "--no-privileges",
              "--lock-wait-timeout=5000", "--file=" + str(destination), "--dbname=" + _conninfo(database.config)],
             dump_env, deadline, cancelled)
    with destination.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise DatabaseDependencyError("The PostgreSQL backup tool did not produce a custom archive")
    # Decodes every selected archive item to the null sink, without opening a
    # database connection or writing restored SQL/data to disk.
    _run([str(restore), "--no-owner", "--no-privileges", "--exit-on-error", "--file=" + os.devnull, str(destination)],
         restore_env, deadline, cancelled)
