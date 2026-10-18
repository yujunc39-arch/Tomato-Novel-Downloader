"""Tomato-Novel-Downloader 多租户网关。

对外:  nginx(HTTPS) -> 本网关(127.0.0.1:18080)
  - /            用户注册/登录/我的入口页
  - /api/...     后端API(注册/登录/库列表/封禁等)
  - /admin       管理后台(管理员)
每个用户注册后获得独立 port + data_dir，由 instances 模块懒启动番茄进程。
用户通过其专属子域名 u<id>.<domain> 访问番茄 Web UI(nginx反代到用户端口)。
"""
import os, json, time, threading, shutil, hashlib, hmac, tempfile, zipfile, re, subprocess
import bcrypt, jwt
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
import db, instances

def _load_env_file(path="/opt/tomato-multi/app/.env"):
    """从 .env 读取配置(环境变量优先)。"""
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg

_ENV = _load_env_file()
SECRET = (os.environ.get("TOMATO_SECRET") or _ENV.get("TOMATO_SECRET") or "").strip() or "change-me-please"
DOMAIN = (os.environ.get("TOMATO_DOMAIN") or _ENV.get("TOMATO_DOMAIN") or "localhost").strip()
COOKIE_DOMAIN = "." + DOMAIN
BASE_DATA = "/opt/tomato-multi/data/users"
JWT_TTL = 60 * 60 * 24 * 30  # 30天

# ---- Cloudflare Turnstile 真人校验（未配置 SITEKEY 则整个功能自动关闭）----
TURNSTILE_SITEKEY = (os.environ.get("TURNSTILE_SITEKEY") or _ENV.get("TURNSTILE_SITEKEY") or "").strip()
TURNSTILE_SECRET  = (os.environ.get("TURNSTILE_SECRET")  or _ENV.get("TURNSTILE_SECRET")  or "").strip()
HUMAN_COOKIE = "human"
HUMAN_TTL = 24 * 3600        # 通过校验后 24 小时内免重复验证
if TURNSTILE_SITEKEY and not TURNSTILE_SECRET:
    print("warn: TURNSTILE_SITEKEY 已配置但缺少 TURNSTILE_SECRET，真人校验将不可用")

# ---- 邮件发送（注册邮箱验证）。SMTP_HOST/USER/PASS 都配置齐才会启用 ----
SMTP_HOST = (os.environ.get("SMTP_HOST") or _ENV.get("SMTP_HOST") or "").strip()
SMTP_PORT = int((os.environ.get("SMTP_PORT") or _ENV.get("SMTP_PORT") or "465").strip() or 465)
SMTP_USER = (os.environ.get("SMTP_USER") or _ENV.get("SMTP_USER") or "").strip()
SMTP_PASS = (os.environ.get("SMTP_PASS") or _ENV.get("SMTP_PASS") or "").strip()
SMTP_FROM = (os.environ.get("SMTP_FROM") or _ENV.get("SMTP_FROM") or SMTP_USER).strip()
SMTP_SSL  = (os.environ.get("SMTP_SSL")  or _ENV.get("SMTP_SSL")  or "1").strip() != "0"
SMTP_TLS  = (os.environ.get("SMTP_TLS")  or _ENV.get("SMTP_TLS")  or "0").strip() == "1"
EMAIL_VERIFY = bool(SMTP_HOST and SMTP_USER and SMTP_PASS)

# 每日注册上限（同时保护发信额度，避免把 Resend 免费额度打爆）
# 可用 .env 的 DAILY_REG_LIMIT 调整；<=0 表示不限制
DAILY_REG_LIMIT = int((os.environ.get("DAILY_REG_LIMIT")
                       or _ENV.get("DAILY_REG_LIMIT") or "100").strip() or 100)
QUOTA_MSG = "今日已达到最高注册数量，请明天再试"

def quota_exhausted():
    """今天是否已达注册/发信上限（按北京时间自然日）。"""
    if DAILY_REG_LIMIT <= 0:
        return False
    d = db.get_daily()
    return (d.get("regs") or 0) >= DAILY_REG_LIMIT or (d.get("sends") or 0) >= DAILY_REG_LIMIT

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# 只允许这些邮箱服务商（留空 = 不限制）。可用 .env 的 EMAIL_ALLOWED_DOMAINS 覆盖，逗号分隔
_domains_raw = (os.environ.get("EMAIL_ALLOWED_DOMAINS")
                or _ENV.get("EMAIL_ALLOWED_DOMAINS")
                or "qq.com,163.com,gmail.com").strip()
ALLOWED_EMAIL_DOMAINS = {d.strip().lower() for d in _domains_raw.split(",") if d.strip()}
ALLOWED_DOMAINS_HINT = " / ".join("@" + d for d in sorted(ALLOWED_EMAIL_DOMAINS))

def email_domain(e: str) -> str:
    return e.rsplit("@", 1)[-1].lower() if "@" in e else ""

def valid_email(e: str) -> bool:
    if not e or len(e) > 120 or not EMAIL_RE.match(e):
        return False
    if ALLOWED_EMAIL_DOMAINS and email_domain(e) not in ALLOWED_EMAIL_DOMAINS:
        return False
    return True

def check_email(e: str):
    """校验邮箱格式与域名白名单；不合格直接抛 HTTPException（带明确提示）。"""
    if not e or len(e) > 120 or not EMAIL_RE.match(e):
        raise HTTPException(400, "邮箱格式不正确")
    if ALLOWED_EMAIL_DOMAINS and email_domain(e) not in ALLOWED_EMAIL_DOMAINS:
        raise HTTPException(400, "仅支持 " + ALLOWED_DOMAINS_HINT + " 邮箱")

def send_mail(to_addr: str, subject: str, body: str):
    """发送纯文本邮件（标准库 smtplib，不引入额外依赖）。"""
    import smtplib, ssl as _ssl
    from email.message import EmailMessage
    from email.header import Header
    msg = EmailMessage()
    msg["Subject"] = str(Header(subject, "utf-8"))
    # 加个中文发件人显示名，收件箱里更可信（纯地址会自动补上）
    from_addr = SMTP_FROM
    if "<" not in from_addr:
        from_addr = "番茄小说下载器 <{}>".format(from_addr)
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body, charset="utf-8")
    if SMTP_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20,
                              context=_ssl.create_default_context()) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.ehlo()
            if SMTP_TLS:
                s.starttls(context=_ssl.create_default_context())
                s.ehlo()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)

