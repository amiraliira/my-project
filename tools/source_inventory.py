"""Shared public-source inventory for packaging and verification; standard library."""
import os
from pathlib import Path
import stat

EXCLUDED_DIRECTORIES = {
    '.git', '.venv', 'venv', 'node_modules', '__pycache__', '.build',
    'wheelhouse', 'test-data', 'test-payload', 'backups', 'seed', 'pm-originals',
    'runtime', 'postgres', 'qa-deps', 'pak-data', 'logs',
}
EXCLUDED_SUFFIXES = {
    '.pyc', '.tsbuildinfo', '.dump', '.backup', '.bak', '.key', '.pfx', '.pgpass',
    '.pem', '.crt', '.cer', '.log', '.pid', '.zip', '.db', '.sqlite', '.sqlite3',
}
PUBLIC_SUFFIXES = {
    '.py', '.sql', '.json', '.toml', '.md', '.txt', '.ts', '.tsx', '.css',
    '.mjs', '.cjs', '.js', '.yaml', '.yml', '.sh', '.html', '.svg', '.png',
    '.ico', '.webmanifest', '.cs', '.lock', '.conf', '.cmd', '.woff2',
}
PUBLIC_DOTFILES = {'.gitignore', '.editorconfig', '.gitattributes', '.env.example'}


def required_contract_files(release):
    """Files without which the declared storage/client contract cannot ship."""
    required = set()
    for name in release.get('explicitUpgrades', ()):
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name or not name.endswith('.sql'):
            raise ValueError('Invalid migration filename in RELEASE.json')
        required.add('backend/schema/' + name)
    if release['coreSchemaVersion'] >= 11:
        required.update({
            'backend/schema/postgresql-v11.sql', 'backend/schema/sqlite-v11.sql',
            'backend/schema/postgresql-pm-routing-v11.sql', 'backend/schema/sqlite-pm-routing-v11.sql',
            'backend/schema/postgresql-upgrade-10-to-11.sql',
            'backend/src/pak_maintenance/pm_routing_contract.py',
            'backend/src/pak_maintenance/pm_routing_migration.py',
            'backend/tools/upgrade_pm_routing.py', 'backend/tools/check_database.py',
            'frontend/src/PmSupervisorSelect.tsx',
        })
    if release['coreSchemaVersion'] >= 12:
        required.update({
            'backend/schema/postgresql-v12.sql', 'backend/schema/sqlite-v12.sql',
            'backend/schema/postgresql-upgrade-11-to-12.sql',
            'backend/src/pak_maintenance/work_categories.py',
            'backend/src/pak_maintenance/work_category_contract.py',
            'backend/src/pak_maintenance/work_category_migration.py',
            'backend/src/pak_maintenance/activity_reporting.py',
            'backend/tools/upgrade_work_categories.py',
            'frontend/src/ActivityReport.tsx',
        })
    if 'personnel-activity-drilldown' in release.get('requiredCapabilities', ()):
        required.update({'backend/src/pak_maintenance/performance_service.py',
                         'frontend/src/PersonnelActivity.tsx', 'frontend/src/personnel-activity.css'})
    if 'pm-routing-review' in release.get('requiredCapabilities', ()):
        required.update({'backend/src/pak_maintenance/pm_responsibility.py',
                         'frontend/src/PmRoutingReview.tsx', 'frontend/src/pm-routing-review.css'})
    return required


def canonical_local_path(path):
    path = Path(path)
    try:
        return path.resolve(strict=True)
    except PermissionError as error:
        if os.name != 'nt' or getattr(error, 'winerror', None) != 5:
            raise
        absolute = Path(os.path.abspath(path))
        for component in reversed((absolute, *absolute.parents)):
            metadata = component.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_file_attributes & 0x400:
                raise ValueError('Source paths must not contain symbolic links or reparse points') from error
        return absolute


def excluded_file(name):
    lower = name.lower()
    if name == '.env.example':
        return False
    return (lower in {'source-manifest.json', 'server.toml', 'credentials.json',
                     'local-state.json', 'bootstrap.sql', 'acceptance-data.json',
                     'pm-import.json', 'backup.sql', 'dump.sql'}
            or lower == '.env' or lower.startswith('.env.') or lower.endswith('.env')
            or lower.startswith('owner') and lower.endswith('.toml')
            or Path(lower).suffix in EXCLUDED_SUFFIXES)


def source_files(root):
    """Yield public files only; do not follow links or expose runtime data."""
    root = canonical_local_path(root)
    seen = set()
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(current)
        selected = []
        for name in sorted(directories):
            if name.casefold() in EXCLUDED_DIRECTORIES or name.startswith('.') and name != '.github':
                continue
            path = directory / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('Source directory link rejected: ' + path.relative_to(root).as_posix())
            selected.append(name)
        directories[:] = selected
        for name in sorted(filenames):
            if excluded_file(name) or name.startswith('.') and name not in PUBLIC_DOTFILES:
                continue
            path = directory / name
            relative = path.relative_to(root).as_posix()
            allowed = name in PUBLIC_DOTFILES or path.suffix.lower() in PUBLIC_SUFFIXES
            allowed |= relative == 'backend/src/pak_maintenance/DejaVuSans.ttf'
            allowed |= path.suffix == '.gz' and relative.startswith('frontend/dist/assets/')
            if not allowed:
                continue
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('Source file link rejected: ' + relative)
            if not canonical_local_path(path).is_relative_to(root):
                raise ValueError('Source escaped the project: ' + relative)
            if relative.casefold() in seen:
                raise ValueError('Case-insensitive source path collision: ' + relative)
            seen.add(relative.casefold())
            yield relative, path
