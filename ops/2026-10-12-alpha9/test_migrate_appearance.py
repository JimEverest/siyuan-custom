import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('migrate-appearance.py')


class AppearanceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'appearance migration helper is missing')
        spec = importlib.util.spec_from_file_location('appearance_migration', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.original = {'lang': 'zh-CN', 'confVersion': 0, 'sync': {'enabled': True},
                         'appearance': {'icons': ['ant', 'material'], 'icon': 'material',
                          'darkThemes': [{'name': 'midnight', 'label': 'dark'}],
                          'lightThemes': [{'name': 'daylight', 'label': 'light'}],
                          'themeLight': 'daylight', 'themeDark': 'midnight', 'mode': 0,
                          'modeOS': False, 'closeButtonBehavior': 0, 'lang': 'zh-CN',
                          'codeBlockThemeLight': 'github', 'codeBlockThemeDark': 'dracula',
                          'statusBar': {'msgDataSyncDisabled': True}}}

    def test_conf_migration_preserves_all_other_values_and_original(self):
        before = copy.deepcopy(self.original)
        actual = self.module.migrate_conf(self.original)
        self.assertEqual(self.original, before)
        expected = copy.deepcopy(before)
        del expected['appearance']['icons']
        expected['appearance']['icon'] = 'legacy-material'
        self.assertEqual(actual, expected)

    def test_legacy_theme_string_caches_removed(self):
        self.original['appearance']['lightThemes'] = ['daylight']
        result = self.module.migrate_conf(self.original)
        self.assertNotIn('lightThemes', result['appearance'])
        self.assertIn('darkThemes', result['appearance'])

    def test_malformed_cache_rejected(self):
        self.original['appearance']['icons'] = [42]
        with self.assertRaises(RuntimeError):
            self.module.migrate_conf(self.original)

    def setup_packages(self, root):
        stage, workspace = root / 'stage', root / 'workspace'
        (workspace / 'data' / 'icons').mkdir(parents=True)
        for name in ('ant', 'material'):
            package = stage / 'appearance-before' / 'icons' / name
            package.mkdir(parents=True)
            (package / 'icon.js').write_bytes(b'synthetic icon contents')
            (package / 'icon.json').write_text(json.dumps({'name': name, 'version': '1.36.0'}))
            shutil.copytree(package, workspace / 'data/icons' / name)
        return stage, workspace

    def test_original_candidate_package_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, workspace = self.setup_packages(Path(directory))
            (workspace / 'data/icons/material/icon.js').write_bytes(b'changed original candidate icon')
            with self.assertRaises(RuntimeError):
                self.module.prepare_aliases(stage, workspace,
                    {'old_id': 'old', 'candidate_id': 'candidate', 'image_id': 'image'}, self.original)

    def test_aliases_preserve_visual_bytes_and_verify_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, workspace = self.setup_packages(Path(directory))
            state = {'old_id': 'old', 'candidate_id': 'candidate', 'image_id': 'image'}
            summary = self.module.prepare_aliases(stage, workspace, state, self.original)
            state['appearance_aliases_sha256'] = summary['sha256']
            aliases = self.module.verify_aliases(stage, workspace, state, self.original)
            self.assertEqual(aliases, self.module.ALIASES)
            target = workspace / 'data/icons/legacy-material'
            self.assertEqual((target / 'icon.js').read_bytes(), b'synthetic icon contents')
            self.assertEqual(json.loads((target / 'icon.json').read_text())['name'], 'legacy-material')
            self.assertTrue((stage / 'appearance-before/icons/material/icon.json').exists())

    def test_alias_visual_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, workspace = self.setup_packages(Path(directory))
            state = {'old_id': 'old', 'candidate_id': 'candidate', 'image_id': 'image'}
            summary = self.module.prepare_aliases(stage, workspace, state, self.original)
            state['appearance_aliases_sha256'] = summary['sha256']
            (workspace / 'data/icons/legacy-material/icon.js').write_bytes(b'changed')
            with self.assertRaises(RuntimeError):
                self.module.verify_aliases(stage, workspace, state, self.original)

    def test_non_name_metadata_change_rejected(self):
        old = {'name': 'material', 'version': '1.36.0'}
        new = {'name': 'legacy-material', 'version': '9.0'}
        with self.assertRaises(RuntimeError):
            self.module.verify_metadata(old, new, 'material', 'legacy-material')

    def test_preferences_allow_only_explicit_alias_and_derived_updates(self):
        current = self.module.migrate_conf(self.original)['appearance']
        current['icons'] = [{'name': 'legacy-material', 'label': 'legacy-material'}]
        current['themeVer'] = '3.8.7'
        current['statusBar']['version'] = 1
        self.module.verify_preferences(self.original, current, current, self.module.ALIASES)
        current['closeButtonBehavior'] = 1
        with self.assertRaises(RuntimeError):
            self.module.verify_preferences(self.original, current, current, self.module.ALIASES)

    def test_language_follows_explicit_upstream_root_projection(self):
        for old, expected in [('en_US', 'en'), ('zh_CN', 'zh-CN'), ('zh_CHT', 'zh-TW'),
                              ('pt_BR', 'pt-BR'), ('en', 'en'), ('xx_XX', 'xx_XX')]:
            with self.subTest(old=old):
                original = copy.deepcopy(self.original)
                original['lang'] = old
                original['appearance']['lang'] = 'different-appearance-cache'
                current = self.module.migrate_conf(original)['appearance']
                current['lang'] = expected
                current['statusBar']['version'] = 1
                self.module.verify_preferences(original, current, current, self.module.ALIASES)
                current['lang'] = 'wrong-language'
                with self.assertRaises(RuntimeError):
                    self.module.verify_preferences(original, current, current, self.module.ALIASES)

    def test_statusbar_user_flag_cannot_change(self):
        current = self.module.migrate_conf(self.original)['appearance']
        current['statusBar']['version'] = 1
        current['statusBar']['msgDataSyncDisabled'] = False
        with self.assertRaises(RuntimeError):
            self.module.verify_preferences(self.original, current, current, self.module.ALIASES)

    def test_complete_verifier_accepts_alias_and_rejects_preference_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, workspace = self.setup_packages(Path(directory))
            state = {'old_id': 'old', 'candidate_id': 'candidate', 'image_id': 'image'}
            (stage / 'production-conf-before.json').write_text(json.dumps(self.original))
            backup_manifest = stage / 'appearance-before-manifest.json'
            backup_manifest.write_text('{}')
            evidence = {'schema': 1, **state,
                        'appearance_backup_manifest_sha256': self.module.sha(backup_manifest),
                        'old_packages': {'icons': ['ant', 'material'], 'themes': ['daylight', 'midnight']},
                        'new_builtin_packages': {'icons': ['litheness'], 'themes': ['daylight', 'midnight']},
                        'packages': []}
            for name in ('ant', 'material'):
                manifest = self.module.package_manifest(stage / 'appearance-before/icons' / name)
                evidence['packages'].append({'kind': 'icons', 'name': name, 'status': 'copied',
                                              'source_manifest': manifest, 'target_manifest': manifest})
            evidence_path = stage / 'appearance-preservation.json'
            evidence_path.write_text(json.dumps(evidence))
            state['appearance_preservation_sha256'] = self.module.sha(evidence_path)
            summary = self.module.prepare_aliases(stage, workspace, state, self.original)
            state['appearance_aliases_sha256'] = summary['sha256']
            migrated = self.module.migrate_conf(self.original)
            current = migrated['appearance']
            current['icons'] = [{'name': name, 'label': name} for name in
                               ('ant', 'material', 'legacy-ant', 'legacy-material', 'litheness')]
            current['statusBar']['version'] = 1
            (workspace / 'conf').mkdir()
            (workspace / 'conf/conf.json').write_text(json.dumps(migrated))
            verifier_path = SCRIPT.with_name('verify-appearance.py')
            spec = importlib.util.spec_from_file_location('verify_appearance', verifier_path)
            verifier = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(verifier)
            verifier.fetch_runtime_appearance = lambda unused_workspace, unused_port: current
            result = verifier.verify_appearance(stage, workspace, state, 16806)
            self.assertTrue(result['passed'])
            self.assertEqual(result['selection_preserved']['icon'], 'legacy-material')
            current['modeOS'] = True
            with self.assertRaises(RuntimeError):
                verifier.verify_appearance(stage, workspace, state, 16806)

    def test_alias_evidence_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            stage, workspace = self.setup_packages(Path(directory))
            state = {'old_id': 'old', 'candidate_id': 'candidate', 'image_id': 'image'}
            summary = self.module.prepare_aliases(stage, workspace, state, self.original)
            state['appearance_aliases_sha256'] = summary['sha256']
            with (stage / 'appearance-aliases.json').open('a') as handle:
                handle.write(' ')
            with self.assertRaises(RuntimeError):
                self.module.verify_aliases(stage, workspace, state, self.original)


if __name__ == '__main__':
    unittest.main()
