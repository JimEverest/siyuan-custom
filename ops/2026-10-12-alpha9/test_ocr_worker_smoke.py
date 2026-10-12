import importlib.util
import json
from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).with_name('ocr-worker-smoke.py')


class ProbeTests(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.exists(), 'independent OCR probe is missing')
        spec = importlib.util.spec_from_file_location('ocr_worker_smoke', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def runner(self, mode='success'):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            value, code = b'', 0
            if args[1] == 'inspect':
                if args[3] == '{{.Id}} {{.Image}} {{.State.Running}} {{.Name}}':
                    value = b'candidate-id sha256:image-id true /siyuan_alpha9_candidate_20261012\n'
                elif args[3] == '{{json .NetworkSettings.Networks}}':
                    value = b'{"internal":{"NetworkID":"network-id"}}'
                elif args[3] == '{{json .Mounts}}':
                    value = b'[{"Destination":"/siyuan/workspace"}]'
            elif args[1:3] == ['network', 'inspect']:
                value = b'false' if mode == 'network' else b'true'
            elif args[1] == 'stats':
                value = b'200MiB / 4GiB'
            elif 'timeout' in args:
                if mode == 'timeout':
                    raise subprocess.TimeoutExpired(args, kwargs['timeout'])
                if mode == 'wrong':
                    value = b'[{"text":"not the expected synthetic text","conf":"98.4"}]'
                elif mode == 'empty':
                    value = b'[]'
                else:
                    value = b'[{"text":"SiYuan OCR 123","conf":"99.4"}]'
            elif '/bin/rm' in args and mode == 'cleanup':
                code = 1
            return subprocess.CompletedProcess(args, code, value, b'private diagnostics never printed')
        return run, calls

    def test_success_is_actual_worker_execution(self):
        module = self.load()
        runner, calls = self.runner()
        result = module.run_probe(runner=runner)
        self.assertTrue(result['passed'])
        self.assertEqual(result['text'], 'SiYuan OCR 123')
        worker = [(a, k) for a, k in calls if 'timeout' in a]
        self.assertEqual(len(worker), 1)
        self.assertLessEqual(worker[0][1]['timeout'], 120)
        payload = json.loads(worker[0][1]['input'])
        self.assertTrue(payload['image'].startswith('/tmp/siyuan-ocr-smoke-'))
        self.assertEqual(payload['config']['worker'], '')
        self.assertTrue(result['synthetic_file_cleaned'])

    def test_noninternal_candidate_is_rejected_before_copy(self):
        runner, calls = self.runner('network')
        result = self.load().run_probe(runner=runner)
        self.assertFalse(result['passed'])
        self.assertFalse(any(a[1] == 'cp' for a, _ in calls))

    def test_wrong_text_does_not_pass(self):
        for mode in ('wrong', 'empty'):
            runner, _ = self.runner(mode)
            result = self.load().run_probe(runner=runner)
            self.assertFalse(result['passed'])
            self.assertTrue(result['synthetic_file_cleaned'])

    def test_timeout_still_cleans_only_own_file(self):
        runner, calls = self.runner('timeout')
        result = self.load().run_probe(runner=runner)
        self.assertFalse(result['passed'])
        self.assertEqual(result['failure'], 'worker_timeout')
        cleanup = [a for a, _ in calls if '/bin/rm' in a]
        self.assertEqual(len(cleanup), 1)
        self.assertEqual(cleanup[0][-2], '-f')
        self.assertTrue(cleanup[0][-1].startswith('/tmp/siyuan-ocr-smoke-'))

    def test_cleanup_failure_does_not_pass(self):
        runner, _ = self.runner('cleanup')
        result = self.load().run_probe(runner=runner)
        self.assertFalse(result['passed'])
        self.assertFalse(result['synthetic_file_cleaned'])


if __name__ == '__main__':
    unittest.main()
