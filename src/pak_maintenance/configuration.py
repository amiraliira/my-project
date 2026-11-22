"""Explicit software configuration, with no host or firewall modifications."""
from dataclasses import dataclass
from pathlib import Path
import tomllib

from .database import DatabaseConfig
from .http_security import RequestPolicy
from .network_policy import validate_file_path, validate_listener_url, validate_internal_address


@dataclass(frozen=True, slots=True)
class AppConfig:
    database: DatabaseConfig
    listen_url: str
    frontend_directory: str | None
    seed_directory: str
    backup_directory: str
    development: bool = False
    scheduling_enabled: bool = True
    company_name: str = "شرکت لبنیات پاک"
    local_test_mode: bool = False
    automatic_backup_enabled: bool = False
    backup_interval_hours: int = 24
    backup_retention_count: int = 14
    postgresql_dump_executable: str | None = None
    postgresql_restore_executable: str | None = None
    serve_frontend: bool = True
    reverse_proxy: bool = False
    allow_insecure_http: bool = False

    def __post_init__(self):
        if not isinstance(self.database, DatabaseConfig):
            raise ValueError("An explicit database configuration is required")
        if self.database.provider != "postgresql":
            raise ValueError("The application requires PostgreSQL 17 or newer in every mode; SQLite is supported only by offline import tools")
        if type(self.development) is not bool or type(self.scheduling_enabled) is not bool:
            raise ValueError("Software mode flags must be boolean")
        if type(self.serve_frontend) is not bool or type(self.reverse_proxy) is not bool:
            raise ValueError("Frontend serving and reverse proxy modes must be explicit booleans")
        if type(self.allow_insecure_http) is not bool:
            raise ValueError("HTTP allowance must be an explicit boolean")
        if self.allow_insecure_http and not self.reverse_proxy:
            raise ValueError("Explicit public HTTP requires the configured local reverse proxy")
        if self.reverse_proxy and self.local_test_mode:
            raise ValueError("Reverse proxy and personal local-test modes cannot be combined")
        endpoint = validate_listener_url(self.listen_url)
        if type(self.local_test_mode) is not bool or type(self.automatic_backup_enabled) is not bool:
            raise ValueError("Test/backup flags must be explicit booleans")
        if self.local_test_mode and not (self.development and endpoint.address.is_loopback and validate_internal_address(self.database.host).is_loopback):
            raise ValueError("Local acceptance mode requires an explicit loopback PostgreSQL database and listener")
        if type(self.backup_interval_hours) is not int or not 1 <= self.backup_interval_hours <= 720:
            raise ValueError("Backup interval must be 1..720 hours")
        if type(self.backup_retention_count) is not int or not 1 <= self.backup_retention_count <= 365:
            raise ValueError("Backup retention must be 1..365 automatic snapshots")
        if endpoint.scheme != "https" and not self.development and not self.allow_insecure_http:
            raise ValueError("HTTPS is required outside explicit development mode")
        if self.database.development != self.development:
            raise ValueError("Application/database development modes must match")
        for name in ("postgresql_dump_executable", "postgresql_restore_executable"):
            value = getattr(self, name)
            if value is not None:
                path = validate_file_path(value)
                if path.startswith("\\\\") or not Path(path).is_file():
                    raise ValueError("PostgreSQL backup tools must be explicit existing local executable files")
                object.__setattr__(self, name, path)
        if self.automatic_backup_enabled and any(getattr(self, name) is None for name in ("postgresql_dump_executable", "postgresql_restore_executable")):
            raise ValueError("Automatic PostgreSQL backups require explicit pg_dump and pg_restore executable paths")
        if (self.postgresql_dump_executable is None) != (self.postgresql_restore_executable is None):
            raise ValueError("PostgreSQL backup configuration requires both pg_dump and pg_restore paths together")
        for name in ("frontend_directory", "seed_directory", "backup_directory"):
            if name == "frontend_directory" and getattr(self, name) is None and not self.serve_frontend:
                continue
            path = validate_file_path(getattr(self, name))
            if path.startswith("\\\\"):
                raise ValueError("Software assets and application backup directories must be local")
            object.__setattr__(self, name, path)
        if not isinstance(self.company_name, str) or not 1 <= len(self.company_name) <= 200 or "\x00" in self.company_name:
            raise ValueError("Invalid company name")
        object.__setattr__(self, "listen_url", endpoint.url)

    @property
    def request_policy(self):
        # No Host wildcard or DNS lookup; the validated numeric listener is trusted.
        from urllib.parse import urlsplit
        authority = urlsplit(self.listen_url).netloc.lower()
        endpoint = validate_listener_url(self.listen_url)
        authorities = {authority}
        if endpoint.port == (443 if endpoint.scheme == "https" else 80):
            authorities.add(f"[{endpoint.address}]" if endpoint.address.version == 6 else str(endpoint.address))
        return RequestPolicy(frozenset(authorities), development=self.development,
                             allow_insecure_http=self.allow_insecure_http)

    @classmethod
    def load(cls, path):
        path = validate_file_path(str(path))
        if path.startswith("\\\\"):
            raise ValueError("Configuration must be a local file")
        # Bound the read itself, rather than loading an arbitrary-size local file
        # before checking the result.
        with Path(path).open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("Configuration exceeds its size limit")
        data = tomllib.loads(raw.decode("utf-8-sig"))
        allowed = {"database", "listen_url", "frontend_directory", "seed_directory", "backup_directory", "development", "scheduling_enabled", "company_name", "local_test_mode", "automatic_backup_enabled", "backup_interval_hours", "backup_retention_count", "postgresql_dump_executable", "postgresql_restore_executable", "serve_frontend", "reverse_proxy", "allow_insecure_http"}
        if set(data) - allowed:
            raise ValueError("Unknown software configuration option")
        database = data.pop("database", None)
        if not isinstance(database, dict):
            raise ValueError("An explicit database table is required")
        if data.get("serve_frontend") is False:
            data.setdefault("frontend_directory", None)
        return cls(database=DatabaseConfig(**database), **data)
