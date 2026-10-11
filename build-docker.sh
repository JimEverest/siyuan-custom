#!/usr/bin/env bash
# Build a local candidate; this script never publishes or updates :latest.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 <version> [docker-username]" >&2
    exit 2
fi
SIYUAN_VERSION="$1"
DOCKER_USERNAME="${2:-ttqwer1}"
if [[ ! "$SIYUAN_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]]; then
    echo "ERROR: invalid SiYuan version tag" >&2
    exit 2
fi
if [[ ! "$DOCKER_USERNAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo "ERROR: invalid Docker Hub username" >&2
    exit 2
fi
IMAGE_TAG="${IMAGE_TAG:-${SIYUAN_VERSION}-candidate-$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ ! "$IMAGE_TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ || "$IMAGE_TAG" == latest ]]; then
    echo "ERROR: IMAGE_TAG must be an explicit candidate tag, not latest" >&2
    exit 2
fi
IMAGE_REF="${DOCKER_USERNAME}/siyuan-custom:${IMAGE_TAG}"
WORKSPACE="$(mktemp -d -t siyuan-custom.XXXXXXXX)"
SOURCE_DIR="${WORKSPACE}/siyuan"
echo "Preparing ${SIYUAN_VERSION} in ${SOURCE_DIR}"
bash "${SCRIPT_DIR}/scripts/prepare-source.sh" "$SIYUAN_VERSION" "$SOURCE_DIR"

SOURCE_SHA="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
MANIFEST_SHA="$(sha256sum "${SOURCE_DIR}/source-manifest.json" | cut -d ' ' -f 1)"
docker buildx build --load --platform linux/amd64 \
    --label "org.opencontainers.image.source=https://github.com/JimEverest/siyuan-custom" \
    --label "org.opencontainers.image.version=${SIYUAN_VERSION}" \
    --label "io.siyuan.upstream.revision=${SOURCE_SHA}" \
    --label "io.siyuan.source-manifest.sha256=${MANIFEST_SHA}" \
    --metadata-file "${WORKSPACE}/build-metadata.json" \
    -t "$IMAGE_REF" "$SOURCE_DIR"

printf '%s\n' "$IMAGE_REF" > "${WORKSPACE}/image-reference.txt"
echo "Built candidate: ${IMAGE_REF}"
echo "Source/patch record: ${SOURCE_DIR}/source-manifest.json"
echo "Build metadata: ${WORKSPACE}/build-metadata.json"
echo "Publish this exact candidate when ready: docker push ${IMAGE_REF}"