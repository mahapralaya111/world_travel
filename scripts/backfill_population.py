# -*- coding: utf-8 -*-
"""
回填 cities 表 population 人口数据
- 自动检测/添加 population 列（MySQL / SQLite 兼容）
- 以 (country_code, name_en, lat, lng) 精确匹配 cities.json 中的人口数据
- 幂等：可重复运行
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from config import Config  # noqa: E402
from database import get_conn, query_all  # noqa: E402


def ensure_population_column(conn):
    """确保 cities 表存在 population 列"""
    if Config.DB_TYPE == "mysql":
        rows = query_all("SHOW COLUMNS FROM cities LIKE 'population'", ())
        if not rows:
            query_all("ALTER TABLE cities ADD COLUMN population BIGINT DEFAULT 0", ())
            print("[mysql] 已添加 population 列")
    else:
        rows = query_all("PRAGMA table_info(cities)", ())
        if not any(r.get("name") == "population" for r in rows):
            query_all("ALTER TABLE cities ADD COLUMN population INTEGER DEFAULT 0", ())
            print("[sqlite] 已添加 population 列")


def build_index():
    """从 cities.json 构建 (country_code, name_en_lower, lat, lng) -> population 索引"""
    data_file = os.path.join(Config.DATA_DIR, "cities.json")
    if not os.path.exists(data_file):
        print("未找到 %s" % data_file)
        sys.exit(1)
    with open(data_file, "r", encoding="utf-8") as f:
        cities = json.load(f)
    idx = {}
    for c in cities:
        key = (
            str(c.get("country_code", "")).upper(),
            (c.get("name_en") or "").strip().lower(),
            round(float(c.get("lat", 0)), 2),
            round(float(c.get("lng", 0)), 2),
        )
        idx[key] = int(c.get("population", 0) or 0)
    print("[json] 索引条数:", len(idx))
    return idx


def backfill():
    conn = get_conn()
    try:
        ensure_population_column(conn)
        idx = build_index()

        rows = query_all("SELECT id, country_code, name_en, lat, lng FROM cities", ())
        updated = skipped = 0
        batch = []
        for r in rows:
            key = (
                str(r.get("country_code", "")).upper(),
                (r.get("name_en") or "").strip().lower(),
                round(float(r.get("lat", 0)), 2),
                round(float(r.get("lng", 0)), 2),
            )
            pop = idx.get(key)
            if pop is None:
                skipped += 1
                continue
            batch.append((pop, r["id"]))
            updated += 1
            if len(batch) >= 500:
                conn.executemany("UPDATE cities SET population=? WHERE id=?", batch)
                conn.commit()
                batch = []
        if batch:
            conn.executemany("UPDATE cities SET population=? WHERE id=?", batch)
            conn.commit()
        print("[done] 更新 %d 条，未匹配 %d 条" % (updated, skipped))
    finally:
        conn.close()


if __name__ == "__main__":
    backfill()
