# siyuan-custom

基于 [思源笔记](https://github.com/siyuan-note/siyuan) 官方源码的自定义 Docker 镜像构建工具。

通过 patch 机制修改源码后构建镜像，无需维护完整的 fork 分支。

## 工作原理

```
克隆官方 siyuan 指定版本源码 → 应用 patch 文件 → 构建 Docker 镜像 → 推送到 Docker Hub
```

本仓库不包含思源笔记的源码，只包含 patch 文件和构建脚本。每次构建时从 [siyuan-note/siyuan](https://github.com/siyuan-note/siyuan) 拉取指定版本的源码，应用 patch 后编译。

## Patch 说明

| Patch 文件 | 作用 |
|---|---|
| `disable-update.patch` | 禁用自动更新检查，防止覆盖自定义版本 |
| `default-config.patch` | 修改默认配置：同步方式改为 S3、语言改为中文、关闭按钮改为最小化 |
| `mock-vip-user.patch` | 模拟 VIP 用户，免登录使用同步等功能 |
| `rename-endpoints.patch` | 重命名部分 API 端点，绕过学校防火墙拦截 |

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

### 方式一：GitHub Actions 手动触发（推荐）

1. Fork 或使用本仓库
2. 添加 **Repository secrets**（不是 Environment secrets 或 Variables）：
   - 进入仓库页面 → **Settings** → 左侧栏 **Secrets and variables** → **Actions**
   - 点击 **New repository secret**，分别添加以下两个：

   | Name | Value |
   |---|---|
   | `DOCKER_HUB_USER` | 你的 Docker Hub 用户名 |
   | `DOCKER_HUB_PWD` | Docker Hub Access Token（在 https://hub.docker.com/settings/security 创建，不是登录密码） |

3. 进入 **Actions** → 左侧选择 **Release Docker Image** → **Run workflow**
4. 在 version 输入框填写思源官方版本号，如 `v3.5.8`
5. 点击绿色按钮运行

> **版本号说明：** 这里的 `v3.5.8` 是 [思源笔记官方仓库](https://github.com/siyuan-note/siyuan/releases) 的 release tag。构建流程会去官方仓库拉取该版本的完整源码，在此基础上应用 patch，然后编译并发布 Docker 镜像。你可以在官方的 [Releases 页面](https://github.com/siyuan-note/siyuan/releases) 查看所有可用版本。

### 方式二：自动检测上游新版本

`Auto Release` workflow 会定时检查思源官方仓库是否有新版本发布。如果检测到新版本，会自动创建 release 并触发 Docker 镜像构建。

**默认调度：** 每天 UTC 12:00（北京时间 20:00）运行一次。

调度频率通过 `.github/workflows/auto-release.yml` 中的 cron 表达式控制：

```yaml
on:
  schedule:
    - cron: "0 12 * * *"    # 每天 UTC 12:00
```

常用 cron 示例：

| 表达式 | 含义 |
|---|---|
| `0 12 * * *` | 每天 UTC 12:00（北京时间 20:00） |
| `0 12 * * 2,5` | 每周二和周五 UTC 12:00 |
| `0 */6 * * *` | 每 6 小时一次 |
| `0 0 * * 1` | 每周一 UTC 00:00 |

**手动测试：** 进入 Actions → 左侧选择 **Auto Release** → **Run workflow** → 点击运行。这会立即执行一次版本检测，如果上游有新版本就触发构建。

### 方式三：本地构建

```bash
git clone https://github.com/your-username/siyuan-custom.git
cd siyuan-custom

./build-docker.sh v3.5.8 docker_username
```

脚本会自动克隆官方源码、应用 patch、构建镜像。构建完成后按提示运行测试。

## Patch 兼容性

Patch 文件针对特定版本的源码生成（当前适配 **v3.5.8**）。当上游版本更新导致代码变动较大时，patch 可能无法应用，CI 构建会报错退出。

此时需要重新生成 patch：

```bash
# 1. 克隆目标版本
git clone --branch <new-version> --depth=1 https://github.com/siyuan-note/siyuan.git
cd siyuan

# 2. 手动修改对应文件

# 3. 生成 patch
git diff -- <修改的文件列表> > ../patches/<patch-name>.patch

# 4. 提交到本仓库，重新触发构建
```

## 致谢

- [siyuan-note/siyuan](https://github.com/siyuan-note/siyuan) — 思源笔记官方仓库
- [appdev/siyuan-unlock](https://github.com/appdev/siyuan-unlock) — Patch 方案参考
