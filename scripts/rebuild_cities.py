# -*- coding: utf-8 -*-
"""
重建 cities 表（数据源：server/data/cities.json）

1. 清洗：
   - 按 (country_code, name_en) 分组，坐标相近（<0.3 度）的重复记录合并
     合并规则：景点取并集、人口取最大值、首都标记取有
   - 用 GeoNames countryInfo.txt 的 Capital 字段标记首都（精确匹配 -> 前缀匹配）
2. 重写 server/data/cities.json（干净版，含 population / is_capital）
3. 重建数据库 cities 表（DROP + CREATE + 批量导入，含 population 列）

注意：会重建 cities 表，favorites 等按 city_name 关联，不受影响。
用法: python scripts/rebuild_cities.py
"""
import json
import os
import sys
import unicodedata

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, os.path.join(ROOT, "server"))
os.chdir(ROOT)

from config import Config  # noqa: E402
from database import get_conn  # noqa: E402

DATA_FILE = os.path.join(ROOT, "server", "data", "cities.json")
INFO_FILE = os.path.join(BASE, ".countryInfo.txt")
MERGE_DIST = 0.3  # 合并阈值（经纬度度数）


def norm(s):
    """归一化用于匹配：去重音符号 + 小写 + 去标点"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    for ch in ".,;:'’‘\"`-()[]/":
        s = s.replace(ch, "")
    return s.lower().strip()


def parse_country_info():
    """countryInfo.txt -> {alpha2: capital}"""
    capitals = {}
    if not os.path.exists(INFO_FILE):
        print("[warn] 缺少 %s，跳过首都标记" % INFO_FILE)
        return capitals
    with open(INFO_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            alpha2, capital = parts[0], parts[5]
            if alpha2 and capital:
                capitals[alpha2] = capital
    return capitals


def merge_group(group):
    """将坐标相近的重复城市合并为一个"""
    group = sorted(group, key=lambda c: c.get("lat", 0))
    clusters = []  # list[list]
    for c in group:
        placed = False
        for cl in clusters:
            anchor = cl[0]
            if abs(anchor.get("lat", 0) - c.get("lat", 0)) < MERGE_DIST and \
               abs(anchor.get("lng", 0) - c.get("lng", 0)) < MERGE_DIST:
                cl.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])

    out = []
    for cl in clusters:
        if len(cl) == 1:
            out.append(cl[0])
            continue
        # 主记录：优先有景点 -> 有人口 -> 首都 -> 名字含中文
        def rank(c):
            return (
                1 if c.get("sights") else 0,
                1 if (c.get("population") or 0) > 0 else 0,
                1 if c.get("is_capital") else 0,
                1 if any('\u4e00' <= ch <= '\u9fff' for ch in c.get("name", "")) else 0,
                c.get("population") or 0,
            )
        best = dict(max(cl, key=rank))
        sights = set()
        for c in cl:
            for s in (c.get("sights") or []):
                sights.add(s)
        best["sights"] = sorted(sights)
        best["population"] = max((c.get("population") or 0) for c in cl)
        best["is_capital"] = 1 if any(c.get("is_capital") for c in cl) else best.get("is_capital", 0)
        out.append(best)
    return out


def clean_cities(cities, capitals):
    """去重合并 + 首都标记"""
    # 1. 首都标记（精确 -> 前缀）
    by_country = {}
    for c in cities:
        by_country.setdefault(c["country_code"], []).append(c)
    for code, capital in capitals.items():
        clist = by_country.get(code)
        if not clist:
            continue
        ncap = norm(capital)
        if not ncap:
            continue
        # 精确匹配（多个同名城市时选人口最大的，如美国多个 Washington）
        exact = [c for c in clist if norm(c.get("name_en", "")) == ncap]
        if exact:
            matched = max(exact, key=lambda c: c.get("population") or 0)
        else:
            # 前缀匹配：Washington -> Washington D.C. 等
            cands = [c for c in clist
                     if norm(c.get("name_en", "")) and
                     (ncap.startswith(norm(c["name_en"])) or norm(c["name_en"]).startswith(ncap))]
            if cands:
                matched = max(cands, key=lambda c: c.get("population") or 0)
            else:
                matched = None
        if matched:
            # 一个国家只保留一个首都标记（清掉旧标记再设置）
            for c in clist:
                c["is_capital"] = 0
            matched["is_capital"] = 1

    # 2. 聚类合并
    from collections import defaultdict
    groups = defaultdict(list)
    for c in cities:
        key = (c["country_code"].upper(), norm(c.get("name_en", "")))
        groups[key].append(c)

    cleaned = []
    for key in sorted(groups.keys()):
        cleaned.extend(merge_group(groups[key]))
    return cleaned


def rebuild_db(cities):
    conn = get_conn()
    try:
        if Config.DB_TYPE == "mysql":
            conn.execute("DROP TABLE IF EXISTS cities", ())
            conn.execute(
                "CREATE TABLE cities ("
                "id INT AUTO_INCREMENT PRIMARY KEY,"
                "country_code VARCHAR(8) NOT NULL,"
                "country_zh VARCHAR(128) DEFAULT '',"
                "country_en VARCHAR(128) DEFAULT '',"
                "name VARCHAR(255) NOT NULL,"
                "name_en VARCHAR(255) DEFAULT '',"
                "name_local VARCHAR(255) DEFAULT '',"
                "lat DOUBLE NOT NULL,"
                "lng DOUBLE NOT NULL,"
                "sights TEXT,"
                "is_capital INT DEFAULT 0,"
                "population BIGINT DEFAULT 0"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", ())
        else:
            conn.execute("DROP TABLE IF EXISTS cities", ())
            conn.execute(
                "CREATE TABLE cities ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "country_code TEXT NOT NULL,"
                "country_zh TEXT DEFAULT '',"
                "country_en TEXT DEFAULT '',"
                "name TEXT NOT NULL,"
                "name_en TEXT DEFAULT '',"
                "name_local TEXT DEFAULT '',"
                "lat REAL NOT NULL,"
                "lng REAL NOT NULL,"
                "sights TEXT DEFAULT '[]',"
                "is_capital INTEGER DEFAULT 0,"
                "population INTEGER DEFAULT 0"
                ")", ())
        conn.commit()

        rows = [
            (
                c["country_code"], c.get("country_zh", ""), c.get("country_en", ""),
                c.get("name", ""), c.get("name_en", ""), c.get("name_local", ""),
                c.get("lat", 0), c.get("lng", 0),
                json.dumps(c.get("sights") or [], ensure_ascii=False),
                1 if c.get("is_capital") else 0,
                int(c.get("population") or 0),
            )
            for c in cities
        ]
        conn.executemany(
            "INSERT INTO cities (country_code,country_zh,country_en,name,name_en,name_local,"
            "lat,lng,sights,is_capital,population) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        print("[db] 已导入 %d 条城市记录" % len(rows))
    finally:
        conn.close()


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        cities = json.load(f)
    print("原始城市数:", len(cities))

    capitals = parse_country_info()
    cleaned = clean_cities(cities, capitals)
    print("清洗后城市数:", len(cleaned))

    # 统计
    cap_n = sum(1 for c in cleaned if c.get("is_capital"))
    pop_n = sum(1 for c in cleaned if (c.get("population") or 0) > 0)
    cn_n = sum(1 for c in cleaned if c["country_code"] == "CN")
    us_n = sum(1 for c in cleaned if c["country_code"] == "US")
    print("首都标记: %d 个, 有人口: %d 个" % (cap_n, pop_n))
    print("中国: %d 城市, 美国: %d 城市" % (cn_n, us_n))

    # 校验首都
    bj = next((c for c in cleaned if c["country_code"] == "CN" and c.get("name_en") == "Beijing"), None)
    print("北京:", bj)

    # 重写 cities.json
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False)
    print("已重写 cities.json")

    rebuild_db(cleaned)


if __name__ == "__main__":
    main()
