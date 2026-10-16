<!--
提交前请确认：
- 标题前缀：
  - [BUG]  修复: ...
  - [Feat] 新功能: ...
  - [Web]  Web 平台: ...
  - [Android] 安卓客户端: ...
  - [Doc]  文档: ...
  - [Sync] 同步上游: ...
- 自研代码请只改动 Android/ 或 Web/ 目录，不要改 src/（否则同步上游会冲突）
-->

# Pull Request

## 分类

- [ ] BUG 修复
- [ ] 新功能
- [ ] 多租户 Web 平台（`Web/`）
- [ ] Android 客户端（`Android/`）
- [ ] 文档
- [ ] 同步上游

## 变更内容

## 影响范围

- [ ] 仅 Android 客户端
- [ ] 仅 Web 平台
- [ ] 仅文档
- [ ] 涉及 Rust 核心（`src/`）—— 请说明原因

## 验证

- Android：`./gradlew assembleRelease` 是否通过？（`targetSdk` 必须仍为 28）
- Web：`systemctl restart tomato-gateway` 后主站 / 后台 / 用户子域是否正常？
- Rust（如涉及）：`bash ./scripts/maintain.sh` 是否通过？

## 兼容性 / 风险

- [ ] 无破坏性变更
- [ ] 需要重启网关 / 重装 App
- [ ] 涉及配置或环境变量变更（已在文档说明）
- [ ] 涉及数据库结构变更（已确认 `db.init_db()` 能自动补列）

## 提交前检查

- [ ] 已阅读 `MAINTAINING.md`
- [ ] 改动只落在 `Android/` / `Web/` / 文档（未污染上游目录）
- [ ] 未提交任何密钥、口令、证书私钥、`.env`
