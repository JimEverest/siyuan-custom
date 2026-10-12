"""Read-only appearance verification; never emit full configuration or API data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import urllib.request


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def fetch_runtime_appearance(workspace, port):
    require(port in (16806, 6806), 'Unexpected local appearance API port')
    token = read_json(workspace / 'conf' / 'conf.json')['api']['token']
    request = urllib.request.Request(
        'http://127.0.0.1:' + str(port) + '/api/system/getConf',
        data=b'{}', method='POST',
        headers={'Authorization': 'Token ' + token, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=30) as response:
            value = json.load(response)
            require(response.status == 200 and value.get('code') == 0,
                    'Appearance configuration API did not succeed')
            appearance = value['data']['conf']['appearance']
            require(isinstance(appearance, dict), 'Runtime appearance configuration is missing')
            return appearance
    except Exception:
        raise RuntimeError('Authenticated runtime appearance query failed') from None


def package_names(appearance, key):
    items = appearance.get(key)
    require(isinstance(items, list) and items, 'Runtime appearance package list is missing: ' + key)
    require(all(isinstance(item, dict) and isinstance(item.get('name'), str)
                and item['name'] for item in items), 'Runtime appearance package entry is malformed')
    names = [item['name'] for item in items]
    require(len(names) == len(set(names)), 'Runtime appearance package list has duplicates')
    return set(names)


def child_path(root, relative):
    path = Path(relative)
    require(not path.is_absolute() and '..' not in path.parts,
            'Appearance manifest contains an unsafe relative path')
    target = root / path
    resolved = target.resolve()
    require(resolved == root.resolve() or root.resolve() in resolved.parents,
            'Appearance file resolves outside its package')
    return target


def verify_appearance(stage, workspace, state, port):
    stage, workspace = Path(stage), Path(workspace)
    evidence_path = stage / 'appearance-preservation.json'
    require(evidence_path.is_file() and not evidence_path.is_symlink(),
            'Appearance preservation evidence is unavailable')
    require(sha(evidence_path) == state.get('appearance_preservation_sha256'),
            'Appearance preservation evidence SHA256 does not match deployment state')
    evidence = read_json(evidence_path)
    require(evidence.get('schema') == 1, 'Unsupported appearance evidence schema')
    for key in ('old_id', 'candidate_id', 'image_id'):
        require(evidence.get(key) == state.get(key), 'Appearance evidence belongs to another deployment')
    backup_manifest = stage / 'appearance-before-manifest.json'
    require(sha(backup_manifest) == evidence.get('appearance_backup_manifest_sha256'),
            'Original appearance manifest SHA256 changed')
    packages = evidence.get('packages')
    require(isinstance(packages, list), 'Appearance package evidence is missing')
    old_packages, builtins = evidence.get('old_packages', {}), evidence.get('new_builtin_packages', {})
    expected = set()
    for kind in ('icons', 'themes'):
        require(isinstance(old_packages.get(kind), list) and isinstance(builtins.get(kind), list),
                'Appearance package inventories are missing')
        expected.update((kind, name) for name in set(old_packages[kind]) - set(builtins[kind]))
    actual = [(item.get('kind'), item.get('name')) for item in packages]
    require(len(actual) == len(set(actual)) and set(actual) == expected,
            'Appearance evidence omits or duplicates a legacy package')
    verified_packages = []
    total_files = 0
    for package in packages:
        kind, name = package['kind'], package['name']
        require(kind in ('icons', 'themes') and isinstance(name, str)
                and name not in ('', '.', '..') and Path(name).name == name,
                'Appearance package identity is invalid')
        require(package.get('status') in ('copied', 'already_present'),
                'Appearance preservation status is invalid')
        data_root = workspace / 'data'
        require(workspace.resolve() in data_root.resolve().parents,
                'Workspace data resolves outside the workspace')
        root = workspace / 'data' / kind / name
        require(root.is_dir() and not root.is_symlink(), 'Preserved appearance package is missing')
        require(data_root.resolve() in root.resolve().parents,
                'Appearance package resolves outside workspace data')
        manifest = package.get('target_manifest')
        require(isinstance(manifest, dict) and manifest, 'Expected appearance target manifest is missing')
        if package['status'] == 'copied':
            require(package.get('source_manifest') == manifest,
                    'Copied appearance package did not preserve its source manifest')
        files = 0
        for relative, entry in manifest.items():
            target = child_path(root, relative)
            if entry['type'] == 'file':
                require(target.is_file() and not target.is_symlink(), 'Preserved appearance file is missing')
                require(target.stat().st_size == entry['bytes'] and sha(target) == entry['sha256'],
                        'Preserved appearance file content changed')
                files += 1
            elif entry['type'] == 'directory':
                require(target.is_dir() and not target.is_symlink(), 'Preserved appearance directory changed')
            elif entry['type'] == 'link':
                require(target.is_symlink() and os.readlink(target) == entry['target'],
                        'Preserved appearance symlink changed')
            else:
                raise RuntimeError('Unsupported appearance manifest entry')
        require(files > 0, 'Preserved appearance package has no verified files')
        total_files += files
        verified_packages.append({'kind': kind, 'name': name, 'status': package['status'],
                                  'files_sha256_verified': files})
    original_conf = read_json(stage / 'production-conf-before.json')
    old = original_conf['appearance']
    migration = runpy.run_path(str(Path(__file__).with_name('migrate-appearance.py')))
    aliases = {}
    if state.get('appearance_aliases_sha256') or (stage / 'appearance-aliases.json').exists():
        aliases = migration['verify_aliases'](stage, workspace, state, original_conf)
    persisted = read_json(workspace / 'conf' / 'conf.json')['appearance']
    runtime = fetch_runtime_appearance(workspace, port)
    preferences = migration['verify_preferences'](original_conf, persisted, runtime, aliases)
    selection = {}
    for key in ('icon', 'themeLight', 'themeDark'):
        require(isinstance(old.get(key), str) and old[key], 'Original appearance selection is unavailable')
        expected_selection = aliases.get(old[key], old[key]) if key == 'icon' else old[key]
        require(persisted.get(key) == expected_selection, 'Persisted appearance selection changed: ' + key)
        require(runtime.get(key) == expected_selection, 'Runtime appearance selection changed: ' + key)
        selection[key] = expected_selection
    icons = package_names(runtime, 'icons')
    light = package_names(runtime, 'lightThemes')
    dark = package_names(runtime, 'darkThemes')
    require(selection['icon'] in icons, 'Original selected icon is not loaded')
    require(selection['themeLight'] in light and selection['themeDark'] in dark,
            'Original selected theme is not loaded')
    require(set(builtins['icons']).issubset(icons), 'New builtin icons are not available')
    require(set(builtins['themes']).issubset(light | dark), 'New builtin themes are not available')
    for package in verified_packages:
        loaded = icons if package['kind'] == 'icons' else light | dark
        require(package['name'] in loaded, 'A preserved appearance package is not recognized by the new kernel')
    require(set(aliases.values()).issubset(icons), 'A preserved legacy icon alias is not loaded')
    return {'passed': True, 'evidence_sha256': state['appearance_preservation_sha256'],
            'alias_evidence_sha256': state.get('appearance_aliases_sha256'),
            'preferences': preferences,
            'files_sha256_verified': total_files, 'packages': verified_packages,
            'selection_preserved': selection,
            'runtime_icons': sorted(icons), 'runtime_light_themes': sorted(light),
            'runtime_dark_themes': sorted(dark),
            'runtime_source': 'authenticated POST /api/system/getConf data.conf.appearance'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', default='/home/nl/siyuan-upgrade-20261012-alpha9')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--port', required=True, type=int, choices=(16806, 6806))
    args = parser.parse_args()
    result = verify_appearance(Path(args.stage), Path(args.workspace),
                               read_json(Path(args.stage) / 'deployment-state.json'), args.port)
    print(json.dumps(result, sort_keys=True), flush=True)
