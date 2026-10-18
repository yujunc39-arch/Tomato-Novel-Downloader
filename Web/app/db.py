"""数据库层：SQLite，用户与实例管理。启用 WAL + busy_timeout 避免锁问题。"""
import sqlite3, os, time, threading, hashlib

DB_PATH = "/opt/tomato-multi/app/data/data.db"
DATA_ROOT = "/opt/tomato-multi/data/users"
_lock = threading.Lock()

def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def get_conn():
    return _connect()

def init_db():
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            sid TEXT PRIMARY KEY,
            uid INTEGER NOT NULL,
            pwd_ver INTEGER NOT NULL,
            created_at INTEGER,
            expires_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            subdomain TEXT UNIQUE NOT NULL,
            port INTEGER UNIQUE NOT NULL,
            data_dir TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_super INTEGER DEFAULT 0,
            pwd_version INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at INTEGER,
            last_active_at INTEGER
        )
    """)
    # 兼容旧库: 补列
    for col, ddl in [("is_super", "ALTER TABLE users ADD COLUMN is_super INTEGER DEFAULT 0"),
                     ("pwd_version", "ALTER TABLE users ADD COLUMN pwd_version INTEGER DEFAULT 0"),
                     ("email", "ALTER TABLE users ADD COLUMN email TEXT")]:
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    # 邮箱验证码（注册用）：只存哈希，不存明文
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_codes (
            email       TEXT PRIMARY KEY,
            code_hash   TEXT NOT NULL,
            expires_at  INTEGER NOT NULL,
            attempts    INTEGER DEFAULT 0,
            sent_at     INTEGER NOT NULL,
            send_count  INTEGER DEFAULT 0,
            window_start INTEGER,
            ip          TEXT
        )
    """)
    # 每日配额（控制注册量 / 保护发信额度）
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            day   TEXT PRIMARY KEY,
            sends INTEGER DEFAULT 0,
            regs  INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def next_port():
    conn = _connect()
    used = set(r["port"] for r in conn.execute("SELECT port FROM users"))
    conn.close()
    p = 19000
    while p in used:
        p += 1
    return p

def next_subdomain():
    conn = _connect()
    used = set(r["subdomain"] for r in conn.execute("SELECT subdomain FROM users"))
    conn.close()
    n = 1
    while f"u{n}" in used:
        n += 1
    return f"u{n}"

def create_user(username, password_hash, data_dir=None, email=None):
    """创建用户。子域名/端口/数据目录三者确定性绑定：
       子域名 uN  ->  端口 19000+(N-1)  ->  目录 DATA_ROOT/uN
    email: 可选；有邮箱验证时传入（已校验通过）
    """
    sub = next_subdomain()
    port = 19000 + int(sub[1:]) - 1
    if not data_dir:
        data_dir = f"{DATA_ROOT}/{sub}"
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO users(username,password_hash,subdomain,port,data_dir,email,status,created_at) "
            "VALUES(?,?,?,?,?,?, 'active', ?)",
            (username, password_hash, sub, port, data_dir, email, int(time.time())),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        uid = row["id"]
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return uid

# 便捷更新辅助: 每次独立连接, 自动 commit
def _exec(sql, params=()):
    conn = _connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()

def get_user_by_name(username):
    conn = _connect()
    r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(r) if r else None

def get_user_by_id(uid):
    conn = _connect()
    r = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(r) if r else None

def list_users():
    conn = _connect()
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def set_status(uid, status):
    _exec("UPDATE users SET status=? WHERE id=?", (status, uid))

def set_admin(uid, val):
    _exec("UPDATE users SET is_admin=? WHERE id=?", (1 if val else 0, uid))

def delete_user(uid):
    _exec("DELETE FROM users WHERE id=?", (uid,))

def touch(uid):
    _exec("UPDATE users SET last_active_at=? WHERE id=?", (int(time.time()), uid))

def set_data_dir(uid, data_dir):
    _exec("UPDATE users SET data_dir=? WHERE id=?", (data_dir, uid))

def set_super(uid, val=1):
    _exec("UPDATE users SET is_super=? WHERE id=?", (1 if val else 0, uid))

# ---------- 会话管理(token 可吊销) ----------
import secrets

SESSION_TTL = 60 * 60 * 24 * 30  # 30天