# 用户名: 2-20位, 字母/数字/下划线/中文/点/横线
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5.\-]{2,20}$")

def valid_username(u: str) -> bool:
    return bool(u) and bool(USERNAME_RE.match(u))

def valid_password(p: str) -> bool:
    # bcrypt 只取前 72 字节, 限制上限避免截断
    return isinstance(p, str) and 6 <= len(p.encode("utf-8")) <= 72

# 关闭自动文档, 避免接口结构泄露
app = FastAPI(title="Tomato Multi-Tenant Gateway",
              docs_url=None, redoc_url=None, openapi_url=None)

# 静态资源(仅主站公共资源; 后台页面不公开挂载, 由鉴权路由提供)
app.mount("/static", StaticFiles(directory="/opt/tomato-multi/app/static"), name="static")

# ---- 允许匿名访问的接口白名单(其余 /api/* 一律要求登录) ----
PUBLIC_API = {"/api/register", "/api/login", "/api/logout", "/api/auth_check",
              "/api/download/app", "/api/human/status", "/api/human/verify",
              "/api/admin/login", "/api/register/send_code", "/api/site_info"}

def bj_now():
    """北京时间(UTC+8)。服务器运行在 UTC, 这里加 8 小时。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))

db.init_db()

# ---------- 全局安全中间件(默认拒绝 + 安全响应头) ----------
_login_fails = {}          # key -> [fail_count, locked_until_ts]
_reg_ips = {}              # ip -> [count, window_start]
_mail_ips = {}             # ip -> [code_send_count, window_start]
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECS = 300

def _client_ip(request: Request):
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"

def _login_locked(key):
    rec = _login_fails.get(key)
    if rec and rec[1] > time.time():
        return int(rec[1] - time.time())
    return 0

def _login_fail(key):
    rec = _login_fails.get(key) or [0, 0]
    rec[0] += 1
    if rec[0] >= LOGIN_MAX_FAILS:
        rec[1] = time.time() + LOGIN_LOCK_SECS
        rec[0] = 0
    _login_fails[key] = rec

def _login_ok(key):
    _login_fails.pop(key, None)

@app.middleware("http")
async def security_mw(request: Request, call_next):
    path = request.url.path
    # 1) 默认拒绝: 非白名单的 /api/* 必须已登录(纵深防御, 防漏加鉴权)
    if path.startswith("/api/") and path not in PUBLIC_API:
        token = None
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
        if not token:
            token = request.cookies.get("token")
        data = decode_token(token) if token else None
        if not data:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        u = db.get_user_by_id(data.get("uid"))
        if not u or u["status"] != "active":
            return JSONResponse({"detail": "账号不可用"}, status_code=401)
    resp = await call_next(request)
    # 2) 安全响应头
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    return resp

# ---------- 工具 ----------
def hash_pw(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_pw(pw, h):
    return bcrypt.checkpw(pw.encode(), h.encode())

def make_token(uid, is_admin, sid, pv):
    return jwt.encode({"uid": uid, "admin": bool(is_admin), "sid": sid, "pv": pv,
                       "exp": int(time.time()) + JWT_TTL}, SECRET, algorithm="HS256")

def decode_token(token):
    try:
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except Exception:
        return None

def _token_payload(request: Request):
    """提取并校验 token, 返回 payload; 失败返回 None。校验: 签名 + sid 会话有效 + 密码版本一致。"""
    token = None
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    if not token:
        token = request.cookies.get("token")
    if not token:
        return None
    data = decode_token(token)
    if not data:
        return None
    if not db.session_valid(data.get("sid"), data.get("uid"), data.get("pv")):
        return None
    return data

def current_user(request: Request, authorization: str | None = None):
    """从请求中提取 JWT 并返回当前用户。"""
    data = _token_payload(request)
    if not data:
        raise HTTPException(401, "未登录或会话已失效")
    user = db.get_user_by_id(data["uid"])
    if not user:
        raise HTTPException(401, "用户不存在")
    if user["status"] == "banned":
        raise HTTPException(403, "账号已被封禁")
    return user

def admin_required(request: Request, authorization: str | None = None):
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "需要管理员权限")
    return user

def guard_target(me, target, action="操作"):
    """普通管理员只能操作非管理员用户(自己或普通用户); 超级管理员不受限。"""
    if me.get("is_super"):
        return
    if target["id"] == me["id"]:
        return
    if target.get("is_admin"):
        raise HTTPException(403, f"不能{action}其他管理员")

AUDIT_LOG = "/opt/tomato-multi/app/data/audit.log"

def audit(me, request, action, detail=""):
    """记录敏感管理操作(审计日志: 时间/来源IP/操作人/动作/详情)。"""
    try:
        line = "{}\t{}\t{}\t{}\t{}\n".format(
            bj_now(), _client_ip(request),
            me.get("username"), action, detail)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def resolve_data_dir(uid):
    u = db.get_user_by_id(uid)
    if u and u.get("subdomain"):
        return os.path.join(BASE_DATA, u["subdomain"])
    return os.path.join(BASE_DATA, f"u{uid}")

# ---------- Cloudflare Turnstile 真人校验 ----------
def _human_sig(exp: int) -> str:
    """用站点密钥给过期时间签名，防止伪造 cookie 绕过。"""
    return hmac.new(SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()

def make_human_cookie() -> str:
    exp = int(time.time()) + HUMAN_TTL
    return f"{exp}.{_human_sig(exp)}"

def human_ok(request: Request) -> bool:
    """检查当前请求是否已通过真人校验（签名有效且未过期）。"""
    if not TURNSTILE_SITEKEY:
        return True                      # 未启用真人校验，直接放行
    v = request.cookies.get(HUMAN_COOKIE)
    if not v or "." not in v:
        return False
    exp_s, sig = v.split(".", 1)
    if not exp_s.isdigit():
        return False
    exp = int(exp_s)
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _human_sig(exp))

def is_super_session(request: Request) -> bool:
    """当前请求是否携带有效的超级管理员会话（用于豁免真人校验）。"""
    try:
        u = _cookie_user(request)
    except Exception:
        return False
    return bool(u and u.get("is_super"))

def need_human(request: Request) -> bool:
    """该请求是否必须通过真人校验（超管豁免；已通过则 False）。"""
    if not (TURNSTILE_SITEKEY and TURNSTILE_SECRET):
        return False
    if is_super_session(request):
        return False                     # 👑 超级管理员无需验证
    return not human_ok(request)

def turnstile_verify(token: str, ip: str) -> bool:
    """向 Cloudflare 校验 Turnstile token。"""
    if not TURNSTILE_SECRET:
        return False
    import urllib.request, urllib.parse
    payload = urllib.parse.urlencode({
        "secret": TURNSTILE_SECRET, "response": token, "remoteip": ip,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            j = json.loads(resp.read().decode())
        return bool(j.get("success"))
    except Exception as e:
        print("turnstile verify error:", e)
        return False

@app.get("/api/site_info")
async def site_info():
    """公开站点配置：前端据此决定是否显示邮箱验证字段 / 今日是否还有名额。"""
    d = db.get_daily()
    regs, sends = d.get("regs") or 0, d.get("sends") or 0
    left = -1
    if DAILY_REG_LIMIT > 0:
        left = max(0, DAILY_REG_LIMIT - max(regs, sends))
    return {"email_verify": EMAIL_VERIFY,
            "human_required": bool(TURNSTILE_SITEKEY and TURNSTILE_SECRET),
            "code_ttl": db.CODE_TTL,
            "allowed_domains": sorted(ALLOWED_EMAIL_DOMAINS),
            "daily_limit": DAILY_REG_LIMIT,
            "left_today": left,
            "quota_exhausted": bool(EMAIL_VERIFY and quota_exhausted())}

@app.get("/api/human/status")
async def human_status(request: Request):
    """前端用来判断是否需要弹出真人校验（超管豁免）。"""
    super_user = is_super_session(request)
    return {"required": bool(TURNSTILE_SITEKEY and TURNSTILE_SECRET) and not super_user,
            "ok": super_user or human_ok(request),
            "super": super_user,
            "sitekey": TURNSTILE_SITEKEY}

@app.post("/api/human/verify")
async def human_verify(data: dict, request: Request):
    """校验 Turnstile token，通过后下发签名 cookie（跨子域有效）。"""
    if not (TURNSTILE_SITEKEY and TURNSTILE_SECRET):
        return {"ok": True, "disabled": True}
    token = (data.get("token") or "").strip()
    if not token:
        raise HTTPException(400, "缺少验证令牌")
    if not turnstile_verify(token, _client_ip(request)):
        raise HTTPException(400, "真人校验失败，请刷新重试")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(HUMAN_COOKIE, make_human_cookie(), domain=COOKIE_DOMAIN,
                    path="/", httponly=True, secure=True, samesite="lax",
                    max_age=HUMAN_TTL)
    return resp

# ---------- 用户API ----------
@app.post("/api/register/send_code")
async def register_send_code(data: dict, request: Request):
    """发送注册邮箱验证码（仅当 SMTP 配置齐全时可用）。"""
    if not human_ok(request):
        raise HTTPException(403, "请先完成真人校验")
    if not EMAIL_VERIFY:
        raise HTTPException(503, "邮箱验证暂未开启")
    if quota_exhausted():
        raise HTTPException(429, QUOTA_MSG)
    email = (data.get("email") or "").strip().lower()
    check_email(email)
    if db.get_user_by_email(email):
        raise HTTPException(400, "该邮箱已被注册")
    ip = _client_ip(request)
    rec = _mail_ips.get(ip) or [0, time.time()]
    if time.time() - rec[1] > 3600:
        rec = [0, time.time()]
    if rec[0] >= 10:
        raise HTTPException(429, "发送过于频繁，请稍后再试")
    code, err = db.issue_email_code(email, ip)
    if err:
        raise HTTPException(429, err)
    rec[0] += 1
    _mail_ips[ip] = rec
    body = (
        "您好，\n\n"
        "您正在注册「番茄小说下载器」。本次验证码为：\n\n"
        "        {code}\n\n"
        "验证码 {ttl} 分钟内有效。如非本人操作，请忽略本邮件。\n\n"
        "—— 番茄小说下载器"
    ).format(code=code, ttl=db.CODE_TTL // 60)
    try:
        send_mail(email, "【番茄小说下载器】注册验证码", body)
    except Exception as e:
        emsg = str(e).lower()
        # 发信服务商额度耗尽 -> 统一按"今日名额已满"提示
        if any(k in emsg for k in ("quota", "limit", "429", "rate", "exceeded", "too many")):
            raise HTTPException(429, QUOTA_MSG)
        raise HTTPException(502, "邮件发送失败：" + str(e)[:120])
    db.bump_daily("sends")
    return {"ok": True, "expires_in": db.CODE_TTL}

@app.post("/api/register")
async def register(data: dict, request: Request):
    if not human_ok(request):
        raise HTTPException(403, "请先完成真人校验")
    if EMAIL_VERIFY and quota_exhausted():
        raise HTTPException(429, QUOTA_MSG)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not valid_username(username):
        raise HTTPException(400, "用户名需为 2-20 位(字母/数字/下划线/中文)")
    if not valid_password(password):
        raise HTTPException(400, "密码长度需为 6-72 位")
    # 先查用户名：避免"用户名已存在"把验证码白白消耗掉
    if db.get_user_by_name(username):
        raise HTTPException(400, "用户名已存在")
    # 邮箱验证（配置了 SMTP 才强制）
    email = (data.get("email") or "").strip().lower()
    if EMAIL_VERIFY:
        check_email(email)
        if db.get_user_by_email(email):
            raise HTTPException(400, "该邮箱已被注册")
        code = (data.get("code") or "").strip()
        if not code:
            raise HTTPException(400, "请填写邮箱验证码")
        err = db.verify_email_code(email, code)
        if err:
            raise HTTPException(400, err)
    # 限流: 每 IP 每小时最多注册 10 个
    ip = _client_ip(request)
    rec = _reg_ips.get(ip) or [0, time.time()]
    if time.time() - rec[1] > 3600:
        rec = [0, time.time()]
    if rec[0] >= 10:
        raise HTTPException(429, "注册过于频繁，请稍后再试")
    rec[0] += 1
    _reg_ips[ip] = rec
    uid = db.create_user(username, hash_pw(password),
                         email=(email if EMAIL_VERIFY else None))
    if not uid:
        raise HTTPException(400, "注册失败")
    if EMAIL_VERIFY:
        db.bump_daily("regs")
    u = db.get_user_by_id(uid)
    data_dir = u["data_dir"]
    os.makedirs(data_dir, exist_ok=True)
    _write_default_config(data_dir, u)
    # 刷新 nginx 配置(新增该用户子域名反代)
    instances.render_nginx()
    return {"ok": True, "uid": uid, "subdomain": u["subdomain"]}

@app.post("/api/login")
async def login(data: dict, request: Request):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    # 真人校验：全员强制，不做用户名豁免
    # （否则攻击者可用 403/401 差异探测出哪个账号是超管）
    # 超管豁免只走「已登录的超管会话」：见 /api/human/status 与 /api/auth_check
    if not human_ok(request):
        raise HTTPException(403, "请先完成真人校验")
    key = f"{_client_ip(request)}:{username}"
    left = _login_locked(key)
    if left:
        raise HTTPException(429, f"尝试过于频繁，请 {left} 秒后再试")
    user = db.get_user_by_name(username)
    if not user or not user.get("password_hash") or not verify_pw(password, user["password_hash"]):
        _login_fail(key)
        raise HTTPException(401, "用户名或密码错误")
    if user["status"] == "banned":
        raise HTTPException(403, "账号已被封禁")
    _login_ok(key)
    pv = db.get_pwd_version(user["id"])
    sid = db.create_session(user["id"], pv)
    token = make_token(user["id"], user["is_admin"], sid, pv)
    db.touch(user["id"])
    resp = JSONResponse({"ok": True, "token": token, "username": username,
                         "is_admin": bool(user["is_admin"])})
    # 下发跨子域 Cookie：用户访问 u<N>.domain 时浏览器自动携带，用于 nginx 鉴权
    try:
        resp.set_cookie("token", token, domain=COOKIE_DOMAIN, path="/",
                        httponly=True, secure=True, samesite="lax", max_age=JWT_TTL)
    except Exception:
        pass
    return resp

@app.post("/api/admin/login")
async def admin_login(data: dict, request: Request):
    """管理后台专用登录：不要求真人校验（后台登录页没有验证组件），
    但只允许**管理员账号**通过，并沿用同样的登录限流（5 次失败锁 5 分钟）。"""
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    key = f"admin:{_client_ip(request)}:{username}"
    left = _login_locked(key)
    if left:
        raise HTTPException(429, f"尝试过于频繁，请 {left} 秒后再试")
    user = db.get_user_by_name(username)
    if not user or not user.get("password_hash") or not verify_pw(password, user["password_hash"]):
        _login_fail(key)
        raise HTTPException(401, "用户名或密码错误")
    if user["status"] == "banned":
        raise HTTPException(403, "账号已被封禁")
    if not user["is_admin"]:
        # 非管理员账号：也计入失败次数，避免该接口被用来批量撞普通账号
        _login_fail(key)
        raise HTTPException(403, "该账号无权访问管理后台")
    _login_ok(key)
    pv = db.get_pwd_version(user["id"])
    sid = db.create_session(user["id"], pv)
    token = make_token(user["id"], True, sid, pv)
    db.touch(user["id"])
    resp = JSONResponse({"ok": True, "token": token, "username": username, "is_admin": True})
    try:
        resp.set_cookie("token", token, domain=COOKIE_DOMAIN, path="/",
                        httponly=True, secure=True, samesite="lax", max_age=JWT_TTL)
    except Exception:
        pass
    audit(user, request, "管理员登录后台", f"账号={username}")
    return resp

@app.post("/api/logout")
async def logout(request: Request):
    """退出登录: 吊销当前会话(旧 token 立即失效) + 清 cookie。"""
    data = _token_payload(request)
    if data and data.get("sid"):
        db.delete_session(data["sid"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("token", domain=COOKIE_DOMAIN, path="/")
    return resp

@app.get("/api/auth_check")
async def auth_check(request: Request):
    """供 nginx auth_request 调用：校验当前 cookie 用户是否有权访问该子域名空间。
    鉴权通过但实例未启动时返回 503(由 nginx 拦截引导用户回主站启动, 不自动拉起)。"""
    sub = (request.headers.get("x-subdomain") or "").strip()
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(401, "未登录")
    data = decode_token(token)
    if not data:
        raise HTTPException(401, "登录已过期")
    user = db.get_user_by_id(data["uid"])
    if not user:
        raise HTTPException(401, "用户不存在")
    if user["status"] != "active":
        raise HTTPException(403, "账号已被封禁")
    if not sub or user["subdomain"] != sub:
        raise HTTPException(403, "无权访问该空间")
    # 真人校验：全员强制（超管豁免）。未通过则 403 → nginx 弹回主站过验证
    if not user.get("is_super") and not human_ok(request):
        raise HTTPException(403, "请先完成真人校验")
    db.touch(user["id"])
    # 鉴权通过即返回 200; 实例是否启动由 nginx proxy 的 502 处理(引导用户去启动)
    return {"ok": True}

@app.get("/api/me")
async def me(request: Request):
    user = current_user(request)
    db.touch(user["id"])
    running = instances.is_running(user["id"])
    return {"id": user["id"], "username": user["username"],
            "subdomain": user["subdomain"], "is_admin": bool(user["is_admin"]),
            "is_super": bool(user.get("is_super")), "status": user["status"],
            "running": running,
            "started_at": instances.get_started_at(user["id"]) if running else None}

# ---------- 用户自己的库/实例 ----------
@app.post("/api/me/start")
async def me_start(request: Request):
    """懒启动当前用户的番茄实例。"""
    user = current_user(request)
    instances.render_nginx()   # 确保子域名反代存在
    ok = instances.start_instance(user["id"], user["port"], user["data_dir"])
    db.touch(user["id"])
    return {"ok": ok, "port": user["port"], "subdomain": user["subdomain"],
            "started_at": instances.get_started_at(user["id"]) if ok else None}

@app.get("/api/me/library")
async def me_library(request: Request):
    """返回当前用户下载库里的书(目录列表)。"""
    user = current_user(request)
    data_dir = user["data_dir"] or resolve_data_dir(user["id"])
    out = []
    for root, dirs, files in os.walk(data_dir):
        for f in files:
            if f.endswith((".epub", ".txt", ".pdf")):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, data_dir)
                try: size = os.path.getsize(full)
                except: size = 0
                out.append({"name": os.path.basename(f), "path": rel, "size": size,
                            "mtime": os.path.getmtime(full)})
    return {"books": out}

# ---------- 管理后台API ----------
@app.get("/api/admin/users")
async def admin_users(request: Request):
    admin_required(request)
    rows = db.list_users()
    # 批量判活：只抓一次进程快照，避免每个用户 spawn 一次 pgrep
    # （先前 1254 个用户逐个 is_running 实测耗时 16.7 秒）
    running_map = instances.batch_is_running(rows)
    for u in rows:
        u.pop("password_hash", None)   # 不向前端返回密码哈希
        d = u["data_dir"] or resolve_data_dir(u["id"])
        u["disk_usage"] = _dir_size(d) if os.path.isdir(d) else 0
        u["running"] = running_map.get(u["id"], False)
        u["started_at"] = instances.get_started_at(u["id"]) if u["running"] else None
        u["books"] = _count_books(d) if os.path.isdir(d) else 0
    return {"users": rows}

@app.post("/api/admin/stop_all")
async def admin_stop_all(request: Request):
    """一键关闭所有用户的下载实例(并清理 txt/epub)。仅超级管理员。"""
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以一键关闭所有实例")
    users = db.list_users()
    n = 0
    for u in users:
        try:
            instances.stop_instance(u["id"], clean=True)
            n += 1
        except Exception:
            pass
    audit(me, request, "一键关闭所有实例", f"共 {n} 个用户")
    return {"ok": True, "count": n}

@app.post("/api/me/change_password")
async def me_change_password(data: dict, request: Request):
    """用户修改自己的密码: 需旧密码; 改后所有旧 session 失效, 重新登录。"""
    user = current_user(request)
    old = data.get("old_password") or ""
    new = data.get("new_password") or ""
    if not valid_password(new):
        raise HTTPException(400, "新密码长度需为 6-72 位")
    if not verify_pw(old, user["password_hash"]):
        raise HTTPException(400, "旧密码错误")
    db.update_password(user["id"], hash_pw(new))
    db.bump_pwd_version(user["id"])
    db.delete_user_sessions(user["id"])   # 所有旧会话失效
    return {"ok": True, "relogin": True}

@app.post("/api/admin/users/{uid}/set_password")
async def admin_set_password(uid: int, data: dict, request: Request):
    """超级管理员直接重置任意用户密码(无需旧密码); 使该用户所有旧 session 失效。"""
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以重置用户密码")
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    new = data.get("password") or ""
    if not valid_password(new):
        raise HTTPException(400, "密码长度需为 6-72 位")
    db.update_password(uid, hash_pw(new))
    db.bump_pwd_version(uid)
    db.delete_user_sessions(uid)
    audit(me, request, "重置用户密码", f"目标用户={target['username']}(uid={uid})")
    return {"ok": True}

@app.post("/api/admin/users/{uid}/ban")
async def admin_ban(uid: int, request: Request):
    me = admin_required(request)
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.get("is_super"):
        raise HTTPException(400, "超级管理员不能被封禁")
    if not me.get("is_super"):
        if target["id"] == me["id"]:
            raise HTTPException(400, "不能封禁自己")
        if target.get("is_admin"):
            raise HTTPException(400, "不能封禁其他管理员")
    db.set_status(uid, "banned")
    db.delete_user_sessions(uid)          # 封禁即吊销该用户所有会话
    instances.stop_instance(uid, clean=True)  # 封禁强制清理
    instances.render_nginx()
    audit(me, request, "封禁用户", f"目标用户={target['username']}(uid={uid})")
    return {"ok": True}

@app.post("/api/admin/users/{uid}/unban")
async def admin_unban(uid: int, request: Request):
    me = admin_required(request)
    db.set_status(uid, "active")
    instances.render_nginx()
    audit(me, request, "解封用户", f"uid={uid}")
    return {"ok": True}

@app.post("/api/admin/users/{uid}/stop")
async def admin_stop(uid: int, request: Request):
    """管理后台关闭某用户的下载实例。"""
    me = admin_required(request)
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    guard_target(me, target)
    instances.stop_instance(uid, clean=True)  # 手动关实例确认后清理
    return {"ok": True, "running": False}

@app.post("/api/admin/users/{uid}/start")
async def admin_start(uid: int, request: Request):
    """管理后台启动某用户的下载实例。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    instances.render_nginx()
    ok = instances.start_instance(uid, u["port"], u["data_dir"])
    return {"ok": ok, "running": ok}

