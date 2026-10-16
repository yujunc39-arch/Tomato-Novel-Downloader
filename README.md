# Tomato-Novel-Downloader · Android / Web 增强分支

> 本仓库是 [zhongbai2333/Tomato-Novel-Downloader](https://github.com/zhongbai2333/Tomato-Novel-Downloader) 的个人 **Fork**。
> 在上游 Rust 下载器的基础上，新增了两块自研内容：
>
> - 📱 **Android 客户端**（`Android/`）—— 把下载器的 Web UI 变成一个开箱即用的安卓 App
> - 🌐 **多租户 Web 平台**（`Web/`）—— 一台服务器托管多个用户，每人独立子域名 + 独立实例
>
> 上游的 Rust 核心（TUI / Web UI / CLI / 构建选项）保持原样，并持续跟随上游更新。

---

## 目录结构

```
.
├── Android/                  # 📱 安卓客户端（Kotlin + Jetpack Compose）
│   ├── TomatoWeb/            #    应用源码（App 名：Web For Tomato）
│   ├── android-env.sh        #    构建环境变量
│   └── gradle-init.gradle    #    阿里云镜像加速（国内构建用）
│
├── Web/                      # 🌐 多租户 Web 平台（FastAPI + nginx）
│   ├── app/                  #    网关后端 + 前端页面
│   │   ├── main.py           #      FastAPI 网关（注册/登录/管理后台 API）
│   │   ├── db.py             #      SQLite 数据层
│   │   ├── instances.py      #      用户实例管理（启动/探活/回收/nginx 刷新）
│   │   ├── static/           #      主站页面
│   │   └── adminstatic/      #      管理后台页面
│   ├── deploy/               #    systemd / nginx 配置
│   ├── install.sh            #    一键部署脚本
│   ├── .env.example          #    配置模板
│   └── README.md             #    平台详细文档
│
├── src/                      # 🦀 上游 Rust 源码（跟随上游，请勿在此改业务）
├── Cargo.toml                #    上游构建配置
├── installer.sh              #    上游 Termux / Linux 安装脚本
└── README.md
```

---

## 与上游的关系

| 内容 | 来源 | 说明 |
|------|------|------|
| `src/` `Cargo.*` `scripts/` `.github/workflows/` | **上游** | 原样保留，定期 `merge upstream/main` 同步 |
| `Android/` | **本 Fork 自研** | 安卓壳 + Web 服务二进制管理 |
| `Web/` | **本 Fork 自研** | 多租户平台，服务端托管 |
| 文档（`README.md` 等） | **本 Fork 重写** | 描述本 Fork 的完整结构 |

> 上游项目的原始说明（下载器本身的用法、Docker 镜像、`--server` / `--update` 参数、
> Edge TTS 有声书、CLI/TUI 说明等）依然适用，因为 `src/` 未做修改。
> 需要查上游细节时，请直接看 [上游仓库](https://github.com/zhongbai2333/Tomato-Novel-Downloader)。

---

## 一、Android 客户端：Web For Tomato

### 它是什么

一个「把下载器装进手机」的安卓应用。点击 **「获取 Web」** 后，它会：

1. 从上游 Release（或自定义地址）下载与设备架构匹配的 Web 服务二进制
2. 放进应用私有目录并赋予可执行权限（自动绕过 Android 10+ 的 `noexec` 限制）
3. 以 `--server` 模式启动
4. 在本机（`http://127.0.0.1:18423`）与局域网（`http://<手机IP>:18423`）暴露下载器的 Web UI

之后用手机浏览器或同局域网的电脑浏览器打开即可搜索、下载、管理书籍。

### 主要功能

- 📥 **一键获取 / 更新 Web 二进制**，自动识别 CPU 架构；支持加载**自定义二进制**
- 🖥 **访问地址展示**：本机地址 + 局域网地址，一键复制，附带二维码
- 🔌 **端口自定义**：默认 `18423`，范围 1024–65535，重启服务生效
- 📚 **书库管理**：扫描已下载书籍、预览、复制到任意目录、删除
- ⚙️ **前台常驻服务**（`WebServerService`），锁屏 / 切后台不中断，通知栏常驻
- 🛡️ **执行目录探测**：Android 10+ `filesDir` 被挂载为 `noexec`，应用会自动寻找可执行目录（并真实探测而非只看权限位）
- ℹ️ 关于页：版本、作者、参考项目跳转

### 技术栈

| 项 | 值 |
|----|-----|
| 包名 | `com.tomato.downloader` |
| 应用名 | Web For Tomato |
| 版本 | 2.1.8 (`versionCode 7795`) |
| minSdk / targetSdk / compileSdk | **23 / 28 / 35** |
| 语言 & UI | Kotlin + Jetpack Compose (Material 3) |
| 架构 | Navigation Compose + `StateFlow` |

> ⚠️ **`targetSdk = 28` 是刻意为之**：Android 10 (API 29) 起 `filesDir` 被施加 W^X（`noexec`）保护，
> 即使 `chmod +x` 也无法执行下载下来的原生二进制。降到 28 可获得该豁免。
> Termux 之所以能跑，是因为它把二进制放在自己的 `$PREFIX` 目录。

### 构建

```bash
source Android/android-env.sh        # 配置 JAVA_HOME / ANDROID_HOME
cd Android/TomatoWeb
./gradlew assembleRelease            # 或 assembleDebug
```

国内网络建议加 init script 走阿里云镜像：

```bash
./gradlew --init-script ../gradle-init.gradle assembleRelease
```

产物：`Android/TomatoWeb/app/build/outputs/apk/release/app-release.apk`

> 仓库内还提供了 `Android/TomatoWeb/dual_sign.py`，用于手动做 V1 + V2 双签名
> （先 zipalign，再 apksigner 签 V2，最后追加 JAR 签名，避免破坏 V2 块）。

### 已下载书籍的存放位置

应用内「已下载书籍」读取的是 Web 服务数据目录下的 `books/`。
在 Web UI 里下载完成的书籍会出现在这里，可直接预览 / 复制 / 删除。

---

## 二、多租户 Web 平台

### 它是什么

把「一个人跑一个下载器」升级为「一台服务器服务一群人」的网站系统：

```
用户浏览器 ──HTTPS──> nginx ──┬──> 主站网关 (FastAPI :18080)
                              │      ├─ 注册 / 登录 / 我的空间
                              │      ├─ 管理后台 (/linjiuser)
                              │      └─ 为每个用户懒启动独立番茄进程
                              │
                              └──> u<N>.<域名> ──> 该用户的番茄实例 (127.0.0.1:19000+)
                                                   (nginx 反代 + auth_request 鉴权)
```

每个注册用户自动获得：
- 一个专属子域名 `u<N>.<域名>`
- 一个专属端口 `19000 + (N-1)`
- 一个专属数据目录 `/opt/tomato-multi/data/users/u<N>`

### 核心功能

**面向用户**
- 自助注册 / 登录，自动分配子域名与实例
- 一键启动个人下载空间（下载器的 Web UI），启动后即可搜索 / 下载 / 管理书籍
- 修改自己的密码（改后所有旧会话立即失效）
- 忘记密码 → 联系管理员重置（无邮箱体系，bcrypt 不可逆）

**面向管理员**
- 用户管理：搜索 / 多维筛选（封禁状态、管理身份、实例状态）、封禁 / 解封 / 删除
- 实例管理：开 / 关单个实例、**一键关闭所有**、查看运行状态与启动时间
- 用户空间：浏览 / 下载（文件夹自动打包 zip）/ 删除 / **在线编辑 `config.yml`（带语法校验 + 自动备份）**
- 权限分级：👑 超级管理员 / 🛡 普通管理员 / 👤 普通用户
- 📢 全站弹窗公告（nginx `sub_filter` 注入，主站 + 所有用户子域生效）
- 📦 软件下载统计（次数 + IP 聚合）
- 🔄 番茄版本管理（一键从 GitHub 拉最新二进制并替换，带 `.bak` 备份）
- 🧾 审计日志（时间 / 来源 IP / 操作人 / 动作 / 详情）

**自动化**
- 实例**空闲 20 分钟**自动回收，并清空该用户的 `books/` 与 `logs/`
- 关闭实例 / 封禁 / 删除用户时自动清理数据
- 网关启动时清理遗留的孤儿实例，保证与数据库一致

**安全**
- 全局默认拒绝中间件：除白名单外所有 `/api/*` 强制登录
- SQL 全参数化、密码 bcrypt、JWT + `sessions` 表会话吊销
- 登录限流（同 IP+账号 5 次失败锁 5 分钟）、注册限流（每 IP 每小时 10 个）
- nginx 层子域名鉴权（`auth_request`）：未登录 / 越权自动 `302` 弹回主站
- IP 直连 / 未知域名（Host 不匹配）一律 `444` 掐断
- 关闭 `/docs`、`/redoc`、`/openapi.json`，隐藏接口结构

### 一键部署

```bash
cd Web
cp .env.example .env       # 填写 TOMATO_DOMAIN（和可选的 TOMATO_SECRET）
sudo bash install.sh
```

`install.sh` 会自动完成：安装依赖 → 部署代码 → 建 venv → 初始化数据库与管理员
→ 配置 systemd → 配置 nginx → 申请 Let's Encrypt 证书 → 启动服务。

详细说明见 **[Web/README.md](Web/README.md)**。

---

## 三、同步上游

本 Fork 只在上游基础上**新增**目录，理论上永远可以无冲突合并：

```bash
git clone https://github.com/yujunc39-arch/Tomato-Novel-Downloader.git
cd Tomato-Novel-Downloader
git remote add upstream https://github.com/zhongbai2333/Tomato-Novel-Downloader.git
git fetch upstream
git merge upstream/main          # 无冲突（本地改动只在 Android/ 与 Web/）
git push origin main
```

> 若未来双方同时改动了同一文件（例如上游也新增了 `Android/`），才需要手动解冲突。
> 因此**不要把业务代码写进上游目录**，避免 merge 冲突。

---

## 免责声明

- 本程序仅供 **Rust 网络爬虫技术、网页数据处理及相关研究** 的学习用途。
- 请勿用于任何违反法律法规或侵犯他人权益的活动；下载到的小说仅供自行阅读。
- 请勿将第三方 API 转载使用，也请勿把本程序用于批量滥用。
- 使用本程序的用户需自行承担由此引发的任何法律责任和风险。程序作者及贡献者不对
  因使用本程序所造成的任何损失、损害或法律后果负责。

---

## 致谢

- 上游项目：[zhongbai2333/Tomato-Novel-Downloader](https://github.com/zhongbai2333/Tomato-Novel-Downloader)（Rust 下载器核心）
- 上游作者：[zhongbai2333](https://github.com/zhongbai2333) 及所有贡献者
- 前期基础项目：Dimily
- API 支持：[@helloplhm-qwq](https://github.com/helloplhm-qwq)、QQ 用户 @终忆、[@jingluopro](https://github.com/jingluopro)

---

## License

[MIT](LICENSE) · 上游版权归 zhongbai2333 所有，本 Fork 的新增部分同样以 MIT 发布。
