# -*- coding: utf-8 -*-
"""
数据库访问层
- 默认使用 SQLite（Python 内置，零配置）
- 通过 config.DB_TYPE 可切换 MySQL（需安装 pymysql）
对外统一提供 get_conn() 返回连接对象，业务层不关心底层数据库类型
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import Config


def get_conn():
    """获取数据库连接（每请求独立连接，线程安全）"""
    if Config.DB_TYPE == "mysql":
        return _get_mysql_conn()
    return _get_sqlite_conn()


def _get_sqlite_conn():
    os.makedirs(os.path.dirname(Config.SQLITE_PATH) or ".", exist_ok=True)
    # timeout=15：写锁等待上限（秒）。多用户并发写时，避免直接抛 database is locked
    conn = sqlite3.connect(Config.SQLITE_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # 开启外键约束（用于 trip 级联删除）
    conn.execute("PRAGMA foreign_keys = ON")
    # 多用户并发优化：WAL 模式下读写互不阻塞；busy_timeout 让写冲突时排队等待而非立即报错
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error:
        pass  # 某些环境/只读文件系统不支持 WAL，降级为默认模式即可
    return conn


class _MySQLWrapper:
    """
    将 pymysql 连接包装为 sqlite3 风格接口，业务层无需感知数据库类型：
    - conn.execute(sql, params) 可直接调用
    - pymysql 使用 %s 占位符，这里将业务层统一的 ? 自动转换

    连接由线程本地池复用（见 _get_mysql_conn），close() 不再真正关闭连接，
    而是把连接归还线程本地，避免每请求都重新握手（多用户并发下的连接风暴）。
    """

    def __init__(self, conn):
        self._conn = conn
        self._dirty = False  # 是否存在未提交的写操作

    @staticmethod
    def _fix(sql):
        # 先转义 LIKE 等处的字面量 %（pymysql 格式化后还原为 %），
        # 再转换占位符，避免 query % args 误将 LIKE 的 % 当作格式符
        return sql.replace("%", "%%").replace("?", "%s")

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(self._fix(sql), params)
        self._dirty = True
        return cur

    def executemany(self, sql, seq_params):
        cur = self._conn.cursor()
        cur.executemany(self._fix(sql), seq_params)
        self._dirty = True
        return cur

    def commit(self):
        self._conn.commit()
        self._dirty = False

    def rollback(self):
        self._conn.rollback()
        self._dirty = False

    def autocommit(self, flag):
        """切换自动提交：transaction() 用它显式开启/结束事务"""
        self._conn.autocommit(flag)
        self._dirty = False

    def ping(self, reconnect=True):
        return self._conn.ping(reconnect)

    def close(self):
        """归还连接（非真正关闭）：若残留未提交事务，先回滚，保证下个请求从干净状态开始"""
        try:
            if self._dirty:
                self._conn.rollback()
                self._dirty = False
        except Exception:
            pass

    def destroy(self):
        """真正关闭连接（连接失效/进程退出时用）"""
        try:
            self._conn.close()
        except Exception:
            pass

    def cursor(self):
        return self._conn.cursor()


# 线程本地 MySQL 连接池：每个工作线程复用一条连接
_mysql_local = threading.local()


def _get_mysql_conn():
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("切换 MySQL 前请先执行: pip install pymysql")

    # 复用本线程已有连接；ping 失败（服务端空闲断开/重启）则丢弃重建
    wrapper = getattr(_mysql_local, "conn", None)
    if wrapper is not None:
        try:
            wrapper.ping(reconnect=True)
            return wrapper
        except Exception:
            wrapper.destroy()
            _mysql_local.conn = None

    cfg = Config.MYSQL_CONFIG
    raw = pymysql.connect(
        host=cfg["host"], port=cfg["port"],
        user=cfg["user"], password=cfg["password"],
        database=cfg["database"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,        # 默认自动提交：避免长事务读到旧快照（多用户下数据不一致）
        connect_timeout=10,     # 建连超时
        read_timeout=60,        # 读超时，防止慢查询/锁等待无限挂起
        write_timeout=60,       # 写超时
    )
    wrapper = _MySQLWrapper(raw)
    _mysql_local.conn = wrapper
    return wrapper


@contextmanager
def transaction():
    """事务上下文：把多步写操作放进一个事务，失败自动回滚（避免"删了旧数据却没插入新数据"）

    用法：
        with transaction() as conn:
            conn.execute("DELETE ...", (...))
            conn.executemany("INSERT ...", rows)
        # 正常退出自动 commit，异常自动 rollback 并向上抛出
    """
    conn = get_conn()
    is_mysql = Config.DB_TYPE == "mysql"
    try:
        # MySQL 默认 autocommit=True（单条语句即提交，避免长事务读到旧快照）；
        # 这里显式关掉自动提交，把后续多条语句纳入同一个事务，保证原子性
        if is_mysql:
            conn.autocommit(False)
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if is_mysql:
            try:
                conn.autocommit(True)
            except Exception:
                pass
        conn.close()


def query_one(sql, params=()):
    """查询单行，返回 dict 或 None"""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row) if isinstance(row, sqlite3.Row) else row
    finally:
        conn.close()


def query_all(sql, params=()):
    """查询多行，返回 list[dict]"""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def execute(sql, params=()):
    """执行写操作，返回 lastrowid；失败自动回滚后抛出"""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def execute_many(sql, seq_params):
    """批量执行写操作；失败自动回滚后抛出"""
    conn = get_conn()
    try:
        conn.executemany(sql, seq_params)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
