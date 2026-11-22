"""Explicit PostgreSQL core schema 8 -> 9 with a verified native backup first.

Stop every application writer. Run with the protected owner TOML and the
separate existing limited runtime role. The ordinary Uvicorn launcher never
invokes this tool. Passwords remain inside the local protected configuration.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from pak_maintenance.backup import BackupService
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import Database
from pak_maintenance.core_schema_migration import preflight_postgresql_v8, upgrade_postgresql_v8


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-config', required=True, help='Protected local owner TOML with native pg_dump/pg_restore paths')
    parser.add_argument('--runtime-user', required=True, help='Existing limited application role, normally pak')
    args = parser.parse_args(argv)
    config = AppConfig.load(args.owner_config)
    if not config.postgresql_dump_executable or not config.postgresql_restore_executable:
        raise ValueError('Explicit native backup tools are required before the core upgrade')
    database = Database(config.database)
    before = preflight_postgresql_v8(database, runtime_user=args.runtime_user)
    snapshot = BackupService(database, config.backup_directory,
        dump_executable=config.postgresql_dump_executable,
        restore_executable=config.postgresql_restore_executable).create_snapshot()
    result = upgrade_postgresql_v8(database, runtime_user=args.runtime_user)
    print(json.dumps(dict(preflight=before, upgrade=result,
        backup=dict(file=snapshot.path.name, bytes=snapshot.size, sha256=snapshot.sha256),
        applicationDataReset=False), ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
