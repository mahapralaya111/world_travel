# -*- coding: utf-8 -*-
"""
JWT 认证模块
说明：为减少第三方依赖，采用标准库自研实现（HS256 签名，与主流 JWT 完全兼容），
      也可替换为 pyjwt。答辩时可清晰讲解签名与校验过程。
"""
import base64
import hashlib
import hmac
import json
import time

from config import Config


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _sign(header_b64: str, payload_b64: str) -> str:
    message = f"{header_b64}.{payload_b64}".encode("utf-8")
    digest = hmac.new(Config.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).digest()
    return _b64url_encode(digest)


def generate_token(user_id: int, username: str, expires_hours: int = None) -> str:
    """生成 JWT token"""
    expires_hours = expires_hours or Config.JWT_EXPIRES_HOURS
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "uid": user_id,
        "username": username,
        "iat": now,
        "exp": now + expires_hours * 3600,
    }
    header_b64 = _b64url_encode(json.dumps(header, ensure_ascii=False).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    signature = _sign(header_b64, payload_b64)
    return f"{header_b64}.{payload_b64}.{signature}"


def verify_token(token: str):
    """校验 JWT token，成功返回 payload dict，失败返回 None"""
    try:
        header_b64, payload_b64, signature = token.split(".")
        # 签名校验
        if not hmac.compare_digest(_sign(header_b64, payload_b64), signature):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        # 过期校验
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def get_token_from_request(request):
    """提取 token。

    - 优先 Authorization: Bearer xxx（所有方法都支持，推荐）
    - 查询参数 ?token=xxx 只对 GET 生效：用于 <img>/下载链接等无法带请求头的场景。
      写操作(POST/PUT/DELETE)不再接受 URL 里的 token，降低被恶意页面诱导提交(CSRF)的风险。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    if request.method == "GET":
        return (request.args.get("token") or "").strip()
    return ""
