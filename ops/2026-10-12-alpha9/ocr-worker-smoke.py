"""Run one offline synthetic OCR inference on the isolated upgrade candidate.

Uses only a nonce image under container /tmp. Never reads workspace files or
changes OCR settings. The inner hard kill is 115 seconds, with an outer Docker
client timeout of 120 seconds. This verifies the native worker, not the HTTP API.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import time


CANDIDATE = 'siyuan_alpha9_candidate_20261012'
EXPECTED = 'SiYuan OCR 123'
RUNTIME = '/opt/siyuan/stage/ocr/runtime/linux-amd64'
MODEL = '/opt/siyuan/stage/ocr/models/tiny'
# A locally generated 500 x 100 white PNG, Arial 40 px, black synthetic text.
IMAGE_BASE64 = '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'
IMAGE_SHA256 = '4f0e882abebb33e2137edddc59e9de42f64fb2139c5649663f77953217bebc46'


class ProbeError(Exception):
    pass


def run_probe(container=CANDIDATE, runner=subprocess.run):
    started = time.monotonic()
    result = {'passed': False, 'expected_text': EXPECTED, 'text': '',
              'provider': 'paddleocr', 'model': 'tiny',
              'fixture_sha256': IMAGE_SHA256, 'runtime_seconds': None,
              'container_memory_before': None, 'container_memory_after': None,
              'synthetic_file_cleaned': True}
    cid, image_path, copy_attempted = None, None, False

    def command(args, timeout=15, data=None, error='docker_command_failed'):
        completed = runner(['docker', *args], input=data, capture_output=True,
                           timeout=timeout, check=False)
        if completed.returncode != 0:
            raise ProbeError(error)
        return completed.stdout

    def memory():
        try:
            value = command(['stats', '--no-stream', '--format', '{{.MemUsage}}', cid],
                            timeout=8).decode('utf-8').strip()
            return value[:100] or None
        except Exception:
            return None

    try:
        identity = command(['inspect', '--format',
                            '{{.Id}} {{.Image}} {{.State.Running}} {{.Name}}', container])
        fields = identity.decode('ascii').strip().split()
        if len(fields) != 4 or fields[2:] != ['true', '/' + CANDIDATE]:
            raise ProbeError('target_is_not_running_upgrade_candidate')
        cid, result['image_id'] = fields[:2]
        result['candidate_id'] = cid
        networks = json.loads(command(['inspect', '--format',
                                       '{{json .NetworkSettings.Networks}}', cid]))
        if len(networks) != 1:
            raise ProbeError('candidate_network_is_not_isolated')
        network_id = next(iter(networks.values()))['NetworkID']
        internal = command(['network', 'inspect', '--format', '{{.Internal}}', network_id])
        if internal.strip() != b'true':
            raise ProbeError('candidate_network_is_not_internal')
        mounts = json.loads(command(['inspect', '--format', '{{json .Mounts}}', cid]))
        if any(m['Destination'].rstrip('/') in ('', '/tmp') for m in mounts):
            raise ProbeError('container_tmp_is_a_mount')

        png = base64.b64decode(IMAGE_BASE64, validate=True)
        if not png.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(png).hexdigest() != IMAGE_SHA256:
            raise ProbeError('fixture_integrity_failed')
        name = 'siyuan-ocr-smoke-' + secrets.token_hex(16) + '.png'
        image_path = '/tmp/' + name
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.uid, entry.gid = len(png), 0o600, 0, 0
            tar.addfile(entry, io.BytesIO(png))
        copy_attempted = True
        result['synthetic_file_cleaned'] = False
        command(['cp', '-', cid + ':/tmp/'], data=archive.getvalue(), error='fixture_copy_failed')
        result['container_memory_before'] = memory()
        request = {'config': {'library': RUNTIME + '/libonnxruntime.so',
                              'worker': '', 'directory': MODEL,
                              'thresholds': {'detection': None, 'box': None, 'recognition': None}},
                   'image': image_path}
        inference_started = time.monotonic()
        try:
            output = command(['exec', '-i', cid, '/bin/busybox', 'timeout', '-s', 'KILL', '115',
                              RUNTIME + '/siyuan-ocr'], timeout=120,
                             data=json.dumps(request).encode('utf-8'), error='worker_failed')
        except subprocess.TimeoutExpired:
            raise ProbeError('worker_timeout') from None
        finally:
            result['runtime_seconds'] = round(time.monotonic() - inference_started, 3)
        rows = json.loads(output)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ProbeError('worker_returned_no_rows')
        texts = [row.get('text') for row in rows]
        if any(not isinstance(text, str) for text in texts):
            raise ProbeError('worker_returned_invalid_text')
        text = ' '.join(texts)
        result['text'] = text[:256]
        result['recognized_rows'] = len(rows)
        if EXPECTED not in ' '.join(text.split()):
            raise ProbeError('expected_synthetic_text_not_recognized')
        result['passed'] = True
    except ProbeError as error:
        result['failure'] = str(error)
    except subprocess.TimeoutExpired:
        result['failure'] = 'docker_operation_timeout'
    except Exception as error:
        # Never print Docker stderr, environment, a config, or an exception body.
        result['failure'] = 'probe_error_' + type(error).__name__
    finally:
        if copy_attempted:
            try:
                command(['exec', cid, '/bin/rm', '-f', image_path], timeout=15,
                        error='fixture_cleanup_failed')
                result['synthetic_file_cleaned'] = True
            except Exception:
                result['passed'] = False
                result['failure'] = 'fixture_cleanup_failed'
            result['container_memory_after'] = memory()
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default=CANDIDATE)
    parser.add_argument('--output', help='Optional private JSON evidence file')
    args = parser.parse_args()
    result = run_probe(args.container)
    if args.output:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(Path(args.output), flags, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(result, target, indent=2)
            target.write('\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
