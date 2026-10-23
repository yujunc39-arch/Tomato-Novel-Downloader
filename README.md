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
│   ├── TomatoWeb/            #    应用源码（App 名：Web For Tomato）
│   │   ├── app/              #      Kotlin + Compose 源码
│   │   ├── dual_sign.py      #      V1 + V2 双签名脚本
│   │   ├── keystore.properties.example
│   │   └── build.gradle.kts
│   ├── android-env.sh        #    构建环境变量
│   └── gradle-init.gradle    #    阿里云镜像加速（国内构建用）
│
├── src/                      # 🦀 上游 Rust 源码（请勿在此改业务）
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

**签名密钥不进仓库** —— 二选一提供：

```bash
# A. 环境变量
export TOMATO_KS_FILE=../release.jks TOMATO_KS_PASS='口令' TOMATO_KS_ALIAS=release
# B. 本地 keystore.properties（复制 keystore.properties.example 修改，已 gitignore）
```

未配置时 `assembleRelease` 输出未签名 APK（CI 编译校验够用）。

### 已下载书籍的存放位置

应用内「已下载书籍」读取的是 Web 服务数据目录下的 `books/`。
在 Web UI 里下载完成的书籍会出现在这里，可直接预览 / 复制 / 删除。

---

## 同步上游

本 Fork 只在上游基础上**新增** `Android/` 一个目录，理论上永远可以无冲突合并：

```bash
git clone https://github.com/yujunc39-arch/Tomato-Novel-Downloader.git
cd Tomato-Novel-Downloader
git remote add upstream https://github.com/zhongbai2333/Tomato-Novel-Downloader.git
git fetch upstream
git merge upstream/main          # 无冲突（本地改动只在 Android/ 与文档）
git push origin main
```

> 若未来双方同时改动了同一文件，才需要手动解冲突。
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
