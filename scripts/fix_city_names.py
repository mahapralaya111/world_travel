# -*- coding: utf-8 -*-
"""
修正城市中文显示名（GeoNames alternatenames 提取到简称/区域名导致显示异常）
- 如 "沪"(Shanghai) -> "上海"、"宝安"(Shenzhen) -> "深圳"、"天府"(Chengdu) -> "成都"
- 同时更新 server/data/cities.json 与数据库 cities 表
用法: python scripts/fix_city_names.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, os.path.join(ROOT, "server"))
os.chdir(ROOT)

from database import query_all, execute  # noqa: E402

DATA_FILE = os.path.join(ROOT, "server", "data", "cities.json")

# (country_code, 当前 name, name_en) -> 正确中文名
FIXES = {
    ("CN", "沪", "Shanghai"): "上海",
    ("CN", "申", "Shanghai"): "上海",
    ("CN", "宝安", "Shenzhen"): "深圳",
    ("CN", "天府", "Chengdu"): "成都",
    ("CN", "蓉", "Chengdu"): "成都",
    ("CN", "津", "Tianjin"): "天津",
    ("CN", "渝", "Chongqing"): "重庆",
    ("CN", "穗", "Guangzhou"): "广州",
    ("CN", "汉", "Wuhan"): "武汉",
    ("CN", "杭", "Hangzhou"): "杭州",
    ("CN", "宁", "Nanjing"): "南京",
    ("CN", "甬", "Ningbo"): "宁波",
    ("CN", "苏", "Suzhou"): "苏州",
    ("CN", "锡", "Wuxi"): "无锡",
    ("CN", "郑", "Zhengzhou"): "郑州",
    ("CN", "沈", "Shenyang"): "沈阳",
    ("CN", "大", "Dalian"): "大连",
    ("CN", "厦", "Xiamen"): "厦门",
    ("CN", "鹭", "Xiamen"): "厦门",
    ("CN", "榕", "Fuzhou"): "福州",
    ("CN", "昆", "Kunming"): "昆明",
    ("CN", "兰", "Lanzhou"): "兰州",
    ("CN", "青", "Qingdao"): "青岛",
    ("CN", "邕", "Nanning"): "南宁",
    ("CN", "泉", "Quanzhou"): "泉州",
}


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        cities = json.load(f)

    # 统计修正数（可能重复）
    fixed = 0
    for c in cities:
        key = (c.get("country_code", ""), c.get("name", ""), c.get("name_en", ""))
        if key in FIXES:
            c["name"] = FIXES[key]
            fixed += 1

    # 数据库更新（按 name_en + country_code 匹配更新 name）
    db_updated = 0
    for (code, old_name, name_en), new_name in FIXES.items():
        rows = query_all(
            "SELECT id, name FROM cities WHERE country_code=? AND name=? AND name_en=?",
            (code, old_name, name_en))
        for r in rows:
            execute("UPDATE cities SET name=? WHERE id=?", (new_name, r["id"]))
            db_updated += 1

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(cities, f, ensure_ascii=False)

    print("cities.json 修正 %d 条，数据库修正 %d 条" % (fixed, db_updated))
    # 展示修正后的知名城市
    for name_en in ("Shanghai", "Shenzhen", "Chengdu", "Tianjin", "Chongqing"):
        c = next((x for x in cities if x.get("name_en") == name_en and x.get("country_code") == "CN"), None)
        if c:
            print(" ", name_en, "->", c.get("name"))


if __name__ == "__main__":
    main()
