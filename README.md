# siyuan-custom

基于 [思源笔记](https://github.com/siyuan-note/siyuan) 官方源码的自定义 Docker 镜像构建工具。

通过 patch 机制修改源码后构建镜像，无需维护完整的 fork 分支。

## 已完成升级与维护入口

2026-10-12 已将 Azure 上的定制版从 3.5.8 升级至 **3.8.7-alpha.9**。后续维护者和 Agent 请先阅读：

- **[详细中文复盘与后续 Agent 操作指南](docs/upgrade-retrospective-3.5.8-to-3.8.7-alpha9.zh-CN.md)**：决策依据、实际步骤、构建／迁移踩坑、验证方法、回滚边界和下一次升级清单。
- [最终部署报告](docs/deployment-alpha9-20261012.md)与[脱敏验收证据](docs/deployment-alpha9-evidence.json)。
- [运维过程说明](docs/operations-alpha9.md)与[历史操作脚本](ops/2026-10-12-alpha9/)。

本次脚本含固定现场路径、镜像和轮次断言，不能直接重放为下一次升级。下方旧版镜像运行示例及 `DEPLOYMENT.md` 的历史参数，不应代替现网核验；生产升级需要完整数据保护、固定镜像和分阶段验收。

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

### v3.8.7-alpha.9 候选构建

`releases/v3.8.7-alpha.9.json` 固定上游提交
`def127fc774ab2b5ab9a0096609a15aa5ba30df4`，对应补丁位于
`patches/v3.8.7-alpha.9/`。原始 `patches/*.patch` 继续用于 v3.5.8。

新版采用分节配置和 API 契约；补丁同步调整路由、契约、生成类型、插件调用及上传并发处理，
保留三个定制上传地址和原有鉴权。新建工作区的语言默认值为 `zh-CN`；已有用户配置由上游迁移，
不会强制覆盖。免登录账户只用于本地和自托管功能，不提供官方云服务账户。

本地构建现在必须明确传版本；alpha.9 的五份必需补丁（四份业务补丁及一份构建补丁）缺失，或上游提交不匹配，都会立即失败：

```bash
bash build-docker.sh v3.8.7-alpha.9 ttqwer1
```

`Release Docker Image` 工作流默认构建 linux/amd64，运行 API 契约检查和定向 Go 测试，
再生成含 run ID 的候选镜像。准备期间不覆盖 `latest`；生产应使用验证过的 digest。
构建 artifact 保存源提交、补丁 SHA256、完整修改摘要和镜像构建元数据。
此 alpha 版本额外包含 `docker-build.patch`，补齐官方 Docker 前端构建阶段缺少的农历数据文件，
安装 ONNX Runtime 的 Eigen 依赖构建需要的 `patch` 工具，并应用 Alpine 官方维护的
`execinfo.h` 兼容补丁。后者仅调整诊断回溯代码的 glibc 条件，不改变 OCR 识别逻辑。
共享库构建明确关闭原本不会运行的 ONNX 单元测试目标，避免 musl 数学函数声明与测试模板不兼容；
应用自身的定向测试与部署验收仍然保留。

v3.8.7-alpha.9 是预发布版，启动命令须包含 `serve`。
升级前必须保留旧容器的可写层和停止写入后的完整工作区，包括 `conf`、`data`、`repo`、
`history`、`temp`。新版会迁移配置与主题目录，回退时应恢复配套的旧工作区，不能只换旧镜像。
本次具体流程和结果见[中文复盘](docs/upgrade-retrospective-3.5.8-to-3.8.7-alpha9.zh-CN.md)与[部署报告](docs/deployment-alpha9-20261012.md)。原实施计划保留为历史材料；未来仍需分别验证源码、构建和生产部署。

Patch 文件针对特定版本的源码生成：根目录补丁用于 **v3.5.8**，`patches/v3.8.7-alpha.9/` 用于本次 alpha.9。其他新版本需准备对应补丁目录和固定源码 manifest；上游变化可能导致 patch 或语义不再兼容，不能只更换版本号。

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
