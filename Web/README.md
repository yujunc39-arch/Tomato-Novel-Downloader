# 多租户 Web 平台（Web）

把单机下载器升级为「一台服务器服务多用户」的网站系统。
基于 **FastAPI + SQLite + nginx**，为每个注册用户分配独立子域名、独立端口、独立实例。

---

## 架构

```
                         ┌──────────────────────────────────────────┐
   用户浏览器             │              同一台服务器                  │
                         │                                          │
  https://tomato.域名 ───┼─> nginx :443 ─> 网关 FastAPI :18080       │
   (主站/注册/登录)       │     │            ├─ 注册 / 登录 / 我的空间   │
                         │     │            ├─ 管理后台 /linjiuser      │
                         │     │            ├─ 用户空间文件 API         │
                         │     │            └─ 审计 / 公告 / 版本管理   │
                         │     │                                     │
  https://u5.域名 ───────┼─> nginx ──auth_request──> 网关校验 cookie   │
   (用户个人下载空间)      │     │                                     │
                         │     └─> 该用户的番茄实例 127.0.0.1:19004    │
                         │            (懒启动，空闲 20 分钟回收)        │
                         └──────────────────────────────────────────┘
```

### 分配规则（确定性绑定）

| 项 | 规则 |
|----|------|
| 子域名 | `u<N>`（N 为自增编号） |
| 端口 | `19000 + (N - 1)` |
| 数据目录 | `/opt/tomato-multi/data/users/u<N>` |

三者由数据库统一计算，**不会漂移**（历史 bug：曾用 uid 命名导致目录与子域名错位）。

---

## 目录

```
Web/
├── app/
│   ├── main.py            # FastAPI 网关：鉴权 / 用户 API / 管理后台 API / 页面路由
│   ├── db.py              # SQLite 数据层（用户、会话、密码版本）
│   ├── instances.py       # 实例管理：启动 / 探活 / 空闲回收 / 清理 / 生成 nginx 配置
│   ├── static/
│   │   ├── index.html     # 主站（注册/登录/我的空间）
│   │   └── announce.js    # 全站公告弹窗脚本（由 nginx sub_filter 注入）
│   └── adminstatic/
│       ├── admin.html     # 管理后台面板
│       └── login.html     # 后台专用登录页
├── deploy/
│   ├── tomato-gateway.service   # systemd 单元
│   ├── nginx-main.conf          # 主站 nginx 配置
│   ├── nginx-block.conf         # IP/未知域名兜底（444 掐断）
│   └── reload-nginx.sh          # 重新生成用户子域配置并 reload
├── install.sh             # 一键部署脚本
├── .env.example           # 配置模板
└── README.md
```

---

## 快速部署

### 前置条件

- 一台 Linux 服务器（Debian/Ubuntu 系），root 权限
- 一个域名，且 DNS 可添加 `tomato` 与 `*.tomato` 两条 A 记录（指向本机 IP）
- 开放 80 / 443 端口

### 一键安装

```bash
cd Web
cp .env.example .env
vi .env                       # 改 TOMATO_DOMAIN
sudo bash install.sh
```

安装完成后：

| 入口 | 地址 |
|------|------|
| 主站（用户注册/登录） | `https://tomato.你的域名` |
| 管理后台 | `https://tomato.你的域名/linjiuser` |
| 用户空间 | `https://u1.你的域名`… |

---

## 配置项（`.env`）

| 变量 | 必填 | 说明 |
|------|------|------|
| `TOMATO_DOMAIN` | ✅ | 主域名，例如 `tomato.example.com`。子域名会是 `u1.tomato.example.com` |
| `TOMATO_SECRET` | ⬜ | JWT 密钥。留空则安装时自动生成随机值 |
| `TOMATO_CERT` / `TOMATO_CERTKEY` | ⬜ | 证书路径，默认 `/etc/letsencrypt/live/<DOMAIN>/…` |

