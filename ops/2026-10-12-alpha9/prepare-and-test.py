"""Detached preparation runner: only recover the deployment owned by this run."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid

STAGE = Path('/home/nl/siyuan-upgrade-20261012-alpha9')


def wait_for_workspace(token, timeout=900):
    headers = {'Authorization': 'Token ' + token, 'Content-Type': 'application/json'}
    requests = [urllib.request.Request('http://127.0.0.1:16806' + path, data=b'{}', headers=headers)
                for path in ('/api/system/bootProgress', '/api/notebook/lsNotebooks')]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while True:
        try:
            results = []
            for request in requests:
                with opener.open(request, timeout=20) as response:
                    if response.status != 200:
                        raise RuntimeError('Workspace readiness HTTP response failed')
                    results.append(json.load(response))
            boot, notebooks = results
            loaded = notebooks.get('data', {}).get('notebooks')
            if (boot.get('code') == 0 and boot.get('data', {}).get('progress') == 100
                    and notebooks.get('code') == 0 and isinstance(loaded, list) and len(loaded) >= 33):
                return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('workspace readiness timed out')
        time.sleep(2)


def run_preparation(image, stage=STAGE):
    os.umask(0o077)
    # Hold this independent lock across prepare AND all subsequent tests. The
    # deployment driver takes its own lock only for individual transitions.
    with (stage / 'preparation.lock').open('a') as runner_lock:
        fcntl.flock(runner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = stage / 'deployment-state.json'
        # Reject before entering recovery or overwriting the existing status.
        if state_path.exists():
            raise RuntimeError('Deployment state already exists; preparation was not started')
        operation_id = str(uuid.uuid4())
        status = {'phase': 'starting', 'started_at': time.time(), 'operation_id': operation_id}

        def save(phase):
            status['phase'] = phase
            status['updated_at'] = time.time()
            temp = stage / 'preparation-result.json.tmp'
            temp.write_text(json.dumps(status, indent=2))
            os.replace(temp, stage / 'preparation-result.json')

        def run(name, *args, timeout=1800):
            save(name)
            with (stage / (name + '.private.log')).open('wb') as log:
                result = subprocess.run(['python3', str(stage / name), *args],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            if result.returncode:
                raise RuntimeError(name + ' failed')

        try:
            run('deploy.py', 'prepare', '--image', image, '--operation-id', operation_id)
            save('waiting-for-workspace')
            config = json.loads(Path('/opt/siyuan/workspace-alpha9-candidate/conf/conf.json').read_text())
            wait_for_workspace(config['api']['token'])
            run('smoke.py', timeout=300)
            run('wrapper-smoke.py', timeout=240)
            run('verify-data.py', timeout=1800)
            status['passed'] = True
            save('candidate-verified')
            return 0
        except BaseException as error:
            status['passed'] = False
            status['error'] = type(error).__name__ + ': ' + str(error)
            try:
                owned = state_path.exists() and json.loads(state_path.read_text()).get('operation_id') == operation_id
            except (OSError, ValueError):
                owned = False
            if owned:
                try:
                    with (stage / 'automatic-rollback.private.log').open('wb') as log:
                        result = subprocess.run(['python3', str(stage / 'deploy.py'), 'rollback'],
                                                stdout=log, stderr=subprocess.STDOUT, timeout=1800)
                    status['rollback_exit_code'] = result.returncode
                except BaseException as recovery_error:
                    status['rollback_error'] = type(recovery_error).__name__
            else:
                status['rollback_skipped'] = 'deployment state is absent or belongs to another operation'
            save('failed')
            return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    return run_preparation(args.image)


if __name__ == '__main__':
    raise SystemExit(main())
