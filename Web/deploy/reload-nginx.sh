#!/usr/bin/env bash
# 重新生成用户子域名 nginx 配置并 reload。
# 平时不需要手动跑：网关会在新增/删除用户时自动调用 instances.render_nginx()。
# 这个脚本用于：改域名 / 改证书路径 / nginx 配置异常时手动恢复。
set -euo pipefail

APP=/opt/tomato-multi/app
VENV=/opt/tomato-multi/venv

echo "[+] 重新生成用户子域配置 ..."
cd "$APP"
"$VENV/bin/python" -c "
import sys
sys.path.insert(0, '$APP')
import instances
print(instances.render_nginx())
"

echo "[+] 校验 nginx 配置 ..."
nginx -t

echo "[+] reload ..."
nginx -s reload
echo "[+] 完成"
