"""Offline, side-by-side releases. Never migrates or restores a database.

Protocol 1 deliberately accepts only core schema 8 and optional OEE schema 1.
IT stops all writers before apply/rollback. The managed launcher and updater
hold the same OS lock, including for the lifetime of the child application.
"""
import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
from uuid import uuid4

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'src'))
sys.path.insert(0, str(PACKAGE / 'tools'))
from verify_package import verify

VERSION = re.compile(r'([0-9]+)\.([0-9]+)\.([0-9]+)(?:-rc\.([0-9]+))?\Z')
COMPATIBILITY = dict(protocol=1, coreSchema=8, optionalOeeSchema=1, databaseMigration=False)


def version_key(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError('Invalid release version')
    return (*map(int, match.groups()[:3]), int(match[4]) if match[4] else 1_000_000)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def local(path):
    path = Path(os.path.abspath(path))
    if str(path).startswith('\\\\'):
        raise ValueError('A local installation path is required')
    for component in (path, *path.parents):
        if component.exists() and (component.is_symlink() or component.is_junction()):
            raise ValueError('Links and reparse paths are prohibited')
    return path


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Metadata size exceeds limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate metadata key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique)


def atomic_json(path, data):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def installation_lock(root):
    root = local(root)
    root.mkdir(parents=True, exist_ok=True)
    path = local(root / '.operation.lock')
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def package_info(source):
    source = local(source)
    verify(source)
    manifest = read_json(source / 'package-manifest.json')
    version_key(manifest['version'])
    if not {'release-policy.json', 'tools/release_manager.py'} <= manifest['files'].keys():
        raise ValueError('Release protocol files must be covered by the package manifest')
    if manifest.get('localAcceptanceOnly') or manifest.get('containsCompanyData') is not False:
        raise ValueError('Only the data-free IT package can be installed here')
    if read_json(source / 'release-policy.json') != COMPATIBILITY:
        raise ValueError('This release requires a separately reviewed migration protocol')
    return manifest


def selected(root):
    state = read_json(local(root / 'current.json'))
    version_key(state['current'])
    version_key(state['controller'])
    if state.get('previous') is not None:
        version_key(state['previous'])
    if state.get('protocol') != 1:
        raise ValueError('Unsupported installation protocol')
    return state


def configuration(root, release):
    from pak_maintenance.configuration import AppConfig
    config = AppConfig.load(local(root / 'shared/server.toml'))
    # Shared operational data cannot reside inside an immutable release.
    for name, folder in (('seed_directory', 'seed'), ('backup_directory', 'backups')):
        value = local(getattr(config, name))
        if value != local(root / 'shared' / folder):
            raise ValueError('Operational directories must stay under shared')
    if config.development or config.local_test_mode:
        raise ValueError('Managed IT installations require HTTPS production configuration')
    return replace(config, frontend_directory=str(local(release / 'frontend')))


def preflight(root, release, *, stopped):
    from pak_maintenance.database import Database
    from pak_maintenance.network_policy import validate_listener_url
    config = configuration(root, release)
    database = Database(config.database)
    database.verify_schema()
    with database.connect() as connection:
        oee = connection.scalar("SELECT to_regclass('public.oee_module') IS NOT NULL")
        if oee and connection.query('SELECT version FROM oee_module') != [dict(version=1)]:
            raise ValueError('Unsupported OEE schema')
        if oee:
            tables={row['table_name'] for row in connection.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('oee_module','oee_lines','oee_sheets','oee_sheet_revisions')")}
            guards={row['tgname'] for row in connection.query("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled IN ('O','A') AND tgrelid IN ('public.oee_sheets'::regclass,'public.oee_sheet_revisions'::regclass)")}
            if len(tables) != 4 or not {'oee_revisions_immutable','oee_sheets_no_delete'} <= guards:
                raise ValueError('OEE tables or immutable evidence guards are incomplete')
        if stopped and connection.scalar("SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND application_name LIKE 'PakMaintenance%'"):
            raise ValueError('Application database sessions are still running')
    if stopped:
        endpoint = validate_listener_url(config.listen_url)
        with socket.socket(socket.AF_INET6 if endpoint.address.version == 6 else socket.AF_INET) as probe:
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            # A local bind without listen proves both interface ownership and
            # availability. A refused/blocked remote connect would prove neither.
            probe.bind((str(endpoint.address), endpoint.port))
    for name in ('server.crt', 'server.key'):
        if not local(root / 'shared/certificates' / name).is_file():
            raise ValueError('IT-managed HTTPS certificate and key are required')
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(root / 'shared/certificates/server.crt'), str(root / 'shared/certificates/server.key'))
    if not (Path(config.seed_directory) / 'pm-import.json').is_file() or not (Path(config.seed_directory) / 'pm-originals').is_dir():
        raise ValueError('Transferred original PM files are required')
    return config, database


