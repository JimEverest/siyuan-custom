"""Preparation ownership and concurrency regression tests; no VM access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False
    sys.modules['fcntl'] = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *args: None)

spec = importlib.util.spec_from_file_location('prepare_runner', Path(__file__).with_name('prepare-and-test.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class PrepareRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stage = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)

    def write_state(self, operation_id):
        (self.stage / 'deployment-state.json').write_text(json.dumps({
            'operation_id': operation_id, 'phase': 'prepared'}))

    def read_result(self):
        return json.loads((self.stage / 'preparation-result.json').read_text())

    def test_existing_state_does_not_run_or_rollback_or_overwrite_status(self):
        self.write_state('previous-operation')
        status = self.stage / 'preparation-result.json'
        status.write_text('existing status must survive')
        with patch.object(runner.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                runner.run_preparation('candidate-image', self.stage)
        run.assert_not_called()
        self.assertEqual(status.read_text(), 'existing status must survive')
        self.assertEqual(json.loads((self.stage / 'deployment-state.json').read_text())['operation_id'],
                         'previous-operation')

    def test_busy_runner_lock_does_not_run_or_create_state(self):
        with patch.object(runner.fcntl, 'flock', side_effect=BlockingIOError), \
                patch.object(runner.subprocess, 'run') as run:
            with self.assertRaises(BlockingIOError):
                runner.run_preparation('candidate-image', self.stage)
        run.assert_not_called()
        self.assertFalse((self.stage / 'preparation-result.json').exists())
        self.assertFalse((self.stage / 'deployment-state.json').exists())

    def test_foreign_state_created_during_failed_prepare_is_never_rolled_back(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            self.write_state('foreign-operation')
            return subprocess.CompletedProcess(command, 1)
        with patch.object(runner.subprocess, 'run', side_effect=run):
            self.assertEqual(runner.run_preparation('candidate-image', self.stage), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], 'prepare')
        self.assertIn('rollback_skipped', self.read_result())

    def test_failed_owned_prepare_rolls_back_only_its_operation(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[2] == 'prepare':
                operation_id = command[command.index('--operation-id') + 1]
                self.write_state(operation_id)
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(runner.subprocess, 'run', side_effect=run):
            self.assertEqual(runner.run_preparation('candidate-image', self.stage), 1)
        self.assertEqual([command[2] for command in calls], ['prepare', 'rollback'])
        result = self.read_result()
        self.assertEqual(result['rollback_exit_code'], 0)
        self.assertEqual(result['operation_id'], json.loads((self.stage / 'deployment-state.json').read_text())['operation_id'])

    def test_failure_before_state_creation_does_not_rollback(self):
        with patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(runner.run_preparation('candidate-image', self.stage), 1)
        self.assertEqual(run.call_count, 1)
        self.assertIn('rollback_skipped', self.read_result())

    @unittest.skipUnless(HAS_FLOCK, 'Real advisory-lock concurrency requires Linux/Unix fcntl')
    def test_second_live_runner_cannot_enter_preparation(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def run(command, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test synchronization timed out')
            return subprocess.CompletedProcess(command, 1)
        with patch.object(runner.subprocess, 'run', side_effect=run) as subprocess_run:
            first = threading.Thread(target=lambda: results.append(runner.run_preparation('candidate-image', self.stage)))
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(BlockingIOError):
                    runner.run_preparation('candidate-image', self.stage)
            finally:
                release.set()
                first.join(5)
            self.assertFalse(first.is_alive())
            self.assertEqual(results, [1])
            self.assertEqual(subprocess_run.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
