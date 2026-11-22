"""Explicit settings for manual Uvicorn and a protected local .env file.

Values are literal: no shell commands, interpolation, network lookup or secret
printing. Process environment values take precedence over the selected file.
"""

from pathlib import Path
from dataclasses import dataclass
import os
import re

from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pak_maintenance.network_policy import validate_file_path

BACKEND_DIRECTORY = Path(__file__).absolute().parents[2]
MAX_ENV_BYTES = 64 * 1024
ENV_NAMES = frozenset({
    "PAK_PUBLIC_URL", "PAK_ALLOW_INSECURE_HTTP", "PAK_DB_HOST", "PAK_DB_PORT", "PAK_DB_NAME",
    "PAK_DB_USER", "PAK_DB_PASSWORD", "PAK_DB_SSLMODE", "PAK_DB_SSLROOTCERT",
    "PAK_DB_ALLOW_LOOPBACK_PLAINTEXT", "PAK_DB_ALLOW_LAN_PLAINTEXT", "PAK_DB_CONNECT_TIMEOUT",
    "PAK_PM_SEED_DIR", "PAK_BACKUP_DIR", "PAK_DEVELOPMENT",
    "PAK_SCHEDULING_ENABLED", "PAK_AUTOMATIC_BACKUP_ENABLED",
    "PAK_BACKUP_INTERVAL_HOURS", "PAK_BACKUP_RETENTION_COUNT",
    "PAK_POSTGRESQL_DUMP_EXECUTABLE", "PAK_POSTGRESQL_RESTORE_EXECUTABLE",
    "PAK_COMPANY_NAME",
})
_PLACEHOLDERS = frozenset({"change_me", "change_me_database_name", "change_me_password"})


class SettingsError(ValueError):
    """A configuration error whose message contains names/line numbers only."""


@dataclass(frozen=True, slots=True)
class ConfigurationSource:
    """Safe provenance; setting values and credentials are deliberately absent."""

    path: str
    explicit: bool
    file_exists: bool
    environment_settings: tuple[str, ...]


def _configuration_location(environ, backend_directory):
    environment = dict(os.environ if environ is None else environ)
    directory = Path(backend_directory) if backend_directory is not None else BACKEND_DIRECTORY
    selected = environment.get("PAK_ENV_FILE")
    return environment, Path(selected) if selected else directory / ".env", bool(selected)


def configuration_source(environ=None, *, backend_directory=None):
    """Describe selection and override names without reading or printing values.

    The path is validated before display, so control characters cannot inject
    extra log lines. No directory creation, shell expansion or network access.
    """
    environment, path, explicit = _configuration_location(environ, backend_directory)
    try:
        checked = validate_file_path(str(path))
    except ValueError:
        raise SettingsError("PAK_ENV_FILE must select an absolute local file path") from None
    if checked.startswith("\\\\"):
        raise SettingsError("PAK_ENV_FILE must select a local file")
    return ConfigurationSource(checked, explicit, Path(checked).is_file(),
        tuple(sorted(name for name in environment if name in ENV_NAMES)))


