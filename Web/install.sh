#!/usr/bin/env bash
# ============================================================
#  番茄多租户 Web 平台 - 一键部署脚本
#  用法:
#    cp .env.example .env && vi .env
#    sudo bash install.sh
#  也可用环境变量直接传参:
#    sudo TOMATO_DOMAIN=tomato.example.com bash install.sh
# ============================================================
set -euo pipefail

APP_DIR=/opt/tomato-multi
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE=tomato-gateway

log()  { echo -e "\033[32m[+]\033[0m $*"; }
warn() { echo -e "\033[33m[!]\033[0m $*"; }
die()  { echo -e "\033[31m[x]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "请用 root 运行: sudo bash install.sh"

# ---------- 1. 读取配置 ----------
if [ -f "$SRC_DIR/.env" ]; then
  # shellcheck disable=SC1090
  set -a; . "$SRC_DIR/.env"; set +a
fi

TOMATO_DOMAIN=${TOMATO_DOMAIN:-}
if [ -z "$TOMATO_DOMAIN" ]; then
  read -rp "请输入主域名 (例如 tomato.example.com): " TOMATO_DOMAIN
fi
[ -n "$TOMATO_DOMAIN" ] || die "TOMATO_DOMAIN 不能为空"

TOMATO_SECRET=${TOMATO_SECRET:-}
[ -n "$TOMATO_SECRET" ] || TOMATO_SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '/+=' | head -c 48)

TOMATO_ADMIN_USER=${TOMATO_ADMIN_USER:-admin}
TOMATO_ADMIN_PASS=${TOMATO_ADMIN_PASS:-}
if [ -z "$TOMATO_ADMIN_PASS" ]; then
  TOMATO_ADMIN_PASS=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 16)
fi

CERT_DIR=/etc/letsencrypt/live/$TOMATO_DOMAIN
log "域名: $TOMATO_DOMAIN"
log "超级管理员: $TOMATO_ADMIN_USER"

# ---------- 2. 系统依赖 ----------
log "安装系统依赖 ..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx python3 python3-venv python3-pip \
    certbot curl ca-certificates git unzip >/dev/null
if [ -n "${CF_API_TOKEN:-}" ]; then
  apt-get install -y -qq python3-certbot-dns-cloudflare >/dev/null || true
fi

# ---------- 3. 部署代码 ----------
log "部署代码到 $APP_DIR ..."
mkdir -p "$APP_DIR/app/data" "$APP_DIR/data/users" "$APP_DIR/bin"
cp -r "$SRC_DIR/app/." "$APP_DIR/app/"
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled

# ---------- 4. python venv ----------
log "创建虚拟环境并安装依赖 ..."
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q fastapi uvicorn bcrypt pyjwt python-multipart pyyaml

# ---------- 5. .env ----------
log "写入 $APP_DIR/app/.env ..."
cat > "$APP_DIR/app/.env" <<EOF
TOMATO_DOMAIN=$TOMATO_DOMAIN
TOMATO_SECRET=$TOMATO_SECRET
EOF
chmod 600 "$APP_DIR/app/.env"

# ---------- 6. 下载番茄二进制 ----------
# 通过 git ls-remote 取最新 tag（不走 GitHub REST API，无 60/h 限流）
if [ ! -x "$APP_DIR/bin/tomato" ]; then
  log "下载最新番茄二进制 ..."
  REPO=https://github.com/zhongbai2333/Tomato-Novel-Downloader
  TAG=$(git ls-remote --tags "$REPO.git" 2>/dev/null \
        | sed 's#.*refs/tags/##' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -V | tail -1)
  [ -n "$TAG" ] || die "无法获取最新版本号（检查服务器能否访问 GitHub）"
  URL="$REPO/releases/download/$TAG/TomatoNovelDownloader-Linux_amd64-$TAG"
  log "  版本 $TAG"
  curl -fL --retry 3 -o "$APP_DIR/bin/tomato" "$URL" || die "二进制下载失败"
  chmod +x "$APP_DIR/bin/tomato"
fi
log "番茄二进制: $("$APP_DIR/bin/tomato" --version 2>/dev/null || echo '已存在')"

