# -*- coding: utf-8 -*-
"""认证路由：注册 / 登录 / 当前用户信息"""
import re
import threading
import time

from flask import Blueprint, jsonify, request

from auth import generate_token, verify_token, get_token_from_request
from database import query_one, execute
from utils import is_valid_username, is_valid_password
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# ---------- 登录失败限流（防暴力破解） ----------
_FAIL_WINDOW = 300   # 统计窗口（秒）
_FAIL_MAX = 10       # 窗口内允许的最大失败次数
_fail_lock = threading.Lock()
_login_fails = {}    # "ip|username" -> [失败时间戳, ...]


def _client_ip():
    """取客户端 IP（兼容 Nginx 等反向代理）"""
    fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return fwd or request.remote_addr or "-"


def _fail_key(username):
    return "%s|%s" % (_client_ip(), (username or "").lower())


def _is_locked(key):
    now = time.time()
    with _fail_lock:
        hits = [t for t in _login_fails.get(key, []) if now - t < _FAIL_WINDOW]
        _login_fails[key] = hits
        if len(_login_fails) > 5000:  # 兜底：避免异常流量把字典撑爆
            for k in [k for k, v in list(_login_fails.items()) if not v][:1000]:
                _login_fails.pop(k, None)
        return len(hits) >= _FAIL_MAX


def _record_fail(key):
    with _fail_lock:
        _login_fails.setdefault(key, []).append(time.time())


def _clear_fails(key):
    with _fail_lock:
        _login_fails.pop(key, None)


def ok(data=None, msg="success"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=1, http=200):
    return jsonify({"code": code, "msg": msg, "data": None}), http


@auth_bp.route("/register", methods=["POST"])
def register():
    """用户注册"""
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    nickname = (body.get("nickname") or "").strip()
    email = (body.get("email") or "").strip()

    if not is_valid_username(username):
        return fail("用户名需为2-20位中英文/数字/下划线")
    if not is_valid_password(password):
        return fail("密码长度至少6位")
    if len(nickname) > 30:
        return fail("昵称最长 30 个字符")
    if email and (len(email) > 100 or not _EMAIL_RE.match(email)):
        return fail("邮箱格式不正确")
    if query_one("SELECT id FROM users WHERE username = ?", (username,)):
        return fail("用户名已被注册")

    password_hash = generate_password_hash(password)
    try:
        uid = execute(
            "INSERT INTO users (username, password_hash, nickname, email) VALUES (?,?,?,?)",
            (username, password_hash, nickname or username, email),
        )
    except Exception:
        # 并发注册同一用户名时由数据库唯一索引兜底，转成友好提示而不是 500
        return fail("用户名已被注册")
    token = generate_token(uid, username)
    # 注册成功自动创建一个默认行程
    execute(
        "INSERT INTO trips (user_id, name, description, cover_color) VALUES (?,?,?,?)",
        (uid, "我的首次旅行", "默认创建的旅行计划，可随时修改或删除", "#4a9eff"),
    )
    return ok({"id": uid, "username": username, "nickname": nickname or username, "token": token}, "注册成功")


@auth_bp.route("/login", methods=["POST"])
def login():
    """用户登录"""
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return fail("请输入用户名和密码")

    # 限流：同一 IP + 账号 5 分钟内失败超过 10 次则拒绝，防止密码被暴力破解
    key = _fail_key(username)
    if _is_locked(key):
        return fail("登录失败次数过多，请 5 分钟后再试", 429, 429)

    user = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user or not check_password_hash(user["password_hash"], password):
        _record_fail(key)
        return fail("用户名或密码错误")
    _clear_fails(key)
    token = generate_token(user["id"], user["username"])
    return ok({
        "id": user["id"],
        "username": user["username"],
        "nickname": user["nickname"] or user["username"],
        "email": user["email"],
        "token": token,
    }, "登录成功")


@auth_bp.route("/me", methods=["GET"])
def me():
    """获取当前登录用户信息"""
    payload = verify_token(get_token_from_request(request))
    if not payload:
        return fail("未登录或登录已过期", 401, 401)
    user = query_one("SELECT id, username, nickname, email, created_at FROM users WHERE id = ?", (payload["uid"],))
    if not user:
        return fail("用户不存在", 401, 401)
    return ok(user)


@auth_bp.route("/update", methods=["POST"])
def update_profile():
    """更新个人资料（昵称 / 邮箱 / 密码）"""
    payload = verify_token(get_token_from_request(request))
    if not payload:
        return fail("未登录或登录已过期", 401, 401)
    body = request.get_json(silent=True) or {}
    nickname = (body.get("nickname") or "").strip()
    email = (body.get("email") or "").strip()
    old_pw = body.get("old_password") or ""
    new_pw = body.get("new_password") or ""

    user = query_one("SELECT * FROM users WHERE id = ?", (payload["uid"],))
    if not user:
        return fail("用户不存在", 401, 401)
    if len(nickname) > 30:
        return fail("昵称最长 30 个字符")
    if email and (len(email) > 100 or not _EMAIL_RE.match(email)):
        return fail("邮箱格式不正确")

    updates, params = [], []
    if nickname and nickname != user["username"]:
        updates.append("nickname = ?")
        params.append(nickname)
    if email:
        updates.append("email = ?")
        params.append(email)
    if new_pw:
        if not old_pw or not check_password_hash(user["password_hash"], old_pw):
            return fail("原密码不正确")
        if not is_valid_password(new_pw):
            return fail("新密码长度至少6位")
        updates.append("password_hash = ?")
        params.append(generate_password_hash(new_pw))
    if not updates:
        return fail("没有需要更新的内容")
    params.append(payload["uid"])
    execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    return ok(msg="资料更新成功")