def get_pwd_version(uid):
    conn = _connect()
    r = conn.execute("SELECT pwd_version FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return (r["pwd_version"] or 0) if r else 0

def create_session(uid, pwd_ver):
    """签发会话, 返回 sid。token 里带 sid, 只有该 sid 有效才放行。"""
    sid = secrets.token_hex(16)
    now = int(time.time())
    _exec("INSERT INTO sessions(sid,uid,pwd_ver,created_at,expires_at) VALUES(?,?,?,?,?)",
          (sid, uid, pwd_ver, now, now + SESSION_TTL))
    # 顺带清理过期会话
    _exec("DELETE FROM sessions WHERE expires_at<?", (now,))
    return sid

def session_valid(sid, uid, pwd_ver):
    """会话存在且未过期且与密码版本一致才有效。"""
    if not sid:
        return False
    conn = _connect()
    r = conn.execute("SELECT uid,pwd_ver,expires_at FROM sessions WHERE sid=?", (sid,)).fetchone()
    conn.close()
    if not r:
        return False
    if r["expires_at"] < int(time.time()):
        return False
    if r["uid"] != uid:
        return False
    if r["pwd_ver"] != pwd_ver:
        return False
    return True

def delete_session(sid):
    _exec("DELETE FROM sessions WHERE sid=?", (sid,))

def delete_user_sessions(uid):
    """让用户所有会话失效(改密码/封禁时调用)。"""
    _exec("DELETE FROM sessions WHERE uid=?", (uid,))

def bump_pwd_version(uid):
    """改密码时自增版本号, 使该用户所有旧 token/session 失效。"""
    _exec("UPDATE users SET pwd_version = COALESCE(pwd_version,0)+1 WHERE id=?", (uid,))

def update_password(uid, password_hash):
    _exec("UPDATE users SET password_hash=? WHERE id=?", (password_hash, uid))

# ---------- 邮箱验证码（注册用；只存哈希，不存明文） ----------
CODE_TTL = 600                  # 验证码有效期 10 分钟
CODE_MAX_ATTEMPTS = 5           # 同一验证码最多试 5 次
CODE_MAX_SEND_PER_HOUR = 5      # 同一邮箱每小时最多发 5 封

def _code_hash(code) -> str:
    return hashlib.sha256(("tomato:" + str(code).strip()).encode()).hexdigest()

def issue_email_code(email, ip=""):
    """生成并保存验证码。返回 (code, None) 或 (None, 错误信息)。"""
    now = int(time.time())
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT send_count, window_start FROM email_codes WHERE email=?", (email,)
        ).fetchone()
        send_count, window_start = 0, now
        if row:
            send_count = row["send_count"] or 0
            window_start = row["window_start"] or now
            if now - window_start > 3600:      # 窗口过期，重新计数
                send_count, window_start = 0, now
            if send_count >= CODE_MAX_SEND_PER_HOUR:
                left = 3600 - (now - window_start)
                return None, "发送过于频繁，请 {} 分钟后再试".format(max(1, left // 60))
        code = "{:06d}".format(secrets.randbelow(1000000))
        conn.execute(
            "INSERT INTO email_codes(email,code_hash,expires_at,attempts,sent_at,send_count,window_start,ip) "
            "VALUES(?,?,?,0,?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash, "
            "expires_at=excluded.expires_at, attempts=0, sent_at=excluded.sent_at, "
            "send_count=excluded.send_count, window_start=excluded.window_start, ip=excluded.ip",
            (email, _code_hash(code), now + CODE_TTL, now, send_count + 1, window_start, ip),
        )
        conn.commit()
        return code, None
    finally:
        conn.close()

def verify_email_code(email, code):
    """校验验证码。返回 None=通过，否则返回错误信息。"""
    now = int(time.time())
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM email_codes WHERE email=?", (email,)).fetchone()
        if not row:
            return "请先获取邮箱验证码"
        if (row["expires_at"] or 0) < now:
            conn.execute("DELETE FROM email_codes WHERE email=?", (email,))
            conn.commit()
            return "验证码已过期，请重新获取"
        if (row["attempts"] or 0) >= CODE_MAX_ATTEMPTS:
            return "错误次数过多，请重新获取验证码"
        if _code_hash(code) != row["code_hash"]:
            conn.execute("UPDATE email_codes SET attempts=attempts+1 WHERE email=?", (email,))
            conn.commit()
            left = CODE_MAX_ATTEMPTS - (row["attempts"] or 0) - 1
            return "验证码不正确（还可尝试 {} 次）".format(max(0, left))
        conn.execute("DELETE FROM email_codes WHERE email=?", (email,))
        conn.commit()
        return None
    finally:
        conn.close()

def get_user_by_email(email):
    conn = _connect()
    r = conn.execute(
        "SELECT * FROM users WHERE lower(email)=?", ((email or "").strip().lower(),)
    ).fetchone()
    conn.close()
    return dict(r) if r else None

def cleanup_expired_codes():
    conn = _connect()
    try:
        conn.execute("DELETE FROM email_codes WHERE expires_at < ?", (int(time.time()),))
        conn.commit()
    finally:
        conn.close()

# ---------- 每日配额（按北京时间自然日） ----------
def bj_day(ts=None):
    """北京时间（UTC+8）的日期字符串 YYYY-MM-DD。"""
    t = ts if ts is not None else time.time()
    return time.strftime("%Y-%m-%d", time.gmtime(t + 8 * 3600))

def bump_daily(field):
    """给今天的计数器 +1。field: 'sends' 或 'regs'。"""
    if field not in ("sends", "regs"):
        return
    day = bj_day()
    conn = _connect()
    try:
        conn.execute("INSERT INTO daily_stats(day,sends,regs) VALUES(?,0,0) "
                     "ON CONFLICT(day) DO NOTHING", (day,))
        conn.execute("UPDATE daily_stats SET {0} = COALESCE({0},0) + 1 WHERE day=?".format(field),
                     (day,))
        conn.commit()
    finally:
        conn.close()

def get_daily(day=None):
    """返回某天的 {'sends': n, 'regs': n}，默认今天。"""
    day = day or bj_day()
    conn = _connect()
    r = conn.execute("SELECT sends, regs FROM daily_stats WHERE day=?", (day,)).fetchone()
    conn.close()
    return dict(r) if r else {"sends": 0, "regs": 0}
