import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('identity', Path(__file__).with_name('verify-config-identity.py'))
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


def fixture():
    # Explicit expected alpha.9 shape; no use of the verifier's AI constructor.
    old = {
        'api': {'token': 'sample-token'}, 'repo': {'key': 'sample-repo'},
        'system': {'id': 'sample-device'}, 'uiLayout': {'custom': ['notes', 42]},
        'sync': {'enabled': True, 'provider': 2, 's3': {'secret': 'sample-s3'},
                 'synced': 100, 'stat': 'old'},
        'ai': {'openAI': {'id': '20240101010101-aaaaaaa', 'apiKey': 'sample-ai',
                          'apiBaseURL': ' https://example.invalid/custom/v1 ',
                          'apiModel': ' model-a ', 'apiTimeout': 900,
                          'apiMaxContexts': 4, 'apiTemperature': 0.4,
                          'apiMaxTokens': 900, 'agentTimeout': 180,
                          'agentConfirmTimeout': 13, 'agentMaxRetries': 2}}
    }
    runtime = copy.deepcopy(old)
    runtime['sync'].update(enabled=False, synced=0, stat='disabled', newAlphaOption=True)
    runtime['ai'] = {
        'providers': [{'id': '20240101010101-aaaaaaa', 'enabled': True,
                       'apiKey': 'sample-ai', 'baseURL': 'https://example.invalid/custom/v1',
                       'protocol': 'openai', 'requestTimeout': 600,
                       'models': [{'id': '20240101010101-aaaaaaa', 'enabled': True, 'name': 'model-a'}]}],
        'editing': {'modelId': '20240101010101-aaaaaaa', 'maxHistoryMessages': 4,
                    'temperature': 0.4, 'maxCompletionTokens': 900},
        'agent': {'modelId': '20240101010101-aaaaaaa', 'sessionTimeout': 180,
                  'confirmTimeout': 13, 'maxRetries': 2, 'maxToolCallRounds': 64,
                  'streamIdleTimeout': 120, 'temperature': 0, 'maxCompletionTokens': 0},
        'embedding': {'enabled': False, 'apiKey': '', 'baseURL': '', 'name': '', 'timeout': 30},
        'mcp': {'servers': []}}
    return old, copy.deepcopy(runtime), runtime


class ConfigIdentityTests(unittest.TestCase):
    def test_exact_legacy_migration_and_runtime_changes_allowed(self):
        self.assertTrue(identity.verify(*fixture())['passed'])

    def test_preserved_secrets_storage_and_identity_cannot_silently_reset(self):
        for branch in (1, 2):
            for section, key in (('api', 'token'), ('repo', 'key'), ('system', 'id')):
                with self.subTest(branch=branch, section=section):
                    parts = fixture()
                    parts[branch][section][key] = 'reset'
                    self.assertFalse(identity.verify(*parts)['passed'])
            parts = fixture()
            parts[branch]['sync']['s3']['secret'] = 'reset'
            self.assertFalse(identity.verify(*parts)['passed'])
            parts = fixture()
            parts[branch]['uiLayout'] = {}
            self.assertFalse(identity.verify(*parts)['passed'])

    def test_sync_must_still_be_disabled_and_bool_is_not_number(self):
        for value in (True, 0, None):
            parts = fixture()
            parts[2]['sync']['enabled'] = value
            self.assertFalse(identity.verify(*parts)['passed'])

    def test_ai_credential_endpoint_model_and_parameters_cannot_reset(self):
        for key in ('apiKey', 'baseURL', 'protocol', 'requestTimeout'):
            parts = fixture()
            parts[2]['ai']['providers'][0][key] = 'incorrect'
            self.assertFalse(identity.verify(*parts)['passed'])
        parts = fixture()
        parts[2]['ai']['providers'][0]['models'][0]['name'] = 'other-model'
        self.assertFalse(identity.verify(*parts)['passed'])
        parts = fixture()
        parts[2]['ai']['editing']['maxHistoryMessages'] = 7
        self.assertFalse(identity.verify(*parts)['passed'])

    def test_empty_old_ai_requires_actual_source_defaults(self):
        parts = fixture()
        parts[0]['ai'] = {}
        parts[2]['ai']['providers'] = []
        parts[2]['ai']['editing'] = {'modelId': '', 'maxHistoryMessages': 7,
                                     'temperature': 1, 'maxCompletionTokens': 0}
        parts[2]['ai']['agent'] = {'modelId': '', 'sessionTimeout': 1800,
                                  'confirmTimeout': 600, 'maxRetries': 3,
                                  'maxToolCallRounds': 64, 'streamIdleTimeout': 120,
                                  'temperature': 1, 'maxCompletionTokens': 0}
        self.assertTrue(identity.verify(*parts)['passed'])
        parts[2]['ai']['agent']['sessionTimeout'] = 0
        self.assertFalse(identity.verify(*parts)['passed'])

    @unittest.skipUnless(shutil.which('openssl'), 'openssl unavailable')
    def test_upstream_legacy_cipher_with_no_evidence_leak(self):
        secret = 'sample-legacy-secret'
        encrypted = subprocess.run(
            ['openssl', 'enc', '-aes-128-cbc', '-K', b'696D897C9AA0611B'.hex(),
             '-iv', b'RandomInitVector'.hex()], input=secret.encode().hex().encode(),
            capture_output=True, check=True).stdout.hex()
        parts = fixture()
        parts[0]['ai']['openAI']['apiKey'] = encrypted
        parts[2]['ai']['providers'][0]['apiKey'] = secret
        evidence = identity.verify(*parts)
        self.assertTrue(evidence['passed'])
        serialized = json.dumps(evidence)
        for value in (secret, encrypted, 'sample-token', 'sample-s3', 'example.invalid'):
            self.assertNotIn(value, serialized)

    def test_unknown_nonlegacy_ai_fails_closed(self):
        parts = fixture()
        parts[0]['ai'] = {'unreviewed': {'secret': 'value'}}
        self.assertFalse(identity.verify(*parts)['passed'])


if __name__ == '__main__':
    unittest.main()