`.env` 权限应为 `600`，`systemd` 通过 `EnvironmentFile` 加载。

---

## 后端 API 概览

### 用户

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/register` | 注册 |
| POST | `/api/login` | 登录（下发跨子域 `token` cookie） |
| POST | `/api/logout` | 退出（吊销当前会话） |
| GET | `/api/me` | 当前用户信息 + 实例状态 |
| POST | `/api/me/start` | 启动个人实例 |
| POST | `/api/me/change_password` | 修改密码 |
| GET | `/api/auth_check` | 供 nginx `auth_request` 子请求鉴权 |
| GET | `/__announce__` | 公开读取当前公告 |

### 管理（需管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/users` | 用户列表（含磁盘用量 / 运行状态 / 书籍数） |
| POST | `/api/admin/register` | 新增用户（仅超管） |
| POST | `/api/admin/users/{uid}/ban` `/unban` `/delete` | 封禁 / 解封 / 删除 |
| POST | `/api/admin/users/{uid}/start` `/stop` | 开关实例 |
| POST | `/api/admin/stop_all` | 一键关闭所有实例（仅超管） |
| POST | `/api/admin/users/{uid}/set_password` | 重置密码（仅超管） |
| POST | `/api/admin/users/{uid}/admin` | 设置/取消管理员（仅超管） |
| GET | `/api/admin/user_files/{uid}` | 浏览用户空间 |
| GET | `/api/admin/users/{uid}/download` | 下载文件（文件夹自动打包 zip） |
| POST | `/api/admin/users/{uid}/delete_path` | 删除文件 / 文件夹 |
| GET/POST | `/api/admin/users/{uid}/file` | 读取 / 保存 `config.yml`（带语法校验 + `.bak` 备份） |
| GET | `/api/admin/downloads` | 软件下载统计 |
| GET | `/api/admin/announce` · POST `/api/admin/announce` `/announce/clear` | 公告读写 |
| GET | `/api/admin/tomato_version` | 当前版本 vs 最新版本 |
| POST | `/api/admin/tomato_update` | 从 GitHub 下载并替换番茄二进制（仅超管） |

---

## 权限模型

```
👑 超级管理员 (is_super)
   ├─ 增删所有用户（含其他管理员）
   ├─ 设置 / 取消管理员
   ├─ 封禁 / 解封 / 删除任意用户
   ├─ 重置任意用户密码、一键关闭所有实例、发布公告
   └─ 不能封禁 / 删除 / 降级自己

🛡 普通管理员 (is_admin)
   ├─ ✅ 只能管理普通用户（封禁 / 解封 / 删除 / 开关实例 / 用户空间）
   └─ ❌ 不能新增用户、不能操作其他管理员、不能操作自己

👤 普通用户
   └─ 只能用自己空间
```

---

## 维护要点

**实例生命周期**
- 用户点「启动」或访问自己子域名 → 懒启动实例（端口探测 + 串行化防重复拉起）
- 实例空闲 **20 分钟**（`instances.IDLE_TIMEOUT`，单位秒）→ 自动停止并清空 `books/` / `logs/`
- 每 60 秒扫描一次（`sweep_loop`）
- 判断实例是否存活：**端口可连 或 进程存在**（宽松判断，避免误杀）

**改配置后生效**
```bash
systemctl restart tomato-gateway      # 改了 main.py / db.py / instances.py
nginx -s reload                       # 只改了 nginx / 证书
```

**日志**
| 文件 | 内容 |
|------|------|
| `/opt/tomato-multi/app/run.log` | 网关运行日志 |
| `/opt/tomato-multi/app/data/audit.log` | 审计日志（敏感操作） |
| `/opt/tomato-multi/app/data/downloads.log` | 软件下载记录 |
| `每个用户 data/users/u<N>/logs/` | 该用户番茄实例日志 |

---

## 安全说明