def backup_before_switch(root, release):
    from pak_maintenance.backup import BackupService
    config, database = preflight(root, release, stopped=True)
    snapshot = BackupService(database, config.backup_directory,
        dump_executable=config.postgresql_dump_executable,
        restore_executable=config.postgresql_restore_executable).create_snapshot()
    # Keep one exact shared-file snapshot per successful preparation. It contains
    # credentials: shared and history must have the same restricted IT ACL.
    history = local(root / 'history' / uuid4().hex)
    history.mkdir(parents=True)
    shared_hashes = {}
    for path in shared_files(root / 'shared'):
        relative = path.relative_to(root / 'shared')
        target = history / 'shared' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied_hash = digest(target)
        if digest(path) != copied_hash:
            raise ValueError('Shared files changed during maintenance backup')
        shared_hashes[target.relative_to(history).as_posix()] = copied_hash
    facts = dict(databaseFile=snapshot.path.name, databaseBytes=snapshot.size,
        databaseSha256=snapshot.sha256, sharedFiles=shared_hashes, databaseMigration=False)
    atomic_json(history / 'backup.json', facts)
    return history.name


def shared_files(directory):
    """Prune backup history BEFORE traversal, even with years of backups."""
    directory = local(directory)
    def failed(error):
        raise error
    for base, folders, files in os.walk(directory, topdown=True, followlinks=False, onerror=failed):
        base = local(base)
        if base == directory:
            folders[:] = [name for name in folders if base / name != directory / 'backups']
        for name in folders:
            local(base / name)  # Reject links rather than silently omit originals.
        for name in files:
            path = local(base / name)
            if not path.is_file():
                raise ValueError('Shared backup requires regular local files')
            yield path


def prepare(root, source):
    root, source = local(root), local(source)
    manifest = package_info(source)
    version = manifest['version']
    releases = local(root / 'releases')
    releases.mkdir(parents=True, exist_ok=True)
    target = local(releases / version)
    if target.exists():
        if digest(target / 'package-manifest.json') != digest(source / 'package-manifest.json'):
            raise ValueError('A release version must never be reused for different files')
        package_info(target)
        return target
    required = sum(item['bytes'] for item in manifest['files'].values())
    if shutil.disk_usage(root).free < required + 256 * 1024 * 1024:
        raise ValueError('Insufficient space for side-by-side release')
    pending = local(releases / ('.p-' + uuid4().hex[:8]))
    if os.name == 'nt' and any(len(str(pending / name)) >= 248 for name in manifest['files']):
        raise ValueError('Choose a short local installation root such as C:\\PAK')
    pending.mkdir()
    # Copy only manifest-owned files; never server.toml, credentials or unknowns.
    for name in (*manifest['files'], 'package-manifest.json'):
        incoming = local(source / name)
        destination = pending / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(incoming, destination)
    package_info(pending)
    os.replace(pending, target)
    return target


def bootstrap(root, release, *, controller=None):
    shared = local(root / 'shared')
    for name in ('seed', 'backups', 'certificates'):
        (shared / name).mkdir(parents=True, exist_ok=True)
    template = shared / 'server.example.toml'
    if not template.exists():
        shutil.copy2(release / 'server.example.toml', template)
    # First release stays pinned as a small, stable controller. No symlinks,
    # elevated installation, service registration or PowerShell policy changes.
    launcher = root / 'Start-Pak.cmd'
    version = controller or release.name
    version_key(version)
    if not local(root / 'releases' / version).is_dir():
        raise ValueError('The pinned launcher release must remain installed')
    expected = ('@echo off\r\nsetlocal\r\n'
            f'"%~dp0releases\\{version}\\runtime\\python.exe" -B "%~dp0releases\\{version}\\tools\\release_manager.py" run --root "%~dp0."\r\n'
            'if errorlevel 1 pause\r\nendlocal\r\n').encode('utf-8')
    if not launcher.exists() or launcher.read_bytes() != expected:
        temporary = local(root / ('Start-Pak.' + uuid4().hex + '.tmp'))
        with temporary.open('xb') as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, launcher)


