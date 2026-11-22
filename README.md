# Tomato-Novel-Downloader · Android 增强分支

> 本仓库是 [zhongbai2333/Tomato-Novel-Downloader](https://github.com/zhongbai2333/Tomato-Novel-Downloader) 的个人 **Fork**，
> 只做一件事：**给上游下载器做一个开箱即用的安卓客户端**。
>
> - 📱 **自研部分仅 `Android/` 一个目录** —— 把下载器的 Web UI 变成手机 App
> - 🦀 `src/` `Cargo.*` `scripts/` `.github/` 保持上游原样，定期跟随上游更新
> - 解析、抓取、下载、格式转换全部由上游实现，本仓库**没有改动一行**下载逻辑
>
> 🌐 多租户 Web 平台（一台服务器服务多用户）已迁至独立仓库：
> **[Tomato-Web](https://github.com/yujunc39-arch/Tomato-Web)**

---

## 目录结构

```
.
├── Android/                  # 📱 本 Fork 唯一自研目录
│   └── TomatoWeb/            #    Flutter 应用（App 名：Web For Tomato）
│       ├── lib/              #      Dart 源码（3.x 全部 UI 与逻辑）
│       ├── android/          #      安卓壳工程（Gradle / Manifest / 签名）
│       ├── native_src/       #      沿用 2.x 的 Kotlin 服务管理实现
│       ├── packages/prism_ui/#      自研 UI 组件库（FlClash 设计语言）
│       ├── assets/           #      图标等资源
│       └── pubspec.yaml      #      版本号 3.x
│
├── src/                      # 🦀 上游 Rust 源码（请勿在此改业务）
├── third-party/              #    上游第三方许可证
├── Cargo.toml / Cargo.lock   #    上游构建配置
├── installer.sh              #    上游 Termux / Linux 安装脚本
├── scripts/                  #    上游维护脚本
└── README.md
```

---

## 与上游的关系

| 内容 | 来源 | 说明 |
|------|------|------|
| `src/` `Cargo.*` `scripts/` `.github/workflows/` | **上游** | 原样保留，定期 `merge upstream/main` 同步 |
| `Android/` | **本 Fork 自研** | 安卓壳 + 二进制管理 + 前台服务 |
| 文档（`README.md` / `MAINTAINING.md`） | **本 Fork 重写** | 只描述安卓客户端的用法与维护 |

> 上游项目的原始说明（下载器本身的用法、Docker 镜像、`--server` / `--update` 参数、
> Edge TTS 有声书、CLI/TUI 说明等）依然适用，因为 `src/` 未做修改。
> 需要查上游细节时，请直接看 [上游仓库](https://github.com/zhongbai2333/Tomato-Novel-Downloader)。

**下载内核归上游所有** —— 本 App 做的事情就是「帮你把上游的二进制下载到手机上并跑起来」，
任何解析失败、站点改版、导出格式问题都请到上游仓库反馈。

---

## Android 客户端：Web For Tomato

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
| 版本 | 3.0.36 (`versionCode 8036`，见 `pubspec.yaml`) |
| 框架 | **Flutter 3.47+ / Dart 3.10+**，UI 基于自研 `prism_ui`（FlClash 设计语言） |
| 原生层 | Kotlin（`native_src/`：服务管理、端口、书库、前台服务） |
| minSdk / targetSdk / compileSdk | **24 / 28 / 由 Flutter 决定** |
| 签名 | V1 + V2，与 2.x 同一证书保证覆盖升级 |

> ⚠️ **`targetSdk = 28` 是刻意为之**：Android 10 (API 29) 起 `filesDir` 被施加 W^X（`noexec`）保护，
> 即使 `chmod +x` 也无法执行下载下来的原生二进制。降到 28 可获得该豁免。
> Termux 之所以能跑，是因为它把二进制放在自己的 `$PREFIX` 目录。

### 构建

```bash
cd Android/TomatoWeb
flutter pub get
flutter build apk --release          # 产物：build/app/outputs/flutter-apk/app-release.apk
```

国内网络建议为 Flutter / Gradle 配置镜像（`PUB_HOSTED_URL`、`FLUTTER_STORAGE_BASE_URL`，
以及 `android/` 的 init script），这些属于本机环境，不写进仓库。

**签名密钥不进仓库** —— `android/app/build.gradle.kts` 按以下顺序读取：

```bash
# A. keystore.properties（复制 keystore.properties.example，已 gitignore）
#    storeFile=linjiusi.jks  storePassword=...  keyAlias=...  keyPassword=...
# B. 环境变量
export TOMATO_KS_FILE=linjiusi.jks TOMATO_KS_PASS='口令' TOMATO_KS_ALIAS=release
```

两者都没有时，`flutter build apk --release` 输出**未签名 APK**（仅用于编译校验）。

> `targetSdk = 28` 的取舍见上节；`Android/TomatoWeb/native_src/` 是 2.x 的原生服务管理实现，
> 3.x 的 Dart 层通过平台通道复用它。

### 已下载书籍的存放位置

应用内「已下载书籍」读取的是 Web 服务数据目录下的 `books/`。
在 Web UI 里下载完成的书籍会出现在这里，可直接预览 / 复制 / 删除。

---

## 同步上游

本 Fork 只在上游基础上**新增** `Android/` 一个目录，同步方式是**按文件对齐**（不是 `git merge`）：
两个仓库没有共同祖先（fork 时历史被重建过），直接 merge 会报 `refusing to merge unrelated histories`。

```bash
git clone https://github.com/yujunc39-arch/Tomato-Novel-Downloader.git
cd Tomato-Novel-Downloader
git remote add upstream https://github.com/zhongbai2333/Tomato-Novel-Downloader.git
git fetch upstream main

# 用上游覆盖它拥有的路径，本地 Android/ 与 README 保持不动
git rm -rq src scripts third-party .github
git checkout upstream/main -- src scripts third-party .github \
    Cargo.toml Cargo.lock Cargo_no_official.toml build.rs installer.sh \
    docker-entrypoint-webui.sh Dockerfile.webui Dockerfile.webui.musl \
    .cargo .dockerignore .gitattributes img AI_NOT_FIX_CASES.md

git add -A && git commit -m "chore: 同步上游 $(git show upstream/main:Cargo.toml | grep ^version | cut -d'"' -f2)"
git push origin main
```

> 最近一次同步：上游 `2.4.18`（`src/` `Cargo.*` `scripts/` `.github/` 全部对齐，新增 `third-party/`）。
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

- 上游项目：[zhongbai2333/Tomato-Novel-Downloader](https://github.com/zhongbai2333/Tomato-Novel-Downloader)（Rust 下载器核心，本 App 的全部下载能力来源）
- 上游作者：[zhongbai2333](https://github.com/zhongbai2333) 及所有贡献者
- 前期基础项目：Dimily
- API 支持：[@helloplhm-qwq](https://github.com/helloplhm-qwq)、QQ 用户 @终忆、[@jingluopro](https://github.com/jingluopro)

---

## License

[MIT](LICENSE) · 上游版权归 zhongbai2333 所有，本 Fork 的新增部分同样以 MIT 发布。
