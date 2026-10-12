"""Explicit legacy icon aliases for alpha.9, with private preservation evidence.

Run only against a stopped candidate. This helper never starts a kernel, changes
deployment state, or writes conf.json. The caller persists migrate_conf() output
and the returned evidence SHA under its deployment lock.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil


ALIASES = {'ant': 'legacy-ant', 'material': 'legacy-material'}
DERIVED = {'icons', 'lightThemes', 'darkThemes', 'themeVer', 'iconVer', 'themeJS'}
# Exact alpha.9 kernel/util/lang.go LangLegacyToBCP47 mapping. Boot projects
# the migrated root Conf.Lang onto Appearance.Lang (kernel/model/conf.go).
LANG_LEGACY_TO_BCP47 = {
    'zh_CN': 'zh-CN', 'zh_CHT': 'zh-TW', 'en_US': 'en', 'de_DE': 'de',
    'fr_FR': 'fr', 'es_ES': 'es', 'pt_BR': 'pt-BR', 'it_IT': 'it',
    'ja_JP': 'ja', 'ko_KR': 'ko', 'ru_RU': 'ru', 'uk_UA': 'uk',
    'pl_PL': 'pl', 'nl_NL': 'nl', 'ar_SA': 'ar', 'he_IL': 'he',
    'hi_IN': 'hi', 'id_ID': 'id', 'th_TH': 'th', 'tr_TR': 'tr',
    'sk_SK': 'sk', 'sr_RS': 'sr',
}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def cache_kind(value):
    if value is None:
        return 'null'
    require(isinstance(value, list), 'Appearance package cache is not a list')
    if all(isinstance(item, str) and item for item in value):
        return 'strings'
    require(all(isinstance(item, dict) and isinstance(item.get('name'), str)
                and item['name'] and (item.get('label') is None or isinstance(item['label'], str))
                for item in value), 'Appearance package cache contains an invalid entry')
    return 'objects'


def migrate_conf(original_conf):
    """Pure, narrowly scoped conversion; preserve sync and confVersion exactly."""
    result = copy.deepcopy(original_conf)
    appearance = result.get('appearance')
    require(isinstance(appearance, dict), 'Original appearance configuration is missing')
    icon = appearance.get('icon')
    require(isinstance(icon, str) and icon, 'Original icon selection is missing')
    appearance['icon'] = ALIASES.get(icon, icon)
    if 'icons' in appearance:
        cache_kind(appearance['icons'])
        del appearance['icons']  # Runtime LoadIcons regenerates object entries.
    for key in ('lightThemes', 'darkThemes'):
        if key in appearance and cache_kind(appearance[key]) in ('strings', 'null'):
            del appearance[key]
    return result


def package_manifest(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Appearance package is not a real directory')
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'Appearance alias packages cannot contain symbolic links')
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            result[name] = {'type': 'directory'}
        else:
            require(path.is_file(), 'Appearance package contains a special file')
            result[name] = {'type': 'file', 'bytes': path.stat().st_size, 'sha256': sha(path)}
    require('icon.json' in result and 'icon.js' in result, 'Legacy icon files are incomplete')
    return result


def verify_metadata(source, target, name, alias):
    require(isinstance(source, dict) and source.get('name') == name,
            'Original icon metadata does not match its directory')
    expected = copy.deepcopy(source)
    expected['name'] = alias
    require(target == expected, 'Icon metadata changed beyond the explicit name alias')


def verify_package(source, target, name, alias):
    source_manifest, target_manifest = package_manifest(source), package_manifest(target)
    require(set(source_manifest) == set(target_manifest), 'Alias package file inventory changed')
    for relative, entry in source_manifest.items():
        if relative != 'icon.json':
            require(target_manifest[relative] == entry, 'Alias visual resource content changed')
    verify_metadata(read_json(source / 'icon.json'), read_json(target / 'icon.json'), name, alias)
    return source_manifest, target_manifest


def alias_paths(stage, workspace, name, alias):
    require(ALIASES.get(name) == alias, 'Appearance alias is not one of the reviewed mappings')
    source_root = stage / 'appearance-before' / 'icons'
    target_root = workspace / 'data' / 'icons'
    for root, boundary in ((source_root, stage), (target_root, workspace)):
        require(root.is_dir() and not root.is_symlink() and boundary.resolve() in root.resolve().parents,
                'Appearance package root is outside its protected directory')
    return source_root / name, target_root / alias


def prepare_aliases(stage, workspace, state, original_conf):
    """Copy fixed aliases and write evidence; the caller must keep candidate stopped."""
    stage, workspace = Path(stage), Path(workspace)
    migrated = migrate_conf(original_conf)
    packages = []
    for name, alias in ALIASES.items():
        source, target = alias_paths(stage, workspace, name, alias)
        source_manifest = package_manifest(source)
        # The first candidate already retained these original packages. Reject
        # any unexplained candidate changes before creating an alias.
        original_candidate = workspace / 'data' / 'icons' / name
        require(package_manifest(original_candidate) == source_manifest,
                'Original candidate icon package differs from the protected backup')
        require(read_json(source / 'icon.json').get('name') == name,
                'Original icon metadata name is unexpected')
        require(not target.is_symlink(), 'Alias target is a symbolic link')
        if not target.exists():
            shutil.copytree(source, target, copy_function=shutil.copy2)
            metadata = read_json(target / 'icon.json')
            metadata['name'] = alias
            (target / 'icon.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n',
                                            encoding='utf-8')
            if hasattr(os, 'chown'):
                for original in [source, *source.rglob('*')]:
                    destination = target / original.relative_to(source)
                    info = original.stat()
                    os.chown(destination, info.st_uid, info.st_gid)
        source_manifest, target_manifest = verify_package(source, target, name, alias)
        packages.append({'source_name': name, 'target_name': alias,
                         'source_manifest': source_manifest, 'target_manifest': target_manifest,
                         'metadata_change': {'file': 'icon.json', 'field': 'name',
                                             'before': name, 'after': alias}})
    evidence = {'schema': 1, 'mappings': ALIASES,
                **{key: state[key] for key in ('old_id', 'candidate_id', 'image_id')},
                'original_appearance': original_conf['appearance'],
                'migrated_appearance': migrated['appearance'], 'packages': packages}
    path = stage / 'appearance-aliases.json'
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(evidence, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    return {'path': str(path), 'sha256': sha(path), 'mappings': dict(ALIASES),
            'packages': len(packages)}


def verify_aliases(stage, workspace, state, original_conf):
    stage, workspace = Path(stage), Path(workspace)
    path = stage / 'appearance-aliases.json'
    require(path.is_file() and not path.is_symlink(), 'Appearance alias evidence is missing')
    require(sha(path) == state.get('appearance_aliases_sha256'), 'Appearance alias evidence SHA changed')
    evidence = read_json(path)
    require(evidence.get('schema') == 1 and evidence.get('mappings') == ALIASES,
            'Unexpected appearance alias evidence schema or mappings')
    for key in ('old_id', 'candidate_id', 'image_id'):
        require(evidence.get(key) == state.get(key), 'Appearance aliases belong to another deployment')
    require(evidence.get('original_appearance') == original_conf['appearance']
            and evidence.get('migrated_appearance') == migrate_conf(original_conf)['appearance'],
            'Appearance alias configuration evidence does not preserve original preferences')
    packages = evidence.get('packages')
    require(isinstance(packages, list) and len(packages) == 2, 'Appearance alias package list is incomplete')
    names = [package.get('source_name') for package in packages]
    require(len(set(names)) == 2 and set(names) == set(ALIASES), 'Appearance alias packages are duplicated or missing')
    for package in packages:
        name, alias = package['source_name'], package['target_name']
        source, target = alias_paths(stage, workspace, name, alias)
        require(package.get('metadata_change') == {'file': 'icon.json', 'field': 'name',
                'before': name, 'after': alias}, 'Unreviewed appearance metadata change')
        source_manifest, target_manifest = verify_package(source, target, name, alias)
        require(source_manifest == package['source_manifest'] and target_manifest == package['target_manifest'],
                'Appearance alias source or target manifest changed')
    return dict(ALIASES)


def verify_preferences(original_conf, persisted, runtime, aliases=None):
    """Compare every original preference, with explicit upstream derived fields."""
    aliases = aliases or {}
    require(not aliases or aliases == ALIASES, 'Unexpected appearance preference alias')
    old = original_conf['appearance']
    compared = []
    for key, value in old.items():
        if key in DERIVED:
            continue
        expected = aliases.get(value, value) if key == 'icon' else value
        if key == 'lang':
            root_lang = original_conf.get('lang')
            require(isinstance(root_lang, str) and root_lang,
                    'Original root language must be explicit for appearance verification')
            expected = LANG_LEGACY_TO_BCP47.get(root_lang, root_lang)
        if key in ('codeBlockThemeLight', 'codeBlockThemeDark') and value == '':
            expected = 'github' if key.endswith('Light') else 'dracula'
        for actual in (persisted, runtime):
            require(key in actual, 'Original appearance preference is missing: ' + key)
            if key == 'statusBar':
                require(isinstance(actual[key], dict), 'Status bar configuration is missing')
                if value is None:
                    expected_flags = {'msgTaskDatabaseIndexCommitDisabled': False,
                                      'msgTaskHistoryDatabaseIndexCommitDisabled': False,
                                      'msgTaskAssetDatabaseIndexCommitDisabled': False,
                                      'msgTaskHistoryGenerateFileDisabled': False,
                                      'msgDataSyncDisabled': False}
                else:
                    require(isinstance(value, dict), 'Original status bar configuration is invalid')
                    expected_flags = {name: flag for name, flag in value.items() if name != 'version'}
                require(all(actual[key].get(name) == flag for name, flag in expected_flags.items()),
                        'Original status bar preference changed')
                expected_version = max((value or {}).get('version', 0), 1)
                require(actual[key].get('version') == expected_version, 'Unexpected status bar version migration')
            else:
                require(actual[key] == expected, 'Original appearance preference changed: ' + key)
        compared.append(key)
    return {'preference_fields_verified': sorted(compared),
            'derived_fields_regenerated': sorted(set(old) & DERIVED),
            'allowed_icon_aliases': dict(aliases), 'status_bar_version': 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', default='/home/nl/siyuan-upgrade-20261012-alpha9')
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    stage = Path(args.stage)
    result = prepare_aliases(stage, Path(args.workspace), read_json(stage / 'deployment-state.json'),
                             read_json(stage / 'production-conf-before.json'))
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
