"""Read-only candidate identity/storage/AI migration verification.

Pinned source: def127fc774ab2b5ab9a0096609a15aa5ba30df4.
The authenticated getConf response lives only in memory. Evidence contains only
booleans and counts, never configuration values, keys, endpoints or ciphertext.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import uuid

STAGE = Path('/home/nl/siyuan-upgrade-20261012-alpha9')
WORKSPACE = Path('/opt/siyuan/workspace-alpha9-candidate')
NODE_ID = re.compile(r'^[0-9]{14}-[a-z0-9]{7}$')
MISSING = object()


def equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    return type(a) is type(b) and a == b


def string(raw, key):
    return raw[key] if isinstance(raw.get(key), str) else ''


def number(raw, key):
    return raw[key] if type(raw.get(key)) in (int, float) else 0


def normalized_id(value, seed):
    # conf/ai.go:normalizedAIID, uuid.NewSHA1(NameSpaceOID, seed).
    return value if NODE_ID.fullmatch(value) else '20000101000000-' + uuid.uuid5(uuid.NAMESPACE_OID, seed).hex[:7]


def legacy_plain_key(value, trim=True):
    """Reproduce util/crypt.go + AI.DecryptAPIKeys without exposing either form."""
    value = value.strip() if trim else value
    if not value or not re.fullmatch(r'[0-9a-fA-F]+', value) or len(value) % 32:
        return value
    # Public format constants from upstream, not user credentials. Ciphertext
    # goes through stdin; captured plaintext/stderr never enter the evidence.
    result = subprocess.run(
        ['openssl', 'enc', '-d', '-aes-128-cbc', '-K', b'696D897C9AA0611B'.hex(),
         '-iv', b'RandomInitVector'.hex()], input=bytes.fromhex(value),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    if result.returncode:
        raise ValueError('legacy-key-decoding')
    value = bytes.fromhex(result.stdout.decode('ascii')).decode('utf-8')
    return value.strip() if trim else value


def expected_legacy_ai(old_ai):
    """Exact legacy branches used by conf/ai.go:MigrateAI and Normalize."""
    old_ai = old_ai or {}
    if not isinstance(old_ai, dict):
        raise ValueError('unsupported-ai-schema')
    legacy = 'openAI' in old_ai
    # A nonempty already-modern profile needs its own reviewed migration path.
    if not legacy and any(v not in (None, {}, []) for v in old_ai.values()):
        raise ValueError('unsupported-ai-schema')
    provider_sources = []
    embedding_source = None
    primary = old_ai.get('openAI') if legacy else None
    if isinstance(primary, dict):
        provider_sources.append((primary, [primary]))
    if legacy:
        for item in old_ai.get('providers') or []:
            if not isinstance(item, dict):
                raise ValueError('invalid-legacy-provider')
            if string(item, 'type') == 'embedding':
                embedding_source = item
                continue
            base = string(item, 'apiBaseURL')
            existing = next((entry for entry in provider_sources
                             if base and string(entry[0], 'apiBaseURL') == base), None)
            if existing is None:
                provider_sources.append((item, [item]))
            else:
                existing[1].append(item)
    providers = []
    for pi, (source, model_sources) in enumerate(provider_sources):
        pid = normalized_id(string(source, 'id'), 'provider:' + str(pi) + ':')
        timeout = int(number(source, 'apiTimeout'))
        provider = {'id': pid, 'enabled': True,
                    'apiKey': legacy_plain_key(string(source, 'apiKey')),
                    'baseURL': string(source, 'apiBaseURL').strip() or 'https://api.openai.com/v1',
                    'protocol': 'openai', 'requestTimeout': 120 if timeout < 1 else min(600, timeout),
                    'models': []}
        for mi, model in enumerate(model_sources):
            name = string(model, 'apiModel').strip() or 'model'
            provider['models'].append({
                'id': normalized_id(string(model, 'id'), 'model:' + pid + ':' + str(mi) + ':' + name),
                'displayName': string(model, 'name').strip(), 'name': name,
                'enabled': model.get('enabled') is not False})
        providers.append(provider)
    first_id = next((m['id'] for p in providers for m in p['models'] if m['enabled']), '')
    if isinstance(primary, dict):
        editing = {'modelId': first_id,
                   'maxHistoryMessages': max(1, min(64, int(number(primary, 'apiMaxContexts')))),
                   'temperature': max(0, min(2, number(primary, 'apiTemperature'))),
                   'maxCompletionTokens': max(0, int(number(primary, 'apiMaxTokens')))}
        agent = {'modelId': first_id,
                 'sessionTimeout': max(0, min(3600, int(number(primary, 'agentTimeout')))),
                 'confirmTimeout': int(number(primary, 'agentConfirmTimeout')),
                 'maxRetries': max(0, min(10, int(number(primary, 'agentMaxRetries')))),
                 'maxToolCallRounds': 64, 'streamIdleTimeout': 120,
                 'temperature': 0, 'maxCompletionTokens': 0}
    else:
        editing = {'modelId': first_id, 'maxHistoryMessages': 7,
                   'temperature': 1, 'maxCompletionTokens': 0}
        agent = {'modelId': first_id, 'sessionTimeout': 1800, 'confirmTimeout': 600,
                 'maxRetries': 3, 'maxToolCallRounds': 64, 'streamIdleTimeout': 120,
                 'temperature': 1, 'maxCompletionTokens': 0}
    embedding = {'enabled': False, 'apiKey': '', 'baseURL': '', 'name': '', 'timeout': 30}
    if embedding_source is not None:
        timeout = int(number(embedding_source, 'apiTimeout'))
        embedding.update(enabled=embedding_source.get('enabled') is True,
                         apiKey=legacy_plain_key(string(embedding_source, 'apiKey'), trim=False),
                         baseURL=string(embedding_source, 'apiBaseURL'),
                         name=string(embedding_source, 'apiModel'), timeout=timeout if timeout >= 1 else 30)
    mcp_sources = (old_ai.get('mcp') or {}).get('servers') or []
    if not isinstance(mcp_sources, list):
        raise ValueError('invalid-mcp-schema')
    return {'providers': providers, 'editing': editing, 'agent': agent,
            'embedding': embedding, 'mcp_sources': mcp_sources, 'legacy': legacy}


def verify(old, persisted, runtime):
    checks, counts = {}, {}
    def check(path, expected, actual):
        checks[path] = equal(expected, actual)
    def subset(path, expected, actual):
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                checks[path] = False
                return
            for key, value in expected.items():
                subset(path + '.' + key, value, actual.get(key, MISSING))
        else:
            check(path, expected, actual)
    for label, current in (('persisted', persisted), ('runtime', runtime)):
        for section, key in (('api', 'token'), ('repo', 'key'), ('system', 'id')):
            prior = old.get(section, {}).get(key, MISSING)
            check(label + '.' + section + '.' + key, prior, current.get(section, {}).get(key, MISSING))
            checks['baseline.' + section + '.' + key] = prior is not MISSING
        prior_layout = old.get('uiLayout')
        check(label + '.uiLayout', {} if prior_layout is None else prior_layout, current.get('uiLayout'))
        before_sync, now_sync = old.get('sync'), current.get('sync')
        checks[label + '.sync.present'] = isinstance(before_sync, dict) and isinstance(now_sync, dict)
        if checks[label + '.sync.present']:
            check(label + '.sync.enabled', False, now_sync.get('enabled'))
            for key, value in before_sync.items():
                if key not in ('enabled', 'synced', 'stat'):
                    subset(label + '.sync.' + key, value, now_sync.get(key, MISSING))
    try:
        expected = expected_legacy_ai(old.get('ai'))
        checks['ai.expected_migration_constructed'] = True
    except Exception:
        checks['ai.expected_migration_constructed'] = False
        return {'passed': False, 'checks': checks, 'counts': counts}
    ai = runtime.get('ai')
    checks['runtime.ai.present'] = isinstance(ai, dict)
    if not isinstance(ai, dict):
        return {'passed': False, 'checks': checks, 'counts': counts}
    providers = ai.get('providers')
    checks['ai.providers.count'] = isinstance(providers, list) and len(providers) == len(expected['providers'])
    counts['expected_providers'] = len(expected['providers'])
    counts['expected_models'] = sum(len(p['models']) for p in expected['providers'])
    if isinstance(providers, list):
        for pi, source in enumerate(expected['providers']):
            actual = providers[pi] if pi < len(providers) and isinstance(providers[pi], dict) else {}
            for key, value in source.items():
                if key != 'models':
                    check('ai.providers.' + str(pi) + '.' + key, value, actual.get(key, MISSING))
            models = actual.get('models') or []
            checks['ai.providers.' + str(pi) + '.models.count'] = isinstance(models, list) and len(models) == len(source['models'])
            for mi, model in enumerate(source['models']):
                target = models[mi] if isinstance(models, list) and mi < len(models) and isinstance(models[mi], dict) else {}
                for key, value in model.items():
                    check('ai.providers.' + str(pi) + '.models.' + str(mi) + '.' + key,
                          value, target.get(key, '' if key == 'displayName' else MISSING))
    for section in ('editing', 'agent', 'embedding'):
        subset('ai.' + section, expected[section], ai.get(section))
    servers = (ai.get('mcp') or {}).get('servers')
    sources = expected['mcp_sources']
    checks['ai.mcp.servers.count'] = isinstance(servers, list) and len(servers) == len(sources)
    counts['expected_mcp_servers'] = len(sources)
    # All original MCP server preferences are checked; only missing/duplicate
    # IDs undergo the exact deterministic source normalization.
    used_ids = set()
    if isinstance(servers, list):
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                checks['ai.mcp.source_schema'] = False
                continue
            actual = servers[index] if index < len(servers) and isinstance(servers[index], dict) else {}
            for key, value in source.items():
                if key != 'id':
                    check('ai.mcp.servers.' + str(index) + '.' + key, value, actual.get(key, MISSING))
            sid = string(source, 'id')
            if not sid or sid in used_ids:
                sid = str(uuid.uuid5(uuid.NAMESPACE_OID, 'mcp:' + str(index) + ':' + string(source, 'name') + ':' + string(source, 'type')))
            used_ids.add(sid)
            check('ai.mcp.servers.' + str(index) + '.id', sid, actual.get('id'))
    counts['checked_fields'] = len(checks)
    return {'passed': all(checks.values()), 'checks': checks, 'counts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, default=STAGE)
    parser.add_argument('--workspace', type=Path, default=WORKSPACE)
    parser.add_argument('--port', type=int, choices=(16806, 6806), default=16806)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        old = json.loads((args.stage / 'production-conf-before.json').read_text())
        persisted = json.loads((args.workspace / 'conf/conf.json').read_text())
        request = urllib.request.Request('http://127.0.0.1:' + str(args.port) + '/api/system/getConf',
                                        data=b'{}', headers={'Authorization': 'Token ' + old['api']['token'],
                                                          'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=60) as response:
            result = json.load(response)
            if response.status != 200 or result.get('code') != 0:
                raise ValueError('configuration-query-failed')
            report = verify(old, persisted, result['data']['conf'])
    except Exception:
        report = {'passed': False, 'checks': {'verification_execution': False}, 'counts': {}}
    os.umask(0o077)
    output = args.output or args.stage / 'config-identity-evidence.json'
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
    print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
