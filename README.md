# siyuan-custom

基于 [思源笔记](https://github.com/siyuan-note/siyuan) 官方源码的自定义 Docker 镜像构建工具。

通过 patch 机制修改源码后构建镜像，无需维护完整的 fork 分支。

## 工作原理

```
克隆官方 siyuan 源码 → 应用 patch 文件 → 构建 Docker 镜像 → 推送到 Docker Hub
```

本仓库不包含思源笔记的源码，只包含 patch 文件和构建脚本。每次构建时从官方仓库拉取指定版本的源码，应用 patch 后编译。

## Patch 说明

| Patch 文件 | 作用 |
|---|---|
| `disable-update.patch` | 禁用自动更新检查，防止覆盖自定义版本 |
| `default-config.patch` | 修改默认配置：同步方式改为 S3、语言改为中文、关闭按钮改为最小化 |
| `mock-vip-user.patch` | 模拟 VIP 用户，免登录使用同步等功能 |
| `rename-endpoints.patch` | 重命名部分 API 端点，绕过防火墙关键词拦截 |

`rename-endpoints.patch` 的端点映射：

| 原端点 | 新端点 |
|---|---|
| `/upload` | `/u_OCR` |
| `/api/file/putFile` | `/api/file/p_ocr` |
| `/api/asset/upload` | `/api/asset/ass_ocr` |

## 使用镜像

```bash
docker pull docker_username/siyuan-custom:latest

docker run -d --name siyuan \
  -p 6806:6806 \
  -v /path/to/data:/siyuan/workspace \
  docker_username/siyuan-custom:latest \
  --workspace=/siyuan/workspace/ --accessAuthCode=your-password
```

浏览器访问 `http://localhost:6806` 即可使用。

## 构建方法

### GitHub Actions（推荐）

1. Fork 或使用本仓库
2. 在仓库 Settings → Secrets → Actions 中添加：
   - `DOCKER_HUB_USER` — Docker Hub 用户名
   - `DOCKER_HUB_PWD` — Docker Hub Access Token
3. 进入 Actions → **Release Docker Image** → Run workflow → 输入版本号（如 `v3.1.18`）

构建完成后镜像自动推送到 Docker Hub。

另外 **Auto Release** workflow 会在每周二和周五自动检测上游新版本，检测到后自动触发构建。

### 本地构建

```bash
git clone https://github.com/your-username/siyuan-custom.git
cd siyuan-custom

./build-docker.sh v3.1.18 docker_username
```

脚本会自动克隆官方源码、应用 patch、构建镜像。构建完成后按提示运行测试。

## Patch 兼容性

Patch 文件针对特定版本的源码生成。当上游版本更新导致代码变动时，patch 可能无法应用，构建会报错退出。

此时需要重新生成 patch：

```bash
# 克隆目标版本
git clone --branch <new-version> --depth=1 https://github.com/siyuan-note/siyuan.git
cd siyuan

# 手动修改对应文件，然后生成 patch
git diff > ../patches/rename-endpoints.patch
```

## 致谢

- [siyuan-note/siyuan](https://github.com/siyuan-note/siyuan) — 思源笔记官方仓库
- [appdev/siyuan-unlock](https://github.com/appdev/siyuan-unlock) — Patch 方案参考
