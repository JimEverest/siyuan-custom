"""Offline synthetic OCR proof for the exact image in the private pull receipt.

Reads only the image receipt and synthetic fixture source. No workspace, mounts,
kernel, credentials or application settings are used. Docker client timeout is
120 seconds; the in-container worker is killed after 115 seconds.
"""
import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import time

STAGE = Path('/home/nl/siyuan-upgrade-20261012-alpha9')
IMAGE_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
CONTAINER_ID = re.compile(r'^[0-9a-f]{64}$')
UPSTREAM_SHA = 'def127fc774ab2b5ab9a0096609a15aa5ba30df4'
FIXTURE_SHA = '4f0e882abebb33e2137edddc59e9de42f64fb2139c5649663f77953217bebc46'
EXPECTED = 'SiYuan OCR 123'
RUNTIME = '/opt/siyuan/stage/ocr/runtime/linux-amd64'
MODEL = '/opt/siyuan/stage/ocr/models/tiny'
OWNER_LABEL = 'io.siyuan.upgrade.image-ocr-probe'


class ProbeError(Exception):
    pass


def fixture(stage):
    # Parse a literal constant without importing/executing another script.
    tree = ast.parse((stage / 'ocr-worker-smoke.py').read_text(encoding='utf-8'))
    images = [ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and any(
                  isinstance(target, ast.Name) and target.id == 'IMAGE_BASE64'
                  for target in node.targets)]
    if len(images) != 1 or not isinstance(images[0], str):
        raise ProbeError('fixture_constant_missing')
    raw = base64.b64decode(images[0], validate=True)
    if not raw.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(raw).hexdigest() != FIXTURE_SHA:
        raise ProbeError('fixture_integrity_failed')
    return base64.b64encode(raw).decode('ascii')


def run_probe(stage=STAGE, runner=subprocess.run, memory_mib=768):
    started = time.monotonic()
    nonce = secrets.token_hex(16)
    name = 'siyuan-image-ocr-' + nonce
    result = {'passed': False, 'image_id': None, 'fixture_sha256': FIXTURE_SHA,
              'network_disabled': False, 'no_mounts': False,
              'synthetic_text_recognized': False, 'probe_container_cleaned': True}
    run_attempted = False

    def command(args, timeout=15, data=None, failure='docker_operation_failed'):
        completed = runner(['docker', *args], input=data, capture_output=True,
                           timeout=timeout, check=False)
        if completed.returncode != 0:
            raise ProbeError(failure)
        return completed.stdout

    def cleanup():
        # The unguessable ownership label is necessary but not sufficient:
        # inspect and validate name + image before touching the exact ID.
        ids = command(['ps', '--all', '--quiet', '--no-trunc', '--filter',
                       'label=' + OWNER_LABEL + '=' + nonce]).decode('ascii').split()
        for cid in ids:
            if not CONTAINER_ID.fullmatch(cid):
                raise ProbeError('cleanup_identity_invalid')
            rows = json.loads(command(['container', 'inspect', cid]))
            if len(rows) != 1:
                raise ProbeError('cleanup_identity_invalid')
            actual = rows[0]
            if (actual['Id'] != cid or actual['Name'] != '/' + name or
                    actual['Image'] != result['image_id'] or
                    actual['Config'].get('Labels', {}).get(OWNER_LABEL) != nonce):
                raise ProbeError('cleanup_identity_mismatch')
            command(['container', 'rm', '--force', cid],
                    failure='probe_container_cleanup_failed')
        remaining = command(['ps', '--all', '--quiet', '--no-trunc', '--filter',
                             'label=' + OWNER_LABEL + '=' + nonce])
        if remaining.strip():
            raise ProbeError('probe_container_cleanup_failed')

    try:
        if memory_mib < 256 or memory_mib > 1024:
            raise ProbeError('invalid_memory_limit')
        receipt = json.loads((stage / 'image-before-deploy.json').read_text(encoding='utf-8'))
        image_id = receipt.get('image_id', '')
        if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
            raise ProbeError('receipt_image_id_invalid')
        result['image_id'] = image_id
        inspected = json.loads(command(['image', 'inspect', image_id]))
        if len(inspected) != 1:
            raise ProbeError('image_inspection_invalid')
        image = inspected[0]
        labels = image['Config'].get('Labels') or {}
        if (image['Id'] != image_id or image['Os'] != 'linux' or image['Architecture'] != 'amd64' or
                labels.get('io.siyuan.upstream.revision') != UPSTREAM_SHA or
                labels.get('org.opencontainers.image.version') != 'v3.8.7-alpha.9'):
            raise ProbeError('image_identity_mismatch')
        # Even Dockerfile-declared anonymous volumes are rejected.
        if image['Config'].get('Volumes'):
            raise ProbeError('image_declares_mounts')
        encoded_png = fixture(stage)
        png_path = '/tmp/siyuan-synthetic-ocr.png'
        # Base64 has no shell metacharacters. All other shell text is fixed.
        shell = ("umask 077; printf '%s' '" + encoded_png +
                 "' | /bin/busybox base64 -d > " + png_path +
                 " || exit 91; exec /bin/busybox timeout -s KILL 115 " + RUNTIME + '/siyuan-ocr')
        request = {'config': {'library': RUNTIME + '/libonnxruntime.so', 'worker': '',
                              'directory': MODEL,
                              'thresholds': {'detection': None, 'box': None, 'recognition': None}},
                   'image': png_path}
        result['network_disabled'] = True
        result['no_mounts'] = True
        result['memory_limit_mib'] = memory_mib
        run_attempted = True
        result['probe_container_cleaned'] = False
        output = command(['run', '--rm', '--pull', 'never', '--interactive', '--network', 'none', '--name', name,
                          '--label', OWNER_LABEL + '=' + nonce, '--user', '0:0', '--workdir', '/tmp',
                          '--memory', str(memory_mib) + 'm', '--memory-swap', str(memory_mib) + 'm',
                          '--cpus', '1', '--pids-limit', '128', '--entrypoint', '/bin/sh',
                          image_id, '-c', shell], timeout=120,
                         data=json.dumps(request).encode('utf-8'), failure='worker_failed')
        rows = json.loads(output)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ProbeError('worker_returned_no_rows')
        texts = [row.get('text') for row in rows]
        if any(not isinstance(value, str) for value in texts):
            raise ProbeError('worker_returned_invalid_text')
        if EXPECTED not in ' '.join(' '.join(texts).split()):
            raise ProbeError('expected_synthetic_text_not_recognized')
        result['synthetic_text_recognized'] = True
        result['passed'] = True
    except ProbeError as error:
        result['failure'] = str(error)
    except subprocess.TimeoutExpired:
        result['failure'] = 'probe_timeout'
    except KeyboardInterrupt:
        result['failure'] = 'probe_interrupted'
    except Exception as error:
        # No stdout/stderr or exception contents (which may contain shell text).
        result['failure'] = 'probe_error_' + type(error).__name__
    finally:
        if run_attempted:
            try:
                cleanup()
                result['probe_container_cleaned'] = True
            except Exception:
                result['passed'] = False
                result['failure'] = 'probe_container_cleanup_failed'
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, default=STAGE)
    parser.add_argument('--memory-mib', type=int, default=768)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.stage.is_dir() or args.stage.is_symlink():
        raise SystemExit('Private stage directory is invalid')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    os.umask(0o077)
    result = run_probe(args.stage, memory_mib=args.memory_mib)
    output = args.output or args.stage / 'image-ocr-evidence.json'
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as target:
        json.dump(result, target, indent=2)
        target.write('\n')
    print(json.dumps(result), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
