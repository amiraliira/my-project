"""Package the current release from public source; never installs or deploys."""
import argparse
import ast
from datetime import date
import hashlib
import json
import re
from pathlib import Path
import sys
import tomllib
import zipfile

HERE = Path(__file__).absolute().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from source_inventory import canonical_local_path, source_files, required_contract_files

ROOT = HERE.parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def release_metadata(root):
    release = json.loads((root / 'RELEASE.json').read_text(encoding='utf-8-sig'))
    backend = tomllib.loads((root / 'backend/pyproject.toml').read_text(encoding='utf-8-sig'))
    frontend = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8-sig'))
    version = release['applicationVersion']
    if frontend['version'] != version or backend['project']['version'] != version.replace('-rc.', 'rc'):
        raise ValueError('Release/backend/frontend versions do not match')
    if type(release['packageRevision']) is not int or release['packageRevision'] < 1:
        raise ValueError('Invalid release package revision')
    if release.get('startupMigrates') is not False:
        raise ValueError('Manual release must explicitly disable startup migrations')
    policy = json.loads((root / 'backend/schema/release-policy.json').read_text())
    if policy['coreSchema'] != release['coreSchemaVersion'] or policy['optionalOeeSchema'] != release['optionalOeeSchemaVersion']:
        raise ValueError('Release metadata disagrees with the database policy')
    if release['coreSchemaVersion'] >= 11:
        init = ast.parse((root/'backend/src/pak_maintenance/__init__.py').read_text(encoding='utf-8-sig'))
        runtime_values = {target.id:ast.literal_eval(n.value) for n in init.body if isinstance(n,ast.Assign)
                          for target in n.targets if isinstance(target,ast.Name) and target.id in {'__version__','__package_revision__'}}
        if (runtime_values.get('__version__') != backend['project']['version'] or
                runtime_values.get('__package_revision__') != release['packageRevision']):
            raise ValueError('Runtime package version/revision disagrees with RELEASE.json')
        database = ast.parse((root/'backend/src/pak_maintenance/database.py').read_text(encoding='utf-8-sig'))
        versions = [ast.literal_eval(n.value) for node in database.body
                    if isinstance(node,ast.ClassDef) and node.name=='Database' for n in node.body
                    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='schema_version' for t in n.targets)]
        if versions != [release['coreSchemaVersion']]:
            raise ValueError('Runtime database contract disagrees with RELEASE.json')
        api = ast.parse((root/'backend/src/pak_maintenance/api.py').read_text(encoding='utf-8-sig'))
        def constants(key):
            return {ast.literal_eval(value) for node in ast.walk(api) if isinstance(node,ast.Dict)
                    for field,value in zip(node.keys,node.values)
                    if isinstance(field,ast.Constant) and field.value==key}
        if constants('schemaVersion') != {release['coreSchemaVersion']} or constants('apiVersion') != {release['apiVersion']}:
            raise ValueError('API health contract disagrees with RELEASE.json')
        capabilities = next((ast.literal_eval(n.value) for n in api.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='CAPABILITIES' for t in n.targets)), ())
        client = (root/'frontend/src/compatibility.ts').read_text(encoding='utf-8-sig')
        match = re.search(r'requiredCapabilities\s*=\s*(\[[\s\S]*?\])\s*as const',client)
        client_caps = ast.literal_eval(match.group(1)) if match else ()
        required_caps = set(release.get('requiredCapabilities', ())) | {'pm-responsibility-cross-team'}
        if (not required_caps.issubset(capabilities) or not required_caps.issubset(client_caps)
                or not re.search(r'health\.schemaVersion\s*>=\s*'+str(release['coreSchemaVersion'])+r'\b',client)):
            raise ValueError('Frontend/backend required capability or storage contract is incomplete')
    return release


def package(root, destination):
    root = canonical_local_path(root)
    destination = Path(destination)
    if not destination.is_absolute():
        raise ValueError('Choose an absolute archive path outside the source tree')
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = canonical_local_path(destination.parent)
    destination = parent / destination.name
    if destination.is_relative_to(root) or destination.exists():
        raise ValueError('Choose a new archive outside the source tree; existing files are never replaced')
    release = release_metadata(root)
    contents = {name:path.read_bytes() for name,path in source_files(root)}
    required = {'README.md','RELEASE.json','backend/.env.example','backend/app/__init__.py',
        'backend/app/main.py','backend/src/pak_maintenance/__init__.py',
        'backend/src/pak_maintenance/settings.py','tools/verify_source.py',
        'tools/source_inventory.py','frontend/dist/index.html'}
    required.update(required_contract_files(release))
    if not required.issubset(contents):
        raise ValueError('Required release files missing: ' + ', '.join(sorted(required - contents.keys())))
    entries = {n:{'bytes':len(b),'sha256':digest(b)} for n,b in sorted(contents.items())}
    manifest = {'applicationVersion':release['applicationVersion'],
        'handoffRevision':release['packageRevision'],'date':release['updateDate'],
        'apiVersion':release['apiVersion'],'coreSchemaVersion':release['coreSchemaVersion'],
        'containsCompanyData':False,'containsInstalledDependencies':False,
        'linuxTargetAccepted':False,'files':entries}
    contents['source-manifest.json'] = (json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    archive_root = release.get('archiveRoot','PAK-Project')
    if archive_root != 'PAK-Project':
        raise ValueError('The standard source archive root must be PAK-Project')
    stamp = date.fromisoformat(release['updateDate'])
    if stamp.year < 1980:
        raise ValueError('Release date cannot precede the ZIP format epoch')
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in sorted(contents.items()):
            info = zipfile.ZipInfo(archive_root+'/'+name,(stamp.year,stamp.month,stamp.day,0,0,0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644)<<16
            archive.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(contents):
            raise ValueError('Archive integrity check failed')
        for name,data in contents.items():
            if archive.read(archive_root+'/'+name) != data:
                raise ValueError('Archived file differs from source: '+name)
    # Refuse a release whose source changed during creation.
    final = {name:{'bytes':path.stat().st_size,'sha256':digest(path.read_bytes())} for name,path in source_files(root)}
    if final != entries:
        raise ValueError('Source changed while packaging; do not publish the archive')
    return {'archive':str(destination),'bytes':destination.stat().st_size,
        'files':len(contents),'sha256':digest(destination.read_bytes()),
        'applicationVersion':release['applicationVersion'],'packageRevision':release['packageRevision']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', help='New absolute ZIP path outside the project')
    arguments = parser.parse_args()
    try:
        print(json.dumps(package(ROOT,arguments.destination)))
    except (ValueError,KeyError,OSError) as error:
        parser.exit(1,'Source packaging failed: '+str(error)+'\n')


if __name__ == '__main__':
    main()
