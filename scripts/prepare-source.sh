#!/usr/bin/env bash
# Prepare pinned upstream source and record every required patch before building.
set -euo pipefail

fail() { echo "ERROR: $*" >&2; exit 1; }
if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <version> <new-source-directory>" >&2
    exit 2
fi
VERSION="$1"
DESTINATION="$2"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]] || fail "invalid SiYuan version tag"
[[ -n "$DESTINATION" && ! -e "$DESTINATION" && ! -L "$DESTINATION" ]] || fail "source directory must not already exist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UPSTREAM_URL="https://github.com/siyuan-note/siyuan.git"
PATCH_NAMES=(disable-update default-config mock-vip-user rename-endpoints)
PATCH_RELATIVE="patches/${VERSION}"
if [[ ! -d "${REPOSITORY_DIR}/${PATCH_RELATIVE}" ]]; then
    [[ "$VERSION" == v3.5.8 ]] || fail "version-specific patches are required for ${VERSION}"
    PATCH_RELATIVE="patches"
fi
PATCH_DIR="${REPOSITORY_DIR}/${PATCH_RELATIVE}"
for name in "${PATCH_NAMES[@]}"; do
    [[ -s "${PATCH_DIR}/${name}.patch" ]] || fail "required patch is missing or empty: ${PATCH_RELATIVE}/${name}.patch"
done

RELEASE_FILE="${REPOSITORY_DIR}/releases/${VERSION}.json"
if [[ -f "$RELEASE_FILE" ]]; then
    # Python reads JSON from stdin, which also works with Windows Python in Git Bash.
    EXPECTED_SHA="$("${PYTHON_BIN:-python3}" -c '
import json, re, sys
release = json.load(sys.stdin)
assert release["version"] == sys.argv[1], "release version mismatch"
assert release["upstream_repository"] == sys.argv[2], "upstream repository mismatch"
assert release["patch_directory"] == sys.argv[3], "patch directory mismatch"
commit = release["upstream_commit"]
assert re.fullmatch(r"[0-9a-f]{40}", commit), "invalid pinned commit"
print(commit)
' "$VERSION" "$UPSTREAM_URL" "$PATCH_RELATIVE" < "$RELEASE_FILE")"
elif [[ "$VERSION" == v3.5.8 ]]; then
    EXPECTED_SHA="733725fd568ff8989a5249bda89c061123b52ffe"
else
    fail "pinned release manifest is required: releases/${VERSION}.json"
fi

CUSTOM_SHA="$(git -C "$REPOSITORY_DIR" rev-parse HEAD)"
CUSTOM_DIRTY=false
[[ -z "$(git -C "$REPOSITORY_DIR" status --porcelain)" ]] || CUSTOM_DIRTY=true

git init --quiet "$DESTINATION"
git -C "$DESTINATION" remote add origin "$UPSTREAM_URL"
git -C "$DESTINATION" -c core.autocrlf=false fetch --depth=1 origin "refs/tags/${VERSION}"
ACTUAL_SHA="$(git -C "$DESTINATION" rev-parse 'FETCH_HEAD^{commit}')"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || fail "upstream tag resolved to ${ACTUAL_SHA}, expected ${EXPECTED_SHA}"
git -C "$DESTINATION" -c core.autocrlf=false checkout --quiet --detach "$ACTUAL_SHA"
for name in "${PATCH_NAMES[@]}"; do
    echo "Applying ${PATCH_RELATIVE}/${name}.patch"
    git -C "$DESTINATION" apply --check "${PATCH_DIR}/${name}.patch"
    git -C "$DESTINATION" apply "${PATCH_DIR}/${name}.patch"
done
# Include any new files introduced by a patch in the checked/recorded diff.
git -C "$DESTINATION" add --intent-to-add .
git -C "$DESTINATION" diff --check
DIFF_SHA="$(git -C "$DESTINATION" diff --binary | sha256sum | cut -d ' ' -f 1)"
{
    cat <<EOF
{
  "version": "${VERSION}",
  "upstream_repository": "${UPSTREAM_URL}",
  "upstream_commit": "${ACTUAL_SHA}",
  "custom_revision": "${CUSTOM_SHA}",
  "custom_worktree_dirty": ${CUSTOM_DIRTY},
  "prepared_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "applied_diff_sha256": "${DIFF_SHA}",
  "patches": [
EOF
    separator=""
    for name in "${PATCH_NAMES[@]}"; do
        hash="$(sha256sum "${PATCH_DIR}/${name}.patch" | cut -d ' ' -f 1)"
        printf '%s    {"path": "%s/%s.patch", "sha256": "%s"}' "$separator" "$PATCH_RELATIVE" "$name" "$hash"
        separator=$',\n'
    done
    printf '\n  ]\n}\n'
} > "${DESTINATION}/source-manifest.json"
git -C "$DESTINATION" diff --stat
echo "Prepared ${VERSION} at ${ACTUAL_SHA}"
echo "Provenance: ${DESTINATION}/source-manifest.json"
