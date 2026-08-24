# -*- coding: utf-8 -*-
"""
修复城市数据的国家代码不一致问题
- 旧数据用 ISO 3166-1 数字代码 (如 392=日本, 328=圭亚那)
- GeoNames 新数据用 alpha-2 代码 (如 JP, GY)
统一为 alpha-2，并用英文名匹配补齐国家中文名
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(BASE, "server")
CITIES_JSON = os.path.join(SERVER, "data", "cities.json")

# 复用 import_all_cities.py 中的 ISO 映射
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_all_cities import ISO

# alpha2 -> 中文/英文
A2 = {code: val for code, val in ISO.items()}
# 英文名(小写) -> alpha2
EN2A2 = {en.lower(): code for code, (zh, en) in A2.items()}

NUM_RE = re.compile(r"^\d{3}$")


def main():
    with open(CITIES_JSON, encoding="utf-8") as f:
        arr = json.load(f)

    fixed = 0
    zh_filled = 0
    for c in arr:
        code = str(c.get("country_code", "") or "").strip()
        zh = c.get("country_zh") or ""
        en = c.get("country_en") or ""

        new_code = code
        if NUM_RE.match(code):
            # 数字代码 -> 用英文名反查 alpha-2
            new_code = EN2A2.get((en or "").lower(), code)
        elif code and code.upper() != code:
            new_code = code.upper()

        if new_code != code or code == "":
            if new_code != code:
                c["country_code"] = new_code
                fixed += 1

        # 补齐国家中文名
        if not zh and new_code in A2:
            c["country_zh"] = A2[new_code][0]
            zh_filled += 1
            zh = A2[new_code][0]
        if not en and new_code in A2:
            c["country_en"] = A2[new_code][1]

    with open(CITIES_JSON, "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=1)

    print(f"国家代码已统一: {fixed} 条")
    print(f"补齐国家中文名: {zh_filled} 条")
    empty = [c["name"] for c in arr if not c.get("country_zh")]
    print("剩余无国家中文名:", len(empty))

    # 同步数据库
    sys.path.insert(0, SERVER)
    from database import get_conn
    conn = get_conn()
    upd = 0
    for c in arr:
        n = c.get("name") or ""
        if not n:
            continue
        row = conn.execute("SELECT id FROM cities WHERE name=? LIMIT 1", (n,)).fetchone()
        if not row:
            continue
        conn.execute(
            "UPDATE cities SET country_code=?, country_zh=?, country_en=? WHERE id=?",
            (c.get("country_code", ""), c.get("country_zh", ""), c.get("country_en", ""), row["id"]),
        )
        upd += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) AS c FROM cities").fetchone()
    conn.close()
    print(f"数据库已更新: {upd} 行, 城市总数: {total['c']}")


if __name__ == "__main__":
    main()
