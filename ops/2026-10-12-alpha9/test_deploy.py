"""Local failure-path checks. Docker is mocked; no VM or production access."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

if os.name == 'nt':
    sys.modules.setdefault('fcntl', types.SimpleNamespace())
spec = importlib.util.spec_from_file_location('deploy', Path(__file__).with_name('deploy.py'))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class SnapshotTests(unittest.TestCase):
    def test_commit_stdout_warning_does_not_pollute_snapshot_id(self):
        image_id = 'sha256:' + 'a' * 64
        with patch.object(deploy, 'docker', side_effect=[
                'Flag --pause has been deprecated\n' + image_id + '\n',
                json.dumps([{'Id': image_id}])]) as docker:
            self.assertEqual(deploy.create_rollback_snapshot('old-container'), image_id)
        self.assertEqual(docker.call_args_list[0].args,
                         ('commit', '--pause=false', 'old-container', deploy.SNAPSHOT))
        self.assertEqual(docker.call_args_list[1].args, ('image', 'inspect', deploy.SNAPSHOT))

    def test_failed_commit_never_accepts_an_existing_tag(self):
        with patch.object(deploy, 'docker', side_effect=deploy.SafeError('commit failed')) as docker:
            with self.assertRaisesRegex(deploy.SafeError, 'commit failed'):
                deploy.create_rollback_snapshot('old-container')
        self.assertEqual(docker.call_count, 1)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = {key: self.root / name for key, name in (
            ('STAGE', 'stage'), ('ORIGINAL', 'workspace'), ('BACKUP', 'before'),
            ('CANDIDATE', 'candidate'))}
        self.paths['STAGE'].mkdir()
        self.paths['BACKUP'] = self.paths['STAGE'] / 'before'
        self.paths['STATE'] = self.paths['STAGE'] / 'state.json'
        self.patchers = [patch.object(deploy, k, v) for k, v in self.paths.items()]
        self.patchers += [patch.object(deploy, 'fsync_dir', lambda _: None),
                          patch.object(deploy, 'no_other_writers', lambda *args: None),
                          patch.object(deploy, 'version_check', lambda *args, **kwargs: '3.5.8')]
        for item in self.patchers:
            item.start()
        self.containers = {
            'old': {'Id': 'old', 'Name': '/' + deploy.OLD_RENAMED,
                    'State': {'Running': False}, 'Config': {'Labels': {}},
                    'HostConfig': {'RestartPolicy': {'Name': 'no'}}},
            'new': {'Id': 'new', 'Name': '/' + deploy.OLD_NAME,
                    'State': {'Running': True},
                    'Config': {'Labels': {deploy.LABEL_KEY: deploy.LABEL_VALUE}},
                    'HostConfig': {'RestartPolicy': {'Name': 'always'}}},
        }
        self.events = []
        self.patchers += [patch.object(deploy, 'inspect_container', self.inspect),
                          patch.object(deploy, 'docker', self.docker)]
        self.patchers[-2].start()
        self.patchers[-1].start()
        deploy.write_json(deploy.STATE, {'phase': 'candidate-moved', 'old_id': 'old'})

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def inspect(self, name, missing_ok=False, **kwargs):
        for cid, info in self.containers.items():
            if cid == name or info['Name'] == '/' + name:
                return copy.deepcopy(info)
        if missing_ok:
            return None
        raise deploy.SafeError('Required container not found')

    def docker(self, *args, **kwargs):
        self.events.append(args)
        action = args[0]
        if action == 'update':
            self.containers[args[-1]]['HostConfig']['RestartPolicy']['Name'] = args[2]
        elif action == 'stop':
            self.containers[args[-1]]['State']['Running'] = False
        elif action == 'rename':
            self.containers[args[1]]['Name'] = '/' + args[2]
        elif action == 'start':
            self.assertFalse(any(i['State']['Running'] for cid, i in self.containers.items() if cid != args[1]))
            self.assertEqual((deploy.ORIGINAL / 'note').read_text(), 'baseline')
            self.containers[args[1]]['State']['Running'] = True
        else:
            raise AssertionError(args)
        return ''

    def write_tree(self, root, contents):
        root.mkdir()
        (root / 'note').write_text(contents)

    def backup_manifest(self):
        deploy.write_json(deploy.STAGE / 'workspace-manifest-before.json', deploy.manifest(deploy.BACKUP))

    def test_restores_after_second_rename_and_preserves_new_notes(self):
        self.write_tree(deploy.BACKUP, 'baseline')
        self.backup_manifest()
        self.write_tree(deploy.ORIGINAL, 'migrated-new-note')
        result = deploy.rollback()
        self.assertEqual(result['phase'], 'rolled-back')
        self.assertEqual((deploy.ORIGINAL / 'note').read_text(), 'baseline')
        failed = deploy.load_json(deploy.STATE)['failed_workspace']
        self.assertEqual((Path(failed) / 'note').read_text(), 'migrated-new-note')
        self.assertTrue(self.containers['old']['State']['Running'])
        self.assertEqual(self.containers['old']['HostConfig']['RestartPolicy']['Name'], 'always')
        self.assertFalse(self.containers['new']['State']['Running'])

    def test_restores_between_directory_renames(self):
        self.write_tree(deploy.BACKUP, 'baseline')
        self.backup_manifest()
        self.write_tree(deploy.CANDIDATE, 'migrated-new-note')
        del self.containers['new']
        self.containers['old']['Name'] = '/' + deploy.OLD_NAME
        deploy.rollback()
        self.assertEqual((deploy.ORIGINAL / 'note').read_text(), 'baseline')
        self.assertEqual((deploy.CANDIDATE / 'note').read_text(), 'migrated-new-note')

    def test_refuses_migrated_workspace_without_backup(self):
        self.write_tree(deploy.BACKUP, 'baseline')
        self.backup_manifest()
        deploy.BACKUP.rename(self.root / 'unavailable-baseline')
        self.write_tree(deploy.ORIGINAL, 'wrong-migrated-data')
        with self.assertRaisesRegex(deploy.SafeError, 'does not match'):
            deploy.rollback()
        self.assertFalse(self.containers['old']['State']['Running'])
        self.assertFalse(any(event[0] == 'start' for event in self.events))

    def test_refuses_unrelated_reserved_container(self):
        self.write_tree(deploy.ORIGINAL, 'baseline')
        self.containers['new']['Config']['Labels'] = {}
        with self.assertRaisesRegex(deploy.SafeError, 'unrelated'):
            deploy.rollback()
        self.assertTrue(self.containers['new']['State']['Running'])
        self.assertEqual(self.events, [])

    def test_repeat_rollback_does_not_reject_new_old_version_writes(self):
        self.write_tree(deploy.BACKUP, 'baseline')
        self.backup_manifest()
        self.write_tree(deploy.ORIGINAL, 'migration')
        deploy.rollback()
        (deploy.ORIGINAL / 'note').write_text('baseline-now-edited-on-old-version')
        before = len(self.events)
        self.assertEqual(deploy.rollback()['phase'], 'rolled-back')
        self.assertEqual(len(self.events), before)

    def test_directory_rename_never_overwrites_existing_destination(self):
        self.write_tree(deploy.ORIGINAL, 'baseline')
        self.write_tree(deploy.BACKUP, 'existing-backup')
        with self.assertRaisesRegex(deploy.SafeError, 'already exists'):
            deploy.rename_directory(deploy.ORIGINAL, deploy.BACKUP)
        self.assertEqual((deploy.BACKUP / 'note').read_text(), 'existing-backup')

    def test_relative_internal_link_allowed_external_and_absolute_rejected(self):
        self.write_tree(deploy.ORIGINAL, 'baseline')
        link = deploy.ORIGINAL / 'link'
        try:
            link.symlink_to('note')
        except OSError:
            self.skipTest('Local account does not have symlink creation privilege')
        deploy.isolated_symlinks(deploy.ORIGINAL)
        link.unlink()
        link.symlink_to('../outside')
        with self.assertRaisesRegex(deploy.SafeError, 'escapes'):
            deploy.isolated_symlinks(deploy.ORIGINAL)
        link.unlink()
        link.symlink_to(str(deploy.ORIGINAL / 'note'))
        with self.assertRaisesRegex(deploy.SafeError, 'Absolute'):
            deploy.isolated_symlinks(deploy.ORIGINAL)

    def finalize_fixture(self):
        deploy.ORIGINAL.mkdir()
        (deploy.ORIGINAL / 'conf').mkdir()
        deploy.write_json(deploy.ORIGINAL / 'conf' / 'conf.json', {'sync': {'enabled': False}})
        deploy.write_json(deploy.STAGE / 'production-conf-before.json',
                          {'sync': {'enabled': True, 'provider': 2}})
        self.containers['new']['Image'] = 'candidate-image'
        state = {'phase': 'promoted', 'old_id': 'old', 'production_id': 'new',
                 'image_id': 'candidate-image', 'backup_manifest_sha256': 'backup-hash',
                 'expected_version': '3.8.7-alpha.9', 'unrelated_containers': {}}
        deploy.write_json(deploy.STATE, state)
        evidence = {'result': 'pass', 'production_id': 'new', 'image_id': 'candidate-image',
                    'backup_manifest_sha256': 'backup-hash',
                    'checks': {key: True for key in deploy.PRODUCTION_CHECKS}}
        path = deploy.STAGE / 'production-evidence.json'
        deploy.write_json(path, evidence)
        return types.SimpleNamespace(evidence=str(path))

    def test_finalize_marks_irreversible_boundary_before_enabling_sync(self):
        args = self.finalize_fixture()
        calls = []
        def set_sync(enabled):
            self.assertEqual(deploy.load_json(deploy.STATE)['phase'], 'sync-release')
            calls.append(enabled)
        with patch.object(deploy, 'baseline_summary', return_value={}), \
                patch.object(deploy, 'set_sync_enabled', set_sync):
            result = deploy.finalize(args)
        self.assertEqual(result['phase'], 'finalized')
        self.assertEqual(calls, [True])

    def test_failed_sync_release_disables_sync_and_preserves_new_workspace(self):
        args = self.finalize_fixture()
        calls = []
        def set_sync(enabled):
            calls.append(enabled)
            if enabled:
                raise deploy.SafeError('Simulated ambiguous response')
        with patch.object(deploy, 'baseline_summary', return_value={}), \
                patch.object(deploy, 'set_sync_enabled', set_sync):
            with self.assertRaisesRegex(deploy.SafeError, 'rollback blocked'):
                deploy.finalize(args)
        state = deploy.load_json(deploy.STATE)
        self.assertEqual(state['phase'], 'sync-release-failed')
        self.assertTrue(state['sync_disabled_after_failure'])
        self.assertEqual(calls, [True, False])
        self.assertTrue(self.containers['new']['State']['Running'])

    def test_all_sync_release_phases_block_old_workspace_rollback(self):
        for phase in deploy.SYNC_RELEASE_PHASES:
            deploy.write_json(deploy.STATE, {'phase': phase, 'old_id': 'old'})
            with self.assertRaisesRegex(deploy.SafeError, 'remote storage'):
                deploy.rollback()
        self.assertEqual(self.events, [])

    def appearance_fixture(self, existing_material=False):
        old_source, new_source = self.root / 'old-appearance', self.root / 'new-appearance'
        for root, icons in ((old_source, ('material', 'ant')), (new_source, ('litheness',))):
            for kind, names in (('icons', icons), ('themes', ('daylight', 'midnight'))):
                for name in names:
                    package = root / kind / name
                    package.mkdir(parents=True)
                    (package / 'package.bin').write_bytes((root.name + ':' + name).encode())
        (deploy.CANDIDATE / 'data').mkdir(parents=True)
        if existing_material:
            package = deploy.CANDIDATE / 'data' / 'icons' / 'material'
            package.mkdir(parents=True)
            (package / 'package.bin').write_bytes(b'existing-user-customization')
        self.containers['new']['State']['Running'] = False
        state = {'phase': 'candidate-created', 'old_id': 'old', 'candidate_id': 'new', 'image_id': 'image'}
        def docker_cp(*args, **kwargs):
            self.assertEqual(args[:2], ('cp', '--archive'))
            source = old_source if args[2].startswith('old:') else new_source
            shutil.copytree(source, Path(args[3]), symlinks=True)
            return ''
        def cp(args, **kwargs):
            self.assertEqual(args[:3], ['cp', '-a', '--reflink=never'])
            shutil.copytree(Path(args[-2]), Path(args[-1]), symlinks=True)
        return state, docker_cp, cp

    def test_removed_builtin_icons_copied_and_new_bundled_themes_not_overlaid(self):
        state, docker_cp, cp = self.appearance_fixture()
        with patch.object(deploy, 'docker', docker_cp), patch.object(deploy, 'run', cp), \
                patch.object(deploy, 'space_required', lambda *args: None), \
                patch.object(deploy.os, 'chown', lambda *args: None, create=True):
            deploy.preserve_legacy_appearance(state)
        evidence = deploy.load_json(deploy.STAGE / 'appearance-preservation.json')
        self.assertEqual(evidence['new_builtin_packages']['icons'], ['litheness'])
        self.assertEqual({p['name'] for p in evidence['packages']}, {'ant', 'material'})
        self.assertTrue(all(p['status'] == 'copied' for p in evidence['packages']))
        self.assertEqual(state['appearance_copied_count'], 2)
        self.assertFalse((deploy.CANDIDATE / 'data' / 'themes' / 'daylight').exists())
        self.assertEqual((deploy.CANDIDATE / 'data' / 'icons' / 'material' / 'package.bin').read_bytes(),
                         b'old-appearance:material')

    def test_existing_workspace_appearance_package_is_never_overwritten(self):
        state, docker_cp, cp = self.appearance_fixture(existing_material=True)
        with patch.object(deploy, 'docker', docker_cp), patch.object(deploy, 'run', cp), \
                patch.object(deploy, 'space_required', lambda *args: None), \
                patch.object(deploy.os, 'chown', lambda *args: None, create=True):
            deploy.preserve_legacy_appearance(state)
        evidence = deploy.load_json(deploy.STAGE / 'appearance-preservation.json')
        packages = {p['name']: p for p in evidence['packages']}
        self.assertEqual(packages['material']['status'], 'already_present')
        self.assertNotEqual(packages['material']['source_manifest']['package.bin']['sha256'],
                            packages['material']['target_manifest']['package.bin']['sha256'])
        self.assertEqual((deploy.CANDIDATE / 'data' / 'icons' / 'material' / 'package.bin').read_bytes(),
                         b'existing-user-customization')
        self.assertEqual(state['appearance_copied_count'], 1)
        self.assertEqual(state['appearance_already_present_count'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
