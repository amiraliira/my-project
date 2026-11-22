"""Explicit PostgreSQL 7 -> 8 maintenance, with a verified native backup first.

Run while application writers are stopped, using the separate schema owner.
The ordinary launcher never invokes this tool or changes the live schema.
Passwords are read only from the local protected TOML configuration.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pak_maintenance.backup import BackupService
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import Database
from pak_maintenance.pm_archive_migration import upgrade_postgresql_v7


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-config", required=True, help="Protected local TOML with schema-owner connection and native backup tools")
    parser.add_argument("--runtime-user", required=True, help="Existing limited application database role")
    args = parser.parse_args(argv)
    config = AppConfig.load(args.owner_config)
    if not config.postgresql_dump_executable or not config.postgresql_restore_executable:
        raise ValueError("Explicit native backup tools are required before this upgrade")
    database = Database(config.database)
    snapshot = BackupService(database, config.backup_directory,
        dump_executable=config.postgresql_dump_executable,
        restore_executable=config.postgresql_restore_executable).create_snapshot()
    result = upgrade_postgresql_v7(database, runtime_user=args.runtime_user)
    report = dict(upgrade=result, backup=dict(file=snapshot.path.name, bytes=snapshot.size, sha256=snapshot.sha256),
                  applicationDataReset=False)
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
