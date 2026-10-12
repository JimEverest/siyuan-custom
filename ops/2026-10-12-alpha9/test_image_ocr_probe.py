import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('image_ocr', Path(__file__).with_name('image-ocr-probe.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
IMAGE = 'sha256:' + 'a' * 64
CID = 'b' * 64


class FakeDocker:
    def __init__(self, mode='success'):
        self.mode, self.calls, self.exists = mode, [], False
        self.name, self.owner = '', ''

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        output = b''
        if args[1:3] == ['image', 'inspect']:
            output = json.dumps([{'Id': IMAGE, 'Os': 'linux', 'Architecture': 'amd64',
                'Config': {'Volumes': {'/workspace': {}} if self.mode == 'volume' else None,
                           'Labels': {'io.siyuan.upstream.revision': probe.UPSTREAM_SHA,
                                      'org.opencontainers.image.version': 'v3.8.7-alpha.9'}}}]).encode()
        elif args[1] == 'run':
            self.name = args[args.index('--name') + 1]
            self.owner = args[args.index('--label') + 1].split('=', 1)[1]
            if self.mode in ('timeout', 'wrong_owner'):
                self.exists = True
                raise subprocess.TimeoutExpired(args, 120)
            output = json.dumps([{'text': 'SiYuan OCR 123' if self.mode == 'success' else 'unrecognized'}]).encode()
        elif args[1] == 'ps':
            output = (CID + '\n').encode() if self.exists else b''
        elif args[1:3] == ['container', 'inspect']:
            output = json.dumps([{'Id': CID, 'Name': '/' + self.name, 'Image': IMAGE,
                'Config': {'Labels': {probe.OWNER_LABEL: 'other' if self.mode == 'wrong_owner' else self.owner}}}]).encode()
        elif args[1:4] == ['container', 'rm', '--force']:
            self.exists = False
        else:
            raise AssertionError('unexpected Docker command')
        return subprocess.CompletedProcess(args, 0, output, b'')


class ImageOCRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stage = Path(self.temp.name)
        (self.stage / 'image-before-deploy.json').write_text(json.dumps({'image_id': IMAGE}))
        shutil.copyfile(Path(__file__).with_name('ocr-worker-smoke.py'), self.stage / 'ocr-worker-smoke.py')

    def tearDown(self):
        self.temp.cleanup()

    def test_success_is_fixed_image_offline_without_workspace(self):
        docker = FakeDocker()
        result = probe.run_probe(self.stage, docker)
        self.assertTrue(result['passed'])
        args, kw = next(call for call in docker.calls if call[0][1] == 'run')
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertEqual(args[args.index('--entrypoint') + 1], '/bin/sh')
        self.assertEqual(args[args.index('--entrypoint') + 2], IMAGE)
        self.assertEqual(kw['timeout'], 120)
        self.assertIn('/bin/busybox timeout -s KILL 115', args[-1])
        self.assertFalse(any(arg in ('-v', '--volume', '--mount') for arg in args))
        self.assertNotIn('/workspace', ' '.join(args))
        self.assertTrue(json.loads(kw['input'])['image'].startswith('/tmp/'))

    def test_timeout_removes_only_exact_owned_container(self):
        docker = FakeDocker('timeout')
        result = probe.run_probe(self.stage, docker)
        self.assertFalse(result['passed'])
        self.assertEqual(result['failure'], 'probe_timeout')
        self.assertTrue(result['probe_container_cleaned'])
        removals = [call[0] for call in docker.calls if call[0][1:3] == ['container', 'rm']]
        self.assertEqual(removals, [['docker', 'container', 'rm', '--force', CID]])

    def test_cleanup_never_removes_another_owner(self):
        docker = FakeDocker('wrong_owner')
        result = probe.run_probe(self.stage, docker)
        self.assertFalse(result['passed'])
        self.assertFalse(result['probe_container_cleaned'])
        self.assertFalse(any(call[0][1:3] == ['container', 'rm'] for call in docker.calls))

    def test_image_volumes_rejected_before_run(self):
        docker = FakeDocker('volume')
        result = probe.run_probe(self.stage, docker)
        self.assertFalse(result['passed'])
        self.assertFalse(any(call[0][1] == 'run' for call in docker.calls))

    def test_unrecognized_text_does_not_pass(self):
        result = probe.run_probe(self.stage, FakeDocker('wrong_text'))
        self.assertFalse(result['passed'])
        self.assertTrue(result['probe_container_cleaned'])


if __name__ == '__main__':
    unittest.main()
