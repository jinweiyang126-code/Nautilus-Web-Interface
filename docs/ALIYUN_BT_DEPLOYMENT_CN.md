# 阿里云 ECS + 宝塔面板部署指南

本文说明如何在阿里云 ECS 上，用 Docker Compose + 宝塔 Nginx 反向代理部署 Nautilus Web Interface。

适用于当前生产环境约定：

| 项 | 值 |
|----|-----|
| 操作系统 | Alibaba Cloud Linux 3.2104 LTS 64 位 |
| Web 面板 | 宝塔（Nginx 由宝塔管理） |
| 访问域名 | `nt.abcyjw.me` |
| 公网入口 | 仅安全组放行 `80` / `443` |
| 容器端口 | 前端 `3000`、主 API `8000`、Admin API `8001`（本机监听） |

通用本地 / VPS 说明见 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)。

---

## 推荐架构

```text
浏览器
  │
  ▼
https://nt.abcyjw.me  (安全组 80/443)
  │
  ▼
宝塔 Nginx
  ├─ /        → http://127.0.0.1:3000   (frontend)
  ├─ /api/    → http://127.0.0.1:8000   (backend)
  └─ /ws      → http://127.0.0.1:8000   (WebSocket)
```

原则：

- 公网只暴露 `80` / `443`
- 容器端口只给本机 Nginx 用，**不要**在安全组放行 `3000` / `8000` / `8001`
- 不改宝塔以外的系统 Nginx 配置文件，避免和面板冲突

---

## 1. 前置条件

- ECS 已安装 Docker，且支持 `docker compose`（Compose v2）
- 已安装宝塔，站点由宝塔 Nginx 管理
- 域名 `nt.abcyjw.me` 可解析到该 ECS 公网 IP
- 安全组已放行：`22`、`80`、`443`
- 宿主机端口 **3000 / 8000 / 8001 未被占用**

部署前可自检：

```bash
docker --version
docker compose version

# 确认目标端口空闲（无输出或仅显示标题则基本可用）
ss -lntup | grep -E ':3000|:8000|:8001' || echo "ports free"

# 查看已有容器，避免冲突（例如 new-api 占用的是 3001，不影响本项目）
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
```

DNS：

```text
主机记录: nt
记录类型: A
记录值:   <ECS 公网 IP>
```

验证：

```bash
ping nt.abcyjw.me
```

---

## 2. 获取代码并配置环境变量

```bash
cd /root
git clone https://github.com/jinweiyang126-code/Nautilus-Web-Interface.git nautilus-web
cd /root/nautilus-web

cp .env.example .env
nano .env
```

`.env` 生产示例（域名按实际修改）：

```bash
# 安全（必改）
SECRET_KEY=<执行: openssl rand -hex 32>
ADMIN_PASSWORD=<至少 12 位强密码>
ENVIRONMENT=production

# CORS：必须与浏览器访问源一致（不要末尾斜杠）
CORS_ORIGINS=https://nt.abcyjw.me

# 前端构建期变量：走同源域名，由宝塔反代到后端
VITE_NAUTILUS_API_URL=https://nt.abcyjw.me
VITE_WS_URL=wss://nt.abcyjw.me
VITE_ADMIN_DB_API_URL=https://nt.abcyjw.me

# 可选：留空则使用 JWT 登录；若填写则前后端需一致
API_KEY=
ADMIN_API_KEY=

# 端口（与下文宝塔反代目标一致）
BACKEND_PORT=8000
ADMIN_PORT=8001
FRONTEND_PORT=3000

RATE_LIMIT_PER_MINUTE=200
LOGIN_RATE_LIMIT_PER_MINUTE=5
JWT_EXPIRE_HOURS=8
```

说明：

- `VITE_*` 在 **镜像构建时** 写入前端，改完后必须重新 `docker compose build` / `up --build`
- 关于是否修改 `docker-compose.yml`：默认端口未被占用时**不必修改**。若希望容器端口仅绑定本机回环地址（更安全），可将端口改为 `127.0.0.1:3000:80` 等形式；这是加固选项，不是功能必需

