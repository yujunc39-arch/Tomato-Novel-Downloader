# 维护指南

本仓库是上游的增强 Fork，包含 **上游 Rust 核心 + 自研 Android 客户端**。
这份文档记录日常维护流程。

> 🌐 多租户 Web 平台已独立成仓库
> **[yujunc39-arch/Tomato-Web](https://github.com/yujunc39-arch/Tomato-Web)**，
> 它的部署与运维不在本文档范围内。

---

## 0. 改动边界（重要）

| 目录 | 谁维护 | 能否直接改 |
|------|--------|-----------|
| `src/` `Cargo.toml` `Cargo.lock` `scripts/` `.github/workflows/` | 上游 | ❌ 不要改，会被 `merge upstream/main` 覆盖冲突 |
| `Android/` | 本 Fork | ✅ 随便改 |
| 根 `README.md` / 本文档 | 本 Fork | ✅ 随便改 |

> 所有自研内容只放在 `Android/` 一个顶层目录里，
> 这样同步上游时**永远不会冲突**。

---

## 1. 同步上游

```bash
git remote add upstream https://github.com/zhongbai2333/Tomato-Novel-Downloader.git  # 只需一次
git fetch upstream
git merge upstream/main --no-edit
git push origin main
```

同步后检查：

```bash
git rev-list --count HEAD..upstream/main     # 应为 0
git log --oneline -5
```

---

## 2. Rust 核心（上游）维护

`official-api` 与 `no-official-api` 是**互斥** feature，不能用 `--all-features`。

覆盖组合：

1. 默认：`official-api + tts + clipboard + clipboard-arboard`
2. 无官方 API：`--no-default-features --features no-official-api`
3. 轻量：`--no-default-features --features official-api,tts-native,clipboard`

本地检查：

```bash
bash ./scripts/maintain.sh      # Linux/macOS/Git Bash
# 或
powershell -ExecutionPolicy Bypass -File .\scripts\maintain.ps1   # Windows
```

脚本会跑 `cargo fmt --check` / `cargo test` / `cargo clippy -D warnings` / 无官方 API 组合 / `cargo tree -d`。

---

## 3. Android 客户端维护

```bash
cd Android/TomatoWeb
flutter pub get
flutter build apk --release
# 产物: build/app/outputs/flutter-apk/app-release.apk
```

3.x 是 **Flutter 应用**（`lib/` 为 Dart 源码，`native_src/` 为沿用 2.x 的 Kotlin 原生层），
不再直接调 `./gradlew`；镜像与代理属于本机环境，配置在 `~/.gradle/gradle.properties`
或 `PUB_HOSTED_URL` / `FLUTTER_STORAGE_BASE_URL`，不写进仓库。

**签名**：密钥材料**不在仓库里**。构建前二选一：
```bash
# 方式 A：环境变量
export TOMATO_KS_FILE=linjiusi.jks
export TOMATO_KS_PASS='你的口令'
export TOMATO_KS_ALIAS=release

# 方式 B：Android/TomatoWeb/keystore.properties（复制 keystore.properties.example 修改，已 gitignore）
```
两者都缺失时输出**未签名 APK**，仅供编译校验。签名走 Gradle 的 `signingConfigs`（V1 + V2）。

> ⚠️ 改了 `targetSdk` 要小心：必须保持 **28**。
> Android 10+ 会把 `filesDir` 挂载为 `noexec`，`targetSdk` 升到 29+ 就无法执行下载的原生二进制了。

**版本号**：改 `Android/TomatoWeb/pubspec.yaml` 的 `version: 3.x.y+build`（Flutter 会同步到 `versionCode` / `versionName`）。

**下载源**：App 从上游 Release 拉取二进制，如需换源改
`Android/TomatoWeb/native_src/com/tomato/downloader/core/ServerManager.kt` 里的下载地址常量。

---

## 4. 发布清单

- [ ] `git fetch upstream main` 后按 README「同步上游」的文件对齐方式同步（两仓库无共同祖先，不能 merge）
- [ ] Rust：`bash ./scripts/maintain.sh` 全绿
- [ ] Android：`flutter build apk --release` 成功，`targetSdk` 仍为 28，`pubspec.yaml` 版本号已递增
- [ ] 更新 `README.md` 的版本/结构说明（如有变化）
- [ ] `git push origin main`

---

## 5. 已知维护重点

- 第三方 API 相关代码不开源（上游策略），同步上游时注意别把新增的私密文件提交上去。
- PDF 依赖链会引入旧版 `image/time`，包体积敏感时可考虑拆 feature。
- Web UI / TUI 是两个用户入口，涉及配置/下载流程时建议两边都验。
- **密钥安全（已处理）**：签名 keystore 与口令**不再提交到仓库**。
  提供方式二选一：
  1. 环境变量 `TOMATO_KS_FILE` / `TOMATO_KS_PASS` / `TOMATO_KS_ALIAS`
  2. `Android/TomatoWeb/keystore.properties`（已 gitignore，参考 `keystore.properties.example`）

  未配置时 `flutter build apk --release` 产出**未签名** APK（CI 只做编译校验时够用）。
- **历史清理**：`*.jks` 曾误入仓库，已用 `git filter-repo` 从全部历史移除；
  `.gitignore` 已加 `*.jks *.keystore keystore.properties *.pepk`，勿再提交。
