# 部署指南：Azure VM 上部署 siyuan-custom

## 架构概览

```
浏览器
  │
  │  https://obslinux.centralindia.cloudapp.azure.com:6807
  ▼
┌──────────────────────────────────────────┐
│  Azure VM (20.192.24.1)                  │
│                                          │
│  ┌─────────────────────────────────────┐ │
│  │ Caddy (port 443 + 6807)            │ │
│  │ - 自动 HTTPS (Let's Encrypt)       │ │
│  │ - 反向代理 + WebSocket             │ │
│  │ - 443  → 127.0.0.1:3001 (其他app) │ │
│  │ - 6807 → 127.0.0.1:6806 (思源)    │ │
│  └──────────────┬──────────────────────┘ │
│                 │                        │
│                 ▼                        │
│  ┌─────────────────────────────────────┐ │
│  │ Docker: siyuan-custom (port 6806)   │ │
│  │ - 仅监听 127.0.0.1                  │ │
│  │ - 数据持久化到宿主机                  │ │
│  └─────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

**访问地址：** `https://obslinux.centralindia.cloudapp.azure.com:6807`

> **关于 SSL：** VM 上已有 Caddy 作为反向代理运行。Caddy 自动处理 Let's Encrypt 证书的获取和续期，无需手动配置 certbot。
>
> **关于 IP 直接访问：** Let's Encrypt 不支持给 IP 地址签发证书。通过 IP 访问（`http://20.192.24.1:6807`）只能走 HTTP。建议统一使用域名访问。

---

## 第一步：Azure 网络安全组开放端口

在 Azure Portal 中为 VM 开放 6807 端口：

1. 打开 [Azure Portal](https://portal.azure.com)
2. 找到 VM `vm-B2als-2c4g` → **Networking** → **Network settings**
3. 点击 **Create port rule** → **Inbound port rule**
4. 填写：

   | 字段 | 值 |
   |---|---|
   | Source | Any |
   | Source port ranges | * |
   | Destination | Any |
   | Destination port ranges | **6807** |
   | Protocol | TCP |
   | Action | Allow |
   | Priority | 310（或其他未使用的数字） |
   | Name | Allow-Siyuan-6807 |

5. 点击 **Add**

---

## 第二步：SSH 登录 VM

```bash
ssh your-username@obslinux.centralindia.cloudapp.azure.com
```

以下所有命令都在 VM 上执行。

---

## 第三步：启动 siyuan-custom 容器

```bash
# 创建数据目录
sudo mkdir -p /opt/siyuan/workspace

# 启动容器（仅绑定 127.0.0.1，不对外暴露）
docker run -d \
  --name siyuan \
  --restart always \
  -p 127.0.0.1:6806:6806 \
  -v /opt/siyuan/workspace:/siyuan/workspace \
  ttqwer1/siyuan-custom:latest \
  --workspace=/siyuan/workspace/ \
  --accessAuthCode=你的访问密码

# 验证容器运行
docker ps | grep siyuan

# 验证本地可访问
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:6806
# 期望返回 200 或 302
```

> **`--accessAuthCode`** 是思源的访问密码，设置后浏览器访问时需要输入。请替换为你自己的强密码。

---

## 第四步：配置 Caddy 反向代理

VM 上已有 Caddy 在运行（监听 443 端口为其他应用提供服务）。只需在 Caddyfile 中追加 siyuan 配置。

编辑 Caddyfile：

```bash
sudo nano /etc/caddy/Caddyfile
```

在文件末尾追加以下内容：

```
obslinux.centralindia.cloudapp.azure.com:6807 {
    reverse_proxy 127.0.0.1:6806
}
```

完整的 Caddyfile 应该是：

```
obslinux.centralindia.cloudapp.azure.com {
    reverse_proxy 127.0.0.1:3001 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}

obslinux.centralindia.cloudapp.azure.com:6807 {
    reverse_proxy 127.0.0.1:6806
}
```

保存后重载 Caddy：

```bash
sudo systemctl reload caddy
```

> **SSL 证书：** Caddy 会自动为 6807 端口获取 Let's Encrypt 证书并自动续期，无需任何手动操作。

---

## 第五步：验证

### 检查 Caddy 状态

```bash
# Caddy 是否正常运行
sudo systemctl status caddy

# 6807 端口是否在监听
sudo ss -tlnp | grep 6807

# 查看 Caddy 日志（如果有问题）
sudo journalctl -u caddy --no-pager -n 50
```

### 命令行验证

```bash
# 测试 HTTPS 访问
curl -I https://obslinux.centralindia.cloudapp.azure.com:6807

# 检查 SSL 证书信息
echo | openssl s_client -connect obslinux.centralindia.cloudapp.azure.com:6807 2>/dev/null | openssl x509 -noout -subject -dates

# 验证旧端点已重命名（应返回 404）
curl -s -o /dev/null -w "%{http_code}" -X POST https://obslinux.centralindia.cloudapp.azure.com:6807/upload

# 验证新端点存在（应返回 401 或 403）
curl -s -o /dev/null -w "%{http_code}" -X POST https://obslinux.centralindia.cloudapp.azure.com:6807/u_OCR
```

### 浏览器验证

1. 访问 `https://obslinux.centralindia.cloudapp.azure.com:6807`
2. 确认浏览器地址栏显示锁头图标（SSL 有效）
3. 输入 accessAuthCode 登录
4. 创建文档，拖拽一张图片到编辑器中
5. 打开 DevTools → Network，确认图片上传请求发往 `/u_OCR`

---

## 日常维护

### 更新镜像

```bash
docker pull ttqwer1/siyuan-custom:latest
docker stop siyuan
docker rm siyuan
docker run -d \
  --name siyuan \
  --restart always \
  -p 127.0.0.1:6806:6806 \
  -v /opt/siyuan/workspace:/siyuan/workspace \
  ttqwer1/siyuan-custom:latest \
  --workspace=/siyuan/workspace/ \
  --accessAuthCode=你的访问密码
```

### 查看日志

```bash
# 思源容器日志
docker logs -f siyuan --tail 100

# Caddy 日志
sudo journalctl -u caddy -f --no-pager -n 100
```

### 备份数据

```bash
# 思源数据在宿主机上
sudo tar -czf siyuan-backup-$(date +%Y%m%d).tar.gz /opt/siyuan/workspace/
```

---

## 故障排查

| 问题 | 检查命令 | 可能原因 |
|---|---|---|
| 无法访问 6807 端口 | `sudo ss -tlnp \| grep 6807` | Azure NSG 未开放 / Caddy 未监听 |
| SSL 证书错误 | `sudo journalctl -u caddy \| grep tls` | Caddy 无法获取证书（检查域名 DNS 和 80 端口） |
| 502 Bad Gateway | `docker ps \| grep siyuan` | 容器未运行 / 端口未绑定 |
| WebSocket 连接失败 | `sudo journalctl -u caddy -f` | Caddy 默认支持 WebSocket，检查容器状态 |
| 上传文件失败 | `docker logs siyuan --tail 20` | 检查容器日志中的错误信息 |
| Caddy reload 失败 | `caddy validate --config /etc/caddy/Caddyfile` | Caddyfile 语法错误 |