def apply(root, source):
    root = local(root)
    with installation_lock(root):
        incoming = package_info(source)
        old = selected(root) if (root / 'current.json').exists() else None
        if old and version_key(incoming['version']) <= version_key(old['current']):
            raise ValueError('Apply requires a newer version; rollback is a separate operation')
        release = prepare(root, source)
        bootstrap(root, release, controller=old['controller'] if old else release.name)
        if not (root / 'shared/server.toml').exists():
            return dict(status='configuration-required', staged=release.name, activated=False)
        # Candidate's own Python and code verify the database and create backup.
        # Failure leaves the single atomic selection file untouched.
        result = subprocess.run([str(release / 'runtime/python.exe'), '-B',
            str(release / 'tools/release_manager.py'), 'snapshot', '--root', str(root)],
            capture_output=True, text=True, timeout=900, check=False)
        if result.returncode:
            raise ValueError('Candidate preflight/backup failed; current release was not changed')
        history = json.loads(result.stdout)['history']
        state = dict(protocol=1, current=release.name, previous=old['current'] if old else None,
            controller=old['controller'] if old else release.name,
            changedUtc=datetime.now(timezone.utc).isoformat(), history=history)
        atomic_json(root / 'current.json', state)
        return dict(status='activated', version=release.name, history=history,
                    applicationStarted=False, databaseMigration=False)


def rollback(root):
    root = local(root)
    with installation_lock(root):
        state = selected(root)
        version_key(state.get('previous'))
        target = local(root / 'releases' / state['previous'])
        package_info(target)
        history = backup_before_switch(root, target)
        state.update(current=state['previous'], previous=None, history=history,
                     changedUtc=datetime.now(timezone.utc).isoformat())
        atomic_json(root / 'current.json', state)
        return dict(status='rolled-back', version=target.name, databaseRestored=False,
                    databaseMigration=False, applicationStarted=False)


def run(root):
    root = local(root)
    with installation_lock(root):
        state = selected(root)
        release = local(root / 'releases' / state['current'])
        package_info(release)
        # Parent holds the OS lock until the real child exits. No shell parsing.
        return subprocess.call([str(release / 'runtime/python.exe'), '-B',
            str(release / 'tools/release_manager.py'), 'serve', '--root', str(root)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['apply', 'rollback', 'run', 'serve', 'snapshot', 'status'])
    parser.add_argument('--root')
    parser.add_argument('--source', default=str(PACKAGE))
    parser.add_argument('--maintenance-confirmed', action='store_true')
    args = parser.parse_args(argv)
    try:
        destination = args.root or input('Installation root (for example C:\\PAK): ').strip()
        if not destination:
            raise ValueError('Installation root cannot be empty')
        root = local(destination)
        if args.operation in ('apply', 'rollback') and not args.maintenance_confirmed:
            args.maintenance_confirmed = input('Stop ALL application writers first. Type STOPPED to confirm: ').strip() == 'STOPPED'
        if args.operation in ('apply', 'rollback') and not args.maintenance_confirmed:
            raise ValueError('IT must stop all writers and explicitly confirm the maintenance window')
        if args.operation == 'apply':
            report = apply(root, args.source)
        elif args.operation == 'rollback':
            report = rollback(root)
        elif args.operation == 'run':
            return run(root)
        elif args.operation == 'snapshot':
            report = dict(history=backup_before_switch(root, PACKAGE))
        elif args.operation == 'status':
            state = selected(root)
            release = local(root / 'releases' / state['current'])
            package_info(release)
            preflight(root, release, stopped=False)
            report = dict(status='verified', version=state['current'], previous=state['previous'],
                          coreSchema=8, productionAccepted=False)
        else:
            if selected(root)['current'] != PACKAGE.name:
                raise ValueError('Launch only the selected release')
            config, _ = preflight(root, PACKAGE, stopped=False)
            from pak_maintenance.cli import launch
            launch(config, certificate=str(root / 'shared/certificates/server.crt'),
                   private_key=str(root / 'shared/certificates/server.key'))
            return 0
        print(json.dumps(report))
        return 0
    except Exception as error:
        # No exception message: libpq and filesystem errors may contain secrets.
        print(f'FAILED ({type(error).__name__}). Check status/current.json before retrying; see IT runbook. No automatic database restore.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
