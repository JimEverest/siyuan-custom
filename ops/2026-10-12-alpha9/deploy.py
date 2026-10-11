#!/usr/bin/env python3
"""One-copy SiYuan migration. Run as root on the VM; never print raw diagnostics.

prepare deliberately leaves the old kernel STOPPED until promote or rollback.
Notes, original inspect, manifests and configuration stay in this private stage.
No deletion of notes, backups, old containers, snapshots or failed migrations.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.request

STAGE = Path('/home/nl/siyuan-upgrade-20261012-alpha9')
ORIGINAL = Path('/opt/siyuan/workspace')
CANDIDATE = Path('/opt/siyuan/workspace-alpha9-candidate')
BACKUP = STAGE / 'workspace-before'
OLD_NAME = 'siyuan_custom'
OLD_RENAMED = 'siyuan_custom_before_20261012_alpha9'
CANDIDATE_NAME = 'siyuan_alpha9_candidate_20261012'
NETWORK = 'siyuan-alpha9-isolated-20261012'
SNAPSHOT = 'siyuan-custom-private:before-20261012-alpha9'
LABEL_KEY = 'siyuan.upgrade'
LABEL_VALUE = '20261012-alpha9'
RESERVE = 3 * 1024 ** 3
EXPECTED_OLD_IMAGE = 'sha256:1c95e0ccafe8617a8e8c2ef454d32abc11cdc86a74203b10063c1a2d22135995'
STATE = STAGE / 'deployment-state.json'
RECOVERY_ARMED = False
REQUIRED_CHECKS = ('version', 'authentication', 'renamed_endpoints',
                   'wrapper_compatibility', 'document_inventory',
                   'asset_hashes', 'appearance_preserved', 'source_review')
PRODUCTION_CHECKS = ('public_https', 'authentication', 'version', 'renamed_endpoints',
                     'wrapper_compatibility', 'document_inventory', 'asset_hashes',
                     'unrelated_services')
SYNC_RELEASE_PHASES = ('sync-release', 'sync-release-failed', 'finalized')


class SafeError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise SafeError(message)


def run(args, timeout=180, check=True):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SafeError('Command timed out: ' + args[0]) from None
    if check and result.returncode:
        # Docker stderr can include credentials and note filenames.
        raise SafeError('Command failed: ' + args[0] + ' (exit ' + str(result.returncode) + ')')
    return result


def docker(*args, timeout=180):
    return run(['docker', *args], timeout).stdout.decode('utf-8')


def inspect_container(name, missing_ok=False, size=False):
    args = ['docker', 'container', 'inspect'] + (['--size'] if size else []) + [name]
    result = run(args, check=False)
    if result.returncode:
        if missing_ok:
            return None
        raise SafeError('Required container not found')
    return json.loads(result.stdout)[0]


def write_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    fsync_dir(path.parent)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_state(state, phase=None):
    if phase:
        state['phase'] = phase
    state['updated_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    write_json(STATE, state)


def load_json(path):
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 ** 2), b''):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(root):
    info = root.stat()
    result = {'.': {'mode': info.st_mode, 'uid': info.st_uid, 'gid': info.st_gid,
                    'mtime_ns': info.st_mtime_ns, 'type': 'directory'}}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            rel = str(path.relative_to(root))
            info = path.lstat()
            item = {'mode': info.st_mode, 'uid': info.st_uid, 'gid': info.st_gid,
                    'mtime_ns': info.st_mtime_ns}
            if stat.S_ISLNK(info.st_mode):
                item.update(type='link', target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                item['type'] = 'directory'
            elif stat.S_ISREG(info.st_mode):
                item.update(type='file', bytes=info.st_size, sha256=file_hash(path))
            else:
                raise SafeError('Workspace contains an unsupported special file')
            result[rel] = item
    return result


def logical_bytes(root):
    total = 0
    for parent, _, files in os.walk(root, followlinks=False):
        for name in files:
            info = (Path(parent) / name).lstat()
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def isolated_symlinks(root):
    """Only relative links resolving within the copied tree remain isolated."""
    resolved_root = root.resolve()
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink():
                target = os.readlink(path)
                require(not os.path.isabs(target), 'Absolute workspace symlink prevents isolated testing')
                try:
                    resolved = path.resolve()
                except (OSError, RuntimeError):
                    raise SafeError('Unresolvable workspace symlink prevents isolated testing') from None
                require(resolved == resolved_root or resolved_root in resolved.parents,
                        'Workspace symlink escapes the isolated workspace')


def space_required(extra=0):
    require(shutil.disk_usage(ORIGINAL.parent).free >= RESERVE + extra,
            'Insufficient disk space; minimum 3 GiB reserve would be violated')


def verify_paths():
    for path in (STAGE, ORIGINAL, CANDIDATE, BACKUP):
        require(not path.is_symlink(), 'Refusing a symlink at a deployment path')
        require(path.parent.resolve() == path.parent,
                'Deployment path parent resolves through a symlink')
    require(STAGE.is_dir(), 'Private stage is unavailable')
    require(ORIGINAL.parent.stat().st_dev == STAGE.stat().st_dev,
            'Stage and workspace must be on the same filesystem')


def overlaps(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    return source == target or source in target.parents or target in source.parents


def no_other_writers(allowed_ids, paths):
    ids = docker('ps', '-q').split()
    for container_id in ids:
        info = inspect_container(container_id)
        if info['Id'] in allowed_ids:
            continue
        for mount in info.get('Mounts', []):
            if mount.get('RW') and any(overlaps(mount['Source'], p) for p in paths):
                raise SafeError('Another running container can write a deployment workspace')


def stopped(container_id):
    require(not inspect_container(container_id)['State']['Running'],
            'A kernel expected to be stopped is running')


def stop_clean(container_id):
    info = inspect_container(container_id)
    if info['State']['Running']:
        docker('stop', '--time', '180', container_id, timeout=210)
    info = inspect_container(container_id)
    require(not info['State']['Running'], 'Container did not stop')
    require(info['State'].get('ExitCode') != 137 and not info['State'].get('OOMKilled'),
            'Kernel did not stop cleanly; refusing an inconsistent backup')


def baseline_summary(excluded):
    result = {}
    for container_id in docker('ps', '-q').split():
        info = inspect_container(container_id)
        if info['Id'] not in excluded:
            result[info['Name']] = {'id': info['Id'], 'image': info['Image']}
    return result


def validate_original(info):
    require(info['Image'] == EXPECTED_OLD_IMAGE, 'Original image differs from audited baseline')
    require(info['State']['Running'], 'Original kernel must be running before prepare')
    mounts = info.get('Mounts', [])
    require(len(mounts) == 1 and mounts[0]['Type'] == 'bind'
            and mounts[0]['Source'] == str(ORIGINAL)
            and mounts[0]['Destination'] == '/siyuan/workspace' and mounts[0]['RW'],
            'Original workspace mount differs from audited baseline')
    hc = info['HostConfig']
    require(hc['NetworkMode'] in ('bridge', 'default'), 'Unexpected production network')
    require(hc['RestartPolicy']['Name'] == 'always', 'Unexpected original restart policy')
    require(hc.get('PortBindings') == {'6806/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '6806'}]},
            'Unexpected production port mapping')
    require(not any(hc.get(k) for k in ('Privileged', 'AutoRemove', 'Devices', 'DeviceRequests',
                                       'VolumesFrom', 'Links')),
            'Unsupported production container configuration')
    require(len(info['NetworkSettings']['Networks']) == 1, 'Unexpected additional production networks')


def create_container(original, state, candidate):
    cfg, hc = original['Config'], original['HostConfig']
    name = CANDIDATE_NAME if candidate else OLD_NAME
    env_file = STAGE / 'container-environment.env'
    env = cfg.get('Env') or []
    require(all('\n' not in item and '\r' not in item for item in env),
            'Multiline environment needs explicit handling')
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(env) + '\n')
    args = ['create', '--name', name, '--env-file', str(env_file),
            '--restart', 'no' if candidate else 'always',
            '--network', NETWORK if candidate else hc['NetworkMode'],
            '--mount', 'type=bind,source=' + str(CANDIDATE if candidate else ORIGINAL)
            + ',target=/siyuan/workspace',
            '--publish', '127.0.0.1:' + ('16806' if candidate else '6806') + ':6806',
            '--label', LABEL_KEY + '=' + LABEL_VALUE]
    for key, flag in (('User', '--user'), ('WorkingDir', '--workdir'), ('StopSignal', '--stop-signal')):
        if cfg.get(key):
            args += [flag, str(cfg[key])]
    if cfg.get('Hostname') and not candidate:
        args += ['--hostname', cfg['Hostname']]
    for key, value in (cfg.get('Labels') or {}).items():
        require(key != LABEL_KEY, 'Original container already has deployment label')
        args += ['--label', key + '=' + value]
    for key, flag in (('Memory', '--memory'), ('MemorySwap', '--memory-swap'),
                      ('NanoCpus', '--cpus'), ('CpuShares', '--cpu-shares'),
                      ('CpuPeriod', '--cpu-period'), ('CpuQuota', '--cpu-quota'),
                      ('PidsLimit', '--pids-limit'), ('ShmSize', '--shm-size')):
        value = hc.get(key)
        if value:
            args += [flag, str(value / 1e9 if key == 'NanoCpus' else value)]
    for key, flag in (('CpusetCpus', '--cpuset-cpus'), ('CpusetMems', '--cpuset-mems'),
                      ('IpcMode', '--ipc'), ('CgroupnsMode', '--cgroupns')):
        if hc.get(key):
            args += [flag, hc[key]]
    for key, flag in (('CapAdd', '--cap-add'), ('CapDrop', '--cap-drop'),
                      ('SecurityOpt', '--security-opt')):
        for value in hc.get(key) or []:
            args += [flag, value]
    for key, flag in (('Sysctls', '--sysctl'), ('Tmpfs', '--tmpfs')):
        for k, value in (hc.get(key) or {}).items():
            args += [flag, k + ('=' if key == 'Sysctls' else ':') + str(value)]
    for item in hc.get('Ulimits') or []:
        args += ['--ulimit', item['Name'] + '=' + str(item['Soft']) + ':' + str(item['Hard'])]
    for key, flag in (('ReadonlyRootfs', '--read-only'), ('Init', '--init'),
                      ('OomKillDisable', '--oom-kill-disable')):
        if hc.get(key):
            args += [flag]
    log = hc.get('LogConfig') or {}
    if log.get('Type'):
        args += ['--log-driver', log['Type']]
        for key, value in (log.get('Config') or {}).items():
            args += ['--log-opt', key + '=' + value]
    cmd = list(cfg.get('Cmd') or [])
    require(cmd and all(isinstance(v, str) for v in cmd), 'Original command is unavailable')
    if cmd[0] != 'serve':
        cmd.insert(0, 'serve')
    # Use the candidate image's own entrypoint, with the original CLI arguments.
    args += [state['image_id'], *cmd]
    return docker(*args).strip()


def version_check(port, workspace, expected, timeout=240):
    conf = load_json(workspace / 'conf' / 'conf.json')
    token = conf.get('api', {}).get('token', '')
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/api/system/version',
                                     headers={'Authorization': 'Token ' + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=10) as response:
                body = json.load(response)
                version = str(body.get('data', ''))
                if response.status == 200 and body.get('code') == 0 and version == expected:
                    return version
        except Exception:
            pass
        time.sleep(2)
    raise SafeError('Authenticated version check did not reach the expected version')


def set_sync_enabled(enabled):
    config = load_json(ORIGINAL / 'conf' / 'conf.json')
    token = config.get('api', {}).get('token', '')
    request = urllib.request.Request(
        'http://127.0.0.1:6806/api/sync/setSyncEnable',
        data=json.dumps({'enabled': enabled}).encode(), method='POST',
        headers={'Authorization': 'Token ' + token, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=30) as response:
            body = json.load(response)
            require(response.status == 200 and body.get('code') == 0,
                    'Sync enable API did not succeed')
    except SafeError:
        raise
    except Exception:
        raise SafeError('Sync enable API could not be confirmed') from None
    for _ in range(20):
        if load_json(ORIGINAL / 'conf' / 'conf.json').get('sync', {}).get('enabled') is enabled:
            return
        time.sleep(0.25)
    raise SafeError('Sync configuration persistence could not be confirmed')


def rename_directory(source, destination):
    require(source.is_dir() and not source.is_symlink(), 'Rename source is not a real directory')
    require(not destination.exists() and not destination.is_symlink(), 'Rename destination already exists')
    require(source.parent.stat().st_dev == destination.parent.stat().st_dev, 'Rename is not atomic across filesystems')
    os.rename(source, destination)
    fsync_dir(source.parent)
    if destination.parent != source.parent:
        fsync_dir(destination.parent)


def preserve_legacy_appearance(state):
    """Retain packages removed from the new bundled appearance distribution.

    Both containers are stopped/never started. Read actual image contents rather
    than assuming that formerly bundled icons (material/ant) still ship in alpha.9.
    Full copies and per-package manifests remain under the private stage.
    """
    before = STAGE / 'appearance-before'
    builtin = STAGE / 'appearance-candidate-builtin'
    require(not before.exists() and not before.is_symlink()
            and not builtin.exists() and not builtin.is_symlink(),
            'Appearance backup destination already exists')
    stopped(state['old_id'])
    stopped(state['candidate_id'])
    docker('cp', '--archive', state['old_id'] + ':/opt/siyuan/appearance', str(before), timeout=300)
    docker('cp', '--archive', state['candidate_id'] + ':/opt/siyuan/appearance', str(builtin), timeout=300)
    require(before.is_dir() and not before.is_symlink()
            and builtin.is_dir() and not builtin.is_symlink(), 'Appearance copy is not a real directory')
    isolated_symlinks(before)
    isolated_symlinks(builtin)
    before_manifest = STAGE / 'appearance-before-manifest.json'
    write_json(before_manifest, manifest(before))
    evidence = {'schema': 1, 'old_id': state['old_id'], 'candidate_id': state['candidate_id'],
                'image_id': state['image_id'],
                'appearance_backup_manifest_sha256': file_hash(before_manifest),
                'old_packages': {}, 'new_builtin_packages': {}, 'packages': []}
    for kind in ('icons', 'themes'):
        old_root, new_root = before / kind, builtin / kind
        require(old_root.is_dir() and not old_root.is_symlink()
                and new_root.is_dir() and not new_root.is_symlink(),
                'Appearance package root is missing or not a real directory')
        old_packages = {p.name for p in old_root.iterdir() if p.is_dir()}
        new_packages = {p.name for p in new_root.iterdir() if p.is_dir()}
        evidence['old_packages'][kind] = sorted(old_packages)
        evidence['new_builtin_packages'][kind] = sorted(new_packages)
        for name in sorted(old_packages - new_packages):
            source = old_root / name
            require(not source.is_symlink(), 'Legacy appearance package is a symlink')
            isolated_symlinks(source)
            target_root = CANDIDATE / 'data' / kind
            if not target_root.exists():
                target_root.mkdir(mode=0o755)
                parent_info = target_root.parent.stat()
                os.chown(target_root, parent_info.st_uid, parent_info.st_gid)
            require(target_root.is_dir() and not target_root.is_symlink(),
                    'Workspace appearance package root is not a real directory')
            target = target_root / name
            source_manifest = manifest(source)
            if target.exists() or target.is_symlink():
                require(target.is_dir() and not target.is_symlink(),
                        'Existing workspace appearance package is not a real directory')
                isolated_symlinks(target)
                status = 'already_present'
            else:
                run(['cp', '-a', '--reflink=never', '--', str(source), str(target)], timeout=300)
                require(manifest(target) == source_manifest,
                        'Legacy appearance package copy failed manifest verification')
                status = 'copied'
            target_manifest = manifest(target)
            evidence['packages'].append({'kind': kind, 'name': name, 'status': status,
                                         'source_manifest': source_manifest,
                                         'target_manifest': target_manifest})
    evidence_path = STAGE / 'appearance-preservation.json'
    write_json(evidence_path, evidence)
    state['appearance_preservation_sha256'] = file_hash(evidence_path)
    state['appearance_copied_count'] = sum(p['status'] == 'copied' for p in evidence['packages'])
    state['appearance_already_present_count'] = sum(p['status'] == 'already_present' for p in evidence['packages'])
    space_required()
    save_state(state)


def prepare(args):
    global RECOVERY_ARMED
    require(not STATE.exists(), 'Deployment state already exists; inspect it before retrying')
    require(ORIGINAL.is_dir() and not CANDIDATE.exists() and not BACKUP.exists(),
            'Workspace paths are not in a clean pre-upgrade state')
    require(inspect_container(CANDIDATE_NAME, True) is None
            and inspect_container(OLD_RENAMED, True) is None, 'A reserved container name is occupied')
    require(run(['docker', 'image', 'inspect', SNAPSHOT], check=False).returncode != 0,
            'Rollback snapshot tag already exists')
    require(run(['docker', 'network', 'inspect', NETWORK], check=False).returncode != 0,
            'Isolation network already exists')
    original = inspect_container(OLD_NAME, size=True)
    validate_original(original)
    image = json.loads(docker('image', 'inspect', args.image))[0]
    require(image.get('Os') == 'linux' and image.get('Architecture') == 'amd64',
            'Candidate image architecture is incorrect')
    require(image.get('Config', {}).get('Entrypoint') == ['/opt/siyuan/entrypoint.sh'],
            'Candidate entrypoint is unexpected')
    require(image['Id'] != original['Image'], 'Candidate image is identical to original')
    labels = image.get('Config', {}).get('Labels') or {}
    require(labels.get('io.siyuan.upstream.revision') == 'def127fc774ab2b5ab9a0096609a15aa5ba30df4'
            and labels.get('org.opencontainers.image.source') == 'https://github.com/JimEverest/siyuan-custom'
            and labels.get('org.opencontainers.image.version') == 'v3.8.7-alpha.9',
            'Candidate image provenance differs from the reviewed release')
    no_other_writers({original['Id']}, (ORIGINAL, CANDIDATE, BACKUP))
    isolated_symlinks(ORIGINAL)
    estimate = logical_bytes(ORIGINAL)
    snapshot_estimate = max(original.get('SizeRw') or 0, 1024 ** 3)
    space_required(estimate + snapshot_estimate + 512 * 1024 ** 2)
    write_json(STAGE / 'original-inspect.json', original)
    state = {'schema': 1, 'old_id': original['Id'], 'old_image_id': original['Image'],
             'operation_id': args.operation_id,
             'old_expected_version': '3.5.8',
             'image_id': image['Id'], 'image_reference': args.image,
             'expected_version': args.expected_version, 'original_bytes': estimate,
             'started_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'image_labels': labels,
             'unrelated_containers': baseline_summary({original['Id']})}
    save_state(state, 'preparing')
    RECOVERY_ARMED = True
    docker('update', '--restart', 'no', state['old_id'])
    state['downtime_started_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    save_state(state)
    stop_clean(state['old_id'])
    save_state(state, 'old-stopped')
    state['snapshot_id'] = docker('commit', '--pause=false', state['old_id'], SNAPSHOT,
                                  timeout=1200).strip()
    save_state(state, 'snapshot-created')
    space_required(estimate + 256 * 1024 ** 2)
    isolated_symlinks(ORIGINAL)
    baseline = manifest(ORIGINAL)
    write_json(STAGE / 'workspace-manifest-before.json', baseline)
    state['backup_manifest_sha256'] = file_hash(STAGE / 'workspace-manifest-before.json')
    state['backup_file_count'] = sum(v['type'] == 'file' for v in baseline.values())
    save_state(state, 'copying')
    run(['cp', '-a', '--reflink=never', '--', str(ORIGINAL), str(CANDIDATE)], timeout=3600)
    require(manifest(CANDIDATE) == baseline, 'Complete workspace copy failed manifest verification')
    space_required()
    write_json(STAGE / 'production-conf-before.json', load_json(ORIGINAL / 'conf' / 'conf.json'))
    target = CANDIDATE / 'conf' / 'conf.json'
    candidate_conf = load_json(target)
    require(isinstance(candidate_conf.get('sync'), dict), 'Missing original sync configuration')
    candidate_conf['sync']['enabled'] = False
    if isinstance(candidate_conf['sync'].get('lan'), dict):
        candidate_conf['sync']['lan']['enabled'] = False
    info = target.stat()
    write_json(target, candidate_conf)
    os.chown(target, info.st_uid, info.st_gid)
    os.chmod(target, stat.S_IMODE(info.st_mode))
    docker('network', 'create', '--internal', '--driver', 'bridge',
           '--label', LABEL_KEY + '=' + LABEL_VALUE, NETWORK)
    network = json.loads(docker('network', 'inspect', NETWORK))[0]
    require(network.get('Internal') is True, 'Candidate network permits external routing')
    state['candidate_id'] = create_container(original, state, candidate=True)
    save_state(state, 'candidate-created')
    preserve_legacy_appearance(state)
    stopped(state['old_id'])
    no_other_writers(set(), (CANDIDATE,))
    docker('start', state['candidate_id'])
    version_check(16806, CANDIDATE, state['expected_version'])
    save_state(state, 'prepared')
    return summary(state)


def summary(state):
    keys = ('phase', 'image_id', 'candidate_id', 'production_id', 'snapshot_id',
            'expected_version', 'backup_manifest_sha256', 'backup_file_count')
    return {key: state[key] for key in keys if key in state}


def verify(args):
    state = load_json(STATE)
    require(state['phase'] == 'prepared', 'Candidate is not ready for independent tests')
    stopped(state['old_id'])
    candidate = inspect_container(state['candidate_id'])
    require(candidate['State']['Running'] and candidate['Image'] == state['image_id'],
            'Candidate container no longer matches prepared state')
    require(candidate['HostConfig']['NetworkMode'] == NETWORK, 'Candidate network changed')
    require(json.loads(docker('network', 'inspect', NETWORK))[0].get('Internal') is True,
            'Candidate network is no longer isolated')
    version_check(16806, CANDIDATE, state['expected_version'], timeout=20)
    result = summary(state)
    result['required_evidence_checks'] = list(REQUIRED_CHECKS)
    result['independent_smoke_still_required'] = True
    return result


def promote(args):
    global RECOVERY_ARMED
    state = load_json(STATE)
    verify(args)
    evidence_path = Path(args.evidence)
    require(evidence_path.resolve().parent == STAGE.resolve() and not evidence_path.is_symlink(),
            'Review/test evidence must be a regular file in the private stage')
    evidence = load_json(evidence_path)
    require(evidence.get('result') == 'pass', 'Independent tests/review did not pass')
    for key in ('candidate_id', 'image_id', 'backup_manifest_sha256'):
        require(evidence.get(key) == state.get(key), 'Evidence does not match this candidate and backup')
    require(all(evidence.get('checks', {}).get(key) is True for key in REQUIRED_CHECKS),
            'Required independent review/test evidence is missing')
    require(file_hash(STAGE / 'workspace-manifest-before.json') == state['backup_manifest_sha256'],
            'Backup manifest has changed')
    state['evidence_sha256'] = file_hash(evidence_path)
    space_required()
    RECOVERY_ARMED = True
    stop_clean(state['candidate_id'])
    stopped(state['old_id'])
    no_other_writers(set(), (ORIGINAL, CANDIDATE, BACKUP))
    require(load_json(CANDIDATE / 'conf' / 'conf.json').get('sync', {}).get('enabled') is False,
            'Candidate sync was unexpectedly enabled')
    # Old production has stayed stopped. Verify its rollback data is still exact.
    require(manifest(ORIGINAL) == load_json(STAGE / 'workspace-manifest-before.json'),
            'Rollback workspace changed during candidate testing')
    save_state(state, 'promoting')
    rename_directory(ORIGINAL, BACKUP)
    save_state(state, 'original-moved')
    rename_directory(CANDIDATE, ORIGINAL)
    save_state(state, 'candidate-moved')
    docker('rename', state['old_id'], OLD_RENAMED)
    save_state(state, 'old-renamed')
    original = load_json(STAGE / 'original-inspect.json')
    state['production_id'] = create_container(original, state, candidate=False)
    save_state(state, 'production-created')
    stopped(state['old_id'])
    docker('start', state['production_id'])
    version_check(6806, ORIGINAL, state['expected_version'])
    state['production_ready_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    require(baseline_summary({state['production_id']}) == state['unrelated_containers'],
            'Unrelated running container inventory changed')
    space_required()
    save_state(state, 'promoted')
    return summary(state)


def finalize(args):
    state = load_json(STATE)
    require(state['phase'] == 'promoted', 'Production is not awaiting final validation')
    evidence_path = Path(args.evidence)
    require(evidence_path.resolve().parent == STAGE.resolve() and not evidence_path.is_symlink(),
            'Production evidence must be a regular file in the private stage')
    evidence = load_json(evidence_path)
    require(evidence.get('result') == 'pass', 'Production validation did not pass')
    for key in ('production_id', 'image_id', 'backup_manifest_sha256'):
        require(evidence.get(key) == state.get(key), 'Production evidence does not match this deployment')
    require(all(evidence.get('checks', {}).get(key) is True for key in PRODUCTION_CHECKS),
            'Required production validation evidence is missing')
    production = inspect_container(state['production_id'])
    require(production['State']['Running'] and production['Image'] == state['image_id'],
            'Production container changed before sync release')
    stopped(state['old_id'])
    require(baseline_summary({state['production_id']}) == state['unrelated_containers'],
            'Unrelated running container inventory changed')
    version_check(6806, ORIGINAL, state['expected_version'], timeout=30)
    old_sync = load_json(STAGE / 'production-conf-before.json').get('sync', {})
    old_lan = old_sync.get('lan') or {}
    require(old_lan.get('enabled') is not True,
            'Original LAN sync needs explicit restoration before finalize')
    enabled = old_sync.get('enabled')
    require(isinstance(enabled, bool), 'Original sync setting is not a boolean')
    require(load_json(ORIGINAL / 'conf' / 'conf.json').get('sync', {}).get('enabled') is False,
            'Production sync was unexpectedly enabled before release')
    state['production_evidence_sha256'] = file_hash(evidence_path)
    state['original_sync_enabled'] = enabled
    # From this point a timed-out response may already have enabled remote writes.
    # Local rollback cannot undo writes to S3/WebDAV and is deliberately blocked.
    save_state(state, 'sync-release')
    try:
        set_sync_enabled(enabled)
        state['sync_restored'] = True
        save_state(state, 'finalized')
    except (Exception, KeyboardInterrupt):
        try:
            set_sync_enabled(False)
            state['sync_disabled_after_failure'] = True
        except (Exception, KeyboardInterrupt):
            state['sync_disabled_after_failure'] = False
        save_state(state, 'sync-release-failed')
        raise SafeError('Sync release failed; new workspace preserved, old-version rollback blocked') from None
    return summary(state)


def rollback(args=None):
    state = load_json(STATE)
    require(state.get('phase') not in SYNC_RELEASE_PHASES,
            'Sync may have written remote storage; automatic old-workspace rollback is blocked')
    old = inspect_container(state['old_id'])
    if state.get('phase') == 'rolled-back':
        require(old['State']['Running'] and old['Name'] == '/' + OLD_NAME,
                'Previously restored kernel is no longer running under its original name')
        return summary(state)
    # Stop only containers created by this operation, including create-before-state failures.
    for name in (CANDIDATE_NAME, OLD_NAME):
        info = inspect_container(name, missing_ok=True)
        if info is None or info['Id'] == state['old_id']:
            continue
        require(info['Config'].get('Labels', {}).get(LABEL_KEY) == LABEL_VALUE,
                'Refusing to stop an unrelated container using a reserved name')
        docker('update', '--restart', 'no', info['Id'])
        if info['State']['Running']:
            docker('stop', '--time', '180', info['Id'], timeout=210)
        stopped(info['Id'])
        if name == OLD_NAME:
            failed_name = 'siyuan_custom_failed_' + str(time.time_ns())
            docker('rename', info['Id'], failed_name)
            state['failed_container_name'] = failed_name
    no_other_writers({state['old_id']}, (ORIGINAL, CANDIDATE, BACKUP))
    if BACKUP.exists():
        stopped(state['old_id'])
        if ORIGINAL.exists():
            failed_path = STAGE / ('workspace-failed-alpha9-' + str(time.time_ns()))
            rename_directory(ORIGINAL, failed_path)
            state['failed_workspace'] = str(failed_path)
        rename_directory(BACKUP, ORIGINAL)
    require(ORIGINAL.is_dir(), 'Original workspace is absent; manual recovery required')
    # The old container is never permitted to open a candidate-migrated workspace.
    if (STAGE / 'workspace-manifest-before.json').exists():
        require(manifest(ORIGINAL) == load_json(STAGE / 'workspace-manifest-before.json'),
                'Rollback workspace does not match the pre-upgrade manifest')
    old = inspect_container(state['old_id'])
    if old['Name'] != '/' + OLD_NAME:
        docker('rename', state['old_id'], OLD_NAME)
    docker('update', '--restart', 'always', state['old_id'])
    if not old['State']['Running']:
        docker('start', state['old_id'])
    require(inspect_container(state['old_id'])['State']['Running'], 'Original kernel failed to restart')
    version_check(6806, ORIGINAL, state.get('old_expected_version', '3.5.8'))
    save_state(state, 'rolled-back')
    return summary(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--image', required=True, help='Already pulled immutable candidate image reference')
    prep.add_argument('--operation-id', default=None, help='Optional owning preparation runner ID')
    prep.add_argument('--expected-version', default='3.8.7-alpha.9')
    sub.add_parser('verify')
    promote_parser = sub.add_parser('promote')
    promote_parser.add_argument('--evidence', required=True)
    final_parser = sub.add_parser('finalize')
    final_parser.add_argument('--evidence', required=True)
    sub.add_parser('rollback')
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    require(os.geteuid() == 0, 'Run this deployment driver as root')
    os.umask(0o077)
    STAGE.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not STAGE.is_symlink(), 'Private stage cannot be a symlink')
    os.chmod(STAGE, 0o700)
    verify_paths()
    with (STAGE / 'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = globals()[args.action](args)
        except (Exception, KeyboardInterrupt) as exc:
            message = str(exc) if isinstance(exc, SafeError) else type(exc).__name__
            result = {'action': args.action, 'success': False, 'error': message}
            if RECOVERY_ARMED and args.action in ('prepare', 'promote') and STATE.exists():
                try:
                    result['recovery'] = rollback()
                except (Exception, KeyboardInterrupt) as recovery_exc:
                    result['recovery_error'] = (str(recovery_exc) if isinstance(recovery_exc, SafeError)
                                                else type(recovery_exc).__name__)
            print(json.dumps(result, sort_keys=True), flush=True)
            return 1
        print(json.dumps({'success': True, **result}, sort_keys=True), flush=True)
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'success': False,
                          'error': str(exc) if isinstance(exc, SafeError) else type(exc).__name__}), flush=True)
        sys.exit(1)
