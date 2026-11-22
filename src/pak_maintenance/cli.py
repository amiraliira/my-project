"""Explicit local software launcher; no installation or host configuration."""
import argparse
from pathlib import Path
from .application import create_app
from .configuration import AppConfig
from .database import DatabaseDependencyError
from .network_policy import validate_file_path, validate_listener_url


def _certificate_path(value):
    path = validate_file_path(value)
    if path.startswith("\\\\") or not Path(path).is_file():
        raise ValueError("TLS certificate and key must be existing local files")
    return path


def launch(config, *, certificate=None, private_key=None):
    """Start only the caller-selected internal endpoint, never a wildcard.

    Explicit arguments override Uvicorn's proxy-trusting defaults. No proxy,
    DNS lookup, reload worker, telemetry, migration or automatic account seed
    is configured. Supplied HTTPS files stay local and are not downloaded.
    """
    if not isinstance(config, AppConfig):
        raise ValueError("A validated explicit application configuration is required")
    endpoint = validate_listener_url(config.listen_url)
    options = {}
    if endpoint.scheme == "https":
        if not certificate or not private_key:
            raise ValueError("HTTPS requires an explicit local certificate and private key")
        options.update(ssl_certfile=_certificate_path(certificate), ssl_keyfile=_certificate_path(private_key))
    elif certificate is not None or private_key is not None:
        raise ValueError("TLS files cannot be used with an HTTP listener")
    try:
        import uvicorn
    except ImportError:
        raise DatabaseDependencyError("The approved offline Uvicorn dependency is unavailable; nothing has been installed or started.") from None
    app = create_app(config)
    uvicorn.run(app, host=str(endpoint.address), port=endpoint.port,
        proxy_headers=False, forwarded_allow_ips="", reload=False, workers=1,
        loop="asyncio", http="h11", ws="none", lifespan="on",
        limit_concurrency=128, backlog=128, timeout_keep_alive=5,
        timeout_graceful_shutdown=30, server_header=False, access_log=False,
        log_level="warning", **options)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the explicit local PAK maintenance application configuration")
    parser.add_argument("--config", required=True, help="Absolute local TOML software configuration")
    parser.add_argument("--certificate", help="Absolute local HTTPS certificate file")
    parser.add_argument("--private-key", help="Absolute local HTTPS private key file")
    args = parser.parse_args(argv)
    try:
        launch(AppConfig.load(args.config), certificate=args.certificate, private_key=args.private_key)
    except (ValueError, OSError, DatabaseDependencyError) as error:
        # Do not print paths, credentials, connection strings or DB errors.
        parser.exit(1, f"Software startup rejected ({type(error).__name__}); check the local configuration and offline dependencies.\n")
    return 0
