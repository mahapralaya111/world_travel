# -*- coding: utf-8 -*-
"""
生成 server/data/country_codes.json
- 数据源: GeoNames countryInfo.txt (官方 ISO 3166-1: 数字码/两位码/英文名)
- 中文名: 从 cities.json 聚合 (country_code -> country_zh)
- 输出: [{"id": "156", "code": "CN", "en": "China", "zh": "中国"}, ...]
  用于前端把 GeoJSON 国家数字 id 映射为数据库两位国家码
"""
import json
import os
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DATA_DIR = os.path.join(ROOT, "server", "data")
INFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
INFO_FILE = os.path.join(BASE, ".countryInfo.txt")


def ensure_info_file():
    if os.path.exists(INFO_FILE) and os.path.getsize(INFO_FILE) > 1000:
        return
    print("下载 countryInfo.txt ...")
    req = urllib.request.Request(INFO_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read().decode("utf-8")
    with open(INFO_FILE, "w", encoding="utf-8") as f:
        f.write(data)
    print("已保存:", INFO_FILE)


def load_zh_map():
    """cities.json -> {code: zh}"""
    with open(os.path.join(DATA_DIR, "cities.json"), "r", encoding="utf-8") as f:
        cities = json.load(f)
    m = {}
    for c in cities:
        code = str(c.get("country_code", "")).upper()
        zh = (c.get("country_zh") or "").strip()
        if code and zh:
            m[code] = zh  # 后出现的覆盖（数据按国家分组，zh 一致）
    return m


def build():
    ensure_info_file()
    zh_map = load_zh_map()
    out = []
    with open(INFO_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            alpha2, _, numeric, _, en = parts[0], parts[1], parts[2], parts[3], parts[4]
            if not alpha2 or not numeric:
                continue
            out.append({
                "id": numeric,
                "code": alpha2,
                "en": en,
                "zh": zh_map.get(alpha2, en),
            })
    # 科索沃（ISO 未正式分配，world-atlas 中使用临时码）
    if not any(x["code"] == "XK" for x in out):
        out.append({"id": "383", "code": "XK", "en": "Kosovo", "zh": zh_map.get("XK", "科索沃")})
    out.sort(key=lambda x: int(x["id"]))
    target = os.path.join(DATA_DIR, "country_codes.json")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print("生成 %s, 共 %d 条" % (target, len(out)))
    print("样例:", json.dumps(out[:3], ensure_ascii=False))
    print("中国:", json.dumps(next(x for x in out if x["code"] == "CN"), ensure_ascii=False))
    print("美国:", json.dumps(next(x for x in out if x["code"] == "US"), ensure_ascii=False))


if __name__ == "__main__":
    build()