@app.post("/api/admin/users/{uid}/delete")
async def admin_delete(uid: int, request: Request):
    me = admin_required(request)
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.get("is_super"):
        raise HTTPException(400, "超级管理员不能被删除")
    if not me.get("is_super"):
        if target["id"] == me["id"]:
            raise HTTPException(400, "不能删除自己")
        if target.get("is_admin"):
            raise HTTPException(400, "不能删除其他管理员")
    instances.stop_instance(uid, clean=True)  # 删除用户强制清理
    d = resolve_data_dir(uid)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    db.delete_user(uid)
    instances.render_nginx()
    audit(me, request, "删除用户", f"目标用户={target['username']}(uid={uid})")
    return {"ok": True}

@app.post("/api/admin/users/{uid}/admin")
async def admin_setadmin(uid: int, data: dict, request: Request):
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以设置管理员")
    target = db.get_user_by_id(uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.get("is_super") and not data.get("is_admin", False):
        raise HTTPException(400, "超级管理员不能被降级")
    db.set_admin(uid, data.get("is_admin", False))
    audit(me, request, "设置管理员", f"目标用户={target['username']}(uid={uid}) 设为={data.get('is_admin', False)}")
    return {"ok": True}

@app.get("/api/admin/user_files/{uid}")
async def admin_user_files(uid: int, request: Request, path: str = ""):
    """管理员浏览任意用户的「用户空间」内容(可进入子目录)。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    base = os.path.realpath(u["data_dir"] or resolve_data_dir(uid))
    root = base
    sub = (path or "").strip().strip("/")
    target = os.path.realpath(os.path.join(root, sub)) if sub else root
    # 防目录穿越
    if target != root and not target.startswith(root + os.sep):
        raise HTTPException(400, "非法路径")
    items = []
    if os.path.isdir(target):
        for name in sorted(os.listdir(target)):
            full = os.path.join(target, name)
            try:
                if os.path.isdir(full):
                    items.append({"name": name, "type": "dir", "size": _dir_size(full)})
                else:
                    items.append({"name": name, "type": "file", "size": os.path.getsize(full)})
            except Exception:
                pass
    return {"path": sub, "items": items}

@app.get("/api/admin/users/{uid}/download")
async def admin_download(uid: int, request: Request, path: str = ""):
    """管理员下载用户空间中的文件(文件夹自动打包为zip)。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    base = os.path.realpath(u["data_dir"] or resolve_data_dir(uid))
    root = base
    rel = (path or "").strip().strip("/")
    if not rel:
        raise HTTPException(400, "缺少路径")
    target = os.path.realpath(os.path.join(root, rel))
    if target == root or not target.startswith(root + os.sep):
        raise HTTPException(400, "非法路径")
    if os.path.isfile(target):
        return FileResponse(target, filename=os.path.basename(target))
    if os.path.isdir(target):
        tmp = tempfile.NamedTemporaryFile(prefix="tnd_space_", suffix=".zip",
                                          delete=False, dir="/tmp")
        tmp.close()
        try:
            with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
                for r, dirs, files in os.walk(target):
                    for f in files:
                        full = os.path.join(r, f)
                        z.write(full, os.path.relpath(full, target))
        except Exception as e:
            raise HTTPException(500, f"打包失败: {e}")
        return FileResponse(tmp.name, filename=os.path.basename(target) + ".zip",
                            background=BackgroundTask(lambda: os.path.exists(tmp.name) and os.remove(tmp.name)))
    raise HTTPException(404, "文件不存在")

@app.post("/api/admin/users/{uid}/delete_path")
async def admin_delete_path(uid: int, data: dict, request: Request):
    """管理员删除用户空间中的文件/文件夹。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    base = os.path.realpath(u["data_dir"] or resolve_data_dir(uid))
    root = base
    rel = (data.get("path") or "").strip().strip("/")
    if not rel:
        raise HTTPException(400, "缺少路径")
    target = os.path.realpath(os.path.join(root, rel))
    # 必须位于用户空间内, 且不能是根目录本身
    if target == root or not target.startswith(root + os.sep):
        raise HTTPException(400, "非法路径")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.isfile(target):
            os.remove(target)
        else:
            raise HTTPException(404, "文件不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")
    audit(me, request, "删除文件", f"目标用户={u['username']}(uid={uid}) 路径={rel}")
    return {"ok": True}

# ---- 在线查看/编辑用户配置文件 ----
EDITABLE_EXT = {".yml", ".yaml", ".jsonl"}
VIEWONLY_EXT = {".log"}
TEXT_EXT = EDITABLE_EXT | VIEWONLY_EXT
MAX_VIEW_BYTES = 3 * 1024 * 1024

def _space_file(u, rel):
    base = os.path.realpath(u["data_dir"] or resolve_data_dir(u["id"]))
    rel = (rel or "").strip().strip("/")
    if not rel:
        raise HTTPException(400, "缺少路径")
    target = os.path.realpath(os.path.join(base, rel))
    if target == base or not target.startswith(base + os.sep):
        raise HTTPException(400, "非法路径")
    ext = os.path.splitext(target)[1].lower()
    if ext not in TEXT_EXT:
        raise HTTPException(400, "只支持 .yml / .jsonl / .log 文件")
    return target, ext

# ---- 软件下载(统计次数与IP) ----
APK_PATH = "/opt/tomato-multi/app/static/tomato-app.apk"
DLLOG = "/opt/tomato-multi/app/data/downloads.log"

@app.get("/api/download/app")
async def download_app(request: Request):
    """软件下载: 记录下载次数与IP。仅限安卓。"""
    if not os.path.isfile(APK_PATH):
        raise HTTPException(404, "安装包不存在")
    ip = _client_ip(request)
    try:
        with open(DLLOG, "a", encoding="utf-8") as f:
            f.write(f"{bj_now()}\t{ip}\n")
    except Exception:
        pass
    return FileResponse(APK_PATH, filename="tomato-app.apk",
                        media_type="application/vnd.android.package-archive")

@app.get("/api/admin/downloads")
async def admin_downloads(request: Request):
    """管理员查看软件下载统计(次数 + IP 列表)。"""
    admin_required(request)
    rows = []
    if os.path.isfile(DLLOG):
        with open(DLLOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                rows.append({"time": parts[0], "ip": parts[1] if len(parts) > 1 else "?"})
    # 按IP聚合
    from collections import Counter
    ipcount = Counter(r["ip"] for r in rows)
    by_ip = [{"ip": ip, "count": c} for ip, c in ipcount.most_common()]
    return {"total": len(rows), "by_ip": by_ip, "records": list(reversed(rows))[:100]}

@app.get("/api/admin/users/{uid}/file")
async def admin_read_file(uid: int, request: Request, path: str = ""):
    """读取用户空间中的文本文件(.yml/.jsonl/.log)。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    target, ext = _space_file(u, path)
    if not os.path.isfile(target):
        raise HTTPException(404, "文件不存在")
    size = os.path.getsize(target)
    if size > MAX_VIEW_BYTES:
        raise HTTPException(400, "文件过大，无法在线打开")
    try:
        content = open(target, encoding="utf-8", errors="replace").read()
    except Exception as e:
        raise HTTPException(500, f"读取失败: {e}")
    return {"path": path, "ext": ext.lstrip("."), "size": size,
            "editable": ext in EDITABLE_EXT, "content": content}

@app.post("/api/admin/users/{uid}/file")
async def admin_write_file(uid: int, data: dict, request: Request):
    """保存用户空间中的 .yml/.jsonl 文件(校验语法; .log 只读)。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    target, ext = _space_file(u, data.get("path"))
    if ext not in EDITABLE_EXT:
        raise HTTPException(403, "该文件只读(.log 不可修改)")
    content = data.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "内容无效")
    if len(content.encode("utf-8")) > MAX_VIEW_BYTES:
        raise HTTPException(400, "内容过大")
    # —— 语法校验(编译) ——
    if ext in (".yml", ".yaml"):
        try:
            import yaml
            yaml.safe_load(content)
        except Exception as e:
            raise HTTPException(400, f"YAML 语法错误: {e}")
    elif ext == ".jsonl":
        for i, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except Exception as e:
                raise HTTPException(400, f"第 {i} 行 JSON 格式错误: {e}")
    # —— 备份 + 写入 ——
    try:
        if os.path.isfile(target):
            shutil.copy2(target, target + ".bak")
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")
    audit(me, request, "修改配置文件", f"目标用户={u['username']}(uid={uid}) 文件={data.get('path')} 大小={len(content)}")
    return {"ok": True}

@app.get("/api/admin/user_books/{uid}")
async def admin_user_books(uid: int, request: Request):
    """管理员查看任意用户下载的书。"""
    me = admin_required(request)
    u = db.get_user_by_id(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    guard_target(me, u)
    data_dir = u["data_dir"] or resolve_data_dir(uid)
    out = []
    if os.path.isdir(data_dir):
        for root, dirs, files in os.walk(data_dir):
            for f in files:
                if f.endswith((".epub", ".txt", ".pdf")):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, data_dir)
                    try: size = os.path.getsize(full)
                    except: size = 0
                    out.append({"name": os.path.basename(f), "path": rel, "size": size})
    return {"books": out}

@app.post("/api/admin/register")
async def admin_register(data: dict, request: Request):
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以新增用户")
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not valid_username(username):
        raise HTTPException(400, "用户名需为 2-20 位(字母/数字/下划线/中文)")
    if not valid_password(password):
        raise HTTPException(400, "密码长度需为 6-72 位")
    if db.get_user_by_name(username):
        raise HTTPException(400, "用户名已存在")
    uid = db.create_user(username, hash_pw(password))
    u = db.get_user_by_id(uid)
    d = u["data_dir"]
    os.makedirs(d, exist_ok=True)
    _write_default_config(d, u)
    if data.get("is_admin"):
        db.set_admin(uid, True)
    instances.render_nginx()
    audit(me, request, "新增用户", f"新用户={username}(uid={uid}) 管理员={bool(data.get('is_admin'))}")
    return {"ok": True, "uid": uid}

# ---------- 前端页面 ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("/opt/tomato-multi/app/static/index.html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

# ---- 全站公告 ----
ANNOUNCE_PATH = "/opt/tomato-multi/app/data/announce.json"

@app.get("/__announce__")
async def announce_get():
    """公开读取当前公告(登录/未登录都能拿到)。"""
    try:
        with open(ANNOUNCE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if not d.get("enabled") or not d.get("content"):
            return {"enabled": False}
        return {"enabled": True, "id": d.get("id") or "",
                "title": d.get("title") or "公告",
                "content": d.get("content") or "",
                "updated": d.get("updated") or ""}
    except Exception:
        return {"enabled": False}

@app.post("/api/admin/announce")
async def announce_set(data: dict, request: Request):
    """设置/更新公告(仅超级管理员)。"""
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以发布公告")
    title = (data.get("title") or "").strip()[:60]
    content = (data.get("content") or "").strip()[:2000]
    if not content:
        raise HTTPException(400, "公告内容不能为空")
    d = {"enabled": True, "id": str(int(time.time())), "title": title or "公告",
         "content": content, "updated": bj_now()}
    with open(ANNOUNCE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    audit(me, request, "发布公告", f"标题={title or '公告'} 长度={len(content)}")
    return {"ok": True, "id": d["id"]}

@app.post("/api/admin/announce/clear")
async def announce_clear(request: Request):
    """清除公告(仅超级管理员)。"""
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以清除公告")
    d = {"enabled": False, "id": str(int(time.time())), "title": "", "content": "", "updated": bj_now()}
    with open(ANNOUNCE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    audit(me, request, "清除公告", "")
    return {"ok": True}

@app.get("/api/admin/announce")
async def announce_read(request: Request):
    """读取公告当前内容(管理员, 用于编辑)。"""
    admin_required(request)
    try:
        with open(ANNOUNCE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "title": "", "content": ""}

def _cookie_user(request: Request):
    """从 cookie 中解析当前登录用户(用于页面级鉴权; 校验会话有效)。"""
    data = _token_payload(request)
    if not data:
        return None
    u = db.get_user_by_id(data["uid"])
    if not u or u["status"] != "active":
        return None
    return u

# 管理后台: 未登录/非管理员 -> 只显示登录页; 是管理员 -> 显示管理面板
@app.get("/linjiuser", response_class=HTMLResponse)
async def admin_page(request: Request):
    u = _cookie_user(request)
    no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate"}
    if not u or not u["is_admin"]:
        return FileResponse("/opt/tomato-multi/app/adminstatic/login.html", headers=no_cache)
    return FileResponse("/opt/tomato-multi/app/adminstatic/admin.html", headers=no_cache)

# (旧地址 /admin 不再跳转, 保持后台隐藏; 直接 404)

# ---------- 配置 / 工具 ----------
def _write_default_config(data_dir, user):
    if isinstance(user, dict):
        sub = user.get("subdomain", "user")
    else:
        uu = db.get_user_by_id(user) or {}
        sub = uu.get("subdomain", f"u{user}")
    cfg = f"""# 番茄小说下载器 - {sub} 配置 (自动生成)
save_path: "{os.path.join(data_dir, 'books')}"
# 保存小说格式, 可选: [txt, epub, pdf]
novel_format: txt
# 是否以散装形式保存小说(每章一个文件); false=合并成单个文件
bulk_files: false
max_workers: 8
"""
    try:
        with open(os.path.join(data_dir, "config.yml"), "w") as f:
            f.write(cfg)
        os.makedirs(os.path.join(data_dir, "books"), exist_ok=True)
    except Exception:
        pass

def _dir_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except: pass
    return total

def _count_books(path):
    n = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            if f.endswith((".epub", ".txt", ".pdf")):
                n += 1
    return n

# ---------- 番茄二进制版本管理 ----------
GITHUB_REPO   = "zhongbai2333/Tomato-Novel-Downloader"
TOMATO_BIN    = "/opt/tomato-multi/bin/tomato"
TOMATO_VER_FL = "/opt/tomato-multi/app/data/tomato_version.json"

def _get_current_tomato_version() -> str:
    """读当前二进制版本（优先本地文件，否则执行 --version）。"""
    try:
        with open(TOMATO_VER_FL, encoding="utf-8") as f:
            return json.load(f).get("version", "")
    except Exception:
        pass
    try:
        r = subprocess.run([TOMATO_BIN, "--version"], capture_output=True, text=True, timeout=5)
        m = re.search(r"v?\d+\.\d+\.\d+", r.stdout)
        return ("v" + m.group(0).lstrip("v")) if m else "unknown"
    except Exception:
        return "unknown"

def _save_tomato_version(ver: str):
    os.makedirs(os.path.dirname(TOMATO_VER_FL), exist_ok=True)
    with open(TOMATO_VER_FL, "w", encoding="utf-8") as f:
        json.dump({"version": ver, "updated_at": bj_now()}, f)

def _get_latest_github_version() -> dict:
    """用 git ls-remote 获取最新 release tag（不走 REST API，零限流）。
    返回 {"tag": "vX.Y.Z", "asset_url": "https://..."}"""
    r = subprocess.run(
        ["git", "ls-remote", "--tags",
         f"https://github.com/{GITHUB_REPO}.git"],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0:
        raise Exception(f"git ls-remote 失败: {r.stderr.strip()}")
    ver_pat = re.compile(r'^v\d+\.\d+\.\d+$')
    tags = set()
    for line in r.stdout.strip().splitlines():
        parts = line.split('\t')
        if len(parts) == 2:
            t = parts[1].replace('refs/tags/', '').replace('^{}', '').strip()
            if ver_pat.match(t):
                tags.add(t)
    if not tags:
        raise Exception("未找到任何 vX.Y.Z 格式的 tag")
    def _key(t):
        m = re.match(r'^v(\d+)\.(\d+)\.(\d+)', t)
        return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
    latest = sorted(tags, key=_key)[-1]
    asset_name = f"TomatoNovelDownloader-Linux_amd64-{latest}"
    asset_url  = f"https://github.com/{GITHUB_REPO}/releases/download/{latest}/{asset_name}"
    return {"tag": latest, "asset_url": asset_url}

@app.get("/api/admin/tomato_version")
async def admin_tomato_version(request: Request):
    """查当前版本 + GitHub 最新版本（所有管理员可查）。"""
    admin_required(request)
    current = _get_current_tomato_version()
    try:
        info = _get_latest_github_version()
        latest_tag = info["tag"]
        has_update = latest_tag != current and current and latest_tag.startswith("v")
        return {"current": current, "latest": latest_tag,
                "has_update": has_update,
                "note": "更新二进制后，已启动的实例需重启才生效"}
    except Exception as e:
        return {"current": current, "latest": f"error:{e}",
                "has_update": False, "note": ""}

@app.post("/api/admin/tomato_update")
async def admin_tomato_update(request: Request):
    """下载 GitHub 最新 release 的 Linux_amd64 二进制并替换（仅超级管理员）。"""
    me = admin_required(request)
    if not me.get("is_super"):
        raise HTTPException(403, "只有超级管理员可以更新版本")
    try:
        info = _get_latest_github_version()
    except Exception as e:
        raise HTTPException(502, f"获取最新版本失败: {e}")
    tag       = info["tag"]
    asset_url = info["asset_url"]
    old_ver   = _get_current_tomato_version()
    import urllib.request
    tmp = "/tmp/tomato_new_bin"
    try:
        req = urllib.request.Request(asset_url, headers={"User-Agent": "tomato-gateway"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
        os.chmod(tmp, 0o755)
    except Exception as e:
        raise HTTPException(502, f"下载二进制失败: {e}")
    bak = TOMATO_BIN + ".bak"
    if os.path.isfile(TOMATO_BIN):
        shutil.copy2(TOMATO_BIN, bak)
    try:
        os.replace(tmp, TOMATO_BIN)
    except Exception as e:
        raise HTTPException(500, f"替换二进制失败: {e}")
    _save_tomato_version(tag)
    audit(me, request, "更新番茄版本", f"{old_ver} → {tag}")
    return {"ok": True, "from": old_ver, "to": tag,
            "note": "二进制已更新，运行中的实例重启后生效"}

# 启动时清理遗留实例 + 刷新 nginx
@app.on_event("startup")
async def on_startup():
    threading.Thread(target=instances.sweep_loop, daemon=True).start()
    instances.kill_all_instances()   # 清掉上次遗留的实例, 保证与 DB 一致
    instances.render_nginx()
