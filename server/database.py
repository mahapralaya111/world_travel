# -*- coding: utf-8 -*-
"""
数据库访问层
- 默认使用 SQLite（Python 内置，零配置）
- 通过 config.DB_TYPE 可切换 MySQL（需安装 pymysql）
对外统一提供 get_conn() 返回连接对象，业务层不关心底层数据库类型
"""
import os
import sqlite3
from contextlib import contextmanager

from config import Config


def get_conn():
    """获取数据库连接（每请求独立连接，线程安全）"""
    if Config.DB_TYPE == "mysql":
        return _get_mysql_conn()
    return _get_sqlite_conn()


def _get_sqlite_conn():
    os.makedirs(os.path.dirname(Config.SQLITE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(Config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    # 开启外键约束（用于 trip 级联删除）
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class _MySQLWrapper:
    """
    将 pymysql 连接包装为 sqlite3 风格接口，业务层无需感知数据库类型：
    - conn.execute(sql, params) 可直接调用
    - pymysql 使用 %s 占位符，这里将业务层统一的 ? 自动转换
    """

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _fix(sql):
        # 先转义 LIKE 等处的字面量 %（pymysql 格式化后还原为 %），
        # 再转换占位符，避免 query % args 误将 LIKE 的 % 当作格式符
        return sql.replace("%", "%%").replace("?", "%s")

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(self._fix(sql), params)
        return cur

    def executemany(self, sql, seq_params):
        cur = self._conn.cursor()
        cur.executemany(self._fix(sql), seq_params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        return self._conn.cursor()


def _get_mysql_conn():
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("切换 MySQL 前请先执行: pip install pymysql")
    cfg = Config.MYSQL_CONFIG
    raw = pymysql.connect(
        host=cfg["host"], port=cfg["port"],
        user=cfg["user"], password=cfg["password"],
        database=cfg["database"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    return _MySQLWrapper(raw)


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
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
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