- 全部 `/api/*` 走**默认拒绝**中间件（白名单外强制登录）
- SQL 全参数化；密码 bcrypt；JWT 校验签名 + 会话 sid + 密码版本号
- 退出 / 改密 / 封禁 → 立即吊销该用户全部会话
- 登录限流、注册限流、目录穿越防护、路径 `realpath` 校验
- nginx 兜底块：Host 不匹配的请求（IP 直连 / 未知域名）一律 `return 444`
- 关闭 `/docs` `/redoc` `/openapi.json`

---

## 🛡 真人校验（Cloudflare Turnstile）

**作用**：拦掉机器人与脚本批量注册/登录，访客进站先过一次真人验证。

**覆盖范围**（全部强制，**超级管理员豁免**）：

| 入口 | 说明 |
|------|------|
| 主站落地页 | 全屏验证门，通过后才显示登录/注册 |
| `POST /api/register` | 未验证 → `403` |
| `POST /api/login` | 未验证 → `403`（超管账号除外） |
| 用户子域名 `uN.域名` | `auth_check` 子请求同样校验，未通过 → 弹回主站验证 |

**通过后**下发一个 HMAC 签名 cookie（域级 `.DOMAIN`，24 小时有效），
所以**验证一次，主站和所有子域名都通行**。cookie 无法伪造（签名用 `TOMATO_SECRET` 校验）。

**超管豁免的实现**（只认「已登录的超管会话」，**不认用户名**）：
- `/api/human/status`：当前会话是超管 → 返回 `required:false`，前端**不弹验证门**
- `/api/auth_check`：超管访问子域名不校验
- `POST /api/login`：**不豁免** —— 全员一律先过验证
  （否则攻击者可用 `403` vs `401` 的差异探测出哪个账号是超管）

> 代价：超管换个新浏览器时要先过 1 次验证；之后只要保持登录，就不会再看到验证门。

**紧急安全阀**：万一 Cloudflare 在国内不可达，SSH 上服务器把 `.env` 里的
`TURNSTILE_SITEKEY` / `TURNSTILE_SECRET` 注释掉并重启网关，整站验证即关闭：

```bash
sed -i 's/^TURNSTILE_/#&/' /opt/tomato-multi/app/.env
systemctl restart tomato-gateway
```

**配置**（`.env`）：
```ini
TURNSTILE_SITEKEY=0x4AAAAAAA...   # 公开，前端用
TURNSTILE_SECRET=0x4AAAAAAA...    # 私密，服务端校验用
```
不配置这两项 = **整个功能自动关闭**，站点行为与之前完全一致。

**Cloudflare 侧准备**：
1. 打开 [Turnstile 控制台](https://dash.cloudflare.com/?to=/:account/turnstile)
2. Add site → 域名填 `tomato.你的域名`
3. Widget Mode 选 **Managed**，复制 **Site Key** 与 **Secret Key** 填入 `.env`

也可用 API 创建：
```bash
curl -X POST -H "Authorization: Bearer $CF_API_TOKEN" -H 'Content-Type: application/json' \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/challenges/widgets" \
  -d '{"name":"my-site","domains":["tomato.example.com"],"mode":"managed"}'
```

**坑位提醒**：
- 公告弹窗的 `z-index` 是 `2147483647`（int 最大值），会盖住验证门 →
  `announce.js` 里做了「等验证门通关再弹」的处理（轮询 `window.__HUMAN_GATE_PENDING__`）
- 更新 `announce.js` 后，把 nginx 注入 URL 的版本号 `?v=N` 加一，否则浏览器用旧缓存
- 验证组件依赖 `challenges.cloudflare.com`，国内网络可能偏慢

---

## 已知约束

- 主站/子域用 Let's Encrypt **泛域名证书**（`*.域名`），续期后需 `nginx -s reload`
- 登录限流是**进程内存态**，重启网关即清空
- 管理员无法「查看」密码，只能「重置」（bcrypt 不可逆）
- 实例清理**只针对已关闭的实例**，运行中的绝不动
