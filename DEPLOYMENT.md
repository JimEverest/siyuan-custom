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
│  │ Nginx (port 6807)                   │ │
│  │ - SSL termination (Let's Encrypt)   │ │
│  │ - 反向代理 + WebSocket              │ │
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

> **关于 IP 直接访问：** Let's Encrypt 不支持给 IP 地址签发 SSL 证书。通过 IP 访问（`http://20.192.24.1:6807`）只能走 HTTP，浏览器会提示不安全。建议统一使用域名访问。

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

> 如果后续需要 certbot HTTP-01 验证（首次获取证书），还需要临时开放 **80** 端口。如果 80 端口已开放可跳过。

---

## 第二步：SSH 登录 VM

```bash
ssh your-username@obslinux.centralindia.cloudapp.azure.com
```

以下所有命令都在 VM 上执行。

---

## 第三步：检查环境

```bash
# 检查 Docker
docker --version

# 检查 Nginx 是否已安装
nginx -v 2>&1 || echo "Nginx 未安装"

# 检查 Nginx 是否在运行
systemctl status nginx 2>/dev/null || echo "Nginx 未运行"

# 检查 6806 和 6807 端口是否被占用
ss -tlnp | grep -E '6806|6807'
```

---

## 第四步：安装 Nginx（如果未安装）

```bash
sudo apt update
sudo apt install -y nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

---

## 第五步：启动 siyuan-custom 容器

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

## 第六步：配置 Nginx 反向代理

创建 Nginx 配置文件：

```bash
sudo tee /etc/nginx/sites-available/siyuan <<'EOF'
# 思源笔记 - 反向代理配置
# 监听 6807 端口，代理到本地 siyuan 容器

server {
    listen 6807 ssl http2;
    listen [::]:6807 ssl http2;
    server_name obslinux.centralindia.cloudapp.azure.com;

    # SSL 证书路径（certbot 会自动填充，先用占位）
    ssl_certificate     /etc/letsencrypt/live/obslinux.centralindia.cloudapp.azure.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/obslinux.centralindia.cloudapp.azure.com/privkey.pem;

    # SSL 安全配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # 反向代理到 siyuan 容器
    location / {
        proxy_pass http://127.0.0.1:6806;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 支持（思源笔记需要）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 300s;

        # 上传文件大小限制（思源可能上传较大附件）
        client_max_body_size 128m;
    }
}
EOF
```

先不启用这个配置（等获取 SSL 证书后再启用）。

---

## 第七步：获取 SSL 证书

### 安装 Certbot

```bash
sudo apt install -y certbot
```

### 获取证书

使用 standalone 模式获取证书（临时占用 80 端口）：

```bash
# 如果 nginx 正在占用 80 端口，先临时停止
sudo systemctl stop nginx

# 获取证书
sudo certbot certonly \
  --standalone \
  -d obslinux.centralindia.cloudapp.azure.com \
  --non-interactive \
  --agree-tos \
  --email your-email@example.com

# 重新启动 nginx
sudo systemctl start nginx
```

> **替换 `your-email@example.com`** 为你的实际邮箱，Let's Encrypt 会在证书即将过期时发邮件提醒。

验证证书已生成：

```bash
sudo ls /etc/letsencrypt/live/obslinux.centralindia.cloudapp.azure.com/
# 应该看到 fullchain.pem  privkey.pem  chain.pem  cert.pem
```

---

## 第八步：启用 Nginx 配置

```bash
# 启用站点配置
sudo ln -sf /etc/nginx/sites-available/siyuan /etc/nginx/sites-enabled/siyuan

# 测试配置语法
sudo nginx -t

# 重新加载 nginx
sudo systemctl reload nginx
```

如果 `nginx -t` 报错，检查证书路径是否正确。

---

## 第九步：配置证书自动续期

Let's Encrypt 证书有效期 90 天。Certbot 安装时通常会自动创建定时任务，但需要确认：

```bash
# 检查自动续期定时任务是否存在
systemctl list-timers | grep certbot

# 如果没有，手动添加 cron
sudo crontab -e
# 添加以下行（每天凌晨 3 点检查续期）：
# 0 3 * * * certbot renew --quiet --deploy-hook "systemctl reload nginx"
```

测试续期流程（不会真正续期，只是模拟）：

```bash
sudo certbot renew --dry-run
```

---

## 第十步：验证

### 命令行验证

```bash
# 在 VM 上测试
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
# 思源日志
docker logs -f siyuan --tail 100

# Nginx 访问日志
sudo tail -f /var/log/nginx/access.log

# Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
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
| 无法访问 6807 端口 | `ss -tlnp \| grep 6807` | Azure NSG 未开放 / Nginx 未启动 |
| SSL 证书错误 | `sudo certbot certificates` | 证书过期 / 路径错误 |
| 502 Bad Gateway | `docker ps \| grep siyuan` | 容器未运行 / 端口未绑定 |
| WebSocket 连接失败 | 检查 Nginx 配置中的 Upgrade 头 | 缺少 WebSocket 代理配置 |
| 上传文件失败 | 检查 Nginx `client_max_body_size` | 文件超过大小限制 |
