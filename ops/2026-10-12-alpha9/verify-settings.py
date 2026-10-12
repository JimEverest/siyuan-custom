"""Fail-closed preference comparison for the pinned alpha.9 migration.

Only field paths, rule identifiers, counts and whole-file hashes are emitted.
Appearance, API identity and storage credentials have separate stricter checks.
New schema fields are recorded; no pre-existing field is silently ignored.
"""
import argparse
import hashlib
import json
import os
import posixpath
from pathlib import Path, PurePosixPath

SECTIONS = ('editor', 'fileTree', 'export', 'keymap', 'search', 'graph',
            'flashcard', 'tag', 'snippet', 'repo', 'ocr')
REQUIRED = ('editor', 'fileTree', 'export', 'keymap')
MISSING = object()
NORMALIZE = 'kernel/conf/settings_normalize.go'


def same(a, b):
    # Python True == 1 must not make a changed JSON preference pass.
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    return type(a) is type(b) and a == b


def migration(path, before, old):
    """Return one exact expected value with its source, or fail closed."""
    numeric = type(before) in (int, float)
    rules = {
        'editor.fontSize': lambda v: v if 9 <= v <= 72 else 16,
        'editor.blockRefDynamicAnchorTextMaxLen': lambda v: min(5120, v if v >= 1 else 64),
        'editor.generateHistoryInterval': lambda v: min(1440, v),
        'editor.historyRetentionDays': lambda v: min(3650, v if v >= 1 else 30),
        'editor.floatWindowDelay': lambda v: max(0, min(2000, v)),
        'editor.cursorSurroundingLines': lambda v: max(0, min(20, v)),
        'editor.dynamicLoadBlocks': lambda v: max(48, v),
        'editor.backlinkExpandCount': lambda v: max(-1, v),
        'editor.backmentionExpandCount': lambda v: max(-1, v),
        'fileTree.maxListCount': lambda v: v if v >= 1 else 512,
        'fileTree.maxOpenTabCount': lambda v: min(32, v if v >= 1 else 8),
        'fileTree.docIconClickMode': lambda v: v if 0 <= v <= 1 else 0,
        'fileTree.parentDocTitleClickMode': lambda v: v if 0 <= v <= 2 else 0,
        'fileTree.largeFileWarningSize': lambda v: v if v >= 2 else 8,
        'fileTree.recentDocsMaxListCount': lambda v: max(32, min(256, v)),
        'search.limit': lambda v: max(32, v if v >= 1 else 64),
        'search.backlinkMentionKeywordsLimit': lambda v: v if v >= 1 else 512,
        'repo.syncIndexTiming': lambda v: max(12000, v),
        'repo.indexRetentionDays': lambda v: v if v >= 1 else 180,
        'repo.retentionIndexesDaily': lambda v: v if v >= 1 else 2,
    }
    if path in rules and numeric:
        return rules[path](before), NORMALIZE
    if path == 'export.blockRefMode' and before in (0, 1, 5) and numeric:
        return 4, NORMALIZE + ':MigrateExportBlockRefMode'
    if path == 'graph.maxBlocks' and numeric and (before < 1 or before == 10240):
        return 16384, 'kernel/conf/graph.go:NormalizeMaxBlocks'
    if path == 'ocr.model' and before == 'small':
        return 'tiny', NORMALIZE + ':NormalizeOCR'
    empty_defaults = {
        'editor.plantUMLServePath': 'https://www.plantuml.com/plantuml/svg/~1',
        'editor.katexMacros': '{}',
        'editor.headingNumberFormat': 'decimal-hierarchical',
    }
    if path in empty_defaults and before == '':
        return empty_defaults[path], NORMALIZE + ':NormalizeEditor'
    null_defaults = {'editor.floatWindowDelay': 620, 'editor.databaseAttrShow': True,
                     'editor.databaseAttrUseTabs': True, 'editor.checkBlockRef': True,
                     'fileTree.createDocAtTop': True, 'fileTree.boxDocEnabled': False,
                     'fileTree.useSVGDefaultIcon': False}
    if path in null_defaults and before is None:
        return null_defaults[path], NORMALIZE + ':null-default'
    if path == 'editor.spellcheckLanguages' and before in (None, []):
        return ['en-US'], NORMALIZE + ':NormalizeEditor'
    if path == 'editor.fontWeight' and numeric:
        editor = old.get('editor', {})
        if not editor.get('fontFamily') and not editor.get('fontFamilies'):
            return 400, 'kernel/conf/editor.go:NormalizeFontFamilies'
        if numeric and (before < 1 or before > 1000) and not editor.get('fontFamilies'):
            return 400, 'kernel/conf/editor.go:normalizeEditorFontFamilies'
    trim_paths = {'fileTree.docCreateSavePath', 'fileTree.refCreateSavePath',
                  'fileTree.shorthandSavePath', 'fileTree.docCreateTemplatePath'}
    if path in trim_paths and isinstance(before, str):
        value = '/'.join(part.strip() for part in before.split('/'))
        if value and path == 'fileTree.docCreateTemplatePath' and not value.endswith('.md'):
            value += '.md'
        if value and path in ('fileTree.docCreateTemplatePath', 'fileTree.shorthandSavePath'):
            value = '/' + value.lstrip('/') if not value.startswith('/') else value
        return value, 'kernel/util/path.go:NormalizeTemplatePath/TrimSpaceInPath'
    if path == 'export.pandocBin' and isinstance(before, str):
        value = PurePosixPath(posixpath.normpath(before))
        root = PurePosixPath('/siyuan/workspace/temp/pandoc')
        if value == root or root in value.parents:
            return '', 'kernel/model/pandoc_config.go:migratePandocConfig'
    # Complex template, font-list, emoji and FSRS transformations deliberately
    # remain unexplained unless unchanged: inspect actual differences first.
    return MISSING, None


def verify_preferences(old, new):
    report = {'passed': True, 'sections': [], 'unchanged_fields': 0,
              'allowed_migrations': [], 'new_schema_fields': [], 'unexpected_changes': []}

    def compare(path, before, after):
        if after is not MISSING and same(before, after):
            report['unchanged_fields'] += 1
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before):
                compare(path + '.' + key, before[key], after.get(key, MISSING))
            report['new_schema_fields'].extend(path + '.' + key for key in sorted(set(after) - set(before)))
            return
        expected, source = migration(path, before, old)
        if after is not MISSING and expected is not MISSING and same(expected, after):
            report['allowed_migrations'].append({'path': path, 'source': source})
            return
        report['unexpected_changes'].append({'path': path,
                                             'kind': 'missing' if after is MISSING else 'changed'})

    for section in SECTIONS:
        if section not in old:
            if section in REQUIRED:
                report['unexpected_changes'].append({'path': section, 'kind': 'missing-baseline'})
            continue
        report['sections'].append(section)
        compare(section, old[section], new.get(section, MISSING))
    report['passed'] = not report['unexpected_changes']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', required=True)
    parser.add_argument('--new', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        before, after = Path(args.old).read_bytes(), Path(args.new).read_bytes()
        report = verify_preferences(json.loads(before), json.loads(after))
        report['old_conf_sha256'] = hashlib.sha256(before).hexdigest()
        report['new_conf_sha256'] = hashlib.sha256(after).hexdigest()
    except Exception as error:
        # Never include exception text, which may contain source data.
        report = {'passed': False, 'error_type': type(error).__name__}
    os.umask(0o077)
    path = Path(args.output)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
    print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
