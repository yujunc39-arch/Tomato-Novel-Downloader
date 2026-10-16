"""实例管理器：为每个用户启动/管理独立的番茄进程。

每个用户一个进程，监听独立端口 (127.0.0.1:port)，data-dir 独立。
用 systemd 常驻的本进程 spawn 番茄子进程，因此不会随 SSH 会话消亡。
并提供探活、超时空闲回收、nginx 配置刷新。
"""
import os, signal, subprocess, time, threading, json, socket, shutil, re

BIN = "/opt/tomato-multi/bin/tomato"
BASE_DATA = "/opt/tomato-multi/data/users"

def _proc_line_matches(line, data_dir):
    """判断进程命令行是否属于该 data_dir。
    必须精确匹配路径边界(后面是空格或行尾), 否则 u2 会误匹配 u25/u253。"""
    try:
        return re.search(r"--data-dir\s+" + re.escape(data_dir) + r"(\s|$)", line) is not None
    except Exception:
        return False
NGINX_CONF = "/etc/nginx/sites-available/tomato-users"
NGINX_LINK = "/etc/nginx/sites-enabled/tomato-users"

def _load_domain():
    d = (os.environ.get("TOMATO_DOMAIN") or "").strip()
    if d:
        return d
    # 兜底: 从 .env 读取
    try:
        with open("/opt/tomato-multi/app/.env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TOMATO_DOMAIN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

DOMAIN = _load_domain()

# 证书路径: 支持环境变量覆盖, 默认基于域名(换服务器只改域名即可)
CERT = os.environ.get("TOMATO_CERT", "").strip() or f"/etc/letsencrypt/live/{DOMAIN}/fullchain.pem"
CERTKEY = os.environ.get("TOMATO_CERTKEY", "").strip() or f"/etc/letsencrypt/live/{DOMAIN}/privkey.pem"

# in-memory: pid -> info
_procs = {}       # uid -> {pid, started_at, proc}
_lock = threading.Lock()
_start_lock = threading.Lock()   # 串行化启动, 防止并发重复拉起
IDLE_TIMEOUT = 1200  # 20分钟无活动回收

def _free(uid):
    p = _procs.get(uid)
    if p and os.path.exists(f"/proc/{p['pid']}"):
        return False
    return True

def start_instance(uid, port, data_dir):
    """启动一个用户的番茄实例(串行化)。返回 True 若已运行或启动成功。"""
    data_dir = data_dir or os.path.join(BASE_DATA, f"u{uid}")
    with _start_lock:
        # 已跟踪且存活
        with _lock:
            p = _procs.get(uid)
        if p and os.path.exists(f"/proc/{p['pid']}"):
            return True
        # 端口已有实例在监听(可能是遗漏的孤儿) -> 视为已运行
        if _port_open("127.0.0.1", port):
            return True
        os.makedirs(data_dir, exist_ok=True)
        env = dict(os.environ)
        env["TOMATO_WEB_ADDR"] = f"127.0.0.1:{port}"
        proc = subprocess.Popen(
            [BIN, "--server", "--data-dir", data_dir],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        with _lock:
            _procs[uid] = {"pid": proc.pid, "started_at": time.time(), "proc": proc}
        for _ in range(80):
            if _port_open("127.0.0.1", port):
                return True
            time.sleep(0.3)
        return _port_open("127.0.0.1", port)

def _is_process_alive(uid):
    """检查用户实例是否活着(端口通 或 进程在, 只要有一个活着就算在跑)。
    只有端口不通 且 进程不在, 才算未运行。"""
    import db, subprocess, re
    u = db.get_user_by_id(uid)
    if not u:
        return False
    port = u.get("port")
    data_dir = u.get("data_dir") or os.path.join(BASE_DATA, u["subdomain"])
    
    # 1) 端口探测
    if port and _port_open("127.0.0.1", port):
        return True
    
    # 2) 进程探测(通过 --data-dir 匹配)
    try:
        out = subprocess.run("pgrep -a tomato", shell=True, 
                            capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            if _proc_line_matches(line, data_dir):
                return True
    except Exception:
        pass
    
    return False

def clean_user_data(data_dir):
    """清空用户的 books 和 logs 目录下所有文件(实例关闭时自动执行)。
    确保目录最终为空(即使部分文件删除失败也强制处理)。"""
    n = 0
    for subdir in ("books", "logs"):
        d = os.path.join(data_dir or "", subdir)
        if not os.path.isdir(d):
            continue
        # 强制清空: 遍历并删除所有项(文件/文件夹)
        for name in os.listdir(d):
            full = os.path.join(d, name)
            try:
                if os.path.isfile(full) or os.path.islink(full):
                    os.remove(full)
                    n += 1
                elif os.path.isdir(full):
                    shutil.rmtree(full, ignore_errors=False)
                    n += 1
            except Exception as e:
                # 记录错误但继续处理其他项
                print(f"warn: failed to remove {full}: {e}")
    if n:
        print(f"cleaned {n} item(s) in {data_dir}")
    return n

def stop_instance(uid, clean=True):
    """停止用户实例: 先杀掉进程(确保真的停了), 再清空 books 和 logs。"""
    import db as _db
    u = _db.get_user_by_id(uid)
    data_dir = (u or {}).get("data_dir") or os.path.join(BASE_DATA, f"u{uid}")

    # 1) 杀掉 _procs 中跟踪的进程
    with _lock:
        p = _procs.pop(uid, None)
    if p:
        try:
            os.killpg(os.getpgid(p["pid"]), signal.SIGTERM)
        except Exception:
            try:
                os.kill(p["pid"], signal.SIGTERM)
            except Exception:
                pass
        proc = p.get("proc")
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # 2) 兜底: 按 data-dir 精确杀掉未被跟踪的残留进程(避免"清数据但进程还在")
    try:
        outp = subprocess.run("pgrep -af tomato", shell=True,
                              capture_output=True, text=True, timeout=3).stdout
        for line in outp.splitlines():
            if _proc_line_matches(line, data_dir):
                try:
                    pid = int(line.split()[0])
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
    except Exception:
        pass

    # 3) 等待进程真正退出(最多 5 秒)
    for _ in range(10):
        if not _is_process_alive(uid):
            break
        time.sleep(0.5)

    # 4) 确认已停止后清理(若进程仍未停, 不清理, 避免破坏运行中的实例)
    if clean and not _is_process_alive(uid):
        clean_user_data(data_dir)

def kill_all_instances():
    """停止所有番茄实例(清理泄漏/孤儿实例, 保证与DB一致)。"""
    with _lock:
        procs = list(_procs.values())
        _procs.clear()
    try:
        subprocess.run(["pkill", "-x", "tomato"], check=False)
    except Exception:
        pass
    for p in procs:
        proc = p.get("proc")
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

def is_running(uid):
    """检查用户实例是否在运行(通过端口探测)。"""
    return _is_process_alive(uid)

def get_started_at(uid):
    """返回实例启动时间戳(秒); 未运行返回 None。
    通过进程的 etimes(已运行秒数) 反推, 不受网关重启影响。"""
    import db, subprocess
    u = db.get_user_by_id(uid)
    if not u:
        return None
    data_dir = u.get("data_dir") or os.path.join(BASE_DATA, u["subdomain"])
    try:
        out = subprocess.run("pgrep -af tomato", shell=True,
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            if _proc_line_matches(line, data_dir):
                pid = line.split()[0]
                e = subprocess.run("ps -o etimes= -p " + pid, shell=True,
                                   capture_output=True, text=True, timeout=3).stdout.strip()
                if e and e.isdigit():
                    return int(time.time()) - int(e)
    except Exception:
        pass
    return None

def _port_open(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()

def health_check(uid):
    """返回该用户实例是否健康(端口可达)。"""
    import db
    u = db.get_user_by_id(uid)
    if not u:
        return False
    return _port_open("127.0.0.1", u["port"])

def render_nginx():
    """生成 nginx 反代配置（用户子域名 -> 本地端口）。"""
    import db
    if not DOMAIN:
        # 防止在环境变量缺失时写出坏配置(如 server_name u1.;)
        return "DOMAIN empty, skip nginx render"
    users = db.list_users()
    lines = [
        f"# 自动生成 @ {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# {DOMAIN}",
    ]
    for u in users:
        if u["status"] != "active":
            continue
        srv = u["subdomain"]
        lines.append(f"""
server {{
    listen 443 ssl http2;
    server_name {srv}.{DOMAIN};

    ssl_certificate     {CERT};
    ssl_certificate_key {CERTKEY};
    ssl_protocols TLSv1.2 TLSv1.3;

    # 未登录/非本人 -> 弹回主站登录页; 实例未启动(proxy 502) -> 弹回主站提示启动
    error_page 401 403 = @need_login;
    error_page 502 = @need_start;

    location / {{
        auth_request /_auth;
        proxy_pass http://127.0.0.1:{u["port"]};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 600s;
        client_max_body_size 200m;
        sub_filter '</head>' '<script src="https://{DOMAIN}/static/announce.js?v=3"></script></head>';
        sub_filter_once on;
    }}

    # 公告接口透传到网关(用户子域也能取公告)
    location = /__announce__ {{
        proxy_pass http://127.0.0.1:18080/__announce__;
    }}

    # 内部鉴权子请求 -> 网关校验 cookie 用户是否匹配该子域名
    location = /_auth {{
        internal;
        proxy_pass http://127.0.0.1:18080/api/auth_check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Subdomain {srv};
    }}

    location @need_login {{
        return 302 https://{DOMAIN}/?need_login=1;
    }}

    location @need_start {{
        return 302 https://{DOMAIN}/?need_start=1;
    }}
}}

server {{
    listen 80;
    server_name {srv}.{DOMAIN};
    return 301 https://$host$request_uri;
}}""")
    try:
        with open(NGINX_CONF, "w") as f:
            f.write("\n".join(lines))
        subprocess.run(["nginx", "-t"], capture_output=True)
        subprocess.run(["nginx", "-s", "reload"])
    except Exception as e:
        return f"nginx reload failed: {e}"
    return "ok"

def _dir_latest_mtime(path):
    """返回目录内最新文件的修改时间(判断是否活跃)。"""
    latest = 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    m = os.path.getmtime(os.path.join(root, f))
                    if m > latest:
                        latest = m
                except Exception:
                    pass
    except Exception:
        pass
    return latest

def reap_stale():
    """回收空闲实例(1小时无活动) + 检测已停止的实例并清理其数据。"""
    import db
    now = time.time()
    users = db.list_users()
    
    # 1) 回收 _procs 中跟踪的空闲实例(确认停止后清理)
    with _lock:
        items = list(_procs.items())
    for uid, p in items:
        u = db.get_user_by_id(uid)
        if not u:
            # 用户已被删除, 强制清理
            stop_instance(uid, clean=True)
            continue
        last = u.get("last_active_at") or p["started_at"]
        d = u.get("data_dir") or os.path.join(BASE_DATA, f"u{uid}")
        last = max(last, _dir_latest_mtime(d))
        if now - last > IDLE_TIMEOUT:
            # 空闲超时: 先停止实例, 确认停止后再清理
            stop_instance(uid, clean=True)
    
    # 2) 检测所有用户: 若实例已关闭(端口和进程都不在)则清空 books 和 logs
    #    保证: 实例关闭后, books 和 logs 目录下必定为空
    for u in users:
        if u["status"] != "active":
            continue
        uid = u["id"]
        data_dir = u.get("data_dir") or os.path.join(BASE_DATA, u["subdomain"])
        # 进程和端口都不在 = 已关闭 -> 清空 books 和 logs
        if not _is_process_alive(uid):
            try:
                clean_user_data(data_dir)
            except Exception:
                pass

def sweep_loop(interval=60):
    while True:
        try:
            reap_stale()
        except Exception as e:
            print("sweep err", e)
        time.sleep(interval)
