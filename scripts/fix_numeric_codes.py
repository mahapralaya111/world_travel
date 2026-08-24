# -*- coding: utf-8 -*-
"""
修正城市表中国家数字码 -> ISO 两位码（旧数据遗留）
数字码: 180=CD(刚果金) 203=CZ(捷克) 336=VA(梵蒂冈) 626=TL(东帝汶)
同时更新 server/data/cities.json 与数据库 cities 表
用法: python scripts/fix_numeric_codes.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, os.path.join(ROOT, "server"))
os.chdir(ROOT)

from database import execute, query_all  # noqa: E402

DATA_FILE = os.path.join(ROOT, "server", "data", "cities.json")

NUMERIC_TO_ALPHA2 = {
    "180": "CD",  # 刚果民主共和国
    "203": "CZ",  # 捷克
    "336": "VA",  # 梵蒂冈
    "626": "TL",  # 东帝汶
}


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        cities = json.load(f)

    fixed_json = 0
    for c in cities:
        cc = c.get("country_code", "")
        if cc in NUMERIC_TO_ALPHA2:
            c["country_code"] = NUMERIC_TO_ALPHA2[cc]
            fixed_json += 1

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(cities, f, ensure_ascii=False)

    # 数据库同步
    db_updated = 0
    for num_code, a2 in NUMERIC_TO_ALPHA2.items():
        rows = query_all("SELECT id FROM cities WHERE country_code=?", (num_code,))
        for r in rows:
            execute("UPDATE cities SET country_code=? WHERE id=?", (a2, r["id"]))
            db_updated += 1

    print("cities.json 修正 %d 条，数据库修正 %d 条" % (fixed_json, db_updated))


if __name__ == "__main__":
    main()