def read_env_file(path):
    """Read a bounded local file with full-line comments and literal quoting.

    Quotes do not process backslashes or dollar signs. Inline comments and
    shell export statements are unsupported; password punctuation stays literal.
    Errors contain line numbers only, never input values.
    """
    try:
        target = Path(validate_file_path(str(path)))
    except ValueError:
        raise SettingsError("PAK_ENV_FILE must select an absolute local file path") from None
    if str(target).startswith("\\\\"):
        raise SettingsError("PAK_ENV_FILE must be a local file")
    with target.open("rb") as stream:
        raw = stream.read(MAX_ENV_BYTES + 1)
    if len(raw) > MAX_ENV_BYTES:
        raise SettingsError("PAK environment file exceeds 64 KiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SettingsError("PAK environment file must be UTF-8") from None
    values = {}
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in ENV_NAMES:
            raise SettingsError(f"Invalid PAK environment setting at line {number}")
        if name in values:
            raise SettingsError(f"Duplicate PAK environment setting at line {number}")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise SettingsError(f"Unclosed PAK environment value at line {number}")
            value = value[1:-1]
        if "\x00" in value or any(ord(c) < 32 for c in value):
            raise SettingsError(f"Invalid PAK environment value at line {number}")
        values[name] = value
    return values


def _boolean(values, name, default=False):
    value = values.get(name, "true" if default else "false")
    if value.lower() not in {"true", "false"}:
        raise SettingsError(f"{name} must be true or false")
    return value.lower() == "true"


def _integer(values, name, default):
    value = values.get(name, str(default))
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise SettingsError(f"{name} must be an integer")
    return int(value)


def _required(values, name):
    value = values.get(name)
    if not isinstance(value, str) or not value or value.lower() in _PLACEHOLDERS:
        raise SettingsError(f"Set {name} in the selected configuration file or process environment")
    return value


def _runtime_directory(values, name):
    """Expand only a leading current-user ~ for the two runtime directories.

    No shell evaluation, environment interpolation or password expansion occurs.
    AppConfig still validates the resulting absolute local path.
    """
    value = _required(values, name)
    if value.startswith("~"):
        if value != "~" and not value.startswith(("~/", "~\\")):
            raise SettingsError(f"{name} supports ~ only for the current process user")
        try:
            return str(Path(value).expanduser())
        except (RuntimeError, KeyError):
            raise SettingsError(f"Cannot determine the current user's home for {name}") from None
    return value


def load_config(environ=None, *, backend_directory=None):
    """Validate settings without opening a database.

    A missing default backend/.env is allowed when process environment supplies
    all required values. An explicit PAK_ENV_FILE must exist.
    """
    environment, env_path, explicit = _configuration_location(environ, backend_directory)
    values = read_env_file(env_path) if explicit or env_path.is_file() else {}
    values.update({name: value for name, value in environment.items() if name in ENV_NAMES})
    development = _boolean(values, "PAK_DEVELOPMENT")
    database = DatabaseConfig(
        provider="postgresql",
        host=values.get("PAK_DB_HOST", "127.0.0.1"),
        port=_integer(values, "PAK_DB_PORT", 5432),
        database=_required(values, "PAK_DB_NAME"),
        user=_required(values, "PAK_DB_USER"),
        password=_required(values, "PAK_DB_PASSWORD"),
        sslmode=values.get("PAK_DB_SSLMODE", "verify-full"),
        sslrootcert=values.get("PAK_DB_SSLROOTCERT") or None,
        development=development,
        allow_loopback_plaintext=_boolean(values, "PAK_DB_ALLOW_LOOPBACK_PLAINTEXT"),
        allow_lan_plaintext=_boolean(values, "PAK_DB_ALLOW_LAN_PLAINTEXT"),
        connect_timeout=_integer(values, "PAK_DB_CONNECT_TIMEOUT", 5),
    )
    return AppConfig(
        database=database,
        listen_url=values.get("PAK_PUBLIC_URL", "https://192.168.12.92"),
        frontend_directory=None,
        serve_frontend=False,
        reverse_proxy=True,
        allow_insecure_http=_boolean(values, "PAK_ALLOW_INSECURE_HTTP"),
        seed_directory=_runtime_directory(values, "PAK_PM_SEED_DIR"),
        backup_directory=_runtime_directory(values, "PAK_BACKUP_DIR"),
        development=development,
        scheduling_enabled=_boolean(values, "PAK_SCHEDULING_ENABLED"),
        automatic_backup_enabled=_boolean(values, "PAK_AUTOMATIC_BACKUP_ENABLED"),
        backup_interval_hours=_integer(values, "PAK_BACKUP_INTERVAL_HOURS", 24),
        backup_retention_count=_integer(values, "PAK_BACKUP_RETENTION_COUNT", 14),
        postgresql_dump_executable=values.get("PAK_POSTGRESQL_DUMP_EXECUTABLE") or None,
        postgresql_restore_executable=values.get("PAK_POSTGRESQL_RESTORE_EXECUTABLE") or None,
        company_name=values.get("PAK_COMPANY_NAME", "شرکت لبنیات پاک"),
    )
