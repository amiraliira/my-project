"""Enable optional OEE module on core schema 8, after a native backup.

Explicit maintenance command only. Stop application writers first. Uses a
protected local schema-owner TOML and a separate limited runtime account.
No source PDF import, existing data reset, password change or PM activation.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tools'))
from pak_maintenance.backup import BackupService
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import Database
from pak_maintenance.oee_migration import enable_oee


def enable_local(root):
    from pak_maintenance.local_test import checked_child,digest,instance_lock,prepare_copy,test_config
    from pak_maintenance.local_postgres import LocalPostgres
    from pak_maintenance.network_policy import validate_file_path
    from verify_package import verify
    root=Path(validate_file_path(str(root))).resolve(strict=True)
    verify(root)
    directory=root/'test-data'
    with instance_lock(directory) as owned:
        if not owned:
            raise ValueError('Close the running application window before enabling OEE')
        manifest=prepare_copy(root,directory)
        postgres=LocalPostgres(root/'postgres',directory/'postgresql',checked_child(root/'test-payload',manifest['databaseFile']),payload_sha256=digest(root/'test-payload/acceptance-data.json'))
        with postgres.running() as runtime:
            config=test_config(root,directory,5187,runtime)
            owner=Database(postgres.database_config(role='owner'))
            snapshot=BackupService(owner,config.backup_directory,dump_executable=config.postgresql_dump_executable,
                restore_executable=config.postgresql_restore_executable).create_snapshot()
            result=enable_oee(owner,runtime_user=runtime.user)
            return dict(upgrade=result,backup=dict(file=snapshot.path.name,bytes=snapshot.size,sha256=snapshot.sha256),passwordsChanged=False,pmSchedulingChanged=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--owner-config')
    choice.add_argument('--local-root')
    parser.add_argument('--runtime-user')
    args=parser.parse_args()
    if args.local_root:
        if args.runtime_user:
            parser.error('--runtime-user is only for an owner configuration')
        print(json.dumps(enable_local(args.local_root),ensure_ascii=True))
        return
    if not args.runtime_user:
        parser.error('--runtime-user is required with --owner-config')
    config=AppConfig.load(args.owner_config)
    database=Database(config.database)
    database.verify_schema(runtime_role=False)
    snapshot=BackupService(database,config.backup_directory,
        dump_executable=config.postgresql_dump_executable,
        restore_executable=config.postgresql_restore_executable).create_snapshot()
    result=enable_oee(database,runtime_user=args.runtime_user)
    print(json.dumps(dict(upgrade=result,backup=dict(file=snapshot.path.name,bytes=snapshot.size,sha256=snapshot.sha256)),ensure_ascii=True))


if __name__=='__main__':
    main()