---

## 3. 启动 Docker 服务

```bash
cd /root/nautilus-web
docker compose up -d --build
docker compose ps
```

本机健康检查：

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8001/api/admin/health
curl -I http://127.0.0.1:3000
```

期望 API 返回类似：

```json
{"status":"healthy", ...}
```

常用运维命令：

```bash
# 日志
docker compose logs -f --tail=200

# 重启
docker compose restart

# 停止
docker compose down
```

---

## 4. 宝塔：添加站点

1. 登录宝塔面板（常见端口 `8888`）
2. **网站** → **添加站点**
3. 域名填写：`nt.abcyjw.me`
4. 根目录可保持默认（实际流量走反向代理）
5. PHP 版本选「纯静态」即可
6. 创建站点后进入该站点 → **SSL**
   - 申请 Let's Encrypt，或上传已有证书
   - 开启「强制 HTTPS」

---

## 5. 宝塔：配置反向代理

进入站点 → **反向代理** → **添加反向代理**。建议配置三条：

### 5.1 前端（默认）

| 项 | 值 |
|----|-----|
| 代理名称 | `nautilus-web` |
| 目标 URL | `http://127.0.0.1:3000`（**不要**写成 `3000/`，端口后不能有斜杠） |
| 发送域名 | `$host` |
| 代理目录 | `/` |

### 5.2 API

| 项 | 值 |
|----|-----|
| 代理名称 | `nautilus-api` |
| 目标 URL | `http://127.0.0.1:8000`（**不要**写成 `8000/`） |
| 发送域名 | `$host` |
| 代理目录 | `/api` |

打开该反代「配置文件」，确认是下面这种写法（保留 `/api` 前缀，否则登录会报 `Not Found`）：

```nginx
location ^~ /api
{
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
}
```

错误示例：`proxy_pass http://127.0.0.1:8000/;`（末尾有 `/`）会把 `/api/auth/login` 剥成 `/auth/login` → 404。

### 5.3 WebSocket

| 项 | 值 |
|----|-----|
| 代理名称 | `nautilus-ws` |
| 目标 URL | `http://127.0.0.1:8000` |
| 发送域名 | `$host` |
| 代理目录 | `/ws` |

为 `/ws` 打开配置文件，确认包含（没有则补充）：

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 86400;
```

注意：

- 更具体的路径（`/api`、`/ws`）应优先于 `/`，否则 API 会被错误转到前端
- 若宝塔界面里顺序不对，到站点「配置文件」中把 `/api`、`/ws` 的 `location` 放在 `/` 前面
- Admin DB API 默认监听本机 `8001`。当前 `.env` 将 `VITE_ADMIN_DB_API_URL` 设为同源域名时，如管理页访问异常，可在宝塔再增加一条反代（按实际前端请求路径调整），或临时把该变量改回 `http://127.0.0.1:8001` 仅用于本机调试（浏览器跨域需额外处理，生产仍建议同源反代）

---

## 6. 安全组与端口策略

| 端口 | 建议 |
|------|------|
| `80` / `443` | 允许（站点入口） |
| `22` | 允许（SSH，建议限制来源 IP） |
| `3000` / `8000` / `8001` | **不要**对公网放行；由宝塔反代访问 |
| 宝塔面板端口（如 `8888`） | 建议限制来源 IP |

本机已有其它服务示例（部署时仅作参考，勿冲突）：

- `3001`：`new-api` 容器
- `3333`：PM2 应用
- `8501`：Streamlit
- `80` / `443` / `888`：Nginx（宝塔）

---

## 7. 验证

```bash
curl -I https://nt.abcyjw.me
curl -s https://nt.abcyjw.me/api/health
```

浏览器打开：

- 站点：`https://nt.abcyjw.me`
- API 文档（若已反代到后端根路径相关路由）：以实际可访问路径为准；本机可先访问 `http://127.0.0.1:8000/docs`

