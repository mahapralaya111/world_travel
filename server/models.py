# -*- coding: utf-8 -*-
"""
数据模型：建表语句 + 基础数据初始化
表清单：
  users        用户表（账号密码 / 注册登录）
  trips        行程表（一个用户可建多个行程，行程间数据隔离）
  annotations  批注表（标注/笔记，归属行程+国家）
  routes       路线表（绘制的路线，归属行程+国家）
  notes        游记表（公开分享）
  cities       城市表（内置数据，支持多语言搜索）
"""
import json
import os

from config import Config
from database import get_conn

# 建表语句（与数据库类型无关的标准 SQL）
TABLES = {
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname      TEXT DEFAULT '',
            email         TEXT DEFAULT '',
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """,
    "trips": """
        CREATE TABLE IF NOT EXISTS trips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            start_date  TEXT DEFAULT '',
            end_date    TEXT DEFAULT '',
            cover_color TEXT DEFAULT '#4a9eff',
            status      TEXT DEFAULT 'planning',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            updated_at  TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """,
    "annotations": """
        CREATE TABLE IF NOT EXISTS annotations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id      INTEGER NOT NULL,
            country_id   TEXT NOT NULL,
            country_name TEXT DEFAULT '',
            client_id    TEXT DEFAULT '',
            title        TEXT NOT NULL,
            content      TEXT DEFAULT '',
            lat          REAL NOT NULL,
            lng          REAL NOT NULL,
            color        TEXT DEFAULT '#ff5252',
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        )
    """,
    "routes": """
        CREATE TABLE IF NOT EXISTS routes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id      INTEGER NOT NULL,
            country_id   TEXT NOT NULL,
            country_name TEXT DEFAULT '',
            client_id    TEXT DEFAULT '',
            name         TEXT NOT NULL,
            points       TEXT NOT NULL,
            color        TEXT DEFAULT '#4a9eff',
            distance     REAL DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        )
    """,
    "notes": """
        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            trip_id     INTEGER DEFAULT NULL,
            title       TEXT NOT NULL,
            content     TEXT DEFAULT '',
            is_public   INTEGER DEFAULT 1,
            view_count  INTEGER DEFAULT 0,
            share_token VARCHAR(64) DEFAULT NULL,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """,
    "cities": """
        CREATE TABLE IF NOT EXISTS cities (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT NOT NULL,
            country_zh   TEXT DEFAULT '',
            country_en   TEXT DEFAULT '',
            name         TEXT NOT NULL,
            name_en      TEXT DEFAULT '',
            name_local   TEXT DEFAULT '',
            lat          REAL NOT NULL,
            lng          REAL NOT NULL,
            sights       TEXT DEFAULT '[]',
            is_capital   INTEGER DEFAULT 0,
            population   INTEGER DEFAULT 0
        )
    """,
    "trip_plans": """
        CREATE TABLE IF NOT EXISTS trip_plans (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id    INTEGER NOT NULL UNIQUE,
            plan_data  TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
        )
    """,
    "favorites": """
        CREATE TABLE IF NOT EXISTS favorites (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            item_type    TEXT NOT NULL,          -- 'city' 城市 | 'sight' 景点
            item_key     TEXT NOT NULL,          -- 唯一标识: city_<id> / sight_<id>_<name>
            city_id      INTEGER DEFAULT 0,
            city_name    TEXT DEFAULT '',
            sight_name   TEXT DEFAULT '',
            country_code TEXT DEFAULT '',
            country_zh   TEXT DEFAULT '',
            lat          REAL DEFAULT 0,
            lng          REAL DEFAULT 0,
            tags         TEXT DEFAULT '[]',      -- JSON 标签数组（用于推荐算法）
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, item_key)
        )
    """,
    "share_tokens": """
        CREATE TABLE IF NOT EXISTS share_tokens (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            token        VARCHAR(64) UNIQUE NOT NULL,
            owner_id     INTEGER NOT NULL,
            trip_id      INTEGER DEFAULT NULL,
            scope        VARCHAR(16) NOT NULL DEFAULT 'country',
            country_id   VARCHAR(64) DEFAULT '',
            country_name VARCHAR(128) DEFAULT '',
            title        VARCHAR(200) DEFAULT '',
            snapshot     TEXT NOT NULL,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            expire_at    TEXT DEFAULT NULL,
            FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """,
    # 乐观锁版本表：记录"行程+国家"粒度的同步版本号。
    # 多端/多标签页同时同步同一国家时，版本不匹配即拒绝，避免后写覆盖先写导致数据丢失。
    "sync_state": """
        CREATE TABLE IF NOT EXISTS sync_state (
            trip_id        INTEGER NOT NULL,
            country_id     VARCHAR(64) NOT NULL,
            rev            INTEGER NOT NULL DEFAULT 0,
            last_client_id VARCHAR(64) DEFAULT '',
            updated_at     TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (trip_id, country_id)
        )
    """,
}

# ---------------------------------------------------------------------------
# 索引：多用户/数据量增长后，避免全表扫描（幂等，每次启动补齐缺失的索引）
# 说明：MySQL 的 TEXT 列必须带前缀长度才能建索引，故 SQLite / MySQL 列表达式分开写
# ---------------------------------------------------------------------------
INDEXES = [
    # (索引名, 表名, SQLite 列表达式, MySQL 列表达式)
    ("idx_annotations_trip",        "annotations", "trip_id",            "trip_id"),
    ("idx_annotations_trip_country", "annotations", "trip_id, country_id", "trip_id, country_id(32)"),
    ("idx_routes_trip",             "routes",      "trip_id",            "trip_id"),
    ("idx_routes_trip_country",     "routes",      "trip_id, country_id", "trip_id, country_id(32)"),
    ("idx_trips_user",              "trips",       "user_id",            "user_id"),
    ("idx_cities_country",          "cities",      "country_code",       "country_code(16)"),
    ("idx_cities_name",             "cities",      "name",               "name(24)"),
    ("idx_notes_user",              "notes",       "user_id",            "user_id"),
    ("idx_notes_share",             "notes",       "share_token",        "share_token"),
    ("idx_favorites_user",          "favorites",   "user_id",            "user_id"),
]


def _mysql_index_exists(conn, table, name):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?",
        (table, name),
    ).fetchone()
    if row is None:
        return False
    return bool(row["c"] if isinstance(row, dict) else row[0])


def _ensure_indexes(conn):
    """补齐缺失的索引（幂等，失败不阻断启动）"""
    for name, table, sqlite_cols, mysql_cols in INDEXES:
        try:
            if Config.DB_TYPE == "mysql":
                # MySQL 不支持 CREATE INDEX IF NOT EXISTS，先查 information_schema 再建
                if _mysql_index_exists(conn, table, name):
                    continue
                try:
                    conn.execute(f"CREATE INDEX {name} ON {table} ({mysql_cols})")
                except Exception:
                    # 列可能是较短的 VARCHAR（前缀长度会超过列长），退化为不带前缀再建
                    plain = re.sub(r"\(\d+\)", "", mysql_cols)
                    conn.execute(f"CREATE INDEX {name} ON {table} ({plain})")
            else:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({sqlite_cols})")
        except Exception as e:
            # 索引已存在（并发创建）或旧库列缺失等情况：记录后继续，不影响服务
            print(f"[models] 跳过索引 {name}: {e}")


import re


def _compat_ddl(ddl):
    """将通用建表 SQL 转为当前数据库类型兼容语法"""
    if Config.DB_TYPE != "mysql":
        return ddl
    # SQLite -> MySQL 语法差异
    ddl = ddl.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        "INTEGER PRIMARY KEY AUTO_INCREMENT",
    )
    ddl = ddl.replace(
        "TEXT DEFAULT (datetime('now','localtime'))",
        "DATETIME DEFAULT CURRENT_TIMESTAMP",
    )
    # share_tokens 建表里特意写成 "TEXT DEFAULT NULL" / "VARCHAR(...) DEFAULT NULL" 的列，
    # MySQL 下统一转 DATETIME DEFAULT NULL 以便跟 trips.created_at 同类型一致
    ddl = re.sub(
        r"(created_at|expire_at|updated_at)\s+(TEXT|VARCHAR\(\d+\))\s+DEFAULT\s+NULL",
        r"\1 DATETIME DEFAULT NULL",
        ddl,
        flags=re.IGNORECASE,
    )
    # MySQL 中 TEXT 列不能用于 UNIQUE/索引 → 转 VARCHAR（列名后有多个空格，用正则匹配）
    ddl = re.sub(
        r"username\s+TEXT UNIQUE NOT NULL",
        "username VARCHAR(50) UNIQUE NOT NULL",
        ddl,
    )
    ddl = re.sub(
        r"item_key\s+TEXT NOT NULL",
        "item_key VARCHAR(255) NOT NULL",
        ddl,
    )
    # MySQL 的 TEXT 列不允许 DEFAULT 值，去掉（业务 INSERT 均已显式提供这些列）
    ddl = re.sub(r"TEXT DEFAULT '[^']*'", "TEXT", ddl)
    return ddl


def init_db():
    """建表 + 导入城市基础数据（幂等）"""
    conn = get_conn()
    try:
        for name, ddl in TABLES.items():
            conn.execute(_compat_ddl(ddl))
        conn.commit()

        # 补齐缺失索引（多用户/大数据量下避免全表扫描）
        _ensure_indexes(conn)
        conn.commit()

        # 城市表为空时导入 data/cities.json
        count = conn.execute("SELECT COUNT(*) AS c FROM cities").fetchone()
        if isinstance(count, dict):
            total = count["c"]
        else:
            total = count[0]
        if total == 0:
            _seed_cities(conn)
            # 校验结果：全新部署若两个数据源都没有，城市表会为空（搜索/AI 规划会失效），
            # 这里给出明确指引，避免"启动成功但功能不可用"的困惑
            count2 = conn.execute("SELECT COUNT(*) AS c FROM cities").fetchone()
            total2 = count2["c"] if isinstance(count2, dict) else count2[0]
            if total2 == 0:
                print("[models] [警告] 城市表为空：未找到 server/data/cities.json。"
                      "城市搜索与 AI 规划将不可用，请先执行: python scripts/import_all_cities.py")
    finally:
        conn.close()


def _seed_cities(conn):
    """从 data/cities.json 导入城市数据"""
    data_file = os.path.join(Config.DATA_DIR, "cities.json")
    if not os.path.exists(data_file):
        return
    with open(data_file, "r", encoding="utf-8") as f:
        cities = json.load(f)
    rows = []
    for c in cities:
        rows.append((
            str(c.get("country_code", "")),
            c.get("country_zh", ""),
            c.get("country_en", ""),
            c.get("name", ""),
            c.get("name_en", ""),
            c.get("name_local", ""),
            c.get("lat", 0),
            c.get("lng", 0),
            json.dumps(c.get("sights", []), ensure_ascii=False),
            1 if c.get("is_capital") else 0,
            int(c.get("population") or 0),
        ))
    if rows:
        conn.executemany(
            "INSERT INTO cities (country_code,country_zh,country_en,name,name_en,name_local,lat,lng,sights,is_capital,population) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
