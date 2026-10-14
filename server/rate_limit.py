# -*- coding: utf-8 -*-
"""
轻量级进程内限流（滑动窗口）
============================
用于保护昂贵的接口（AI 规划、行程生成、离线包导出）在多用户场景下不被滥用：
- 按 key（通常是 IP）限制单位时间内的调用次数
- 纯标准库实现，无第三方依赖
- 线程安全

注意：进程内有效。若将来用多进程/多机部署（gunicorn -w >1），
需替换为 Redis 等共享存储的限流实现。
"""
import threading
import time
from collections import deque

from flask import request

_lock = threading.Lock()
_hits = {}          # key -> deque[timestamp, ...]
_MAX_KEYS = 20000   # key 数量上限，防止异常流量把内存撑爆


def client_ip():
    """取客户端 IP（兼容 Nginx 等反向代理），与 auth_routes 保持一致"""
    fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return fwd or request.remote_addr or "-"


def allow(key, max_calls, window_sec):
    """滑动窗口限流：window_sec 秒内最多 max_calls 次。

    返回 True 表示放行；False 表示已超限（调用方应返回 429）。
    """
    now = time.time()
    with _lock:
        dq = _hits.get(key)
        if dq is None:
            dq = deque()
            _hits[key] = dq
        # 丢弃窗口外的旧记录
        while dq and now - dq[0] >= window_sec:
            dq.popleft()
        if len(dq) >= max_calls:
            return False
        dq.append(now)
        # 兜底清理：防止 key 无限增长（清理空桶）
        if len(_hits) > _MAX_KEYS:
            for k in [k for k, v in list(_hits.items()) if not v][: _MAX_KEYS // 10]:
                _hits.pop(k, None)
        return True


def retry_after(key, window_sec):
    """距离最早一次调用滑出窗口还需多少秒（用于 Retry-After 提示）"""
    now = time.time()
    with _lock:
        dq = _hits.get(key)
        if not dq:
            return 0
        return max(0, int(window_sec - (now - dq[0])) + 1)