# ---------- 7. 初始化数据库 + 超级管理员 ----------
log "初始化数据库 ..."
cd "$APP_DIR/app"
"$APP_DIR/venv/bin/python" - <<PYEOF
import sys, os
sys.path.insert(0, "$APP_DIR/app")
import db, bcrypt
db.init_db()
u = db.get_user_by_name("$TOMATO_ADMIN_USER")
if not u:
    pw = bcrypt.hashpw("$TOMATO_ADMIN_PASS".encode(), bcrypt.gensalt()).decode()
    uid = db.create_user("$TOMATO_ADMIN_USER", pw)
    db.set_admin(uid, True); db.set_super(uid, True)
    print("created admin uid=%s" % uid)
else:
    db.set_admin(u["id"], True); db.set_super(u["id"], True)
    print("admin exists uid=%s" % u["id"])
PYEOF

# ---------- 8. systemd ----------
log "配置 systemd 服务 ..."
cp "$SRC_DIR/deploy/tomato-gateway.service" /etc/systemd/system/$SERVICE.service
systemctl daemon-reload
systemctl enable $SERVICE >/dev/null

# ---------- 9. nginx ----------
log "配置 nginx ..."
for f in nginx-main nginx-block; do
  sed "s/tomato\.898954782\.xyz/$TOMATO_DOMAIN/g" \
      "$SRC_DIR/deploy/$f.conf" > /etc/nginx/sites-available/$f
  ln -sf /etc/nginx/sites-available/$f /etc/nginx/sites-enabled/$f
done
[ -L /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default || true
# 用户子域配置由 instances.render_nginx() 动态生成（写入 tomato-users）
printf '# 用户子域配置：由 instances.render_nginx() 自动生成，请勿手改\n' \
    > /etc/nginx/sites-available/tomato-users
ln -sf /etc/nginx/sites-available/tomato-users /etc/nginx/sites-enabled/tomato-users

# 用户多了以后 server_name 会很多，放开哈希表限制
if ! grep -q '^server_names_hash_bucket_size' /etc/nginx/nginx.conf; then
  sed -i '/^http {/a\    server_names_hash_bucket_size 128;\n    server_names_hash_max_size 4096;' /etc/nginx/nginx.conf
fi

# ---------- 10. 证书 ----------
log "申请 Let's Encrypt 泛域名证书 ..."
if [ -d "$CERT_DIR" ]; then
  log "  证书已存在，跳过"
elif [ -n "${CF_API_TOKEN:-}" ]; then
  mkdir -p /root/.secrets
  printf 'dns_cloudflare_api_token = %s\n' "$CF_API_TOKEN" > /root/.secrets/cf.ini
  chmod 600 /root/.secrets/cf.ini
  certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cf.ini \
      -d "$TOMATO_DOMAIN" -d "*.$TOMATO_DOMAIN" \
      --agree-tos --register-unsafely-without-email --non-interactive || \
      warn "certbot 失败，请稍后手动申请后 nginx -s reload"
else
  warn "未提供 CF_API_TOKEN，跳过自动证书。"
  warn "泛域名证书需要 DNS-01 验证，请手动执行："
  warn "  certbot certonly --manual --preferred-challenges dns -d $TOMATO_DOMAIN -d '*.$TOMATO_DOMAIN'"
fi

# ---------- 11. 启动 ----------
log "启动服务 ..."
nginx -t && systemctl reload nginx
systemctl restart $SERVICE
sleep 2
systemctl is-active --quiet $SERVICE && log "网关已启动 ✅" || die "网关启动失败，看 journalctl -u $SERVICE"

cat <<EOF

==================== 部署完成 ====================
  主站:     https://$TOMATO_DOMAIN
  管理后台: https://$TOMATO_DOMAIN/linjiuser
  超级管理员: $TOMATO_ADMIN_USER
  初始密码:  $TOMATO_ADMIN_PASS      <-- 请尽快登录后修改
=================================================

记得把 DNS 的 tomato 与 *.tomato 两条 A 记录指向本机 IP。
EOF