---

## 8. 升级

```bash
cd /root/nautilus-web
git pull origin main
# 若改过 .env 中的 VITE_*，必须重新 build
docker compose up -d --build
docker compose ps
```

---

## 9. 常见问题

### Demo 回测 HTTP 500：`bar_type.aggregation_source ... was not equal to required source`

原因：Demo 用 `engine.add_data(bars)` 喂入已生成的 K 线时，BarType 必须是 `EXTERNAL`，不能是 `INTERNAL`。

仓库已修复为 `...-1-MINUTE-BID-EXTERNAL`。服务器更新：

```bash
cd /root/nautilus-web
git pull origin main
docker compose up -d --build backend
```

### 宝塔反代报错：`invalid port in upstream "127.0.0.1:3000/"`

目标 URL 端口后多了 `/`。应填：

```text
http://127.0.0.1:3000
http://127.0.0.1:8000
```

不要填 `http://127.0.0.1:3000/`。改完后保存，再重载 Nginx。

### 前端构建失败：`npm ci` / `ERESOLVE`（vite peer 依赖）

现象：`docker compose up -d --build` 在 `frontend` 的 `RUN npm ci` 失败，日志含 `ERESOLVE`、`vite@7` 与 `@builder.io/vite-plugin-jsx-loc` 冲突。

处理：确认 `frontend/Dockerfile` 使用：

```dockerfile
RUN npm ci --legacy-peer-deps
```

然后重新构建：

```bash
cd /root/nautilus-web
docker compose up -d --build
```

### 容器启动失败：端口已被占用

```bash
ss -lntup | grep -E ':3000|:8000|:8001'
```

若冲突：在 `.env` 中改 `FRONTEND_PORT` / `BACKEND_PORT` / `ADMIN_PORT`，并同步修改宝塔反代目标端口，然后重新 `docker compose up -d --build`。

### 浏览器 CORS 报错

1. 确认 `CORS_ORIGINS=https://nt.abcyjw.me`（无尾斜杠）
2. 确认前端构建使用的 `VITE_NAUTILUS_API_URL` 也是该域名
3. 修改后重新 build 并重启：`docker compose up -d --build`

### WebSocket 连不上

1. 确认使用 `wss://nt.abcyjw.me`（HTTPS 场景）
2. 确认宝塔 `/ws` 反代已配置 `Upgrade` / `Connection`
3. 安全组无需单独放行 WebSocket 端口

### 外网打不开，本机 curl 正常

1. 检查 DNS 是否指向当前 ECS
2. 检查安全组是否放行 `80` / `443`
3. 检查宝塔站点是否启用、SSL 是否正常、反代是否指向 `127.0.0.1:3000/8000`

### 与 `deploy_to_vps.sh` 的关系

`deploy_to_vps.sh` 面向 Ubuntu/Debian + 系统 Nginx/systemd，**不适用于**「Alibaba Cloud Linux + 宝塔」场景。本环境请按本文使用 Docker Compose + 宝塔反代。

---

## 10. 检查清单

- [ ] DNS：`nt.abcyjw.me` → ECS 公网 IP
- [ ] 安全组仅开放必要端口（含 `80` / `443`）
- [ ] `.env` 已设置强 `SECRET_KEY` / `ADMIN_PASSWORD`，`ENVIRONMENT=production`
- [ ] `CORS_ORIGINS` 与 `VITE_*` 指向 `https://nt.abcyjw.me` / `wss://nt.abcyjw.me`
- [ ] `docker compose up -d --build` 成功，本机 health 检查通过
- [ ] 宝塔站点 + SSL + `/` `/api` `/ws` 反代已配置
- [ ] `https://nt.abcyjw.me` 与 `/api/health` 可从公网访问

---

**版本**：与仓库 Docker Compose 部署一致 | **域名示例**：`nt.abcyjw.me` | **最后更新**：2026-03
