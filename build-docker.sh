#!/bin/bash
# 本地构建自定义思源笔记 Docker 镜像
# 使用方法：./build-docker.sh <version> <docker-username>

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES_DIR="${SCRIPT_DIR}/patches"

SIYUAN_VERSION="${1:-v3.1.18}"
DOCKER_USERNAME="${2:-ttqwer1}"
IMAGE_NAME="siyuan-custom"

echo "================================================="
echo "构建 ${DOCKER_USERNAME}/${IMAGE_NAME}:${SIYUAN_VERSION}"
echo "================================================="

if [ ! -d "$PATCHES_DIR" ]; then
    echo "错误：找不到 patches 目录 $PATCHES_DIR"
    exit 1
fi

WORKSPACE=$(mktemp -d)
echo "工作目录：$WORKSPACE"

echo "克隆 siyuan-note/siyuan@${SIYUAN_VERSION}..."
git clone --branch "$SIYUAN_VERSION" --depth=1 https://github.com/siyuan-note/siyuan.git "${WORKSPACE}/siyuan"
cd "${WORKSPACE}/siyuan"

echo "应用 patches..."
for patch in disable-update default-config mock-vip-user rename-endpoints; do
    patch_file="${PATCHES_DIR}/${patch}.patch"
    if [ -f "$patch_file" ]; then
        echo "  - ${patch}.patch"
        git apply "$patch_file" || {
            echo "    错误：${patch}.patch 应用失败"
            echo "    请检查 patch 是否与 ${SIYUAN_VERSION} 兼容"
            exit 1
        }
    fi
done

echo ""
git diff --stat

echo "================================================="
echo "构建 Docker 镜像..."
echo "================================================="

docker buildx build \
    --load \
    --platform linux/amd64 \
    -t "${DOCKER_USERNAME}/${IMAGE_NAME}:latest" \
    -t "${DOCKER_USERNAME}/${IMAGE_NAME}:${SIYUAN_VERSION}" \
    .

echo "================================================="
echo "完成！"
echo "================================================="
echo ""
echo "测试："
echo "  docker run -d --name siyuan-test -p 6806:6806 \\"
echo "    -v /tmp/siyuan-data:/siyuan/workspace \\"
echo "    ${DOCKER_USERNAME}/${IMAGE_NAME}:latest \\"
echo "    --workspace=/siyuan/workspace/ --accessAuthCode=test123"
echo ""
echo "验证："
echo "  curl -sw '%{http_code}' -o /dev/null -X POST http://localhost:6806/upload     # 期望 404"
echo "  curl -sw '%{http_code}' -o /dev/null -X POST http://localhost:6806/u_OCR      # 期望 401"
echo ""
echo "推送："
echo "  docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:latest"
echo "  docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:${SIYUAN_VERSION}"
echo ""
echo "清理：rm -rf $WORKSPACE"
