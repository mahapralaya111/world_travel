# -*- coding: utf-8 -*-
"""
大模型并发治理（AI Gate）
=========================
多用户同时使用时的三个核心问题，在这里统一解决：

1) 并发闸门（信号量）：限制"同一时刻真正打大模型"的请求数。
   本地 Ollama 单实例默认串行推理，若不加限制，N 个用户同时点"AI 规划"
   会全部堆在模型侧排队，先到的占满 180s 超时窗口，后到的直接超时失败。
   这里把并发数限制在模型能承受的范围内，超出部分排队等待，
   等待超过上限则快速失败并提示"当前排队人数较多，请稍后重试"。

2) 结果缓存：相同入参（天数/预算/月份/人数/出发地/偏好）直接命中缓存，
   不再重复请求模型。既省算力，又让重复请求秒回。

3) in-flight 去重：多个线程同时请求完全相同的内容时，只让第一个真正调用模型，
   其余线程等待它的结果（Event 通知），避免重复调用。

设计说明：全部基于标准库 threading，无需额外依赖；进程内有效。
"""
import copy
import hashlib
import json
import threading
import time

# ---------------------------------------------------------------------------
# 可调参数（可用环境变量覆盖，见 config.py）
# ---------------------------------------------------------------------------
DEFAULT_MAX_CONCURRENT = 2      # 同时打模型的最大请求数
DEFAULT_WAIT_TIMEOUT = 30       # 排队等待上限（秒），超过则快速失败
DEFAULT_CACHE_TTL = 1800        # 结果缓存有效期（秒），30 分钟
CACHE_MAX = 200                 # 缓存条目上限，防止内存无限增长


class _Gate:
    """并发闸门 + 缓存 + in-flight 去重的组合体"""

    def __init__(self, max_concurrent=DEFAULT_MAX_CONCURRENT,
                 wait_timeout=DEFAULT_WAIT_TIMEOUT, cache_ttl=DEFAULT_CACHE_TTL):
        self.max_concurrent = max(1, int(max_concurrent))
        self.wait_timeout = max(1, int(wait_timeout))
        self.cache_ttl = max(0, int(cache_ttl))
        # 信号量：限制真正在执行模型调用的线程数
        self._sem = threading.BoundedSemaphore(self.max_concurrent)
        # 缓存：key -> (写入时间戳, 结果)
        self._cache = {}
        # in-flight：key -> threading.Event（第一个调用者完成后 set）
        self._inflight = {}
        # 保护 _cache / _inflight 的锁（与信号量分离，防止长时间持锁）
        self._lock = threading.Lock()

    # ---------------- 缓存 ----------------
    def _cache_get(self, key):
        with self._lock:
            hit = self._cache.get(key)
            if not hit:
                return None
            ts, value = hit
            if self.cache_ttl and time.time() - ts >= self.cache_ttl:
                self._cache.pop(key, None)
                return None
            return value

    def _cache_put(self, key, value):
        if not self.cache_ttl:
            return
        with self._lock:
            # 超上限时先清过期项，仍超则整体清空（AI 结果可重算）
            if len(self._cache) >= CACHE_MAX:
                now = time.time()
                for k, (ts, _) in list(self._cache.items()):
                    if now - ts >= self.cache_ttl:
                        self._cache.pop(k, None)
                if len(self._cache) >= CACHE_MAX:
                    self._cache.clear()
            self._cache[key] = (time.time(), value)

    # ---------------- 主入口 ----------------
    def run(self, key, fn):
        """执行 fn（一次模型调用），带闸门 / 缓存 / 去重。

        key: 用于缓存与去重的字符串（相同 key 视为同一请求）
        fn : 无参可调用对象，返回结果（必须可被多线程共享读取的普通对象）

        抛 _Busy 表示排队超时（调用方转成友好提示）。
        fn 自身抛出的异常会原样向上传播（不写入缓存）。
        """
        if self.cache_ttl:
            cached = self._cache_get(key)
            if cached is not None:
                return copy.deepcopy(cached)

        # ---- in-flight 去重：若已有相同 key 在执行，等待它的结果 ----
        with self._lock:
            ev = self._inflight.get(key)
        if ev is not None:
            # 等待正在执行的同 key 请求完成（不超过排队上限）
            if ev.wait(self.wait_timeout):
                cached = self._cache_get(key)
                if cached is not None:
                    return copy.deepcopy(cached)
            # 对方没等到 / 之后缓存又没了 → 自己走正常流程
            with self._lock:
                if self._inflight.get(key) is ev:
                    self._inflight.pop(key, None)

        # ---- 获取并发闸门（排队等待）----
        acquired = self._sem.acquire(timeout=self.wait_timeout)
        if not acquired:
            raise _Busy()

        try:
            # 二次检查缓存（排队期间别人可能已经算好并写入）
            if self.cache_ttl:
                cached = self._cache_get(key)
                if cached is not None:
                    return copy.deepcopy(cached)

            # 登记 in-flight，供后来者等待
            with self._lock:
                my_ev = threading.Event()
                # 若已有他人登记，说明极短窗口内又来了一个，让给对方先登记
                if key in self._inflight:
                    my_ev = None
                else:
                    self._inflight[key] = my_ev

            try:
                result = fn()
            finally:
                if my_ev is not None:
                    with self._lock:
                        # 只清理自己的登记
                        if self._inflight.get(key) is my_ev:
                            self._inflight.pop(key, None)
                    my_ev.set()

            if self.cache_ttl:
                self._cache_put(key, result)
            return result
        finally:
            self._sem.release()

    # ---------------- 观测 ----------------
    def stats(self):
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "cache_entries": len(self._cache),
                "inflight": len(self._inflight),
            }

    def clear_cache(self):
        with self._lock:
            self._cache.clear()


class _Busy(RuntimeError):
    """排队超时（并发已满且等待超过上限）"""

    def __init__(self):
        super().__init__("大模型当前繁忙，请稍后重试")


def make_key(*parts):
    """把任意可哈希参数拼成一个稳定的短 key"""
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
