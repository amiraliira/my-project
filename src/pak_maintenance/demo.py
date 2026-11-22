"""Compatibility entry point for the current PostgreSQL browser launcher.

The retired file-database demo and terminal password prompt are intentionally
removed. Older explicit ``python -m pak_maintenance.demo`` invocations use the
same approved interface, validated PostgreSQL payload and browser setup as the
current one-click presentation launcher.
"""
from .local_test import main


if __name__ == "__main__":
    raise SystemExit(main())
