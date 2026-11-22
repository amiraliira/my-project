"""Compatibility imports for the conventional ``app.main:app`` entry point.

The installed domain package owns all configuration parsing and validation.
Existing integrations importing app.settings keep the same public functions.
"""
from pak_maintenance.settings import (
    AppConfig, BACKEND_DIRECTORY, ConfigurationSource, DatabaseConfig, ENV_NAMES,
    MAX_ENV_BYTES, Path, SettingsError, configuration_source, load_config,
    read_env_file, validate_file_path,
)

__all__ = (
    "AppConfig", "BACKEND_DIRECTORY", "ConfigurationSource", "DatabaseConfig",
    "ENV_NAMES", "MAX_ENV_BYTES", "Path", "SettingsError", "configuration_source",
    "load_config", "read_env_file", "validate_file_path",
)
