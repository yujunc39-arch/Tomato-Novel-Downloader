# 维护指南

本仓库是上游的增强 Fork，包含 **上游 Rust 核心 + 自研 Android 客户端 + 自研 Web 平台**。
这份文档记录日常维护流程。

---

## 0. 改动边界（重要）

| 目录 | 谁维护 | 能否直接改 |
|------|--------|-----------|
| `src/` `Cargo.toml` `Cargo.lock` `scripts/` `.github/workflows/` | 上游 | ❌ 不要改，会被 `merge upstream/main` 覆盖冲突 |
| `Android/` | 本 Fork | ✅ 随便改 |
| `Web/` | 本 Fork | ✅ 随便改 |
| 根 `README.md` / 本文档 | 本 Fork | ✅ 随便改 |

> 所有自研内容只放在 `Android/`、`Web/` 两个顶层目录里，
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

**发布新版本二进制后**，线上平台的「版本管理」卡片即可一键更新（见下）。

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
source Android/android-env.sh
cd Android/TomatoWeb
./gradlew assembleRelease
# 产物: app/build/outputs/apk/release/app-release.apk

# 国内网络加镜像
./gradlew --init-script ../gradle-init.gradle assembleRelease
```

**签名**：`Android/TomatoWeb/dual_sign.py` 做 V1 + V2 双签名
（zipalign → apksigner 签 V2 → 追加 JAR 签名，不破坏 V2 块）。

> ⚠️ 改了 `targetSdk` 要小心：必须保持 **28**。
> Android 10+ 会把 `filesDir` 挂载为 `noexec`，`targetSdk` 升到 29+ 就无法执行下载的原生二进制了。

**版本号**：改 `Android/TomatoWeb/app/build.gradle.kts` 里的 `versionCode` / `versionName`。

---

## 4. Web 平台维护

部署见 [`Web/README.md`](Web/README.md)。

日常运维：

```bash
systemctl status tomato-gateway              # 网关状态
systemctl restart tomato-gateway             # 改 main.py/db.py/instances.py 后重启
journalctl -u tomato-gateway -n 50           # 看日志
nginx -s reload                              # 只改 nginx/证书

bash Web/deploy/reload-nginx.sh              # 重新生成用户子域配置
```

关键可调参数：

| 位置 | 含义 | 默认 |
|------|------|------|
| `Web/app/instances.py` `IDLE_TIMEOUT` | 实例空闲回收秒数 | `1200`（20 分钟） |
| `Web/app/instances.py` `sweep_loop(interval)` | 扫描间隔 | 60 秒 |
| `Web/app/main.py` `JWT_TTL` | 登录态有效期 | 30 天 |
| `Web/app/main.py` `LOGIN_MAX_FAILS` / `LOGIN_LOCK_SECS` | 登录限流 | 5 次 / 300 秒 |

**用户数据**：`/opt/tomato-multi/data/users/u<N>/`
- `config.yml`：该用户的下载配置（格式、保存路径等）
- `books/`：下载产物（实例关闭时会被清空）
- `logs/`：实例日志（实例关闭时会被清空）

**审计**：`/opt/tomato-multi/app/data/audit.log`（时间 / IP / 操作人 / 动作 / 详情）

---

## 5. 发布清单

- [ ] `git fetch upstream && git merge upstream/main` 无冲突
- [ ] Rust：`bash ./scripts/maintain.sh` 全绿
- [ ] Android：`./gradlew assembleRelease` 成功，`targetSdk` 仍为 28，版本号已递增
- [ ] Web：`systemctl restart tomato-gateway` 后主站 / 后台 / 用户子域正常
- [ ] 更新 `README.md` 的版本/结构说明（如有变化）
- [ ] `git push origin main`

---

## 6. 已知维护重点

- 第三方 API 相关代码不开源（上游策略），同步上游时注意别把新增的私密文件提交上去。
- PDF 依赖链会引入旧版 `image/time`，包体积敏感时可考虑拆 feature。
- Web UI / TUI 是两个用户入口，涉及配置/下载流程时建议两边都验。
- **密钥安全**：`Android/TomatoWeb/release.jks` 与 `dual_sign.py` 中的口令若已提交，
  公网仓库等于泄露签名密钥，务必评估是否移除（改走私密分发）。
